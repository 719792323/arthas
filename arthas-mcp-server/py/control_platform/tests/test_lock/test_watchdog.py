"""
看门狗（Watchdog）自动续期机制测试

测试看门狗的启动、续期、取消、异常处理和配置开关。
使用 unittest.mock.AsyncMock 模拟 Redis 客户端，不依赖真实 Redis。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from redis.exceptions import RedisError

from control_platform.lock.redis_lock import RedisTaskLock


def _create_lock(watchdog_enabled: bool = True, ttl: int = 3) -> RedisTaskLock:
    """创建 RedisTaskLock 实例并替换 Redis 客户端为 mock（短 TTL 方便测试）"""
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
    lock._release_script = AsyncMock(return_value=1)
    lock._renew_script = AsyncMock(return_value=1)
    return lock


class TestWatchdogStart:
    """看门狗启动测试"""

    @pytest.mark.asyncio
    async def test_watchdog_started_after_acquire(self):
        """测试目的：acquire 成功后，_watchdog_tasks 中应存在对应 task_id 的 asyncio.Task"""
        lock = _create_lock(watchdog_enabled=True)
        lock._redis.set = AsyncMock(return_value=True)

        await lock.acquire("task-001")

        assert "task-001" in lock._watchdog_tasks
        task = lock._watchdog_tasks["task-001"]
        assert isinstance(task, asyncio.Task)
        assert not task.done()

        # 清理
        await lock._stop_watchdog("task-001")

    @pytest.mark.asyncio
    async def test_watchdog_not_started_when_disabled(self):
        """测试目的：watchdog_enabled=False 时，acquire 成功后不应启动看门狗"""
        lock = _create_lock(watchdog_enabled=False)
        lock._redis.set = AsyncMock(return_value=True)

        await lock.acquire("task-001")

        assert "task-001" not in lock._watchdog_tasks

    @pytest.mark.asyncio
    async def test_watchdog_not_started_on_acquire_failure(self):
        """测试目的：acquire 失败时，不应启动看门狗"""
        lock = _create_lock(watchdog_enabled=True)
        lock._redis.set = AsyncMock(return_value=None)

        await lock.acquire("task-001")

        assert "task-001" not in lock._watchdog_tasks


class TestWatchdogRenewal:
    """看门狗续期测试"""

    @pytest.mark.asyncio
    async def test_watchdog_calls_renew_script(self):
        """测试目的：看门狗应按预期间隔调用续期 Lua 脚本"""
        lock = _create_lock(watchdog_enabled=True, ttl=3)
        lock._redis.set = AsyncMock(return_value=True)
        lock._renew_script = AsyncMock(return_value=1)

        await lock.acquire("task-001")

        # 等待看门狗执行一次续期（TTL/3 = 1 秒）
        await asyncio.sleep(1.2)

        # 验证续期脚本被调用
        lock._renew_script.assert_awaited()
        call_args = lock._renew_script.call_args
        assert call_args.kwargs["keys"] == ["test:lock:task-001"]
        assert call_args.kwargs["args"][0] == lock._owner_id
        assert call_args.kwargs["args"][1] == 3000  # TTL 毫秒数

        # 清理
        await lock._stop_watchdog("task-001")

    @pytest.mark.asyncio
    async def test_watchdog_stops_on_renew_owner_mismatch(self):
        """测试目的：续期 Lua 脚本返回 0（owner 不匹配）时，看门狗应自动停止"""
        lock = _create_lock(watchdog_enabled=True, ttl=3)
        lock._redis.set = AsyncMock(return_value=True)
        # 第一次续期就返回 0（owner 不匹配）
        lock._renew_script = AsyncMock(return_value=0)

        await lock.acquire("task-001")

        # 等待看门狗尝试续期并停止
        await asyncio.sleep(1.5)

        # 看门狗应已自动退出并从映射表中清理
        assert "task-001" not in lock._watchdog_tasks

    @pytest.mark.asyncio
    async def test_watchdog_stops_on_redis_error(self):
        """测试目的：续期时 Redis 异常，看门狗应停止并记录 WARNING 日志"""
        lock = _create_lock(watchdog_enabled=True, ttl=3)
        lock._redis.set = AsyncMock(return_value=True)
        lock._renew_script = AsyncMock(side_effect=RedisError("Connection lost"))

        await lock.acquire("task-001")

        # 等待看门狗尝试续期并因异常停止
        await asyncio.sleep(1.5)

        # 看门狗应已自动退出
        assert "task-001" not in lock._watchdog_tasks


class TestWatchdogCancel:
    """看门狗取消测试"""

    @pytest.mark.asyncio
    async def test_release_cancels_watchdog(self):
        """测试目的：release 后，看门狗任务应被取消并从映射表中移除"""
        lock = _create_lock(watchdog_enabled=True, ttl=30)
        lock._redis.set = AsyncMock(return_value=True)
        lock._release_script = AsyncMock(return_value=1)

        await lock.acquire("task-001")
        assert "task-001" in lock._watchdog_tasks
        watchdog_task = lock._watchdog_tasks["task-001"]

        await lock.release("task-001")

        # 看门狗应已被取消
        assert "task-001" not in lock._watchdog_tasks
        assert watchdog_task.cancelled() or watchdog_task.done()

    @pytest.mark.asyncio
    async def test_stop_watchdog_handles_cancelled_error(self):
        """测试目的：停止看门狗时应优雅处理 CancelledError，不产生未处理异常"""
        lock = _create_lock(watchdog_enabled=True, ttl=30)
        lock._redis.set = AsyncMock(return_value=True)

        await lock.acquire("task-001")
        assert "task-001" in lock._watchdog_tasks

        # 手动停止看门狗（模拟 release 的行为）
        await lock._stop_watchdog("task-001")

        # 不应抛异常，且映射表已清理
        assert "task-001" not in lock._watchdog_tasks

    @pytest.mark.asyncio
    async def test_stop_watchdog_nonexistent_task_no_error(self):
        """测试目的：停止不存在的看门狗不应抛异常"""
        lock = _create_lock(watchdog_enabled=True)

        # 不应抛异常
        await lock._stop_watchdog("nonexistent-task")

    @pytest.mark.asyncio
    async def test_release_does_not_renew_after_cancel(self):
        """测试目的：release 后看门狗不应继续续期"""
        lock = _create_lock(watchdog_enabled=True, ttl=3)
        lock._redis.set = AsyncMock(return_value=True)
        lock._release_script = AsyncMock(return_value=1)
        lock._renew_script = AsyncMock(return_value=1)

        await lock.acquire("task-001")
        await lock.release("task-001")

        # 记录 release 后的调用次数
        call_count_after_release = lock._renew_script.await_count

        # 等待一个续期周期
        await asyncio.sleep(1.5)

        # 续期次数不应增加
        assert lock._renew_script.await_count == call_count_after_release


class TestWatchdogMultipleTasks:
    """多任务看门狗测试"""

    @pytest.mark.asyncio
    async def test_independent_watchdogs_per_task(self):
        """测试目的：不同 task_id 应有独立的看门狗任务"""
        lock = _create_lock(watchdog_enabled=True, ttl=30)
        lock._redis.set = AsyncMock(return_value=True)
        lock._release_script = AsyncMock(return_value=1)

        await lock.acquire("task-001")
        await lock.acquire("task-002")

        assert "task-001" in lock._watchdog_tasks
        assert "task-002" in lock._watchdog_tasks
        assert lock._watchdog_tasks["task-001"] != lock._watchdog_tasks["task-002"]

        # 释放 task-001，task-002 的看门狗不受影响
        await lock.release("task-001")
        assert "task-001" not in lock._watchdog_tasks
        assert "task-002" in lock._watchdog_tasks

        # 清理
        await lock._stop_watchdog("task-002")
