"""
锁工厂模块

根据配置自动创建对应的 TaskLock 实例，
实现业务代码与锁具体实现的解耦。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from control_platform.lock.base import TaskLock
from control_platform.lock.local_lock import LocalTaskLock

if TYPE_CHECKING:
    from control_platform.config import Settings

logger = logging.getLogger(__name__)


def create_task_lock(settings: Settings) -> TaskLock:
    """
    根据配置创建对应的 TaskLock 实例

    Args:
        settings: 全局配置对象

    Returns:
        TaskLock 实例（LocalTaskLock 或 RedisTaskLock）

    Raises:
        ValueError: lock_type 值不在支持的列表中
    """
    lock_type = settings.lock_type

    if lock_type == "local":
        logger.info("使用本地锁实现: LocalTaskLock, ttl=%ds", settings.lock_ttl)
        return LocalTaskLock(ttl=float(settings.lock_ttl))

    elif lock_type == "redis":
        # 延迟导入，避免未安装 redis 包时影响本地锁模式
        from control_platform.lock.redis_lock import RedisTaskLock

        logger.info(
            "使用 Redis 分布式锁实现: RedisTaskLock, redis_url=%s, ttl=%ds, prefix=%s, watchdog=%s",
            settings.redis_url,
            settings.lock_ttl,
            settings.lock_key_prefix,
            settings.lock_watchdog_enabled,
        )
        return RedisTaskLock(
            redis_url=settings.redis_url,
            ttl=settings.lock_ttl,
            key_prefix=settings.lock_key_prefix,
            watchdog_enabled=settings.lock_watchdog_enabled,
        )

    else:
        raise ValueError(
            f"不支持的锁类型: lock_type='{lock_type}'，"
            f"支持的值为: ['local', 'redis']"
        )
