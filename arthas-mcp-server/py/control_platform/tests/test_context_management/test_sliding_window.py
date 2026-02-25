"""
滑动窗口硬裁剪策略测试

验证锚点保留、最近消息保留、中间消息丢弃、占位消息插入、
以及作为 fallback 的触发场景。
"""

from unittest.mock import patch

import pytest

from control_platform.decision.context_management.sliding_window import SlidingWindowStrategy
from control_platform.decision.context_management.token_counter import TokenCounter


class TestSlidingWindow:
    """滑动窗口核心逻辑测试"""

    def test_no_trim_when_few_messages(self):
        """消息数不足时不裁剪"""
        strategy = SlidingWindowStrategy(keep_recent=6)
        messages = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答1"},
            {"role": "assistant", "content": "回答2"},
        ]
        result = strategy.apply(messages)
        assert len(result) == 3

    def test_trim_preserves_anchor(self):
        """裁剪后保留锚点"""
        strategy = SlidingWindowStrategy(keep_recent=2)
        messages = [
            {"role": "user", "content": "排查内存泄漏"},
            {"role": "assistant", "content": "分析1"},
            {"role": "tool", "content": "工具结果1", "tool_call_id": "c1"},
            {"role": "assistant", "content": "分析2"},
            {"role": "tool", "content": "工具结果2", "tool_call_id": "c2"},
            {"role": "assistant", "content": "分析3"},
        ]
        result = strategy.apply(messages)

        assert result[0]["role"] == "user"
        assert result[0]["content"] == "排查内存泄漏"

    def test_trim_preserves_recent_messages(self):
        """裁剪后保留最近 N 条"""
        strategy = SlidingWindowStrategy(keep_recent=2)
        messages = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "分析1"},
            {"role": "tool", "content": "结果1", "tool_call_id": "c1"},
            {"role": "assistant", "content": "分析2"},
            {"role": "tool", "content": "结果2", "tool_call_id": "c2"},
            {"role": "assistant", "content": "最新分析"},
        ]
        result = strategy.apply(messages)

        # 结构: 锚点 + 占位 + 最近2条
        assert len(result) == 4
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "system"  # 占位消息
        assert "已省略" in result[1]["content"]
        assert result[2]["content"] == "结果2"
        assert result[3]["content"] == "最新分析"

    def test_placeholder_contains_stats(self):
        """占位消息包含统计信息"""
        strategy = SlidingWindowStrategy(keep_recent=2)
        messages = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "A" * 1000},
            {"role": "tool", "content": "B" * 1000, "tool_call_id": "c1"},
            {"role": "assistant", "content": "C" * 1000},
            {"role": "assistant", "content": "最新1"},
            {"role": "assistant", "content": "最新2"},
        ]
        result = strategy.apply(messages)
        placeholder = result[1]
        assert "已省略 3 条" in placeholder["content"]
        assert "tokens" in placeholder["content"]

        print(f"[对比报告] Before: {len(messages)} 条消息 → After: {len(result)} 条消息")
        print(f"  占位消息: {placeholder['content']}")

    def test_ten_round_conversation_trim(self):
        """模拟 10 轮对话的裁剪效果"""
        counter = TokenCounter()
        strategy = SlidingWindowStrategy(token_counter=counter, keep_recent=6)

        messages = [{"role": "user", "content": "排查 Java 应用内存泄漏问题"}]
        for i in range(10):
            messages.append({"role": "assistant", "content": f"第{i+1}轮分析..."})
            messages.append({"role": "tool", "content": "Thread: WAITING\n" * 50, "tool_call_id": f"c{i}"})

        before_count = len(messages)
        before_tokens = counter.count_messages(messages)

        result = strategy.apply(messages)
        after_count = len(result)
        after_tokens = counter.count_messages(result)

        assert after_count < before_count
        assert result[0]["role"] == "user"
        assert "已省略" in result[1]["content"]

        print(f"[对比报告] 10轮对话裁剪:")
        print(f"  Before: {before_count} 条消息, {before_tokens} tokens")
        print(f"  After:  {after_count} 条消息, {after_tokens} tokens")

    def test_does_not_modify_original(self):
        """不修改原始消息列表"""
        strategy = SlidingWindowStrategy(keep_recent=2)
        messages = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "1"},
            {"role": "assistant", "content": "2"},
            {"role": "assistant", "content": "3"},
            {"role": "assistant", "content": "4"},
        ]
        original_len = len(messages)
        _ = strategy.apply(messages)
        assert len(messages) == original_len
