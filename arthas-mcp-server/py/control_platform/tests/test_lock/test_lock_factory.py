"""
锁工厂（create_task_lock）测试

测试工厂方法根据不同配置正确创建对应的 TaskLock 实例。
"""

from unittest.mock import MagicMock

import pytest

from control_platform.lock.factory import create_task_lock
from control_platform.lock.local_lock import LocalTaskLock
from control_platform.lock.redis_lock import RedisTaskLock


def _mock_settings(**overrides) -> MagicMock:
    """创建模拟的 Settings 对象"""
    defaults = {
        "lock_type": "local",
        "redis_url": "redis://localhost:6379/0",
        "lock_ttl": 300,
        "lock_key_prefix": "arthas:lock:",
        "lock_watchdog_enabled": True,
    }
    defaults.update(overrides)
    settings = MagicMock()
    for k, v in defaults.items():
        setattr(settings, k, v)
    return settings


class TestCreateTaskLockLocal:
    """本地锁工厂测试"""

    def test_local_lock_type_returns_local_task_lock(self):
        """测试目的：lock_type='local' 时应返回 LocalTaskLock 实例"""
        settings = _mock_settings(lock_type="local")
        lock = create_task_lock(settings)

        assert isinstance(lock, LocalTaskLock)

    def test_local_lock_uses_configured_ttl(self):
        """测试目的：工厂应将 lock_ttl 传递给 LocalTaskLock"""
        settings = _mock_settings(lock_type="local", lock_ttl=600)
        lock = create_task_lock(settings)

        assert isinstance(lock, LocalTaskLock)
        assert lock._ttl == 600.0


class TestCreateTaskLockRedis:
    """Redis 锁工厂测试"""

    def test_redis_lock_type_returns_redis_task_lock(self):
        """测试目的：lock_type='redis' 时应返回 RedisTaskLock 实例"""
        settings = _mock_settings(lock_type="redis")
        lock = create_task_lock(settings)

        assert isinstance(lock, RedisTaskLock)

    def test_redis_lock_uses_configured_params(self):
        """测试目的：工厂应将所有配置参数正确传递给 RedisTaskLock"""
        settings = _mock_settings(
            lock_type="redis",
            redis_url="redis://myhost:6380/1",
            lock_ttl=120,
            lock_key_prefix="custom:prefix:",
            lock_watchdog_enabled=False,
        )
        lock = create_task_lock(settings)

        assert isinstance(lock, RedisTaskLock)
        assert lock._redis_url == "redis://myhost:6380/1"
        assert lock._ttl == 120
        assert lock._key_prefix == "custom:prefix:"
        assert lock._watchdog_enabled is False


class TestCreateTaskLockInvalid:
    """无效配置测试"""

    def test_invalid_lock_type_raises_value_error(self):
        """测试目的：不支持的 lock_type 值应抛出 ValueError"""
        settings = _mock_settings(lock_type="memcached")

        with pytest.raises(ValueError, match="不支持的锁类型"):
            create_task_lock(settings)

    def test_empty_lock_type_raises_value_error(self):
        """测试目的：空字符串 lock_type 应抛出 ValueError"""
        settings = _mock_settings(lock_type="")

        with pytest.raises(ValueError, match="不支持的锁类型"):
            create_task_lock(settings)
