"""
滑动窗口硬裁剪策略

作为 Fallback 策略，当全文 LLM 摘要失败或摘要后仍超预算时，
直接丢弃中间历史消息，保留锚点和最近 N 条消息。
纯内存操作，不持久化到数据库。
"""

import logging
from typing import Any, Dict, List

from control_platform.config import settings
from control_platform.decision.context_management.token_counter import TokenCounter

logger = logging.getLogger(__name__)


class SlidingWindowStrategy:
    """
    滑动窗口硬裁剪策略

    保留规则：
    - 始终保留锚点（第一条 role=user 的消息）
    - 始终保留最近 N 条消息（sliding_window_keep_recent）
    - 丢弃中间所有消息，并插入占位消息
    """

    def __init__(
        self,
        token_counter: TokenCounter = None,
        keep_recent: int = None,
    ):
        self._token_counter = token_counter or TokenCounter()
        self._keep_recent = keep_recent if keep_recent is not None else settings.sliding_window_keep_recent

    def apply(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        对消息列表执行滑动窗口裁剪。

        Args:
            messages: 原始消息列表

        Returns:
            裁剪后的新消息列表（不修改原列表）
        """
        if len(messages) <= self._keep_recent + 1:
            # 消息数不超过 锚点(1) + keep_recent，无需裁剪
            return list(messages)

        # 锚点：第一条 role=user 的消息
        anchor = None
        anchor_idx = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "user":
                anchor = msg
                anchor_idx = i
                break

        if anchor is None:
            # 找不到 user 消息（异常情况），使用第一条作为锚点
            anchor = messages[0]
            anchor_idx = 0

        # 保留窗口：最近 N 条消息
        recent_start = len(messages) - self._keep_recent
        if recent_start <= anchor_idx + 1:
            # 保留窗口已覆盖到锚点之后，无需裁剪
            return list(messages)

        # 边界对齐：向前扩展保留区以包含完整的工具调用组
        # 如果切分点处的消息是工具调用组的中间部分（function_call/function_result），
        # 向前移动切分点，避免拆开 assistant(tool_call) + function_call + function_result 配对
        while recent_start > anchor_idx + 1:
            boundary_msg = messages[recent_start]
            role = boundary_msg.get("role", "")
            stage_type = boundary_msg.get("stage_type", "")
            if role in ("function_call", "function_result") or stage_type in ("TOOL_CALL", "TOOL_RESULT"):
                recent_start -= 1
            else:
                break

        recent_messages = messages[recent_start:]

        # 被丢弃的中间消息
        middle_messages = messages[anchor_idx + 1:recent_start]
        dropped_count = len(middle_messages)
        dropped_tokens = self._token_counter.count_messages(middle_messages)

        # 构建占位消息
        placeholder = {
            "role": "system",
            "content": (
                f"[已省略 {dropped_count} 条历史消息（约 {dropped_tokens} tokens），"
                f"保留最近 {self._keep_recent} 条]"
            ),
        }

        result = [anchor, placeholder] + recent_messages

        logger.info(
            "滑动窗口裁剪完成: 原始 %d 条 → %d 条, 丢弃 %d 条 (约 %d tokens)",
            len(messages),
            len(result),
            dropped_count,
            dropped_tokens,
        )

        return result
