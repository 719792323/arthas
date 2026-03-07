"""
ContextWindowManager 端到端管道测试

测试覆盖：
- 完整管道流程、fallback 链路和优化报告
- System Prompt Token 动态计算（新增）
- 动态计算异常时降级到静态 context_reserved_tokens（新增）
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from control_platform.decision.context import DecisionContext
from control_platform.decision.context_management.manager import ContextWindowManager
from control_platform.decision.context_management.token_counter import TokenCounter


def _make_context(message_count=20, big_content=True, rag_context=None, available_tools=None):
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
        available_tools=available_tools or [],
        current_stage_seq=message_count,
        rag_context=rag_context,
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
            with patch(
                "control_platform.decision.openai_engine.build_system_prompt",
                return_value="mock system prompt",
            ):
                result = await manager.optimize(context)

        assert result.task_id == context.task_id
        assert "context_optimization" in result.metadata
        report = result.metadata["context_optimization"]
        assert report["strategies_applied"] == []

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
                    with patch(
                        "control_platform.decision.openai_engine.build_system_prompt",
                        return_value="mock system prompt",
                    ):
                        result = await manager.optimize(context)

        report = result.metadata["context_optimization"]
        assert "full_summary" in report["strategies_applied"]

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
                return_value=(context.messages[:8], False, None),
            ):
                with patch.object(counter, "count_messages", side_effect=[5000, 80, 80]):
                    with patch(
                        "control_platform.decision.openai_engine.build_system_prompt",
                        return_value="mock system prompt",
                    ):
                        result = await manager.optimize(context)

        report = result.metadata["context_optimization"]
        assert "sliding_window" in report["strategies_applied"]

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
            with patch(
                "control_platform.decision.openai_engine.build_system_prompt",
                return_value="mock system prompt",
            ):
                result = await manager.optimize(context)

        assert len(context.messages) == len(original_messages)
        assert context.metadata == {}
        assert "context_optimization" in result.metadata


class TestDynamicSystemPromptTokens:
    """System Prompt Token 动态计算测试"""

    @pytest.mark.asyncio
    async def test_dynamic_system_prompt_tokens(self):
        """测试动态计算 system prompt token 数"""
        counter = TokenCounter()
        manager = ContextWindowManager(
            token_counter=counter,
            llm_summarizer=MagicMock(),
            repo=MagicMock(),
        )

        rag_context = {"knowledge": "一些 RAG 知识内容", "total_tokens": 100}
        context = _make_context(
            message_count=4,
            big_content=False,
            rag_context=rag_context,
            available_tools=[{"name": "tool1"}, {"name": "tool2"}],
        )

        mock_system_prompt = "这是一个包含 RAG 内容和工具列表的 system prompt，" * 10

        with patch(
            "control_platform.decision.openai_engine.build_system_prompt",
            return_value=mock_system_prompt,
        ) as mock_build:
            with patch.object(counter, "get_available_budget", return_value=100000) as mock_budget:
                result = await manager.optimize(context)

            # build_system_prompt 应该被调用
            mock_build.assert_called_once_with(
                context.available_tools,
                context.rag_context,
            )

            # get_available_budget 应该被传入动态计算的 system_prompt_tokens
            call_args = mock_budget.call_args
            assert call_args is not None
            assert "system_prompt_tokens" in call_args.kwargs
            tokens_passed = call_args.kwargs["system_prompt_tokens"]
            assert tokens_passed is not None
            assert tokens_passed > 0

    @pytest.mark.asyncio
    async def test_dynamic_calculation_failure_degrades(self):
        """测试动态计算失败时降级到静态 context_reserved_tokens"""
        counter = TokenCounter()
        manager = ContextWindowManager(
            token_counter=counter,
            llm_summarizer=MagicMock(),
            repo=MagicMock(),
        )

        context = _make_context(message_count=4, big_content=False)

        with patch(
            "control_platform.decision.openai_engine.build_system_prompt",
            side_effect=Exception("导入失败或其他异常"),
        ):
            with patch.object(counter, "get_available_budget", return_value=100000) as mock_budget:
                result = await manager.optimize(context)

            # 异常时 system_prompt_tokens 应该为 None（降级）
            call_args = mock_budget.call_args
            assert call_args is not None
            assert call_args.kwargs.get("system_prompt_tokens") is None

        # 管道不应中断
        assert "context_optimization" in result.metadata


class TestTokenCounterBudget:
    """TokenCounter.get_available_budget 测试"""

    def test_dynamic_budget_calculation(self):
        """测试传入 system_prompt_tokens 时的动态计算"""
        counter = TokenCounter()

        with patch("control_platform.decision.context_management.token_counter.settings") as mock_settings:
            mock_settings.context_max_tokens = 128000
            mock_settings.llm_max_tokens = 4096

            budget = counter.get_available_budget(system_prompt_tokens=5000)
            expected = 128000 - 5000 - 4096
            assert budget == expected

    def test_static_fallback_budget(self):
        """测试未传入 system_prompt_tokens 时使用静态值"""
        counter = TokenCounter()

        with patch("control_platform.decision.context_management.token_counter.settings") as mock_settings:
            mock_settings.context_max_tokens = 128000
            mock_settings.context_reserved_tokens = 8192

            budget = counter.get_available_budget(system_prompt_tokens=None)
            expected = 128000 - 8192
            assert budget == expected

    def test_budget_never_negative(self):
        """测试预算永远不为负数"""
        counter = TokenCounter()

        with patch("control_platform.decision.context_management.token_counter.settings") as mock_settings:
            mock_settings.context_max_tokens = 1000
            mock_settings.llm_max_tokens = 4096

            # system_prompt_tokens 超过 context_max_tokens
            budget = counter.get_available_budget(system_prompt_tokens=999999)
            assert budget == 0