"""
本地 TaskLock 实现

基于 asyncio.Lock 的本地锁实现，每个 taskId 一把锁。
适用于单进程部署场景，后续可替换为分布式锁。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict

from control_platform.lock.base import TaskLock

logger = logging.getLogger(__name__)


class LocalTaskLock(TaskLock):
    """
    本地任务锁实现

    为每个 taskId 维护一把 asyncio.Lock，使用非阻塞模式尝试获取。
    支持自动清理长时间未使用的锁（基于 TTL）。

    Attributes:
        _locks: taskId → asyncio.Lock 映射
        _lock_times: taskId → 最后访问时间映射
        _meta_lock: 保护 _locks 字典的元锁
        _ttl: 锁的存活时间（秒），超过此时间未使用则清理
    """

    def __init__(self, ttl: float = 300.0):
        """
        Args:
            ttl: 锁的 TTL（秒），默认 5 分钟
        """
        self._locks: Dict[str, asyncio.Lock] = {}
        self._lock_times: Dict[str, float] = {}
        self._meta_lock = asyncio.Lock()
        self._ttl = ttl

    async def acquire(self, task_id: str) -> bool:
        """
        非阻塞尝试获取 taskId 对应的锁

        Args:
            task_id: 任务 ID

        Returns:
            True 表示获取成功，False 表示锁已被占用
        """
        lock = await self._get_or_create_lock(task_id)

        # 非阻塞尝试获取锁：直接用 wait_for + 极短超时，避免 check-then-act 竞态条件
        try:
            await asyncio.wait_for(lock.acquire(), timeout=0.01)
            self._lock_times[task_id] = time.time()
            logger.debug(f"🔓 获取锁成功: task_id={task_id}")
            return True
        except asyncio.TimeoutError:
            logger.debug(f"🔒 锁已被占用: task_id={task_id}")
            return False

    async def release(self, task_id: str) -> None:
        """
        释放 taskId 对应的锁

        Args:
            task_id: 任务 ID
        """
        async with self._meta_lock:
            lock = self._locks.get(task_id)

        if lock and lock.locked():
            lock.release()
            logger.debug(f"🔓 释放锁: task_id={task_id}")
        else:
            logger.warning(f"尝试释放未持有的锁: task_id={task_id}")

    async def _get_or_create_lock(self, task_id: str) -> asyncio.Lock:
        """获取或创建 taskId 对应的锁"""
        async with self._meta_lock:
            if task_id not in self._locks:
                self._locks[task_id] = asyncio.Lock()
                self._lock_times[task_id] = time.time()
            return self._locks[task_id]

    async def cleanup_stale_locks(self) -> int:
        """
        清理过期的锁（未被持有且超过 TTL）

        Returns:
            清理的锁数量
        """
        now = time.time()
        stale_keys = []

        async with self._meta_lock:
            for task_id, last_time in self._lock_times.items():
                if now - last_time > self._ttl:
                    lock = self._locks.get(task_id)
                    if lock and not lock.locked():
                        stale_keys.append(task_id)

            for key in stale_keys:
                del self._locks[key]
                del self._lock_times[key]

        if stale_keys:
            logger.info(f"🧹 清理过期锁: {len(stale_keys)} 个")
        return len(stale_keys)

    @property
    def lock_count(self) -> int:
        """当前锁数量"""
        return len(self._locks)

    @property
    def held_lock_count(self) -> int:
        """当前被持有的锁数量"""
        return sum(1 for lock in self._locks.values() if lock.locked())
