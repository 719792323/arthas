"""
诊断仓储层（DiagnosisRepository）

封装所有与 diagnosis_task / diagnosis_stage 相关的数据库操作，
保证事务原子性和状态一致性。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, update, and_, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from control_platform.config import settings
from control_platform.db.database import get_session
from control_platform.db.models import (
    ApprovalStatus,
    DiagnosisStage,
    DiagnosisTask,
    LlmPromptLog,
    StageStatus,
    StageType,
    TaskStatus,
)

logger = logging.getLogger(__name__)


class DiagnosisRepository:
    """
    诊断任务与阶段的仓储层

    所有方法默认使用内部 session 管理事务（通过 get_session() 上下文管理器），
    也支持外部传入 session 以便在更大的事务范围内操作。
    """

    # ======================== 任务操作 ========================

    async def create_task(
        self,
        session_id: str,
        user_query: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DiagnosisTask:
        """
        创建诊断任务及其初始 USER_QUERY 阶段。

        在同一事务中插入 task + 初始 stage（stage_seq=1, type=USER_QUERY, status=pending）。

        Args:
            session_id: 关联的 Arthas 客户端会话 ID
            user_query: 用户原始提问
            metadata: 附加元数据

        Returns:
            新创建的 DiagnosisTask 对象（包含初始 stage）
        """
        async with get_session() as session:
            # 创建任务
            task = DiagnosisTask(
                session_id=session_id,
                user_query=user_query,
                status=TaskStatus.RUNNING.value,
                current_stage_seq=1,
                metadata_=metadata,
            )
            session.add(task)
            await session.flush()  # 获取 task_id

            # 创建初始 USER_QUERY 阶段
            initial_stage = DiagnosisStage(
                task_id=task.task_id,
                stage_seq=1,
                stage_type=StageType.USER_QUERY.value,
                status=StageStatus.PENDING.value,
                input_data={"user_query": user_query},
                approval_status=ApprovalStatus.NOT_REQUIRED.value,
            )
            session.add(initial_stage)

            logger.info(f"创建诊断任务 task_id={task.task_id}, session_id={session_id}")
            return task

    async def get_task(self, task_id: str) -> Optional[DiagnosisTask]:
        """根据 task_id 获取任务（包含关联的 stages）"""
        async with get_session() as session:
            result = await session.execute(
                select(DiagnosisTask).where(DiagnosisTask.task_id == task_id)
            )
            return result.scalar_one_or_none()

    async def get_tasks(
        self,
        session_id: Optional[str] = None,
        status: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[DiagnosisTask]:
        """
        查询任务列表（支持多条件筛选）。

        Args:
            session_id: 按 session_id 筛选
            status: 按任务状态筛选
            start_time: 创建时间起始
            end_time: 创建时间截止
            limit: 分页大小
            offset: 分页偏移

        Returns:
            任务列表
        """
        async with get_session() as session:
            query = select(DiagnosisTask)
            conditions = []

            if session_id:
                conditions.append(DiagnosisTask.session_id == session_id)
            if status:
                conditions.append(DiagnosisTask.status == status)
            if start_time:
                conditions.append(DiagnosisTask.created_at >= start_time)
            if end_time:
                conditions.append(DiagnosisTask.created_at <= end_time)

            if conditions:
                query = query.where(and_(*conditions))

            query = query.order_by(DiagnosisTask.created_at.desc()).limit(limit).offset(offset)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def complete_task(self, task_id: str, conclusion: str) -> None:
        """
        标记任务为已完成，写入最终结论。

        Args:
            task_id: 任务 ID
            conclusion: 最终诊断结论
        """
        async with get_session() as session:
            await session.execute(
                update(DiagnosisTask)
                .where(DiagnosisTask.task_id == task_id)
                .values(
                    status=TaskStatus.COMPLETED.value,
                    conclusion=conclusion,
                    updated_at=datetime.now(),
                )
            )
            logger.info(f"任务完成 task_id={task_id}")

    async def fail_task(self, task_id: str) -> None:
        """标记任务为失败"""
        async with get_session() as session:
            await session.execute(
                update(DiagnosisTask)
                .where(DiagnosisTask.task_id == task_id)
                .values(
                    status=TaskStatus.FAILED.value,
                    updated_at=datetime.now(),
                )
            )
            logger.info(f"任务失败 task_id={task_id}")

    async def delete_task(self, task_id: str) -> bool:
        """
        删除诊断任务及其所有关联数据（stages、prompt 日志）。

        Args:
            task_id: 任务 ID

        Returns:
            True 如果任务存在并被删除，False 如果任务不存在
        """
        from sqlalchemy import delete as sa_delete

        async with get_session() as session:
            # 检查任务是否存在
            task = await session.get(DiagnosisTask, task_id)
            if task is None:
                return False

            # 1. 删除关联的 LLM Prompt 日志
            await session.execute(
                sa_delete(LlmPromptLog).where(LlmPromptLog.task_id == task_id)
            )

            # 2. 删除关联的所有 stages
            await session.execute(
                sa_delete(DiagnosisStage).where(DiagnosisStage.task_id == task_id)
            )

            # 3. 删除任务本身
            await session.execute(
                sa_delete(DiagnosisTask).where(DiagnosisTask.task_id == task_id)
            )

            logger.info(f"任务已删除 task_id={task_id}")
            return True

    # ======================== 阶段轮询 ========================

    async def get_pending_stages(
        self,
        active_session_ids: Optional[List[str]] = None,
    ) -> List[Tuple[DiagnosisTask, DiagnosisStage]]:
        """
        查询所有运行中任务的最新待处理阶段。

        逻辑：
        1. 找到所有 status=running 的 task
        2. 如果提供了 active_session_ids，则只查询这些 session 的 task
        3. 对每个 task，取 stage_seq 最大的那条 stage
        4. 如果该 stage 的 status 为 pending，则返回

        Args:
            active_session_ids: 当前活跃的 session_id 列表。
                如果提供，则只拉取属于这些 session 的任务；
                如果为 None 或空列表，则不返回任何结果（避免无会话时空跑）。

        Returns:
            (task, stage) 元组列表
        """
        # 没有活跃 session 时直接返回空，避免无意义的数据库查询
        if active_session_ids is not None and len(active_session_ids) == 0:
            return []

        async with get_session() as session:
            # 子查询基础条件：running 任务
            subquery_conditions = [
                DiagnosisTask.status == TaskStatus.RUNNING.value,
            ]
            # 按活跃 session_id 过滤
            if active_session_ids is not None:
                subquery_conditions.append(
                    DiagnosisTask.session_id.in_(active_session_ids)
                )

            # 子查询：每个 running task 的最大 stage_seq
            max_seq_subquery = (
                select(
                    DiagnosisStage.task_id,
                    sa_func.max(DiagnosisStage.stage_seq).label("max_seq"),
                )
                .join(DiagnosisTask, DiagnosisStage.task_id == DiagnosisTask.task_id)
                .where(and_(*subquery_conditions))
                .group_by(DiagnosisStage.task_id)
                .subquery()
            )

            # 主查询：获取最新 stage 且 status=pending 的记录
            query = (
                select(DiagnosisTask, DiagnosisStage)
                .join(DiagnosisStage, DiagnosisTask.task_id == DiagnosisStage.task_id)
                .join(
                    max_seq_subquery,
                    and_(
                        DiagnosisStage.task_id == max_seq_subquery.c.task_id,
                        DiagnosisStage.stage_seq == max_seq_subquery.c.max_seq,
                    ),
                )
                .where(DiagnosisStage.status == StageStatus.PENDING.value)
            )

            result = await session.execute(query)
            items = list(result.all())

            # 冷却过滤：排除在冷却时间内的 TOOL_CALL stage
            # 避免调度器在工具响应返回前重复发送请求
            cooldown = settings.tool_call_cooldown
            if cooldown > 0:
                now = datetime.now()
                filtered = []
                for task, stage in items:
                    if (
                        stage.stage_type == StageType.TOOL_CALL.value
                        and stage.last_sent_at is not None
                        and (now - stage.last_sent_at).total_seconds() < cooldown
                    ):
                        logger.debug(
                            f"TOOL_CALL stage 在冷却中，跳过: task_id={task.task_id}, "
                            f"stage_seq={stage.stage_seq}, "
                            f"last_sent={stage.last_sent_at}, cooldown={cooldown}s"
                        )
                        continue
                    filtered.append((task, stage))
                return filtered

            return items

    # ======================== 阶段状态流转 ========================

    async def complete_and_next(
        self,
        stage_id: int,
        output_data: Optional[Dict[str, Any]],
        next_stage_type: str,
        next_input_data: Optional[Dict[str, Any]] = None,
        next_tool_name: Optional[str] = None,
        next_tool_arguments: Optional[Dict[str, Any]] = None,
        next_approval_status: str = ApprovalStatus.NOT_REQUIRED.value,
        tool_result: Optional[str] = None,
    ) -> DiagnosisStage:
        """
        在同一事务中完成当前 stage 并创建下一个 stage。

        Args:
            stage_id: 当前 stage 的数据库 ID
            output_data: 当前 stage 的输出数据
            next_stage_type: 下一个 stage 的类型
            next_input_data: 下一个 stage 的输入数据
            next_tool_name: 下一个 stage 的工具名（仅 TOOL_CALL）
            next_tool_arguments: 下一个 stage 的工具参数（仅 TOOL_CALL）
            next_approval_status: 下一个 stage 的审核状态
            tool_result: 当前 stage 的工具执行结果文本（仅 TOOL_CALL 阶段使用）

        Returns:
            新创建的下一个 DiagnosisStage
        """
        async with get_session() as session:
            # 获取当前 stage
            current_stage = await session.get(DiagnosisStage, stage_id)
            if current_stage is None:
                raise ValueError(f"Stage not found: {stage_id}")

            # 前置检查：只有 PENDING 状态的 stage 才能被 complete
            # 防止已经 COMPLETED/FAILED 的 stage 被重复完成并创建重复的 next_stage
            if current_stage.status != StageStatus.PENDING.value:
                raise ValueError(
                    f"Stage {stage_id} 状态不是 PENDING（当前: {current_stage.status}），"
                    f"无法 complete_and_next"
                )

            task_id = current_stage.task_id
            current_seq = current_stage.stage_seq

            # 1. 标记当前 stage 为 completed
            current_stage.status = StageStatus.COMPLETED.value
            current_stage.output_data = output_data
            if tool_result is not None:
                current_stage.tool_result = tool_result
            current_stage.updated_at = datetime.now()

            # 2. 从 task.current_stage_seq 获取最新序号，而非 current_stage.stage_seq + 1
            #    因为在 LLM_THINKING 执行过程中，上下文优化可能已经插入了
            #    CONTEXT_SUMMARY stage 并更新了 task.current_stage_seq，
            #    如果直接用 current_stage.stage_seq + 1 会导致 UNIQUE 约束冲突
            task = await session.get(DiagnosisTask, task_id)
            next_seq = task.current_stage_seq + 1

            # 3. 创建下一个 stage
            next_stage = DiagnosisStage(
                task_id=task_id,
                stage_seq=next_seq,
                stage_type=next_stage_type,
                status=StageStatus.PENDING.value,
                input_data=next_input_data,
                tool_name=next_tool_name,
                tool_arguments=next_tool_arguments,
                approval_status=next_approval_status,
            )
            session.add(next_stage)

            # 4. 更新 task 的 current_stage_seq
            await session.execute(
                update(DiagnosisTask)
                .where(DiagnosisTask.task_id == task_id)
                .values(
                    current_stage_seq=next_seq,
                    updated_at=datetime.now(),
                )
            )

            await session.flush()
            logger.info(
                f"Stage 完成并创建下一个: task_id={task_id}, "
                f"completed_seq={current_seq}, next_seq={next_seq}, "
                f"next_type={next_stage_type}"
            )
            return next_stage

    async def complete_stage(
        self,
        stage_id: int,
        output_data: Optional[Dict[str, Any]] = None,
        tool_result: Optional[str] = None,
    ) -> None:
        """
        仅标记当前 stage 为 completed（不创建下一个 stage）。

        用于最终阶段（如 LLM_CONCLUSION）。

        Args:
            stage_id: 当前 stage 的数据库 ID
            output_data: stage 输出数据
            tool_result: 工具执行结果原文
        """
        async with get_session() as session:
            # 前置检查：只有 PENDING 状态的 stage 才能被 complete
            # 防止并发导致已完成/失败的 stage 被重复标记为 completed
            stage = await session.get(DiagnosisStage, stage_id)
            if stage is None:
                raise ValueError(f"Stage not found: {stage_id}")
            if stage.status != StageStatus.PENDING.value:
                raise ValueError(
                    f"Stage {stage_id} 状态不是 PENDING（当前: {stage.status}），"
                    f"无法 complete_stage"
                )

            stage.status = StageStatus.COMPLETED.value
            stage.updated_at = datetime.now()
            if output_data is not None:
                stage.output_data = output_data
            if tool_result is not None:
                stage.tool_result = tool_result

    async def mark_failed(self, stage_id: int, error_message: str) -> bool:
        """
        标记 stage 失败：递增 retry_count，若达到 max_retries 则标记为 failed。

        Args:
            stage_id: stage 的数据库 ID
            error_message: 错误信息

        Returns:
            True 如果标记为终态 failed（达到最大重试），False 如果仍为 pending（等待重试）
        """
        async with get_session() as session:
            stage = await session.get(DiagnosisStage, stage_id)
            if stage is None:
                raise ValueError(f"Stage not found: {stage_id}")

            stage.retry_count += 1
            stage.error_message = error_message
            stage.updated_at = datetime.now()

            if stage.retry_count >= stage.max_retries:
                # 达到最大重试次数，标记为 failed
                stage.status = StageStatus.FAILED.value
                logger.warning(
                    f"Stage 达到最大重试次数，标记为 failed: "
                    f"stage_id={stage_id}, task_id={stage.task_id}, "
                    f"retry_count={stage.retry_count}/{stage.max_retries}"
                )
                return True
            else:
                # 保持 pending，等待下次重试
                logger.info(
                    f"Stage 执行失败，等待重试: stage_id={stage_id}, "
                    f"retry_count={stage.retry_count}/{stage.max_retries}, "
                    f"error={error_message}"
                )
                return False

    async def update_stage_summary(
        self,
        stage_id: int,
        summarized_content: str,
        summary_tokens: int,
        original_tokens: int,
        summary_type: str,
    ) -> None:
        """
        更新 stage 的摘要相关字段。

        用于工具结果即时摘要和全文摘要后回写摘要信息。

        Args:
            stage_id: stage 的数据库 ID
            summarized_content: 摘要后的内容
            summary_tokens: 摘要后的 token 数量
            original_tokens: 原始内容的 token 数量
            summary_type: 摘要类型 ("llm" / "rule")
        """
        async with get_session() as session:
            await session.execute(
                update(DiagnosisStage)
                .where(DiagnosisStage.id == stage_id)
                .values(
                    summarized_content=summarized_content,
                    summary_tokens=summary_tokens,
                    original_tokens=original_tokens,
                    summary_type=summary_type,
                    updated_at=datetime.now(),
                )
            )
            logger.info(
                f"Stage 摘要已更新: stage_id={stage_id}, "
                f"summary_type={summary_type}, "
                f"original_tokens={original_tokens}, summary_tokens={summary_tokens}"
            )

    async def update_stage_last_sent_at(self, stage_id: int) -> None:
        """
        更新 stage 的 last_sent_at 字段为当前时间。

        用于 TOOL_CALL stage 发送后记录发送时间，配合冷却机制使用。

        Args:
            stage_id: stage 的数据库 ID
        """
        async with get_session() as session:
            await session.execute(
                update(DiagnosisStage)
                .where(DiagnosisStage.id == stage_id)
                .values(last_sent_at=datetime.now())
            )

    # ======================== 审核操作 ========================

    async def mark_waiting_approval(self, stage_id: int) -> None:
        """将 stage 标记为等待审核"""
        async with get_session() as session:
            await session.execute(
                update(DiagnosisStage)
                .where(DiagnosisStage.id == stage_id)
                .values(
                    status=StageStatus.WAITING_APPROVAL.value,
                    approval_status=ApprovalStatus.PENDING.value,
                    updated_at=datetime.now(),
                )
            )
            logger.info(f"Stage 等待审核: stage_id={stage_id}")

    async def approve_stage(self, stage_id: int, approved_by: str = "admin") -> None:
        """
        审核通过：将 approval_status 设为 approved，status 改回 pending。

        Args:
            stage_id: stage 数据库 ID
            approved_by: 审核人标识
        """
        async with get_session() as session:
            await session.execute(
                update(DiagnosisStage)
                .where(DiagnosisStage.id == stage_id)
                .values(
                    status=StageStatus.PENDING.value,
                    approval_status=ApprovalStatus.APPROVED.value,
                    approved_by=approved_by,
                    approved_at=datetime.now(),
                    updated_at=datetime.now(),
                )
            )
            logger.info(f"Stage 审核通过: stage_id={stage_id}, approved_by={approved_by}")

    async def reject_stage(self, stage_id: int, rejected_by: str = "admin") -> Optional[DiagnosisStage]:
        """
        审核拒绝：将 stage 标记为 failed，并创建新的 LLM_THINKING stage 让 LLM 重新决策。

        Args:
            stage_id: stage 数据库 ID
            rejected_by: 拒绝人标识

        Returns:
            新创建的 LLM_THINKING stage（用于让 LLM 知道命令被拒绝并重新决策）
        """
        async with get_session() as session:
            # 获取当前 stage
            stage = await session.get(DiagnosisStage, stage_id)
            if stage is None:
                raise ValueError(f"Stage not found: {stage_id}")

            task_id = stage.task_id
            next_seq = stage.stage_seq + 1

            # 1. 标记当前 stage 为 failed
            stage.status = StageStatus.FAILED.value
            stage.approval_status = ApprovalStatus.REJECTED.value
            stage.approved_by = rejected_by
            stage.approved_at = datetime.now()
            stage.updated_at = datetime.now()
            stage.error_message = f"命令被 {rejected_by} 拒绝执行"

            # 2. 创建新的 LLM_THINKING stage，告知 LLM 命令被拒绝
            next_stage = DiagnosisStage(
                task_id=task_id,
                stage_seq=next_seq,
                stage_type=StageType.LLM_THINKING.value,
                status=StageStatus.PENDING.value,
                input_data={
                    "rejected_tool_call": {
                        "tool_name": stage.tool_name,
                        "tool_arguments": stage.tool_arguments,
                        "rejection_reason": f"命令被 {rejected_by} 拒绝执行",
                    }
                },
                approval_status=ApprovalStatus.NOT_REQUIRED.value,
            )
            session.add(next_stage)

            # 3. 更新 task 的 current_stage_seq
            await session.execute(
                update(DiagnosisTask)
                .where(DiagnosisTask.task_id == task_id)
                .values(
                    current_stage_seq=next_seq,
                    updated_at=datetime.now(),
                )
            )

            await session.flush()
            logger.info(f"Stage 审核拒绝: stage_id={stage_id}, rejected_by={rejected_by}")
            return next_stage

    async def get_pending_approval_stages(self) -> List[DiagnosisStage]:
        """查询所有待审核的 stage"""
        async with get_session() as session:
            result = await session.execute(
                select(DiagnosisStage)
                .where(DiagnosisStage.status == StageStatus.WAITING_APPROVAL.value)
                .order_by(DiagnosisStage.created_at.asc())
            )
            return list(result.scalars().all())

    # ======================== 阶段查询 ========================

    async def get_task_stages(self, task_id: str) -> List[DiagnosisStage]:
        """查询 task 下所有 stage（按 stage_seq 升序）"""
        async with get_session() as session:
            result = await session.execute(
                select(DiagnosisStage)
                .where(DiagnosisStage.task_id == task_id)
                .order_by(DiagnosisStage.stage_seq.asc())
            )
            return list(result.scalars().all())

    async def get_stage(self, stage_id: int) -> Optional[DiagnosisStage]:
        """根据 stage ID 获取 stage"""
        async with get_session() as session:
            return await session.get(DiagnosisStage, stage_id)

    async def get_stage_by_task_and_seq(
        self, task_id: str, stage_seq: int,
    ) -> Optional[DiagnosisStage]:
        """
        根据 task_id + stage_seq 查询 stage。

        用于 WS 回调时通过 Java 端回传的 (taskId, stageId/stage_seq) 定位具体的 stage，
        替代内存中的 pending_request_info 映射。

        Args:
            task_id: 任务 ID
            stage_seq: 阶段序号

        Returns:
            匹配的 DiagnosisStage，不存在则返回 None
        """
        async with get_session() as session:
            result = await session.execute(
                select(DiagnosisStage).where(
                    and_(
                        DiagnosisStage.task_id == task_id,
                        DiagnosisStage.stage_seq == stage_seq,
                    )
                )
            )
            return result.scalar_one_or_none()

    async def get_latest_stage(self, task_id: str) -> Optional[DiagnosisStage]:
        """获取 task 的最新 stage（stage_seq 最大）"""
        async with get_session() as session:
            result = await session.execute(
                select(DiagnosisStage)
                .where(DiagnosisStage.task_id == task_id)
                .order_by(DiagnosisStage.stage_seq.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()

    # ======================== 故障恢复 ========================

    async def check_and_fail_stale_tasks(self) -> int:
        """
        检查并标记失败的任务：如果 running 任务的最新 stage 为 failed，
        则将 task 标记为 failed。

        Returns:
            标记为 failed 的任务数量
        """
        count = 0
        async with get_session() as session:
            # 查询所有 running 的 task
            running_tasks = await session.execute(
                select(DiagnosisTask)
                .where(DiagnosisTask.status == TaskStatus.RUNNING.value)
            )

            for task in running_tasks.scalars().all():
                # 获取最新 stage
                latest = await session.execute(
                    select(DiagnosisStage)
                    .where(DiagnosisStage.task_id == task.task_id)
                    .order_by(DiagnosisStage.stage_seq.desc())
                    .limit(1)
                )
                latest_stage = latest.scalar_one_or_none()

                if latest_stage and latest_stage.status == StageStatus.FAILED.value:
                    task.status = TaskStatus.FAILED.value
                    task.updated_at = datetime.now()
                    count += 1
                    logger.info(
                        f"检测到失败任务，标记为 failed: task_id={task.task_id}, "
                        f"failed_stage_seq={latest_stage.stage_seq}"
                    )

        return count

    async def create_context_summary_stage(
        self,
        task_id: str,
        summary_content: str,
        summary_tokens: int,
        summary_model: str,
        from_stage_seq: int,
        to_stage_seq: int,
        original_message_count: int,
        original_tokens: int,
        user_query: str = "",
    ) -> DiagnosisStage:
        """
        创建 CONTEXT_SUMMARY 类型的 stage 事件，持久化全文摘要结果。

        Args:
            task_id: 任务 ID
            summary_content: 摘要内容
            summary_tokens: 摘要 token 数
            summary_model: 使用的摘要模型
            from_stage_seq: 被摘要覆盖的起始 stage_seq
            to_stage_seq: 被摘要覆盖的结束 stage_seq
            original_message_count: 被压缩的消息数量
            original_tokens: 被压缩的消息总 token 数
            user_query: 用户原始问题

        Returns:
            新创建的 CONTEXT_SUMMARY DiagnosisStage
        """
        async with get_session() as session:
            # 获取当前 task 的最新 stage_seq
            task = await session.get(DiagnosisTask, task_id)
            if task is None:
                raise ValueError(f"Task not found: {task_id}")

            next_seq = task.current_stage_seq + 1

            summary_stage = DiagnosisStage(
                task_id=task_id,
                stage_seq=next_seq,
                stage_type=StageType.CONTEXT_SUMMARY.value,
                status=StageStatus.COMPLETED.value,
                input_data={
                    "from_stage_seq": from_stage_seq,
                    "to_stage_seq": to_stage_seq,
                    "original_message_count": original_message_count,
                    "original_tokens": original_tokens,
                    "user_query": user_query,
                },
                output_data={
                    "summary": summary_content,
                    "summary_tokens": summary_tokens,
                    "summary_model": summary_model,
                },
                approval_status=ApprovalStatus.NOT_REQUIRED.value,
            )
            session.add(summary_stage)

            # 更新 task 的 current_stage_seq
            task.current_stage_seq = next_seq
            task.updated_at = datetime.now()

            await session.flush()
            logger.info(
                f"创建 CONTEXT_SUMMARY 事件: task_id={task_id}, "
                f"stage_seq={next_seq}, from={from_stage_seq}, to={to_stage_seq}, "
                f"original_tokens={original_tokens}, summary_tokens={summary_tokens}"
            )
            return summary_stage

    # ======================== LLM Prompt 日志 ========================

    async def save_prompt_log(
        self,
        task_id: str,
        stage_seq: int,
        model: str,
        system_prompt: Optional[str] = None,
        chat_messages: Optional[List[Dict[str, Any]]] = None,
        tools_schema: Optional[List[Dict[str, Any]]] = None,
        response_content: Optional[str] = None,
        response_tool_calls: Optional[List[Dict[str, Any]]] = None,
        finish_reason: Optional[str] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
    ) -> None:
        """
        保存 LLM Prompt 日志。

        仅在配置 CP_ENABLE_PROMPT_LOGGING=true 时才会被调用。

        Args:
            task_id: 关联任务 ID
            stage_seq: 触发推理的 stage 序号
            model: LLM 模型名称
            system_prompt: 系统提示词
            chat_messages: 完整的 chat messages
            tools_schema: tools schema
            response_content: LLM 响应文本
            response_tool_calls: LLM 返回的 tool_calls
            finish_reason: finish_reason
            prompt_tokens: prompt token 数
            completion_tokens: completion token 数
            total_tokens: 总 token 数
        """
        async with get_session() as session:
            log = LlmPromptLog(
                task_id=task_id,
                stage_seq=stage_seq,
                model=model,
                system_prompt=system_prompt,
                chat_messages=chat_messages,
                tools_schema=tools_schema,
                response_content=response_content,
                response_tool_calls=response_tool_calls,
                finish_reason=finish_reason,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )
            session.add(log)
            logger.debug(
                f"Prompt 日志已保存: task_id={task_id}, stage_seq={stage_seq}, model={model}"
            )
