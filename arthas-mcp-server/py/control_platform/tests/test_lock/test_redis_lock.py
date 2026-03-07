"""
RedisTaskLock 单元测试

使用 unittest.mock.AsyncMock 模拟 Redis 客户端，不依赖真实 Redis。
测试 acquire/release 核心逻辑、owner 校验、Redis 异常降级。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from redis.exceptions import RedisError

from control_platform.lock.redis_lock import RedisTaskLock


def _create_lock(watchdog_enabled: bool = False, ttl: int = 30) -> RedisTaskLock:
    """创建 RedisTaskLock 实例并替换 Redis 客户端为 mock"""
    lock = RedisTaskLock(
        redis_url="redis://localhost:6379/0",
        ttl=ttl,
        key_prefix="test:lock:",
        watchdog_enabled=watchdog_enabled,
    )
    # 替换 Redis 客户端为 AsyncMock
    mock_redis = AsyncMock()
    mock_redis.register_script = MagicMock()
    lock._redis = mock_redis
    # 预设 Lua 脚本为 AsyncMock
    lock._release_script = AsyncMock()
    lock._renew_script = AsyncMock()
    return lock


class TestRedisTaskLockAcquire:
    """锁获取测试"""

    @pytest.mark.asyncio
    async def test_acquire_success(self):
        """测试目的：SET NX EX 返回 True 时，acquire 应返回 True"""
        lock = _create_lock()
        lock._redis.set = AsyncMock(return_value=True)

        result = await lock.acquire("task-001")

        assert result is True
        lock._redis.set.assert_awaited_once_with(
            name="test:lock:task-001",
            value=lock._owner_id,
            nx=True,
            ex=lock._ttl,
        )

    @pytest.mark.asyncio
    async def test_acquire_fail_already_held(self):
        """测试目的：SET NX EX 返回 None 时（锁已被占用），acquire 应返回 False"""
        lock = _create_lock()
        lock._redis.set = AsyncMock(return_value=None)

        result = await lock.acquire("task-001")

        assert result is False

    @pytest.mark.asyncio
    async def test_acquire_redis_error_returns_false(self):
        """测试目的：Redis 异常时，acquire 应返回 False 且不抛异常"""
        lock = _create_lock()
        lock._redis.set = AsyncMock(side_effect=RedisError("Connection refused"))

        result = await lock.acquire("task-001")

        assert result is False

    @pytest.mark.asyncio
    async def test_acquire_starts_watchdog_when_enabled(self):
        """测试目的：watchdog_enabled=True 且 acquire 成功时，应启动看门狗"""
        lock = _create_lock(watchdog_enabled=True, ttl=30)
        lock._redis.set = AsyncMock(return_value=True)

        result = await lock.acquire("task-001")

        assert result is True
        assert "task-001" in lock._watchdog_tasks

        # 清理看门狗
        await lock._stop_watchdog("task-001")

    @pytest.mark.asyncio
    async def test_acquire_no_watchdog_when_disabled(self):
        """测试目的：watchdog_enabled=False 且 acquire 成功时，不应启动看门狗"""
        lock = _create_lock(watchdog_enabled=False)
        lock._redis.set = AsyncMock(return_value=True)

        result = await lock.acquire("task-001")

        assert result is True
        assert "task-001" not in lock._watchdog_tasks


class TestRedisTaskLockRelease:
    """锁释放测试"""

    @pytest.mark.asyncio
    async def test_release_success(self):
        """测试目的：Lua 脚本返回 1 时，release 应成功（不抛异常）"""
        lock = _create_lock()
        lock._release_script = AsyncMock(return_value=1)

        await lock.release("task-001")

        lock._release_script.assert_awaited_once_with(
            keys=["test:lock:task-001"],
            args=[lock._owner_id],
        )

    @pytest.mark.asyncio
    async def test_release_owner_mismatch(self):
        """测试目的：Lua 脚本返回 0（owner 不匹配）时，应记录 WARNING 但不抛异常"""
        lock = _create_lock()
        lock._release_script = AsyncMock(return_value=0)

        # 不应抛异常
        await lock.release("task-001")

        lock._release_script.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_release_redis_error_no_exception(self):
        """测试目的：Redis 异常时，release 应记录日志但不抛异常"""
        lock = _create_lock()
        lock._release_script = AsyncMock(side_effect=RedisError("Connection lost"))

        # 不应抛异常
        await lock.release("task-001")

    @pytest.mark.asyncio
    async def test_release_stops_watchdog(self):
        """测试目的：release 时应先停止看门狗任务"""
        lock = _create_lock(watchdog_enabled=True, ttl=30)
        lock._redis.set = AsyncMock(return_value=True)
        lock._release_script = AsyncMock(return_value=1)

        # 先获取锁（启动看门狗）
        await lock.acquire("task-001")
        assert "task-001" in lock._watchdog_tasks

        # 释放锁（应停止看门狗）
        await lock.release("task-001")
        assert "task-001" not in lock._watchdog_tasks


class TestRedisTaskLockOwner:
    """Owner 唯一性测试"""

    def test_owner_id_unique(self):
        """测试目的：不同 RedisTaskLock 实例应有不同的 owner_id"""
        lock1 = RedisTaskLock(redis_url="redis://localhost:6379/0")
        lock2 = RedisTaskLock(redis_url="redis://localhost:6379/0")

        assert lock1._owner_id != lock2._owner_id

    def test_owner_id_format(self):
        """测试目的：owner_id 格式应为 {hostname}:{pid}:{uuid}"""
        lock = RedisTaskLock(redis_url="redis://localhost:6379/0")
        parts = lock._owner_id.split(":")

        assert len(parts) == 3
        # 第二部分应为 PID（数字）
        assert parts[1].isdigit()

    def test_lock_key_prefix(self):
        """测试目的：lock key 应包含配置的前缀"""
        lock = RedisTaskLock(
            redis_url="redis://localhost:6379/0",
            key_prefix="myapp:lock:",
        )
        key = lock._get_lock_key("task-abc")
        assert key == "myapp:lock:task-abc"


class TestRedisTaskLockClose:
    """关闭/资源释放测试"""

    @pytest.mark.asyncio
    async def test_close_stops_all_watchdogs_and_redis(self):
        """测试目的：close 应停止所有看门狗并关闭 Redis 连接"""
        lock = _create_lock(watchdog_enabled=True, ttl=30)
        lock._redis.set = AsyncMock(return_value=True)
        lock._redis.aclose = AsyncMock()

        # 获取多个锁
        await lock.acquire("task-001")
        await lock.acquire("task-002")
        assert len(lock._watchdog_tasks) == 2

        # 关闭
        await lock.close()
        assert len(lock._watchdog_tasks) == 0
        lock._redis.aclose.assert_awaited_once()
