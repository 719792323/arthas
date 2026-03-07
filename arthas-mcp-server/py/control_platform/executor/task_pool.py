"""
任务执行池

负责：并发控制、锁管理、handler 执行、错误处理、链式投递。
Scheduler 只负责轮询 + 提交到 TaskPool，所有执行逻辑均在此处。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Set, TYPE_CHECKING

from control_platform.db.database import shared_session
from control_platform.executor.base import TaskExecutor
from control_platform.lock.base import TaskLockNotAcquired
from control_platform.db.models import StageStatus

if TYPE_CHECKING:
    from control_platform.db.repository import DiagnosisRepository
    from control_platform.event.handler import StageHandlerRegistry
    from control_platform.lock.base import TaskLock

logger = logging.getLogger(__name__)


class TaskPool(TaskExecutor):
    """
    任务执行池

    接收 (task, stage) 后立即创建 asyncio.Task 开始执行。
    使用 asyncio.Semaphore 控制最大并发数。

    执行流程（每个 submit 内部）：
    1. 查找 stage_type 对应的 handler
    2. 通过 TaskLock.locked(task_id) 获取锁
    3. 在锁保护下执行 handler
    4. 锁释放后，若 handler 返回 next_stage，递归提交到自身（链式执行）

    Attributes:
        _semaphore: 并发控制信号量
        _running_tasks: 正在执行的 asyncio.Task 集合
    """

    def __init__(
        self,
        max_concurrency: int = 20,
        repo: Optional[DiagnosisRepository] = None,
        handler_registry: Optional[StageHandlerRegistry] = None,
        task_lock: Optional[TaskLock] = None,
    ):
        super().__init__(max_concurrency, repo, handler_registry, task_lock)
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._running_tasks: Set[asyncio.Task] = set()

    async def submit(self, task: Any, stage: Any) -> None:
        """
        提交 (task, stage) 到执行池（立即开始执行）

        创建异步任务，在信号量控制下执行完整的 stage 处理流程。

        Args:
            task: 要执行的任务（DiagnosisTask）
            stage: 当前待处理的阶段（DiagnosisStage）
        """
        async_task = asyncio.create_task(
            self._execute_stage(task, stage)
        )
        self._running_tasks.add(async_task)
        async_task.add_done_callback(self._running_tasks.discard)

        logger.info(
            f"🚀 [TaskPool] 任务已提交: task_id={task.task_id}, "
            f"stage_seq={stage.stage_seq} "
            f"(当前运行: {len(self._running_tasks)}/{self._max_concurrency})"
        )

    async def _execute_stage(self, task: Any, stage: Any) -> None:
        """
        执行单个 stage 的完整流程（在信号量控制下）

        流程：
        1. 查找 handler
        2. 在锁保护下执行 handler（加锁 → 执行 → 释放锁）
        3. 锁释放后，若有 next_stage，递归提交到自身

        Args:
            task: 所属的 DiagnosisTask
            stage: 当前待处理的 DiagnosisStage
        """
        stage_type = stage.stage_type
        task_id = task.task_id

        try:
            async with self._semaphore:
                # 1. 查找处理器
                handler = self._handler_registry.get_handler(stage_type)
                if handler is None:
                    logger.warning(
                        f"⚠️ 未找到阶段处理器: stage_type={stage_type}, "
                        f"task_id={task_id}, stage_seq={stage.stage_seq}, 跳过"
                    )
                    return

                # 2. 在锁保护下执行处理器
                next_stage = None
                try:
                    async with self._task_lock.locked(task_id):
                        next_stage = await self._run_handler(task, stage, handler)
                except TaskLockNotAcquired:
                    logger.debug(
                        f"⏭️ 任务锁已被占用，跳过: task_id={task_id}, "
                        f"stage_type={stage_type}, stage_seq={stage.stage_seq}"
                    )
                    return

                # 3. 锁已释放，如果有 next_stage 则递归提交
                #    无需重新加载：next_stage 是 handler 刚创建的最新对象，
                #    且 _run_handler 内部会从 DB 重新加载并做 PENDING 状态检查
                if next_stage:
                    logger.info(
                        f"🔗 链式投递: task_id={task_id}, "
                        f"next_stage_seq={next_stage.stage_seq}"
                    )
                    await self.submit(task, next_stage)

        except Exception as e:
            logger.error(
                f"❌ [TaskPool] 执行异常: task_id={task_id}, "
                f"stage_type={stage_type}, error={e}",
                exc_info=True,
            )

    async def _run_handler(self, task, stage, handler):
        """
        运行 handler 的核心逻辑（在锁保护内调用）

        调用方必须已持有锁。
        在执行前从 DB 重新加载 task 和 stage，确保使用最新数据，
        避免 detached ORM 对象和过时状态问题。

        使用 shared_session() 包裹整个 handler 执行链，确保：
        1. 重新加载 task/stage + handler 内部所有 repo 操作在同一事务中
        2. handler 执行失败时自动 rollback 所有中间变更
        3. handler 执行成功时统一 commit

        Returns:
            handler 返回的 next_stage（可能为 None）
        """
        task_id = task.task_id
        stage_type = stage.stage_type
        stage_id = stage.id
        next_stage = None
        try:
            # shared_session 保证以下所有 repo 操作在同一个数据库事务中
            async with shared_session():
                # 从 DB 重新加载最新的 task 和 stage，避免使用 detached/过时的对象
                fresh_task = await self._repo.get_task(task_id)
                fresh_stage = await self._repo.get_stage(stage_id)
                if fresh_task is None or fresh_stage is None:
                    logger.warning(
                        f"⚠️ task 或 stage 已不存在，跳过: task_id={task_id}, "
                        f"stage_id={stage.id}"
                    )
                    return None
                if fresh_stage.status != StageStatus.PENDING.value:
                    logger.info(
                        f"⏭️ stage 状态已非 PENDING（当前: {fresh_stage.status}），跳过: "
                        f"task_id={task_id}, stage_seq={fresh_stage.stage_seq}"
                    )
                    return None

                logger.info(
                    f"▶️ 开始处理 stage: task_id={task_id}, "
                    f"stage_type={stage_type}, stage_seq={fresh_stage.stage_seq}"
                )
                result = await handler.handle(fresh_task, fresh_stage, self._repo)
                if result is not None:
                    next_stage = result
                logger.info(
                    f"✅ stage 处理完成: task_id={task_id}, "
                    f"stage_type={stage_type}, stage_seq={stage.stage_seq}"
                )
        except Exception as e:
            logger.error(
                f"❌ stage 处理失败: task_id={task_id}, "
                f"stage_type={stage_type}, stage_seq={stage.stage_seq}, "
                f"error={e}",
                exc_info=True,
            )
            try:
                is_final = await self._repo.mark_failed(stage_id, str(e))
                if is_final:
                    await self._repo.fail_task(task_id)
            except Exception as inner_e:
                logger.error(f"兜底标记失败也失败了: {inner_e}", exc_info=True)
        return next_stage

    async def shutdown(self) -> None:
        """关闭执行池，等待所有正在执行的任务完成"""
        if self._running_tasks:
            logger.info(
                f"[TaskPool] 等待 {len(self._running_tasks)} 个任务完成..."
            )
            await asyncio.gather(*self._running_tasks, return_exceptions=True)
        logger.info("[TaskPool] 已关闭")

    @property
    def running_count(self) -> int:
        """当前正在运行的任务数"""
        return len(self._running_tasks)
