"""
执行池 + 锁集成测试

验证 TaskPool 在各种场景下的正确性：
1. locked() 基本行为（acquire 成功 → yield → release）
2. locked() 获取失败抛 TaskLockNotAcquired
3. locked() 内异常仍然释放锁
4. TaskPool._execute_stage 锁排他性
5. pool.submit 提交后正确加解锁
6. 链式执行（handler 返回 next_stage）的锁安全性
7. 并发多任务各自独立加锁不阻塞
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from control_platform.db.models import (
    DiagnosisStage,
    DiagnosisTask,
    StageStatus,
    StageType,
    TaskStatus,
)
from control_platform.db.repository import DiagnosisRepository
from control_platform.event.handler import StageHandler, StageHandlerRegistry
from control_platform.executor.task_pool import TaskPool
from control_platform.lock.base import TaskLock, TaskLockNotAcquired
from control_platform.lock.local_lock import LocalTaskLock


# ======================== locked() 上下文管理器基础测试 ========================

class TestLockedContextManager:
    """TaskLock.locked() 上下文管理器的基础行为测试"""

    @pytest_asyncio.fixture
    async def lock(self):
        return LocalTaskLock(ttl=10.0)

    @pytest.mark.asyncio
    async def test_locked_acquires_and_releases(self, lock: LocalTaskLock):
        """测试目的：async with locked() 正常退出时，锁应自动释放"""
        async with lock.locked("task-1"):
            assert lock.held_lock_count == 1
        assert lock.held_lock_count == 0

    @pytest.mark.asyncio
    async def test_locked_raises_on_contention(self, lock: LocalTaskLock):
        """测试目的：锁已被占用时，locked() 应抛出 TaskLockNotAcquired"""
        await lock.acquire("task-1")

        with pytest.raises(TaskLockNotAcquired) as exc_info:
            async with lock.locked("task-1"):
                pass
        assert "task-1" in str(exc_info.value)
        assert lock.held_lock_count == 1
        await lock.release("task-1")

    @pytest.mark.asyncio
    async def test_locked_releases_on_exception(self, lock: LocalTaskLock):
        """测试目的：locked() 内抛出异常时，锁仍应被释放"""
        with pytest.raises(ValueError, match="handler 内部错误"):
            async with lock.locked("task-1"):
                assert lock.held_lock_count == 1
                raise ValueError("handler 内部错误")
        assert lock.held_lock_count == 0

    @pytest.mark.asyncio
    async def test_locked_different_tasks_independent(self, lock: LocalTaskLock):
        """测试目的：不同 task_id 的 locked() 互不干扰"""
        async with lock.locked("task-a"):
            async with lock.locked("task-b"):
                assert lock.held_lock_count == 2
            assert lock.held_lock_count == 1
        assert lock.held_lock_count == 0

    @pytest.mark.asyncio
    async def test_locked_reentrant_fails(self, lock: LocalTaskLock):
        """测试目的：同一 task_id 嵌套 locked() 应失败（非可重入锁）"""
        async with lock.locked("task-1"):
            with pytest.raises(TaskLockNotAcquired):
                async with lock.locked("task-1"):
                    pass
        assert lock.held_lock_count == 0


# ======================== TaskPool 执行 + 锁集成测试 ========================

class TestTaskPoolExecution:
    """TaskPool 执行 stage 的完整流程测试"""

    def _make_pool(
        self,
        repo: DiagnosisRepository,
        registry: StageHandlerRegistry,
        lock: LocalTaskLock,
        max_concurrency: int = 20,
    ) -> TaskPool:
        """创建配置好的 TaskPool"""
        return TaskPool(
            max_concurrency=max_concurrency,
            repo=repo,
            handler_registry=registry,
            task_lock=lock,
        )

    @pytest.mark.asyncio
    async def test_submit_executes_handler(self, repo: DiagnosisRepository, sample_task):
        """测试目的：submit 后 handler 应被执行"""
        task, stage = sample_task

        mock_handler = AsyncMock(spec=StageHandler)
        mock_handler.handler_name = "MockHandler"
        mock_handler.handle.return_value = None

        registry = StageHandlerRegistry()
        registry.register(StageType.USER_QUERY.value, mock_handler)

        lock = LocalTaskLock()
        pool = self._make_pool(repo, registry, lock)

        await pool.submit(task, stage)
        await asyncio.sleep(0.3)  # 等待异步任务完成

        mock_handler.handle.assert_awaited_once()
        assert lock.held_lock_count == 0

        await pool.shutdown()

    @pytest.mark.asyncio
    async def test_submit_acquires_and_releases_lock(self, repo: DiagnosisRepository, sample_task):
        """测试目的：submit 执行后，锁应自动释放"""
        task, stage = sample_task

        lock_held_during_execution = False

        class CheckLockHandler(StageHandler):
            async def handle(self, task, stage, repo):
                nonlocal lock_held_during_execution
                lock_held_during_execution = True
                return None

        registry = StageHandlerRegistry()
        registry.register(StageType.USER_QUERY.value, CheckLockHandler())

        lock = LocalTaskLock()
        pool = self._make_pool(repo, registry, lock)

        await pool.submit(task, stage)
        await asyncio.sleep(0.3)

        assert lock_held_during_execution is True
        assert lock.held_lock_count == 0

        await pool.shutdown()

    @pytest.mark.asyncio
    async def test_submit_handler_exception_releases_lock(self, repo: DiagnosisRepository, sample_task):
        """测试目的：handler 抛出异常时，锁仍应被释放，stage 应被 mark_failed"""
        task, stage = sample_task

        mock_handler = AsyncMock(spec=StageHandler)
        mock_handler.handler_name = "FailingHandler"
        mock_handler.handle.side_effect = Exception("处理器内部错误")

        registry = StageHandlerRegistry()
        registry.register(StageType.USER_QUERY.value, mock_handler)

        lock = LocalTaskLock()
        pool = self._make_pool(repo, registry, lock)

        await pool.submit(task, stage)
        await asyncio.sleep(0.3)

        # stage 应被 mark_failed
        loaded = await repo.get_stage(stage.id)
        assert loaded.retry_count >= 1
        assert "处理器内部错误" in loaded.error_message

        # 锁应被释放
        assert lock.held_lock_count == 0

        await pool.shutdown()

    @pytest.mark.asyncio
    async def test_submit_lock_occupied_skips(self, repo: DiagnosisRepository, sample_task):
        """测试目的：task 的锁被占用时，submit 应跳过处理"""
        task, stage = sample_task

        mock_handler = AsyncMock(spec=StageHandler)
        mock_handler.handler_name = "MockHandler"

        registry = StageHandlerRegistry()
        registry.register(StageType.USER_QUERY.value, mock_handler)

        lock = LocalTaskLock()
        await lock.acquire(task.task_id)  # 提前占住锁

        pool = self._make_pool(repo, registry, lock)
        await pool.submit(task, stage)
        await asyncio.sleep(0.3)

        # handler 不应被调用
        mock_handler.handle.assert_not_awaited()

        await lock.release(task.task_id)
        await pool.shutdown()

    @pytest.mark.asyncio
    async def test_submit_handler_not_found_skips(self, repo: DiagnosisRepository, sample_task):
        """测试目的：找不到 stage_type 对应的处理器时，应跳过不报错"""
        task, stage = sample_task

        registry = StageHandlerRegistry()  # 空注册表
        lock = LocalTaskLock()
        pool = self._make_pool(repo, registry, lock)

        # 不应抛出异常
        await pool.submit(task, stage)
        await asyncio.sleep(0.3)

        assert lock.held_lock_count == 0
        await pool.shutdown()


# ======================== 并发测试 ========================

class TestTaskPoolConcurrency:
    """TaskPool 并发控制测试"""

    def _make_pool(
        self,
        repo: DiagnosisRepository,
        registry: StageHandlerRegistry,
        lock: LocalTaskLock,
        max_concurrency: int = 20,
    ) -> TaskPool:
        return TaskPool(
            max_concurrency=max_concurrency,
            repo=repo,
            handler_registry=registry,
            task_lock=lock,
        )

    @pytest.mark.asyncio
    async def test_concurrent_submit_same_task_only_one_runs(
        self, repo: DiagnosisRepository, sample_task
    ):
        """测试目的：并发对同一 task 提交，只有一个成功获取锁执行"""
        task, stage = sample_task

        call_count = 0

        class SlowHandler(StageHandler):
            async def handle(self, task, stage, repo):
                nonlocal call_count
                call_count += 1
                await asyncio.sleep(0.3)
                return None

        registry = StageHandlerRegistry()
        registry.register(StageType.USER_QUERY.value, SlowHandler())

        lock = LocalTaskLock()
        pool = self._make_pool(repo, registry, lock)

        # 并发提交 5 个相同 task
        for _ in range(5):
            await pool.submit(task, stage)
        await asyncio.sleep(1.0)

        # 只有 1 个 handler 被实际执行（其余因锁竞争而跳过）
        assert call_count == 1
        assert lock.held_lock_count == 0

        await pool.shutdown()

    @pytest.mark.asyncio
    async def test_concurrent_submit_different_tasks_all_run(
        self, repo: DiagnosisRepository
    ):
        """测试目的：不同 task 并发提交，应全部成功执行"""
        tasks_and_stages = []
        for i in range(3):
            task = await repo.create_task(
                session_id=f"session-{i}",
                user_query=f"问题 {i}",
            )
            stages = await repo.get_task_stages(task.task_id)
            tasks_and_stages.append((task, stages[0]))

        call_ids = []

        class TrackingHandler(StageHandler):
            async def handle(self, task, stage, repo):
                call_ids.append(task.task_id)
                await asyncio.sleep(0.05)
                return None

        registry = StageHandlerRegistry()
        registry.register(StageType.USER_QUERY.value, TrackingHandler())

        lock = LocalTaskLock()
        pool = TaskPool(
            max_concurrency=20,
            repo=repo,
            handler_registry=registry,
            task_lock=lock,
        )

        for task, stage in tasks_and_stages:
            await pool.submit(task, stage)
        await asyncio.sleep(1.0)

        assert len(call_ids) == 3
        assert lock.held_lock_count == 0

        await pool.shutdown()

    @pytest.mark.asyncio
    async def test_pool_concurrent_execution_semaphore(self):
        """测试目的：信号量应限制最大并发数"""
        started_count = 0
        max_concurrent = 0
        all_started = asyncio.Event()

        registry = StageHandlerRegistry()
        lock = LocalTaskLock()
        repo_mock = AsyncMock(spec=DiagnosisRepository)

        class ConcurrentHandler(StageHandler):
            async def handle(self, task, stage, repo):
                nonlocal started_count, max_concurrent
                started_count += 1
                if started_count > max_concurrent:
                    max_concurrent = started_count
                if started_count >= 3:
                    all_started.set()
                await asyncio.sleep(0.2)
                started_count -= 1
                return None

        registry.register(StageType.USER_QUERY.value, ConcurrentHandler())

        pool = TaskPool(
            max_concurrency=10,
            repo=repo_mock,
            handler_registry=registry,
            task_lock=lock,
        )

        for i in range(3):
            mock_task = MagicMock()
            mock_task.task_id = f"task-{i}"
            mock_stage = MagicMock()
            mock_stage.stage_type = StageType.USER_QUERY.value
            mock_stage.stage_seq = 1
            mock_stage.id = i + 1
            await pool.submit(mock_task, mock_stage)

        await asyncio.wait_for(all_started.wait(), timeout=2.0)
        # 至少 2 个应并发执行（不同 task 的锁不互斥）
        assert max_concurrent >= 2

        await pool.shutdown()


# ======================== 链式执行测试 ========================

class TestChainExecution:
    """测试 handler 返回 next_stage 后的链式投递"""

    def _make_pool(
        self,
        repo: DiagnosisRepository,
        registry: StageHandlerRegistry,
        lock: LocalTaskLock,
        max_concurrency: int = 20,
    ) -> TaskPool:
        return TaskPool(
            max_concurrency=max_concurrency,
            repo=repo,
            handler_registry=registry,
            task_lock=lock,
        )

    @pytest.mark.asyncio
    async def test_chain_execution_submits_next_stage(
        self, repo: DiagnosisRepository, sample_task
    ):
        """
        测试目的：handler 返回 next_stage 时，Pool 应自动递归提交。
        handler_1 返回 next_stage → Pool 自动提交 → handler_2 被调用。
        """
        task, initial_stage = sample_task

        execution_log = []

        mock_next_stage = MagicMock()
        mock_next_stage.stage_seq = 2
        mock_next_stage.stage_type = StageType.LLM_THINKING.value
        mock_next_stage.id = 999

        class FirstHandler(StageHandler):
            async def handle(self, task, stage, repo):
                execution_log.append(f"first:{stage.stage_seq}")
                return mock_next_stage

        class SecondHandler(StageHandler):
            async def handle(self, task, stage, repo):
                execution_log.append(f"second:{stage.stage_seq}")
                return None

        registry = StageHandlerRegistry()
        registry.register(StageType.USER_QUERY.value, FirstHandler())
        registry.register(StageType.LLM_THINKING.value, SecondHandler())

        lock = LocalTaskLock()
        pool = self._make_pool(repo, registry, lock)

        await pool.submit(task, initial_stage)
        await asyncio.sleep(1.0)

        assert "first:1" in execution_log
        assert "second:2" in execution_log
        assert len(execution_log) == 2
        assert lock.held_lock_count == 0

        await pool.shutdown()

    @pytest.mark.asyncio
    async def test_chain_execution_lock_released_between_stages(
        self, repo: DiagnosisRepository, sample_task
    ):
        """
        测试目的：链式执行时，第一个 handler 完成后锁应释放，
        第二个 handler 执行时需要重新获取锁。
        """
        task, initial_stage = sample_task

        lock_states = []

        mock_next_stage = MagicMock()
        mock_next_stage.stage_seq = 2
        mock_next_stage.stage_type = StageType.LLM_THINKING.value
        mock_next_stage.id = 999

        class FirstHandler(StageHandler):
            async def handle(self_handler, task, stage, repo):
                lock_states.append(("first", lock.held_lock_count))
                return mock_next_stage

        class SecondHandler(StageHandler):
            async def handle(self_handler, task, stage, repo):
                lock_states.append(("second", lock.held_lock_count))
                return None

        registry = StageHandlerRegistry()
        registry.register(StageType.USER_QUERY.value, FirstHandler())
        registry.register(StageType.LLM_THINKING.value, SecondHandler())

        lock = LocalTaskLock()
        pool = self._make_pool(repo, registry, lock)

        await pool.submit(task, initial_stage)
        await asyncio.sleep(1.0)

        # 两个 handler 执行时各自持有锁
        assert len(lock_states) == 2
        assert lock_states[0] == ("first", 1)
        assert lock_states[1] == ("second", 1)
        # 最终锁应释放
        assert lock.held_lock_count == 0

        await pool.shutdown()