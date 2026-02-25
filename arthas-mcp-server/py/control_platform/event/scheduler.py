"""
诊断事件轮询调度器

EventScheduler 只负责轮询和提交，不涉及任何执行逻辑。
定时从数据库轮询待处理的 DiagnosisStage，提交到 TaskPool 执行。
所有执行逻辑（锁管理、handler 调用、错误处理、链式投递）均由 TaskPool 负责。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, TYPE_CHECKING

from control_platform.config import settings
from control_platform.db.repository import DiagnosisRepository

if TYPE_CHECKING:
    from control_platform.session.session_manager import SessionManager
    from control_platform.executor.task_pool import TaskPool

logger = logging.getLogger(__name__)


class EventScheduler:
    """
    诊断事件轮询调度器（纯调度，不执行）

    定时从 DiagnosisRepository 轮询所有 running 任务中 stage_seq 最大且
    status=pending 的阶段，提交到 TaskPool 执行。

    Scheduler 只做两件事：
    1. 轮询：定时从 DB 拉取 pending stages
    2. 提交：将 (task, stage) 交给 Pool

    所有执行逻辑（锁管理、handler 调用、错误处理、链式投递）均由 Pool 内部负责。

    Attributes:
        _repo: 诊断仓储层
        _pool: 执行池（负责加锁、执行、链式投递）
        _session_manager: 会话管理器（用于过滤活跃 session）
        _poll_task: 轮询后台任务
    """

    def __init__(
        self,
        repo: DiagnosisRepository,
        pool: TaskPool,
        session_manager: Optional[SessionManager] = None,
    ):
        self._repo = repo
        self._pool = pool
        self._session_manager = session_manager
        self._poll_task: Optional[asyncio.Task] = None

    def start(self) -> None:
        """启动事件轮询"""
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop())
            logger.info(
                f"🔄 诊断事件轮询调度器已启动 "
                f"(间隔={settings.event_poll_interval}s)"
            )

    def stop(self) -> None:
        """停止事件轮询"""
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            logger.info("🔄 诊断事件轮询调度器已停止")

    async def _poll_loop(self) -> None:
        """
        事件轮询主循环

        按配置间隔从数据库获取待处理 stage，提交到 Pool 执行。
        """
        interval = settings.event_poll_interval

        while True:
            try:
                await asyncio.sleep(interval)
                await self._poll_and_dispatch()
            except asyncio.CancelledError:
                logger.info("🔄 事件轮询循环已取消")
                break
            except Exception as e:
                logger.error(f"事件轮询异常: {e}", exc_info=True)
                await asyncio.sleep(5)  # 异常后短暂等待再重试

    async def _poll_and_dispatch(self) -> None:
        """获取 pending stages 并提交到 Pool 执行（只拉取当前持有活跃 session 的任务）"""
        # 获取当前活跃的 session_id 列表，只拉取这些 session 的任务
        active_session_ids = None
        if self._session_manager:
            active_session_ids = await self._session_manager.get_all_session_ids()
            if not active_session_ids:
                return  # 没有活跃 session，无需轮询

        try:
            pending_items = await self._repo.get_pending_stages(
                active_session_ids=active_session_ids,
            )
        except Exception as e:
            logger.error(f"获取待处理 stage 失败: {e}", exc_info=True)
            return

        if not pending_items:
            return

        logger.info(f"📨 轮询到 {len(pending_items)} 个待处理 stage")

        # 同时也检查是否有失败任务需要标记
        # try:
        #     failed_count = await self._repo.check_and_fail_stale_tasks()
        #     if failed_count > 0:
        #         logger.info(f"标记了 {failed_count} 个失败任务")
        # except Exception as e:
        #     logger.error(f"检查失败任务时出错: {e}", exc_info=True)

        # 将每个 (task, stage) 提交到 Pool，由 Pool 负责加锁、执行、链式投递
        for task, stage in pending_items:
            await self._pool.submit(task, stage)

    @property
    def is_running(self) -> bool:
        """轮询是否正在运行"""
        return self._poll_task is not None and not self._poll_task.done()