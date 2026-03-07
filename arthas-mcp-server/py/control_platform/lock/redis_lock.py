"""
基于 Redis 的分布式任务锁实现

使用 Redis SET NX EX 实现分布式互斥锁，
通过 Lua 脚本保证释放/续期的原子性，
内置看门狗（Watchdog）机制自动续期防止长任务锁过期。
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from typing import Dict, Optional

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from control_platform.lock.base import TaskLock

logger = logging.getLogger(__name__)

# 释放锁的 Lua 脚本：原子性校验 owner 后 DEL
# KEYS[1] = 锁键名, ARGV[1] = owner_id
# 返回 1 表示释放成功，0 表示 owner 不匹配（锁不属于当前实例）
RELEASE_LOCK_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""

# 续期锁的 Lua 脚本：原子性校验 owner 后 PEXPIRE
# KEYS[1] = 锁键名, ARGV[1] = owner_id, ARGV[2] = TTL 毫秒数
# 返回 1 表示续期成功，0 表示 owner 不匹配
RENEW_LOCK_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("PEXPIRE", KEYS[1], ARGV[2])
else
    return 0
end
"""


class RedisTaskLock(TaskLock):
    """
    基于 Redis 的分布式任务锁

    特性：
    - 使用 SET NX EX 原子获取锁
    - 使用 Lua 脚本原子释放/续期锁，防止误操作
    - 每个实例生成唯一 owner_id，确保锁持有者身份校验
    - 内置看门狗机制，按 TTL/3 间隔自动续期
    - Redis 异常时快速失败，不阻塞业务调度
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        ttl: int = 300,
        key_prefix: str = "arthas:lock:",
        watchdog_enabled: bool = True,
    ):
        """
        初始化 Redis 分布式锁

        Args:
            redis_url: Redis 连接 URL
            ttl: 锁的 TTL 秒数
            key_prefix: Redis 锁键前缀
            watchdog_enabled: 是否启用看门狗自动续期
        """
        self._redis_url = redis_url
        self._ttl = ttl
        self._key_prefix = key_prefix
        self._watchdog_enabled = watchdog_enabled

        # 生成唯一 owner 标识：{hostname}:{pid}:{uuid4}
        hostname = socket.gethostname()
        pid = os.getpid()
        unique_id = uuid.uuid4().hex[:8]
        self._owner_id = f"{hostname}:{pid}:{unique_id}"

        # 创建 Redis 异步连接实例
        self._redis: aioredis.Redis = aioredis.from_url(
            redis_url,
            decode_responses=True,
        )

        # 看门狗任务映射表：task_id -> asyncio.Task
        self._watchdog_tasks: Dict[str, asyncio.Task] = {}

        # 预注册 Lua 脚本（延迟注册，在首次使用时完成）
        self._release_script: Optional[aioredis.client.Script] = None
        self._renew_script: Optional[aioredis.client.Script] = None

        logger.info(
            "RedisTaskLock 初始化完成: owner=%s, ttl=%ds, prefix=%s, watchdog=%s",
            self._owner_id, self._ttl, self._key_prefix, self._watchdog_enabled,
        )

    def _get_lock_key(self, task_id: str) -> str:
        """获取锁的 Redis 键名"""
        return f"{self._key_prefix}{task_id}"

    async def _ensure_scripts(self) -> None:
        """确保 Lua 脚本已注册"""
        if self._release_script is None:
            self._release_script = self._redis.register_script(RELEASE_LOCK_SCRIPT)
        if self._renew_script is None:
            self._renew_script = self._redis.register_script(RENEW_LOCK_SCRIPT)

    async def acquire(self, task_id: str) -> bool:
        """
        尝试获取指定 taskId 的分布式锁（非阻塞）

        使用 Redis SET NX EX 原子命令获取锁。
        成功时启动看门狗任务（如已启用）。

        Args:
            task_id: 任务 ID

        Returns:
            True 表示获取成功，False 表示锁已被占用或 Redis 异常
        """
        lock_key = self._get_lock_key(task_id)
        try:
            result = await self._redis.set(
                name=lock_key,
                value=self._owner_id,
                nx=True,
                ex=self._ttl,
            )
            if result:
                logger.debug(
                    "锁获取成功: task_id=%s, owner=%s, ttl=%ds",
                    task_id, self._owner_id, self._ttl,
                )
                # 启动看门狗
                if self._watchdog_enabled:
                    self._start_watchdog(task_id)
                return True
            else:
                logger.debug("锁获取失败（已被占用）: task_id=%s", task_id)
                return False
        except RedisError as e:
            logger.error("锁获取异常: task_id=%s, error=%s", task_id, e)
            return False

    async def release(self, task_id: str) -> None:
        """
        释放指定 taskId 的分布式锁

        先取消看门狗任务，再使用 Lua 脚本原子性校验 owner 并释放锁。

        Args:
            task_id: 任务 ID
        """
        # 先停止看门狗
        await self._stop_watchdog(task_id)

        lock_key = self._get_lock_key(task_id)
        try:
            await self._ensure_scripts()
            result = await self._release_script(
                keys=[lock_key],
                args=[self._owner_id],
            )
            if result == 1:
                logger.debug("锁释放成功: task_id=%s, owner=%s", task_id, self._owner_id)
            else:
                logger.warning(
                    "锁释放失败（owner 不匹配或锁已过期）: task_id=%s, owner=%s",
                    task_id, self._owner_id,
                )
        except RedisError as e:
            logger.error(
                "锁释放异常（将由 TTL 自动过期）: task_id=%s, error=%s",
                task_id, e,
            )

    # ==================== 看门狗（Watchdog）机制 ====================

    def _start_watchdog(self, task_id: str) -> None:
        """
        启动看门狗后台任务

        Args:
            task_id: 任务 ID
        """
        if task_id in self._watchdog_tasks:
            logger.warning("看门狗已存在，跳过启动: task_id=%s", task_id)
            return

        task = asyncio.create_task(
            self._watchdog_loop(task_id),
            name=f"watchdog-{task_id}",
        )
        self._watchdog_tasks[task_id] = task
        logger.debug(
            "看门狗已启动: task_id=%s, 续期间隔=%ds",
            task_id, self._ttl // 3,
        )

    async def _stop_watchdog(self, task_id: str) -> None:
        """
        停止看门狗后台任务

        Args:
            task_id: 任务 ID
        """
        task = self._watchdog_tasks.pop(task_id, None)
        if task is None:
            return

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # 优雅处理取消
        logger.debug("看门狗已停止: task_id=%s", task_id)

    async def _watchdog_loop(self, task_id: str) -> None:
        """
        看门狗续期循环

        按 TTL/3 间隔持续续期锁的过期时间，
        直到锁被释放、续期失败或 Redis 异常。

        Args:
            task_id: 任务 ID
        """
        lock_key = self._get_lock_key(task_id)
        renew_interval = self._ttl / 3  # 续期间隔：TTL 的 1/3
        ttl_ms = self._ttl * 1000  # TTL 毫秒数

        try:
            await self._ensure_scripts()
            while True:
                await asyncio.sleep(renew_interval)
                try:
                    result = await self._renew_script(
                        keys=[lock_key],
                        args=[self._owner_id, ttl_ms],
                    )
                    if result == 1:
                        logger.debug(
                            "看门狗续期成功: task_id=%s, owner=%s",
                            task_id, self._owner_id,
                        )
                    else:
                        logger.warning(
                            "看门狗续期失败（owner 不匹配或锁已过期）: task_id=%s",
                            task_id,
                        )
                        break  # 停止续期
                except RedisError as e:
                    logger.warning(
                        "看门狗续期异常，停止续期: task_id=%s, error=%s",
                        task_id, e,
                    )
                    break  # Redis 异常时停止续期
        except asyncio.CancelledError:
            # release 时会 cancel 看门狗任务，优雅退出
            raise
        finally:
            # 清理映射表（如果是非 cancel 导致的退出）
            self._watchdog_tasks.pop(task_id, None)

    async def close(self) -> None:
        """
        关闭 Redis 连接池

        应在应用关闭时调用，释放所有资源。
        """
        # 停止所有看门狗任务
        task_ids = list(self._watchdog_tasks.keys())
        for task_id in task_ids:
            await self._stop_watchdog(task_id)

        # 关闭 Redis 连接
        await self._redis.aclose()
        logger.info("RedisTaskLock 已关闭: owner=%s", self._owner_id)
