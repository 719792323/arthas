"""
TokenCounter 单元测试

验证 token 计数准确性、编码器选择与 fallback、预算计算逻辑，
并输出 Before/After 对比报告。
"""

import json
from unittest.mock import patch

import pytest

from control_platform.decision.context_management.token_counter import TokenCounter


class TestTokenCounterInit:
    """编码器选择与 fallback 测试"""

    def test_default_model_from_settings(self):
        """使用 settings.llm_model 初始化"""
        counter = TokenCounter()
        assert counter.model != ""
        assert counter.encoding_name != ""

    def test_custom_model(self):
        """指定已知模型"""
        counter = TokenCounter(model="gpt-4")
        assert counter.model == "gpt-4"

    def test_unknown_model_fallback_to_cl100k(self):
        """未知模型回退到 cl100k_base"""
        counter = TokenCounter(model="unknown-model-xyz")
        assert counter.encoding_name == "cl100k_base"


class TestCountText:
    """纯文本计数测试"""

    def test_empty_text(self):
        counter = TokenCounter()
        assert counter.count_text("") == 0

    def test_simple_text(self):
        counter = TokenCounter()
        result = counter.count_text("Hello, world!")
        assert result > 0
        print(f"[对比报告] 'Hello, world!' -> {result} tokens")

    def test_long_text(self):
        counter = TokenCounter()
        long_text = "这是一段很长的中文文本。" * 500
        result = counter.count_text(long_text)
        assert result > 100
        print(f"[对比报告] 长文本({len(long_text)} 字符) -> {result} tokens")


class TestCountMessage:
    """单条消息计数测试"""

    def test_simple_user_message(self):
        counter = TokenCounter()
        msg = {"role": "user", "content": "帮我排查 Java 应用内存泄漏问题"}
        result = counter.count_message(msg)
        assert result > 0
        print(f"[对比报告] 用户消息 -> {result} tokens")

    def test_assistant_message_with_tool_calls(self):
        counter = TokenCounter()
        msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "jstack",
                        "arguments": json.dumps({"pid": "12345"}),
                    },
                }
            ],
        }
        result = counter.count_message(msg)
        assert result > 0
        print(f"[对比报告] 带 tool_calls 的 assistant 消息 -> {result} tokens")

    def test_tool_result_message(self):
        counter = TokenCounter()
        big_result = "java.lang.Thread.State: WAITING\n" * 200
        msg = {
            "role": "tool",
            "tool_call_id": "call_123",
            "content": big_result,
        }
        result = counter.count_message(msg)
        assert result > 200
        print(f"[对比报告] 大体积工具结果消息({len(big_result)} 字符) -> {result} tokens")

    def test_empty_content_message(self):
        counter = TokenCounter()
        msg = {"role": "assistant", "content": None}
        result = counter.count_message(msg)
        assert result >= 4


class TestCountMessages:
    """消息列表计数测试"""

    def test_empty_list(self):
        counter = TokenCounter()
        assert counter.count_messages([]) == 0

    def test_multiple_messages(self):
        counter = TokenCounter()
        messages = [
            {"role": "user", "content": "帮我排查内存泄漏"},
            {"role": "assistant", "content": "好的，我先用 jstack 查看线程状态"},
            {"role": "tool", "tool_call_id": "c1", "content": "Thread-1: WAITING\nThread-2: RUNNABLE"},
            {"role": "assistant", "content": "从线程状态来看..."},
        ]
        result = counter.count_messages(messages)
        assert result > 0
        print(f"[对比报告] {len(messages)} 条消息 -> {result} tokens")

    def test_ten_round_conversation(self):
        """模拟 10 轮诊断对话的 token 统计"""
        counter = TokenCounter()
        messages = []
        messages.append({"role": "user", "content": "帮我排查 Java 应用内存泄漏问题，PID 为 12345"})
        for i in range(10):
            messages.append({"role": "assistant", "content": f"第{i+1}轮分析：让我执行 jstack 查看线程状态..."})
            tool_output = f"=== jstack round {i+1} ===\n" + "java.lang.Thread.State: WAITING (parking)\n" * 100
            messages.append({"role": "tool", "tool_call_id": f"call_{i}", "content": tool_output})
        result = counter.count_messages(messages)
        assert result > 1000
        print(f"[对比报告] 10 轮诊断对话 ({len(messages)} 条消息) -> {result} tokens")


class TestBudget:
    """预算管理测试"""

    @patch("control_platform.decision.context_management.token_counter.settings")
    def test_get_available_budget(self, mock_settings):
        mock_settings.context_max_tokens = 60000
        mock_settings.context_reserved_tokens = 4000
        mock_settings.llm_model = "gpt-4"
        counter = TokenCounter(model="gpt-4")
        budget = counter.get_available_budget()
        assert budget == 56000

    @patch("control_platform.decision.context_management.token_counter.settings")
    def test_budget_floor_at_zero(self, mock_settings):
        mock_settings.context_max_tokens = 1000
        mock_settings.context_reserved_tokens = 5000
        mock_settings.llm_model = "gpt-4"
        counter = TokenCounter(model="gpt-4")
        budget = counter.get_available_budget()
        assert budget == 0

    @patch("control_platform.decision.context_management.token_counter.settings")
    def test_is_over_budget(self, mock_settings):
        mock_settings.context_max_tokens = 100
        mock_settings.context_reserved_tokens = 10
        mock_settings.llm_model = "gpt-4"
        counter = TokenCounter(model="gpt-4")
        # 使用多样化中文内容确保超过 90 tokens 预算
        big_content = "这是一段用于测试的中文文本，包含各种不同的字符。" * 50
        messages = [{"role": "user", "content": big_content}]
        assert counter.is_over_budget(messages) is True

    @patch("control_platform.decision.context_management.token_counter.settings")
    def test_budget_report(self, mock_settings):
        mock_settings.context_max_tokens = 60000
        mock_settings.context_reserved_tokens = 4000
        mock_settings.llm_model = "gpt-4"
        counter = TokenCounter(model="gpt-4")
        messages = [
            {"role": "user", "content": "测试消息"},
            {"role": "assistant", "content": "回复"},
        ]
        report = counter.get_budget_report(messages)
        assert "total_tokens" in report
        assert "available_budget" in report
        assert report["available_budget"] == 56000
        assert report["over_budget"] is False
        assert report["message_count"] == 2
        print(f"[对比报告] 预算报告: {json.dumps(report, indent=2, ensure_ascii=False)}")
