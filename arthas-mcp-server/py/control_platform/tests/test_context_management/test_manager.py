"""
ContextWindowManager 端到端管道测试

验证完整管道流程、fallback 链路和优化报告。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from control_platform.decision.context import DecisionContext
from control_platform.decision.context_management.manager import ContextWindowManager
from control_platform.decision.context_management.token_counter import TokenCounter


def _make_context(message_count=20, big_content=True):
    """构造模拟的 DecisionContext"""
    messages = [{"role": "user", "content": "排查 Java 应用内存泄漏问题"}]
    for i in range(1, message_count):
        if i % 2 == 1:
            messages.append({"role": "assistant", "content": f"第{(i+1)//2}轮分析..."})
        else:
            content = "Thread: WAITING\n" * (200 if big_content else 5)
            messages.append({"role": "tool", "content": content, "tool_call_id": f"c{i}"})

    return DecisionContext(
        task_id="test-task-1",
        session_id="test-session",
        user_query="排查 Java 应用内存泄漏问题",
        messages=messages,
        available_tools=[],
        current_stage_seq=message_count,
        metadata={},
    )


class TestOptimizePipeline:
    """优化管道测试"""

    @pytest.mark.asyncio
    @patch("control_platform.decision.context_management.manager.ContextWindowManager.__init__", return_value=None)
    async def test_skip_optimization_under_budget(self, mock_init):
        """未超预算时跳过优化"""
        counter = TokenCounter()

        manager = ContextWindowManager.__new__(ContextWindowManager)
        manager._token_counter = counter
        manager._llm_summarizer = MagicMock()
        manager._repo = MagicMock()
        manager._sliding_window = MagicMock()
        manager._full_summary = MagicMock()

        # 小上下文
        context = _make_context(message_count=4, big_content=False)

        with patch.object(counter, "get_available_budget", return_value=100000):
            result = await manager.optimize(context)

        assert result.task_id == context.task_id
        assert "context_optimization" in result.metadata
        report = result.metadata["context_optimization"]
        assert report["strategies_applied"] == []
        print(f"[对比报告] 未超预算: {report}")

    @pytest.mark.asyncio
    async def test_full_summary_success_pipeline(self):
        """超预算 → 全文摘要成功"""
        counter = TokenCounter()
        mock_llm = MagicMock()
        mock_repo = MagicMock()
        mock_repo.create_context_summary_stage = AsyncMock()

        manager = ContextWindowManager(
            token_counter=counter,
            llm_summarizer=mock_llm,
            repo=mock_repo,
        )

        # 构造超预算的上下文
        context = _make_context(message_count=20, big_content=True)

        # Mock full_summary 返回压缩后的消息
        compressed_messages = [
            context.messages[0],
            {"role": "system", "content": "[诊断历史摘要]\n压缩后的摘要", "summary_type": "full"},
            context.messages[-2],
            context.messages[-1],
        ]

        with patch.object(counter, "get_available_budget", return_value=100):
            with patch.object(
                manager._full_summary,
                "apply",
                new_callable=AsyncMock,
                return_value=(compressed_messages, True, {"task_id": "test-task-1", "summary_content": "压缩后的摘要"}),
            ):
                with patch.object(counter, "count_messages", side_effect=[5000, 80, 80]):
                    result = await manager.optimize(context)

        report = result.metadata["context_optimization"]
        assert "full_summary" in report["strategies_applied"]
        print(f"[对比报告] 全文摘要成功: {report}")

    @pytest.mark.asyncio
    async def test_fallback_to_sliding_window(self):
        """全文摘要失败 → 回退到滑动窗口"""
        counter = TokenCounter()
        mock_llm = MagicMock()
        mock_repo = MagicMock()

        manager = ContextWindowManager(
            token_counter=counter,
            llm_summarizer=mock_llm,
            repo=mock_repo,
        )

        context = _make_context(message_count=20, big_content=True)

        with patch.object(counter, "get_available_budget", return_value=100):
            with patch.object(
                manager._full_summary,
                "apply",
                new_callable=AsyncMock,
                return_value=(context.messages[:8], False, None),  # 摘要失败返回滑动窗口结果
            ):
                with patch.object(counter, "count_messages", side_effect=[5000, 80, 80]):
                    result = await manager.optimize(context)

        report = result.metadata["context_optimization"]
        assert "sliding_window" in report["strategies_applied"]
        print(f"[对比报告] 回退到滑动窗口: {report}")

    @pytest.mark.asyncio
    async def test_original_context_not_modified(self):
        """原始 context 不被修改"""
        counter = TokenCounter()
        manager = ContextWindowManager(
            token_counter=counter,
            llm_summarizer=MagicMock(),
            repo=MagicMock(),
        )

        context = _make_context(message_count=4, big_content=False)
        original_messages = list(context.messages)

        with patch.object(counter, "get_available_budget", return_value=100000):
            result = await manager.optimize(context)

        # 原始 context 未修改
        assert len(context.messages) == len(original_messages)
        assert context.metadata == {}
        # 新 context 有优化报告
        assert "context_optimization" in result.metadata
