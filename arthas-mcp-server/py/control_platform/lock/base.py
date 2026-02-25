"""
TaskLock 抽象接口

定义任务锁的抽象层，默认提供本地 asyncio.Lock 实现，
后续可替换为分布式锁实现（如 Redis）而无需修改业务代码。
"""

from __future__ import annotations

import abc
from contextlib import asynccontextmanager


class TaskLockNotAcquired(Exception):
    """任务锁获取失败异常"""

    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(f"无法获取任务锁: task_id={task_id}")


class TaskLock(abc.ABC):
    """
    任务锁抽象基类

    确保同一个 taskId 的任务不会被并发执行。
    实现类需要提供 acquire 和 release 方法。
    """

    @abc.abstractmethod
    async def acquire(self, task_id: str) -> bool:
        """
        尝试获取指定 taskId 的锁（非阻塞）

        Args:
            task_id: 任务 ID

        Returns:
            True 表示获取成功，False 表示锁已被占用
        """
        ...

    @abc.abstractmethod
    async def release(self, task_id: str) -> None:
        """
        释放指定 taskId 的锁

        Args:
            task_id: 任务 ID
        """
        ...

    @asynccontextmanager
    async def locked(self, task_id: str):
        """
        统一的锁上下文管理器

        所有业务代码通过 `async with task_lock.locked(task_id)` 使用锁。
        获取失败抛出 TaskLockNotAcquired，保证 acquire/release 成对出现。

        Usage:
            try:
                async with task_lock.locked(task_id):
                    # 在锁保护下执行业务逻辑
                    ...
            except TaskLockNotAcquired:
                # 锁被占用，跳过
                pass

        Args:
            task_id: 任务 ID

        Raises:
            TaskLockNotAcquired: 锁获取失败
        """
        acquired = await self.acquire(task_id)
        if not acquired:
            raise TaskLockNotAcquired(task_id)
        try:
            yield
        finally:
            await self.release(task_id)