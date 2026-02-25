"""
工具结果即时摘要服务

在工具结果产生后立即判断大小，超阈值时调用 LLM 进行摘要并持久化。
LLM 摘要失败时回退到规则式裁剪。
"""

import logging
from typing import Optional

from control_platform.config import settings
from control_platform.db.models import DiagnosisStage
from control_platform.db.repository import DiagnosisRepository
from control_platform.decision.context_management.token_counter import TokenCounter
from control_platform.decision.context_management.llm_summarizer import LLMSummarizer

logger = logging.getLogger(__name__)


class ToolResultSummarizer:
    """
    工具结果即时摘要器

    在工具结果写入数据库后调用，判断结果大小是否超过阈值，
    超过则调用 LLM 摘要并回写到 stage 的摘要字段。
    LLM 失败时回退到规则式裁剪（保留前 500 + 尾部 200 tokens + 占位符）。
    """

    # 规则式裁剪的默认参数
    RULE_HEAD_TOKENS = 500
    RULE_TAIL_TOKENS = 200

    def __init__(
        self,
        token_counter: Optional[TokenCounter] = None,
        llm_summarizer: Optional[LLMSummarizer] = None,
    ):
        self._token_counter = token_counter or TokenCounter()
        self._llm_summarizer = llm_summarizer or LLMSummarizer()

    async def maybe_summarize(
        self,
        stage: DiagnosisStage,
        repo: DiagnosisRepository,
        user_query: str = "",
    ) -> bool:
        """
        判断工具结果是否需要摘要，如需则执行摘要并回写数据库。

        Args:
            stage: 包含工具结果的 DiagnosisStage（通常是 TOOL_CALL 类型）
            repo: 仓储层，用于回写摘要字段
            user_query: 用户原始问题（用于摘要上下文提示）

        Returns:
            True 表示执行了摘要，False 表示不需要摘要
        """
        if not settings.enable_tool_result_summary:
            return False

        # 获取工具结果原文
        tool_result = stage.tool_result
        if not tool_result:
            return False

        # 计算 token 数
        original_tokens = self._token_counter.count_text(tool_result)

        # 未超阈值，不需要摘要
        if original_tokens <= settings.tool_result_summary_threshold:
            logger.info(
                "工具结果未超阈值，跳过摘要: stage_id=%s, tokens=%d, threshold=%d",
                stage.id,
                original_tokens,
                settings.tool_result_summary_threshold,
            )
            return False

        logger.info(
            "工具结果超阈值，开始摘要: stage_id=%s, tool=%s, tokens=%d, threshold=%d",
            stage.id,
            stage.tool_name,
            original_tokens,
            settings.tool_result_summary_threshold,
        )

        # 尝试 LLM 摘要
        try:
            summarized = await self._llm_summarizer.summarize_single(
                content=tool_result,
                context_hint={
                    "tool_name": stage.tool_name or "unknown",
                    "user_query": user_query,
                },
            )
            summary_tokens = self._token_counter.count_text(summarized)
            summary_type = "llm"

            logger.info(
                "LLM 单条摘要完成: stage_id=%s, original=%d tokens, summary=%d tokens, 压缩率=%.1f%%",
                stage.id,
                original_tokens,
                summary_tokens,
                (1 - summary_tokens / original_tokens) * 100 if original_tokens > 0 else 0,
            )

        except Exception as e:
            # LLM 摘要失败，回退到规则式裁剪
            logger.warning(
                "LLM 单条摘要失败，回退到规则式裁剪: stage_id=%s, error=%s",
                stage.id,
                str(e),
            )
            summarized = self._rule_based_trim(tool_result, original_tokens)
            summary_tokens = self._token_counter.count_text(summarized)
            summary_type = "rule"

        # 回写数据库
        try:
            await repo.update_stage_summary(
                stage_id=stage.id,
                summarized_content=summarized,
                summary_tokens=summary_tokens,
                original_tokens=original_tokens,
                summary_type=summary_type,
            )
        except Exception as e:
            logger.warning(
                "摘要回写数据库失败（不阻断流程）: stage_id=%s, error=%s",
                stage.id,
                str(e),
            )

        return True

    def _rule_based_trim(self, content: str, original_tokens: int) -> str:
        """
        规则式裁剪：保留前 RULE_HEAD_TOKENS + 尾部 RULE_TAIL_TOKENS tokens，
        中间插入占位符。

        基于字符级近似裁剪（token 到字符的映射约 1 token ≈ 3~4 字符）。
        """
        # 近似估算：1 token ≈ 3.5 个字符（中英文混合场景）
        char_per_token = 3.5
        head_chars = int(self.RULE_HEAD_TOKENS * char_per_token)
        tail_chars = int(self.RULE_TAIL_TOKENS * char_per_token)

        if len(content) <= head_chars + tail_chars:
            return content

        head = content[:head_chars]
        tail = content[-tail_chars:]
        trimmed_tokens = original_tokens - self.RULE_HEAD_TOKENS - self.RULE_TAIL_TOKENS

        placeholder = f"\n\n[... 已裁剪约 {trimmed_tokens} tokens ...]\n\n"

        return head + placeholder + tail
