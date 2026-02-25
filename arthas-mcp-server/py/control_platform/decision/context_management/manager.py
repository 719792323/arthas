"""
上下文窗口管理器

统一编排推理前的上下文优化策略：
Step 1: 消息已加载（ContextBuilder 完成）
Step 2: Token 计数
Step 3: 全文 LLM 摘要（超预算时）
Step 4: 滑动窗口硬裁剪（fallback）
Step 5: 最终校验
"""

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from control_platform.db.repository import DiagnosisRepository
from control_platform.decision.context import DecisionContext
from control_platform.decision.context_management.token_counter import TokenCounter
from control_platform.decision.context_management.llm_summarizer import LLMSummarizer
from control_platform.decision.context_management.full_summary import FullSummaryStrategy
from control_platform.decision.context_management.sliding_window import SlidingWindowStrategy

logger = logging.getLogger(__name__)


class ContextWindowManager:
    """
    上下文窗口管理器

    依赖注入 TokenCounter、LLMSummarizer、DiagnosisRepository，
    编排推理前的上下文优化管道。

    管道流程：
    1. 消息已加载（来自 ContextBuilder）
    2. Token 计数：评估是否超预算
    3. 全文 LLM 摘要：超预算时对压缩区调用 LLM 压缩（延迟持久化 CONTEXT_SUMMARY 事件）
    4. 滑动窗口硬裁剪：全文摘要失败或仍超预算时的 fallback
    5. 最终校验：记录优化报告

    延迟持久化机制：
    optimize() 不再直接将 CONTEXT_SUMMARY 写入数据库。
    而是将待持久化数据放入 metadata["pending_summary"]。
    调用方（LlmThinkingHandler）根据 LLM 的决策结果决定是否持久化：
    - LLM 返回 tool_call → 还有后续推理，持久化摘要
    - LLM 返回 conclude → 诊断结束，不持久化（避免浪费和序号污染）
    """

    def __init__(
        self,
        token_counter: TokenCounter = None,
        llm_summarizer: LLMSummarizer = None,
        repo: DiagnosisRepository = None,
    ):
        self._token_counter = token_counter or TokenCounter()
        self._llm_summarizer = llm_summarizer or LLMSummarizer()
        self._repo = repo or DiagnosisRepository()

        self._sliding_window = SlidingWindowStrategy(token_counter=self._token_counter)
        self._full_summary = FullSummaryStrategy(
            token_counter=self._token_counter,
            llm_summarizer=self._llm_summarizer,
            repo=self._repo,
            sliding_window=self._sliding_window,
        )

    async def optimize(self, context: DecisionContext) -> DecisionContext:
        """
        执行上下文优化管道。

        Args:
            context: 原始 DecisionContext（不被修改）

        Returns:
            优化后的新 DecisionContext 实例，metadata 中包含优化报告
        """
        messages = context.messages
        strategies_applied = []
        pending_summary = None

        # Step 2: Token 计数
        # 注意：role="function_call" 的消息在 OpenAI _build_chat_messages 中被跳过（不发给 LLM），
        # 其 content 与 role="function_result" 重复，不应重复计入 token 预算
        countable_messages = [m for m in messages if m.get("role") != "function_call"]
        original_tokens = self._token_counter.count_messages(countable_messages)
        original_count = len(messages)
        available_budget = self._token_counter.get_available_budget()

        logger.info(
            "上下文优化开始: task_id=%s, 消息=%d 条, tokens=%d, 预算=%d",
            context.task_id,
            original_count,
            original_tokens,
            available_budget,
        )

        # 是否超预算？
        if original_tokens <= available_budget:
            # 未超预算，跳到 Step 5
            logger.info("上下文未超预算，跳过优化: %d <= %d", original_tokens, available_budget)
        else:
            # Step 3: 全文 LLM 摘要（延迟持久化）
            try:
                messages, summary_success, pending_summary = await self._full_summary.apply(
                    messages=messages,
                    task_id=context.task_id,
                    user_query=context.user_query,
                )

                if summary_success:
                    strategies_applied.append("full_summary")
                else:
                    strategies_applied.append("sliding_window")  # fallback 已在 full_summary 内执行
            except Exception as e:
                logger.warning(
                    "全文摘要管道异常，回退到滑动窗口: task_id=%s, error=%s",
                    context.task_id,
                    str(e),
                )
                messages = self._sliding_window.apply(messages)
                strategies_applied.append("sliding_window")

            # Step 3 后检查：全文摘要成功但仍超预算 → Step 4 滑动窗口兜底
            countable_after_summary = [m for m in messages if m.get("role") != "function_call"]
            current_tokens = self._token_counter.count_messages(countable_after_summary)
            if current_tokens > available_budget and "sliding_window" not in strategies_applied:
                logger.info(
                    "全文摘要后仍超预算 (%d > %d)，执行滑动窗口兜底",
                    current_tokens,
                    available_budget,
                )
                messages = self._sliding_window.apply(messages)
                strategies_applied.append("sliding_window")

        # Step 5: 最终校验
        countable_final = [m for m in messages if m.get("role") != "function_call"]
        final_tokens = self._token_counter.count_messages(countable_final)
        final_count = len(messages)

        # 构建优化报告
        report = {
            "original_tokens": original_tokens,
            "final_tokens": final_tokens,
            "available_budget": available_budget,
            "strategies_applied": strategies_applied,
            "messages_removed": original_count - final_count,
            "messages_summarized": sum(1 for m in messages if m.get("summary_type") == "full"),
            "compression_rate": f"{(1 - final_tokens / original_tokens) * 100:.1f}%" if original_tokens > 0 else "0%",
        }

        logger.info(
            "上下文优化完成: task_id=%s, %d→%d tokens (%s), 策略=%s",
            context.task_id,
            original_tokens,
            final_tokens,
            report["compression_rate"],
            strategies_applied or ["none"],
        )

        # 构建新的 DecisionContext（不修改原实例）
        new_metadata = dict(context.metadata) if context.metadata else {}
        new_metadata["context_optimization"] = report

        # 将待持久化的摘要数据放入 metadata，由调用方决定是否持久化
        if pending_summary is not None:
            new_metadata["pending_summary"] = pending_summary

        return DecisionContext(
            task_id=context.task_id,
            session_id=context.session_id,
            user_query=context.user_query,
            messages=messages,
            available_tools=context.available_tools,
            current_stage_seq=context.current_stage_seq,
            rag_context=context.rag_context,
            metadata=new_metadata,
        )
