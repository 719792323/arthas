"""
Token 计数与预算管理服务

基于 tiktoken 实现精确的 token 计数能力，支持按模型名称自动选择编码器，
并提供预算计算逻辑。
"""

import json
import logging
from typing import Any, Dict, List, Optional

import tiktoken

from control_platform.config import settings

logger = logging.getLogger(__name__)


class TokenCounter:
    """
    Token 计数器

    提供基于 tiktoken 的 token 计数能力，支持：
    - 按模型名自动选择编码器，不支持时 fallback 到 cl100k_base
    - 单条消息计数、消息列表计数、纯文本计数
    - 预算计算（available_budget = context_max_tokens - system_prompt_tokens - llm_max_tokens）
    """

    # 每条消息的固定 token 开销（role + 分隔符等），参照 OpenAI 的计算方式
    _MESSAGE_OVERHEAD = 4  # 每条消息额外消耗约 4 tokens（<|im_start|>role\n...content...<|im_end|>\n）
    # 整个对话的固定尾部开销
    _REPLY_OVERHEAD = 3  # 每次对话末尾的 <|im_start|>assistant 前缀

    def __init__(self, model: Optional[str] = None):
        """
        初始化 TokenCounter

        Args:
            model: 模型名称，用于选择 tiktoken 编码器。为 None 时使用 settings.llm_model。
        """
        self._model = model or settings.llm_model
        self._encoding = self._get_encoding(self._model)

    def _get_encoding(self, model: str) -> tiktoken.Encoding:
        """根据模型名获取编码器，不支持时 fallback 到 cl100k_base"""
        try:
            return tiktoken.encoding_for_model(model)
        except KeyError:
            logger.warning(
                "tiktoken 不支持模型 '%s' 的编码器，回退到 cl100k_base",
                model,
            )
            return tiktoken.get_encoding("cl100k_base")

    @property
    def model(self) -> str:
        """当前使用的模型名称"""
        return self._model

    @property
    def encoding_name(self) -> str:
        """当前使用的编码器名称"""
        return self._encoding.name

    def count_text(self, text: str) -> int:
        """
        计算纯文本的 token 数量

        Args:
            text: 待计数的文本字符串

        Returns:
            token 数量
        """
        if not text:
            return 0
        return len(self._encoding.encode(text))

    def count_message(self, message: Dict[str, Any]) -> int:
        """
        计算单条消息的 token 数量

        消息格式遵循 OpenAI Chat API 的消息结构，包含 role、content、
        name、tool_calls、tool_call_id 等字段。

        Args:
            message: 单条消息字典

        Returns:
            该消息的 token 数量（含固定开销）
        """
        num_tokens = self._MESSAGE_OVERHEAD

        for key, value in message.items():
            if value is None:
                continue
            if isinstance(value, str):
                num_tokens += len(self._encoding.encode(value))
            elif isinstance(value, (list, dict)):
                # tool_calls 等复杂字段，序列化后计数
                serialized = json.dumps(value, ensure_ascii=False)
                num_tokens += len(self._encoding.encode(serialized))
            # 其他类型（int、bool 等）忽略或按字符串处理
            else:
                num_tokens += len(self._encoding.encode(str(value)))

        return num_tokens

    def count_messages(self, messages: List[Dict[str, Any]]) -> int:
        """
        计算消息列表的总 token 数量

        Args:
            messages: 消息列表

        Returns:
            总 token 数量（含每条消息的固定开销 + 对话尾部开销）
        """
        if not messages:
            return 0
        total = sum(self.count_message(msg) for msg in messages)
        total += self._REPLY_OVERHEAD  # 对话尾部开销
        return total

    def get_available_budget(self, system_prompt_tokens: Optional[int] = None) -> int:
        """
        计算可用 token 预算

        当传入 system_prompt_tokens 时（动态计算模式）：
            available_budget = context_max_tokens - system_prompt_tokens - llm_max_tokens
        当未传入时（静态降级模式）：
            available_budget = context_max_tokens - context_reserved_tokens

        Args:
            system_prompt_tokens: 动态计算的 system prompt 实际 token 数。
                为 None 时使用静态 context_reserved_tokens 作为降级默认值。

        Returns:
            可用 token 预算
        """
        if system_prompt_tokens is not None:
            budget = settings.context_max_tokens - system_prompt_tokens - settings.llm_max_tokens
        else:
            budget = settings.context_max_tokens - settings.context_reserved_tokens
        return max(budget, 0)

    def is_over_budget(self, messages: List[Dict[str, Any]]) -> bool:
        """
        判断消息列表是否超出预算

        Args:
            messages: 消息列表

        Returns:
            True 表示超出预算
        """
        total_tokens = self.count_messages(messages)
        return total_tokens > self.get_available_budget()

    def get_budget_report(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        生成预算报告

        Args:
            messages: 消息列表

        Returns:
            包含 total_tokens、available_budget、over_budget、overflow_tokens 的报告字典
        """
        total_tokens = self.count_messages(messages)
        available_budget = self.get_available_budget()
        over_budget = total_tokens > available_budget
        overflow_tokens = max(total_tokens - available_budget, 0)

        return {
            "total_tokens": total_tokens,
            "available_budget": available_budget,
            "context_max_tokens": settings.context_max_tokens,
            "context_reserved_tokens": settings.context_reserved_tokens,
            "over_budget": over_budget,
            "overflow_tokens": overflow_tokens,
            "message_count": len(messages),
        }
