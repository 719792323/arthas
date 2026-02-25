"""
本地任务锁（LocalTaskLock）测试

测试锁的获取、释放、重入、超时清理和并发安全性。
"""

import asyncio

import pytest
import pytest_asyncio

from control_platform.lock.local_lock import LocalTaskLock


class TestLocalTaskLock:
    """本地锁基础操作测试"""

    @pytest_asyncio.fixture
    async def lock(self):
        """每个测试用例使用全新的 LocalTaskLock 实例"""
        return LocalTaskLock(ttl=1.0)  # 短 TTL 方便测试清理

    @pytest.mark.asyncio
    async def test_acquire_first_time_succeeds(self, lock: LocalTaskLock):
        """测试目的：首次获取某个 task_id 的锁应返回 True"""
        acquired = await lock.acquire("task-001")
        assert acquired is True

    @pytest.mark.asyncio
    async def test_acquire_same_task_fails(self, lock: LocalTaskLock):
        """测试目的：同一 task_id 重复获取锁，第二次应返回 False"""
        await lock.acquire("task-001")
        acquired = await lock.acquire("task-001")
        assert acquired is False

    @pytest.mark.asyncio
    async def test_release_then_reacquire(self, lock: LocalTaskLock):
        """测试目的：释放锁后应能重新获取"""
        await lock.acquire("task-001")
        await lock.release("task-001")
        acquired = await lock.acquire("task-001")
        assert acquired is True

    @pytest.mark.asyncio
    async def test_release_unheld_lock_no_error(self, lock: LocalTaskLock):
        """测试目的：释放未持有的锁不应抛出异常（仅 warning）"""
        await lock.release("non-existent-task")  # 不应报错

    @pytest.mark.asyncio
    async def test_different_tasks_independent(self, lock: LocalTaskLock):
        """测试目的：不同 task_id 的锁互不干扰，可同时持有"""
        a = await lock.acquire("task-a")
        b = await lock.acquire("task-b")
        assert a is True
        assert b is True

    @pytest.mark.asyncio
    async def test_lock_count(self, lock: LocalTaskLock):
        """测试目的：lock_count 应正确反映当前管理的锁总数"""
        await lock.acquire("task-1")
        await lock.acquire("task-2")
        assert lock.lock_count == 2

    @pytest.mark.asyncio
    async def test_held_lock_count(self, lock: LocalTaskLock):
        """测试目的：held_lock_count 应正确反映当前被持有的锁数量"""
        await lock.acquire("task-1")
        await lock.acquire("task-2")
        assert lock.held_lock_count == 2

        await lock.release("task-1")
        assert lock.held_lock_count == 1

    @pytest.mark.asyncio
    async def test_cleanup_stale_locks(self, lock: LocalTaskLock):
        """测试目的：cleanup_stale_locks 应清除已过期且未被持有的锁"""
        await lock.acquire("task-old")
        await lock.release("task-old")

        # 等待超过 TTL
        await asyncio.sleep(1.2)

        cleaned = await lock.cleanup_stale_locks()
        assert cleaned == 1
        assert lock.lock_count == 0

    @pytest.mark.asyncio
    async def test_cleanup_does_not_remove_held_locks(self, lock: LocalTaskLock):
        """测试目的：cleanup_stale_locks 不应清除仍被持有的锁，即使已超过 TTL"""
        await lock.acquire("task-held")
        await asyncio.sleep(1.2)  # 超过 TTL

        cleaned = await lock.cleanup_stale_locks()
        assert cleaned == 0  # 被持有的锁不应被清理
        assert lock.lock_count == 1


class TestLocalTaskLockConcurrency:
    """并发场景测试"""

    @pytest.mark.asyncio
    async def test_concurrent_acquire_same_task(self):
        """测试目的：多个协程同时获取同一 task_id 的锁，应只有一个成功"""
        lock = LocalTaskLock()
        results = []

        async def try_acquire(task_id: str, index: int):
            acquired = await lock.acquire(task_id)
            results.append((index, acquired))

        # 并发 10 个协程尝试获取同一把锁
        tasks = [try_acquire("task-concurrent", i) for i in range(10)]
        await asyncio.gather(*tasks)

        # 验证：只有 1 个成功获取
        success_count = sum(1 for _, acquired in results if acquired)
        assert success_count == 1, f"期望只有 1 个成功，实际 {success_count} 个"
