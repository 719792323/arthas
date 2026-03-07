"""
lock 模块公共接口

提供任务锁的抽象基类、异常、本地实现、Redis 分布式实现及工厂方法。
外部模块统一通过此入口导入。
"""

from control_platform.lock.base import TaskLock, TaskLockNotAcquired
from control_platform.lock.local_lock import LocalTaskLock
from control_platform.lock.redis_lock import RedisTaskLock
from control_platform.lock.factory import create_task_lock

__all__ = [
    "TaskLock",
    "TaskLockNotAcquired",
    "LocalTaskLock",
    "RedisTaskLock",
    "create_task_lock",
]
