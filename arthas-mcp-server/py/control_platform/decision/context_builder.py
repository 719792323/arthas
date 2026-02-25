"""
诊断上下文构建器

从数据库加载 task 下所有已完成 stage，按 stage_seq 排序，
转换为结构化消息列表，供 DecisionEngine 使用。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from control_platform.db.models import DiagnosisStage, StageStatus, StageType
from control_platform.db.repository import DiagnosisRepository
from control_platform.decision.context import DecisionContext

logger = logging.getLogger(__name__)


class ContextBuilder:
    """
    诊断上下文构建器

    从数据库加载 task 的完整 stage 历史，
    将每种 stage_type 转换为对应的结构化消息格式，
    构建 DecisionContext 供 DecisionEngine 使用。

    消息格式映射：
    - USER_QUERY → role: "user"
    - LLM_THINKING → role: "assistant"
    - TOOL_CALL → role: "function_call"
    - TOOL_RESULT → role: "function_result"
    - LLM_CONCLUSION → role: "assistant"（conclusion）
    """

    def __init__(self):
        # 按 session_id 索引的工具列表，每个客户端可能有不同的工具集
        self._tools_by_session: Dict[str, List[Dict[str, Any]]] = {}

    def set_available_tools(self, session_id: str, tools: List[Dict[str, Any]]) -> None:
        """设置指定 session 的可用工具列表"""
        self._tools_by_session[session_id] = tools
        logger.info(f"更新 session {session_id[:8]} 的工具列表: {len(tools)} 个工具")

    def get_available_tools(self, session_id: str) -> List[Dict[str, Any]]:
        """获取指定 session 的可用工具列表"""
        return self._tools_by_session.get(session_id, [])

    def has_tools(self, session_id: str) -> bool:
        """检查指定 session 是否已有工具列表"""
        return session_id in self._tools_by_session and len(self._tools_by_session[session_id]) > 0

    def remove_session_tools(self, session_id: str) -> None:
        """移除指定 session 的工具列表（session 断连时调用）"""
        self._tools_by_session.pop(session_id, None)

    async def build_context(
        self,
        task_id: str,
        repo: DiagnosisRepository,
    ) -> DecisionContext:
        """
        从数据库构建完整的诊断上下文。

        Args:
            task_id: 诊断任务 ID
            repo: 诊断仓储层

        Returns:
            构建好的 DecisionContext
        """
        # 1. 加载 task 下所有 stage（按 stage_seq 升序）
        stages = await repo.get_task_stages(task_id)

        # 2. 转换为结构化消息列表
        messages = self._stages_to_messages(stages)

        # 3. 加载 task 基本信息
        task = await repo.get_task(task_id)

        # 4. 获取该 session 的可用工具列表
        session_id = task.session_id if task else ""
        available_tools = self.get_available_tools(session_id)

        # 5. 构建 DecisionContext
        context = DecisionContext(
            task_id=task_id,
            session_id=session_id,
            user_query=task.user_query if task else "",
            messages=messages,
            available_tools=available_tools,
            current_stage_seq=task.current_stage_seq if task else 0,
            rag_context=None,  # 预留 RAG 上下文
        )

        logger.debug(
            f"构建上下文完成: task_id={task_id}, "
            f"messages={len(messages)}, tools={len(available_tools)}"
        )
        return context

    def _stages_to_messages(self, stages: List[DiagnosisStage]) -> List[Dict[str, Any]]:
        """
        将 stage 列表转换为结构化消息列表。

        只包含已完成（completed）的 stage，确保上下文是确定性的。

        如果存在 CONTEXT_SUMMARY 事件，则采用分支加载逻辑：
        只加载 锚点（stage_seq=1 的 USER_QUERY）+ 最新摘要事件 + 摘要事件之后的新消息。
        否则全量加载。
        """
        # 过滤出已完成的 stage
        completed_stages = [
            s for s in stages if s.status == StageStatus.COMPLETED.value
        ]

        # 检查是否存在 CONTEXT_SUMMARY 事件
        summary_stages = [
            s for s in completed_stages
            if s.stage_type == StageType.CONTEXT_SUMMARY.value
        ]

        if summary_stages:
            # 找到最新的 CONTEXT_SUMMARY 事件
            latest_summary = max(summary_stages, key=lambda s: s.stage_seq)

            # 分支加载：锚点 + 最新摘要事件 + 摘要事件之后的新消息
            filtered_stages = []

            for stage in completed_stages:
                if stage.stage_seq == 1 and stage.stage_type == StageType.USER_QUERY.value:
                    # 锚点：第一条用户提问
                    filtered_stages.append(stage)
                elif stage.stage_seq == latest_summary.stage_seq:
                    # 最新摘要事件
                    filtered_stages.append(stage)
                elif stage.stage_seq > latest_summary.stage_seq:
                    # 摘要事件之后的新消息
                    filtered_stages.append(stage)
                # 其他（被摘要覆盖的旧消息）跳过

            logger.info(
                "检测到 CONTEXT_SUMMARY 事件: summary_seq=%d, "
                "原始 %d 条 stage → 加载 %d 条",
                latest_summary.stage_seq,
                len(completed_stages),
                len(filtered_stages),
            )
            completed_stages = filtered_stages

        messages = []
        for stage in completed_stages:
            msg = self._stage_to_message(stage)
            if msg:
                messages.append(msg)

        return messages

    def _stage_to_message(self, stage: DiagnosisStage) -> Optional[Dict[str, Any]]:
        """将单个 stage 转换为结构化消息"""
        stage_type = stage.stage_type
        input_data = stage.input_data or {}
        output_data = stage.output_data or {}

        if stage_type == StageType.USER_QUERY.value:
            return {
                "role": "user",
                "content": input_data.get("user_query", ""),
                "stage_seq": stage.stage_seq,
                "stage_type": stage_type,
            }

        elif stage_type == StageType.LLM_THINKING.value:
            return {
                "role": "assistant",
                "content": output_data.get("thinking", ""),
                "action_type": output_data.get("action_type"),
                "tool_name": output_data.get("tool_name"),
                "tool_arguments": output_data.get("tool_arguments"),
                "stage_seq": stage.stage_seq,
                "stage_type": stage_type,
            }

        elif stage_type == StageType.TOOL_CALL.value:
            # 优先使用摘要内容（如果已被即时摘要处理过）
            content = stage.tool_result or ""
            metadata = {}
            if stage.summarized_content is not None:
                content = stage.summarized_content
                metadata = {
                    "summarized": True,
                    "summary_type": stage.summary_type,
                    "original_tokens": stage.original_tokens,
                }
            return {
                "role": "function_call",
                "tool_name": stage.tool_name,
                "tool_arguments": stage.tool_arguments,
                "content": content,
                "stage_seq": stage.stage_seq,
                "stage_type": stage_type,
                **metadata,
            }

        elif stage_type == StageType.TOOL_RESULT.value:
            # 对 TOOL_RESULT 也检查是否有摘要内容
            tool_result_content = input_data.get("tool_result", "")
            metadata = {}
            if stage.summarized_content is not None:
                tool_result_content = stage.summarized_content
                metadata = {
                    "summarized": True,
                    "summary_type": stage.summary_type,
                    "original_tokens": stage.original_tokens,
                }
            return {
                "role": "function_result",
                "tool_name": input_data.get("tool_name", ""),
                "content": tool_result_content,
                "stage_seq": stage.stage_seq,
                "stage_type": stage_type,
                **metadata,
            }

        elif stage_type == StageType.LLM_CONCLUSION.value:
            return {
                "role": "assistant",
                "content": output_data.get("conclusion", ""),
                "is_conclusion": True,
                "stage_seq": stage.stage_seq,
                "stage_type": stage_type,
            }

        elif stage_type == StageType.CONTEXT_SUMMARY.value:
            # CONTEXT_SUMMARY 事件转为 system 角色的摘要消息
            summary_content = output_data.get("summary", "")
            summary_input = input_data or {}
            return {
                "role": "system",
                "content": f"[诊断历史摘要]\n{summary_content}",
                "stage_seq": stage.stage_seq,
                "stage_type": stage_type,
                "summary_type": "full",
                "summary_stage_id": stage.stage_seq,
                "original_message_count": summary_input.get("original_message_count", 0),
                "original_tokens": summary_input.get("original_tokens", 0),
                "from_stage_seq": summary_input.get("from_stage_seq"),
                "to_stage_seq": summary_input.get("to_stage_seq"),
            }

        else:
            logger.warning(f"未知的 stage_type: {stage_type}")
            return None
