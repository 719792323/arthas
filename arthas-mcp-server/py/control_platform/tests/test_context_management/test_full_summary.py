"""
全文 LLM 摘要策略测试

验证事件生成与持久化、增量摘要、LLM 失败时回退到滑动窗口等。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from control_platform.decision.context_management.full_summary import FullSummaryStrategy
from control_platform.decision.context_management.token_counter import TokenCounter
from control_platform.decision.context_management.sliding_window import SlidingWindowStrategy


def _make_messages(count=10):
    """构造模拟消息列表"""
    messages = [{"role": "user", "content": "排查内存泄漏", "stage_seq": 1, "stage_type": "USER_QUERY"}]
    for i in range(2, count + 1):
        if i % 2 == 0:
            messages.append({
                "role": "assistant",
                "content": f"第{i//2}轮分析...",
                "stage_seq": i,
                "stage_type": "LLM_THINKING",
            })
        else:
            messages.append({
                "role": "tool",
                "content": "Thread: WAITING\n" * 50,
                "tool_call_id": f"c{i}",
                "stage_seq": i,
                "stage_type": "TOOL_CALL",
            })
    return messages


class TestFullSummaryStrategy:
    """全文摘要核心逻辑测试"""

    @pytest.mark.asyncio
    @patch("control_platform.decision.context_management.full_summary.settings")
    async def test_summary_success_creates_event(self, mock_settings):
        """全文摘要成功，验证事件创建"""
        mock_settings.sliding_window_keep_recent = 4

        counter = TokenCounter()
        mock_llm = MagicMock()
        mock_llm.summarize_conversation = AsyncMock(return_value="摘要：5轮诊断，发现线程阻塞")
        mock_llm.model = "gpt-4o-mini"

        mock_repo = MagicMock()
        mock_repo.create_context_summary_stage = AsyncMock()

        strategy = FullSummaryStrategy(
            token_counter=counter,
            llm_summarizer=mock_llm,
            repo=mock_repo,
        )

        messages = _make_messages(12)
        result, success, pending = await strategy.apply(messages, task_id="t1", user_query="排查内存泄漏")

        assert success is True
        assert len(result) < len(messages)
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "system"
        assert "诊断历史摘要" in result[1]["content"]

        # 验证返回待持久化数据（延迟持久化，不再直接调 repo）
        assert pending is not None
        assert pending["task_id"] == "t1"
        assert pending["summary_model"] == "gpt-4o-mini"
        mock_repo.create_context_summary_stage.assert_not_called()

        # 验证调用 persist_summary 可正确持久化
        await strategy.persist_summary(pending)
        mock_repo.create_context_summary_stage.assert_called_once()

        print(f"[对比报告] Before: {len(messages)} 条消息 → After: {len(result)} 条消息")

    @pytest.mark.asyncio
    @patch("control_platform.decision.context_management.full_summary.settings")
    async def test_llm_failure_fallback_to_sliding_window(self, mock_settings):
        """LLM 失败时回退到滑动窗口，不生成事件"""
        mock_settings.sliding_window_keep_recent = 4

        counter = TokenCounter()
        mock_llm = MagicMock()
        mock_llm.summarize_conversation = AsyncMock(side_effect=Exception("API Timeout"))

        mock_repo = MagicMock()
        mock_repo.create_context_summary_stage = AsyncMock()

        strategy = FullSummaryStrategy(
            token_counter=counter,
            llm_summarizer=mock_llm,
            repo=mock_repo,
        )

        messages = _make_messages(12)
        result, success, pending = await strategy.apply(messages, task_id="t1", user_query="排查")

        assert success is False
        assert pending is None
        # 回退到滑动窗口
        assert len(result) < len(messages)
        # 不应创建事件
        mock_repo.create_context_summary_stage.assert_not_called()

    @pytest.mark.asyncio
    @patch("control_platform.decision.context_management.full_summary.settings")
    async def test_incremental_summary(self, mock_settings):
        """增量摘要：压缩区包含旧摘要事件"""
        mock_settings.sliding_window_keep_recent = 3

        counter = TokenCounter()
        mock_llm = MagicMock()
        mock_llm.summarize_conversation = AsyncMock(return_value="增量摘要：覆盖更大范围")
        mock_llm.model = "test-model"

        mock_repo = MagicMock()
        mock_repo.create_context_summary_stage = AsyncMock()

        strategy = FullSummaryStrategy(
            token_counter=counter,
            llm_summarizer=mock_llm,
            repo=mock_repo,
        )

        # 包含旧的 CONTEXT_SUMMARY 事件
        messages = [
            {"role": "user", "content": "排查", "stage_seq": 1, "stage_type": "USER_QUERY"},
            {"role": "system", "content": "[诊断历史摘要]\n旧摘要内容",
             "stage_seq": 6, "stage_type": "CONTEXT_SUMMARY",
             "from_stage_seq": 2, "to_stage_seq": 5},
            {"role": "assistant", "content": "新分析1", "stage_seq": 7, "stage_type": "LLM_THINKING"},
            {"role": "tool", "content": "新结果1", "stage_seq": 8, "stage_type": "TOOL_CALL"},
            {"role": "assistant", "content": "新分析2", "stage_seq": 9, "stage_type": "LLM_THINKING"},
            {"role": "tool", "content": "新结果2", "stage_seq": 10, "stage_type": "TOOL_CALL"},
            {"role": "assistant", "content": "新分析3", "stage_seq": 11, "stage_type": "LLM_THINKING"},
        ]

        result, success, pending = await strategy.apply(messages, task_id="t1", user_query="排查")

        assert success is True
        assert pending is not None
        # 验证增量摘要的 from_stage_seq 从旧摘要覆盖的起始开始
        assert pending["from_stage_seq"] == 2  # 旧摘要的 from_stage_seq

    @pytest.mark.asyncio
    @patch("control_platform.decision.context_management.full_summary.settings")
    async def test_no_compress_when_few_messages(self, mock_settings):
        """消息数不足时不压缩"""
        mock_settings.sliding_window_keep_recent = 6

        counter = TokenCounter()
        mock_llm = MagicMock()
        mock_repo = MagicMock()

        strategy = FullSummaryStrategy(
            token_counter=counter, llm_summarizer=mock_llm, repo=mock_repo,
        )

        messages = [
            {"role": "user", "content": "问题", "stage_seq": 1},
            {"role": "assistant", "content": "回答", "stage_seq": 2},
        ]

        result, success, pending = await strategy.apply(messages, task_id="t1", user_query="问题")
        assert success is False
        assert pending is None
        assert len(result) == 2


class TestSplitZones:
    """消息区域划分测试"""

    @patch("control_platform.decision.context_management.full_summary.settings")
    def test_split_zones_basic(self, mock_settings):
        mock_settings.sliding_window_keep_recent = 3

        strategy = FullSummaryStrategy()
        messages = _make_messages(10)
        anchor, compress, keep = strategy._split_zones(messages)

        assert anchor["role"] == "user"
        assert len(keep) == 3
        assert len(compress) == 10 - 1 - 3  # 总数 - 锚点 - 保留窗口
