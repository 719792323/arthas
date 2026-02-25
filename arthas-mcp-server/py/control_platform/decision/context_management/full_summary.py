"""
全文 LLM 摘要策略

对中间历史消息调用 LLM 进行智能压缩摘要，
并将摘要结果作为 CONTEXT_SUMMARY 事件持久化到数据库。
支持增量摘要：检测已有摘要事件时，压缩区包含旧摘要 + 后续中间消息。
LLM 失败时回退到滑动窗口策略。
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from control_platform.config import settings
from control_platform.db.repository import DiagnosisRepository
from control_platform.decision.context_management.token_counter import TokenCounter
from control_platform.decision.context_management.llm_summarizer import LLMSummarizer
from control_platform.decision.context_management.sliding_window import SlidingWindowStrategy

logger = logging.getLogger(__name__)


class FullSummaryStrategy:
    """
    全文 LLM 摘要策略

    将消息划分为三个区域：
    - 锚点区：第一条 user 消息（始终保留）
    - 压缩区：锚点之后、保留窗口之前的中间消息（被 LLM 压缩）
    - 保留窗口区：最近 N 条消息（始终保留）

    成功时：在数据库中插入 CONTEXT_SUMMARY 事件
    失败时：回退到 SlidingWindowStrategy
    """

    def __init__(
        self,
        token_counter: TokenCounter = None,
        llm_summarizer: LLMSummarizer = None,
        repo: DiagnosisRepository = None,
        sliding_window: SlidingWindowStrategy = None,
    ):
        self._token_counter = token_counter or TokenCounter()
        self._llm_summarizer = llm_summarizer or LLMSummarizer()
        self._repo = repo or DiagnosisRepository()
        self._sliding_window = sliding_window or SlidingWindowStrategy(token_counter=self._token_counter)

    async def apply(
        self,
        messages: List[Dict[str, Any]],
        task_id: str,
        user_query: str,
    ) -> Tuple[List[Dict[str, Any]], bool, Optional[Dict[str, Any]]]:
        """
        执行全文 LLM 摘要。

        Args:
            messages: 当前全部已加载消息列表
            task_id: 诊断任务 ID
            user_query: 用户原始问题

        Returns:
            (优化后的消息列表, 是否成功执行了全文摘要, 待持久化的摘要数据)
            待持久化的摘要数据为 dict（成功时）或 None（失败时），
            调用方应在确认需要时才调用 persist_summary() 写入数据库。
            如果 LLM 失败，返回滑动窗口裁剪的结果、False 和 None。
        """
        # 划分三个区域
        anchor, compress_zone, keep_zone = self._split_zones(messages)

        if not compress_zone:
            # 没有可压缩的内容
            return list(messages), False, None

        # 确定压缩区的 stage_seq 范围（用于持久化）
        from_stage_seq = self._get_stage_seq(compress_zone[0])
        to_stage_seq = self._get_stage_seq(compress_zone[-1])

        # 检查是否包含旧的摘要事件（增量摘要场景）
        old_summary_msg = None
        for msg in compress_zone:
            if msg.get("stage_type") == "CONTEXT_SUMMARY":
                old_summary_msg = msg
                # 增量摘要：from_stage_seq 从旧摘要覆盖的起始开始
                old_from = msg.get("from_stage_seq")
                if old_from is not None:
                    from_stage_seq = old_from
                break

        original_tokens = self._token_counter.count_messages(compress_zone)
        original_message_count = len(compress_zone)

        logger.info(
            "开始全文 LLM 摘要: task_id=%s, 压缩区 %d 条消息 (%d tokens), "
            "from_seq=%s, to_seq=%s, 增量=%s",
            task_id,
            original_message_count,
            original_tokens,
            from_stage_seq,
            to_stage_seq,
            old_summary_msg is not None,
        )

        try:
            # 调用 LLM 全文摘要
            summary_text = await self._llm_summarizer.summarize_conversation(
                messages=compress_zone,
                user_query=user_query,
            )
            summary_tokens = self._token_counter.count_text(summary_text)

            # 构建待持久化的摘要数据（延迟持久化，由调用方决定时机）
            pending_summary = {
                "task_id": task_id,
                "summary_content": summary_text,
                "summary_tokens": summary_tokens,
                "summary_model": self._llm_summarizer.model,
                "from_stage_seq": from_stage_seq,
                "to_stage_seq": to_stage_seq,
                "original_message_count": original_message_count,
                "original_tokens": original_tokens,
                "user_query": user_query,
            }

            # 构建压缩后的消息列表
            summary_message = {
                "role": "system",
                "content": f"[诊断历史摘要]\n{summary_text}",
                "summary_type": "full",
                "original_message_count": original_message_count,
                "original_tokens": original_tokens,
                "from_stage_seq": from_stage_seq,
                "to_stage_seq": to_stage_seq,
            }

            result = [anchor, summary_message] + keep_zone

            logger.info(
                "全文 LLM 摘要完成: task_id=%s, 压缩 %d 条 (%d tokens) → 1 条 (%d tokens), 压缩率=%.1f%%",
                task_id,
                original_message_count,
                original_tokens,
                summary_tokens,
                (1 - summary_tokens / original_tokens) * 100 if original_tokens > 0 else 0,
            )

            return result, True, pending_summary

        except Exception as e:
            # LLM 失败，回退到滑动窗口
            logger.warning(
                "全文 LLM 摘要失败，回退到滑动窗口: task_id=%s, error=%s",
                task_id,
                str(e),
            )
            fallback_result = self._sliding_window.apply(messages)
            return fallback_result, False, None

    def _split_zones(
        self, messages: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        将消息划分为锚点、压缩区、保留窗口区。

        切分时会对齐消息组边界，确保 assistant(tool_call) + function_call + function_result
        三条配对消息不会被拆分到不同区域。

        Returns:
            (anchor, compress_zone, keep_zone)
        """
        if not messages:
            return {}, [], []

        keep_recent = settings.sliding_window_keep_recent

        # 锚点：第一条 user 消息
        anchor = messages[0]

        # 如果消息数不足以划分
        if len(messages) <= keep_recent + 1:
            return anchor, [], messages[1:]

        # 保留窗口区：最近 N 条
        split_idx = len(messages) - keep_recent

        # 边界对齐：向前扩展保留区以包含完整的工具调用组
        # 如果切分点处的消息是工具调用组的中间部分，向前移动切分点
        while split_idx > 1:
            boundary_msg = messages[split_idx]
            role = boundary_msg.get("role", "")
            stage_type = boundary_msg.get("stage_type", "")

            # 如果切分点是 function_call 或 function_result，向前找到对应的 assistant(tool_call)
            if role in ("function_call", "function_result") or stage_type in ("TOOL_CALL", "TOOL_RESULT"):
                split_idx -= 1
            else:
                break

        keep_zone = messages[split_idx:]

        # 压缩区：锚点之后 ~ 保留窗口之前
        compress_zone = messages[1:split_idx]

        return anchor, compress_zone, keep_zone

    @staticmethod
    def _get_stage_seq(msg: Dict[str, Any]) -> Optional[int]:
        """从消息中获取 stage_seq"""
        return msg.get("stage_seq")

    async def persist_summary(self, pending_summary: Dict[str, Any]) -> None:
        """
        持久化 CONTEXT_SUMMARY 事件到数据库。

        应在确认 LLM 返回 tool_call（即还有后续推理）时才调用此方法。
        如果 LLM 返回 conclude（诊断结束），则无需持久化。

        Args:
            pending_summary: apply() 返回的待持久化摘要数据
        """
        await self._repo.create_context_summary_stage(
            task_id=pending_summary["task_id"],
            summary_content=pending_summary["summary_content"],
            summary_tokens=pending_summary["summary_tokens"],
            summary_model=pending_summary["summary_model"],
            from_stage_seq=pending_summary["from_stage_seq"],
            to_stage_seq=pending_summary["to_stage_seq"],
            original_message_count=pending_summary["original_message_count"],
            original_tokens=pending_summary["original_tokens"],
            user_query=pending_summary["user_query"],
        )
        logger.info(
            "CONTEXT_SUMMARY 已持久化: task_id=%s, from=%s, to=%s",
            pending_summary["task_id"],
            pending_summary["from_stage_seq"],
            pending_summary["to_stage_seq"],
        )
