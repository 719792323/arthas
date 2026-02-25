"""
诊断阶段处理器

StageHandler 定义阶段处理的抽象接口。
StageHandlerRegistry 提供处理器注册和查找能力。
各 StageHandler 实现对应不同 stage_type 的处理逻辑。
"""

from __future__ import annotations

import abc
import json
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from control_platform.config import settings
from control_platform.db.models import (
    ApprovalStatus,
    DiagnosisStage,
    DiagnosisTask,
    StageStatus,
    StageType,
)
from control_platform.db.repository import DiagnosisRepository
from control_platform.models.action import ActionType, DecisionResult
from control_platform.protocol.mcp_handler import McpHandler

if TYPE_CHECKING:
    from control_platform.decision.engine import DecisionEngine
    from control_platform.decision.context_builder import ContextBuilder
    from control_platform.session.session_manager import SessionManager

logger = logging.getLogger(__name__)


# ======================== 抽象基类 ========================

class StageHandler(abc.ABC):
    """
    阶段处理器抽象基类

    每种 stage_type 对应一个 StageHandler 实现。
    处理器由 StageHandlerRegistry 管理，由 EventScheduler 调度。
    """

    @abc.abstractmethod
    async def handle(
        self,
        task: DiagnosisTask,
        stage: DiagnosisStage,
        repo: DiagnosisRepository,
    ) -> None:
        """
        处理一个诊断阶段

        Args:
            task: 所属的诊断任务
            stage: 当前待处理的阶段
            repo: 诊断仓储层（用于数据库操作）
        """
        ...

    @property
    def handler_name(self) -> str:
        """处理器名称（默认为类名）"""
        return self.__class__.__name__


class StageHandlerRegistry:
    """
    阶段处理器注册表

    通过 stage_type 注册和查找对应的 StageHandler。
    """

    def __init__(self):
        self._handlers: Dict[str, StageHandler] = {}

    def register(self, stage_type: str, handler: StageHandler) -> None:
        """注册 stage_type 对应的处理器"""
        if stage_type in self._handlers:
            logger.warning(
                f"覆盖已注册的处理器: stage_type={stage_type}, "
                f"old={self._handlers[stage_type].handler_name}, "
                f"new={handler.handler_name}"
            )
        self._handlers[stage_type] = handler
        logger.info(f"📋 注册阶段处理器: stage_type={stage_type}, handler={handler.handler_name}")

    def get_handler(self, stage_type: str) -> Optional[StageHandler]:
        """查找 stage_type 对应的处理器"""
        return self._handlers.get(stage_type)

    @property
    def registered_types(self) -> List[str]:
        """已注册的 stage_type 列表"""
        return list(self._handlers.keys())


# ======================== 具体处理器实现 ========================

class UserQueryHandler(StageHandler):
    """
    USER_QUERY 阶段处理器

    用户提问阶段（stage_seq=1），作为 task 的起点。
    拾取后完成当前 stage 并创建下一个 LLM_THINKING stage。
    返回 next_stage 供调度器投递到 TaskPool 立即执行。

    幂等性：纯状态转换，天然幂等。
    """

    async def handle(
        self,
        task: DiagnosisTask,
        stage: DiagnosisStage,
        repo: DiagnosisRepository,
    ) -> Optional[DiagnosisStage]:
        user_query = task.user_query
        logger.info(f"处理用户提问: task_id={task.task_id}, query={user_query[:50]}...")

        # 完成当前 USER_QUERY stage，创建 LLM_THINKING stage
        next_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={"user_query": user_query},
            next_stage_type=StageType.LLM_THINKING.value,
            next_input_data={
                "user_query": user_query,
                "instruction": "请分析用户问题，决定下一步操作",
            },
        )

        # 返回 next_stage，由调度器在释放锁之后投递到 TaskPool 立即执行 LLM 推理
        return next_stage


class LlmThinkingHandler(StageHandler):
    """
    LLM_THINKING 阶段处理器

    调用 DecisionEngine 进行 LLM 推理，根据返回的 action_type
    创建 TOOL_CALL（继续 ReAct 循环）或 LLM_CONCLUSION（结束诊断）stage。

    幂等性：TaskLock 排他 + LLM 调用无副作用（最多多花一次 API 费用）。
    """

    def __init__(
        self,
        decision_engine: DecisionEngine,
        context_builder: ContextBuilder,
    ):
        self._engine = decision_engine
        self._context_builder = context_builder

    async def handle(
        self,
        task: DiagnosisTask,
        stage: DiagnosisStage,
        repo: DiagnosisRepository,
    ) -> Optional[DiagnosisStage]:
        logger.info(f"LLM 推理开始: task_id={task.task_id}, stage_seq={stage.stage_seq}")

        try:
            # 1. 构建上下文
            context = await self._context_builder.build_context(task.task_id, repo)

            # 2. 调用决策引擎
            result: DecisionResult = await self._engine.decide(context)

            # 2.1 保存 Prompt 日志（仅在配置启用且引擎支持时）
            if (
                settings.enable_prompt_logging
                and hasattr(self._engine, 'last_prompt_log')
                and self._engine.last_prompt_log
            ):
                try:
                    await repo.save_prompt_log(**self._engine.last_prompt_log)
                except Exception as log_err:
                    logger.warning(f"Prompt 日志保存失败（不影响主流程）: {log_err}")

            # 3. 根据决策结果创建下一个 stage
            output_data = {
                "thinking": result.thinking,
                "action_type": result.action_type.value,
            }

            if result.action_type == ActionType.TOOL_CALL:
                # 继续 ReAct 循环：创建 TOOL_CALL stage
                output_data["tool_name"] = result.tool_name
                output_data["tool_arguments"] = result.tool_arguments

                # 延迟持久化：LLM 决定继续调工具，说明还有后续推理，此时持久化 CONTEXT_SUMMARY
                if hasattr(self._engine, 'persist_pending_summary'):
                    try:
                        await self._engine.persist_pending_summary()
                    except Exception as ps_err:
                        logger.warning(
                            f"CONTEXT_SUMMARY 持久化失败（不影响主流程）: {ps_err}"
                        )

                next_stage = await repo.complete_and_next(
                    stage_id=stage.id,
                    output_data=output_data,
                    next_stage_type=StageType.TOOL_CALL.value,
                    next_input_data={
                        "thinking": result.thinking,
                    },
                    next_tool_name=result.tool_name,
                    next_tool_arguments=result.tool_arguments,
                )
                logger.info(
                    f"LLM 决定调用工具: task_id={task.task_id}, "
                    f"tool={result.tool_name}"
                )
                # 返回 TOOL_CALL stage，由调度器投递到 TaskPool 立即发送
                return next_stage

            elif result.action_type == ActionType.CONCLUDE:
                # 诊断结束：创建 LLM_CONCLUSION stage
                output_data["conclusion"] = result.conclusion

                next_stage = await repo.complete_and_next(
                    stage_id=stage.id,
                    output_data=output_data,
                    next_stage_type=StageType.LLM_CONCLUSION.value,
                    next_input_data={
                        "conclusion": result.conclusion,
                        "thinking": result.thinking,
                    },
                )
                logger.info(f"LLM 决定结束诊断: task_id={task.task_id}")
                # 返回 LLM_CONCLUSION stage，由调度器投递到 TaskPool 立即执行
                return next_stage

        except Exception as e:
            logger.error(
                f"LLM 推理失败: task_id={task.task_id}, "
                f"stage_seq={stage.stage_seq}, error={e}",
                exc_info=True,
            )
            is_final_failure = await repo.mark_failed(stage.id, str(e))
            if is_final_failure:
                await repo.fail_task(task.task_id)

        return None


class ToolCallHandler(StageHandler):
    """
    TOOL_CALL 阶段处理器（全异步，只发不等）

    先检查审核配置，需要审核则设为 waiting_approval；
    通过审核或无需审核后，向 Arthas 客户端发送 tools/call 请求。
    发送后 handler 立即结束，释放锁和线程。

    WS 收到 Java 端响应后，由 main.py 中的 WS 回调负责创建 TOOL_RESULT stage
    并投递到 TaskPool 继续执行。

    如果 WS 响应丢失或超时，下次定时轮询仍会拉到这个 PENDING 的 TOOL_CALL stage，
    再次发送请求（Java 端有幂等保护，重复发送安全）。

    幂等性：Java 端 TaskStageTracker.putIfAbsent 保证工具幂等执行。
    """

    def __init__(
        self,
        session_manager: SessionManager,
        mcp_handler: McpHandler,
    ):
        self._session_manager = session_manager
        self._mcp_handler = mcp_handler

    async def handle(
        self,
        task: DiagnosisTask,
        stage: DiagnosisStage,
        repo: DiagnosisRepository,
    ) -> None:
        tool_name = stage.tool_name or ""
        tool_arguments = stage.tool_arguments or {}

        # 1. 检查审核配置
        if self._needs_approval(tool_name, stage):
            await repo.mark_waiting_approval(stage.id)
            logger.info(
                f"工具需要审核: task_id={task.task_id}, tool={tool_name}"
            )
            return

        # 2. 获取 Arthas 客户端 session
        client_session = await self._session_manager.get_session(task.session_id)
        if client_session is None:
            # session 断连是临时状态，不消耗重试次数，保持 pending 等待下次轮询重试
            logger.warning(
                f"Arthas 客户端会话暂不可用，等待重连: "
                f"task_id={task.task_id}, session_id={task.session_id}"
            )
            return

        logger.info(
            f"工具调用处理（异步发送）: task_id={task.task_id}, "
            f"stage_seq={stage.stage_seq}, tool={tool_name}"
        )

        # 3. 构建 tools/call 请求并发送（只发不等）
        #    在请求的 _meta 中注入 taskId 和 stageId（stage_seq），
        #    Java 端会在响应的 _meta 中回传这两个字段，
        #    WS 回调通过 extract_task_stage_from_response 提取后从数据库查 stage，
        #    无需内存中的 pending_request_info。
        request_id = client_session.next_request_id()
        request_msg = McpHandler.build_tools_call_request(
            tool_name=tool_name,
            arguments=tool_arguments,
            task_id=task.task_id,
            stage_id=str(stage.stage_seq),  # 使用 stage_seq 作为 stageId
            request_id=request_id,
        )

        # 只发送，不等待响应。发完 handler 就结束，释放锁和线程。
        # WS 收到响应后由 main.py 的回调负责：
        #   1. 从 response._meta 中提取 taskId + stageId（stage_seq）
        #   2. 从数据库查 stage 获取 tool_name、tool_arguments 等信息
        #   3. 创建 TOOL_RESULT stage 并投递到 TaskPool
        # 如果发送失败或 WS 响应丢失，下次轮询仍会拉到 PENDING 的 TOOL_CALL，重新发送（幂等安全）。
        success = await client_session.send_message(request_msg)
        if not success:
            logger.warning(
                f"工具调用发送失败，等待下次轮询重试: "
                f"task_id={task.task_id}, tool={tool_name}"
            )
            return

        # 更新 last_sent_at，用于冷却时间控制
        await repo.update_stage_last_sent_at(stage.id)

        logger.info(
            f"工具调用已发送（异步等待响应）: task_id={task.task_id}, "
            f"tool={tool_name}, request_id={request_id}"
        )

    def _needs_approval(self, tool_name: str, stage: DiagnosisStage) -> bool:
        """
        检查工具是否需要审核。

        如果已经 approved 则不需要再审核。
        """
        if stage.approval_status == ApprovalStatus.APPROVED.value:
            return False

        commands_requiring_approval = getattr(
            settings, "commands_requiring_approval", []
        )
        return tool_name in commands_requiring_approval

    @staticmethod
    def extract_tool_result(result: Dict[str, Any]) -> str:
        """从 MCP tools/call 响应的 result 中提取文本结果"""
        content = result.get("content", [])
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    texts.append(item.get("text", ""))
            return "\n".join(texts) if texts else json.dumps(result, ensure_ascii=False)
        return str(content)


class ToolResultHandler(StageHandler):
    """
    TOOL_RESULT 阶段处理器

    将工具结果打包为 input_data，创建新的 LLM_THINKING stage。
    返回 next_stage 给调度器，由调度器负责投递到 TaskPool 加速。

    在 complete_and_next 之前，对前一个 TOOL_CALL stage 执行即时摘要
    （如果工具结果超过阈值）。摘要失败不阻断诊断流程。

    幂等性：纯状态转换，天然幂等。
    """

    async def handle(
        self,
        task: DiagnosisTask,
        stage: DiagnosisStage,
        repo: DiagnosisRepository,
    ) -> Optional[DiagnosisStage]:
        logger.info(
            f"处理工具结果: task_id={task.task_id}, stage_seq={stage.stage_seq}"
        )

        # 在 complete_and_next 之前，对前一个 TOOL_CALL stage 执行即时摘要
        await self._try_summarize_tool_result(task, stage, repo)

        input_data = stage.input_data or {}
        tool_result_content = input_data.get("tool_result", "")

        # 检查前一个 TOOL_CALL stage 是否有摘要内容，如果有则优先使用摘要
        # 避免将原始大量内容存入 LLM_THINKING 的 input_data
        try:
            tool_call_seq = stage.stage_seq - 1
            if tool_call_seq >= 1:
                tool_call_stage = await repo.get_stage_by_task_and_seq(
                    task.task_id, tool_call_seq
                )
                if (
                    tool_call_stage is not None
                    and tool_call_stage.stage_type == StageType.TOOL_CALL.value
                    and tool_call_stage.summarized_content is not None
                ):
                    tool_result_content = tool_call_stage.summarized_content
                    logger.info(
                        f"使用摘要内容替代原始工具结果: task_id={task.task_id}, "
                        f"tool_call_seq={tool_call_seq}, "
                        f"original_len={len(input_data.get('tool_result', ''))}, "
                        f"summary_len={len(tool_result_content)}"
                    )
        except Exception as e:
            logger.warning(
                f"获取 TOOL_CALL 摘要失败（使用原始内容）: task_id={task.task_id}, error={e}"
            )

        # 完成当前 TOOL_RESULT stage，创建新的 LLM_THINKING stage
        next_stage = await repo.complete_and_next(
            stage_id=stage.id,
            output_data={"forwarded_to_llm": True},
            next_stage_type=StageType.LLM_THINKING.value,
            next_input_data={
                "tool_name": input_data.get("tool_name"),
                "tool_result": tool_result_content,
                "instruction": "请根据工具执行结果继续分析",
            },
        )

        # 返回 next_stage，由调度器在释放锁之后投递到 TaskPool
        return next_stage

    @staticmethod
    async def _try_summarize_tool_result(
        task: DiagnosisTask,
        current_stage: DiagnosisStage,
        repo: DiagnosisRepository,
    ) -> None:
        """
        尝试对前一个 TOOL_CALL stage 的工具结果执行即时摘要。
        任何异常都被捕获，不阻断诊断流程。
        """
        try:
            from control_platform.decision.context_management.tool_result_summarizer import (
                ToolResultSummarizer,
            )

            # 前一个 stage 就是 TOOL_CALL stage（stage_seq = current - 1）
            tool_call_seq = current_stage.stage_seq - 1
            if tool_call_seq < 1:
                return

            tool_call_stage = await repo.get_stage_by_task_and_seq(
                task.task_id, tool_call_seq
            )
            if tool_call_stage is None:
                return

            # 只对 TOOL_CALL 类型且有 tool_result 且尚未摘要的 stage 执行
            if (
                tool_call_stage.stage_type != StageType.TOOL_CALL.value
                or not tool_call_stage.tool_result
                or tool_call_stage.summarized_content is not None
            ):
                return

            summarizer = ToolResultSummarizer()
            await summarizer.maybe_summarize(
                stage=tool_call_stage,
                repo=repo,
                user_query=task.user_query or "",
            )
        except Exception as e:
            logger.warning(
                f"工具结果即时摘要失败（不阻断流程）: task_id={task.task_id}, "
                f"stage_seq={current_stage.stage_seq}, error={e}",
                exc_info=True,
            )


class LlmConclusionHandler(StageHandler):
    """
    LLM_CONCLUSION 阶段处理器

    保存结论到 stage output_data 和 task.conclusion，
    标记 task 为 completed，整个诊断结束。

    幂等性：TaskLock 排他 + 结论写入幂等。
    """

    async def handle(
        self,
        task: DiagnosisTask,
        stage: DiagnosisStage,
        repo: DiagnosisRepository,
    ) -> None:
        input_data = stage.input_data or {}
        conclusion = input_data.get("conclusion", "诊断完成（无结论）")
        thinking = input_data.get("thinking", "")

        logger.info(f"保存诊断结论: task_id={task.task_id}")

        # 1. 完成当前 stage（不创建下一个）
        await repo.complete_stage(
            stage_id=stage.id,
            output_data={
                "conclusion": conclusion,
                "thinking": thinking,
            },
        )

        # 2. 标记 task 为 completed
        await repo.complete_task(
            task_id=task.task_id,
            conclusion=conclusion,
        )

        logger.info(
            f"诊断结束: task_id={task.task_id}, "
            f"conclusion_length={len(conclusion)}"
        )