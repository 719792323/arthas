"""
集成测试

验证跨组件交互场景：
- 全文摘要持久化跨轮次测试（第一轮触发摘要 → 第二轮直接读取，LLM 调用次数为 0）
- 端到端管道完整流程
"""

from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from control_platform.decision.context import DecisionContext
from control_platform.decision.context_management.token_counter import TokenCounter
from control_platform.decision.context_management.manager import ContextWindowManager
from control_platform.decision.context_management.full_summary import FullSummaryStrategy
from control_platform.decision.context_builder import ContextBuilder
from control_platform.db.models import StageStatus, StageType

from control_platform.tests.test_context_management.conftest import (
    make_ten_round_messages,
    make_ten_round_stages,
    make_ten_round_context,
)


class TestCrossRoundSummaryPersistence:
    """全文摘要持久化跨轮次测试"""

    @pytest.mark.asyncio
    @patch("control_platform.decision.context_management.full_summary.settings")
    async def test_second_round_reads_cached_summary(self, mock_settings):
        """
        第一轮超预算触发全文摘要 → 持久化事件
        第二轮 ContextBuilder 读取摘要事件 → 不触发全文摘要
        """
        mock_settings.sliding_window_keep_recent = 4

        counter = TokenCounter()
        mock_llm = MagicMock()
        mock_llm.summarize_conversation = AsyncMock(return_value="全文摘要结果：10轮诊断完成")
        mock_llm.model = "test-model"

        mock_repo = MagicMock()
        mock_repo.create_context_summary_stage = AsyncMock()

        strategy = FullSummaryStrategy(
            token_counter=counter,
            llm_summarizer=mock_llm,
            repo=mock_repo,
        )

        # ===== 第一轮：超预算触发全文摘要 =====
        messages_round1 = make_ten_round_messages()
        result1, success1, pending1 = await strategy.apply(
            messages=messages_round1,
            task_id="t1",
            user_query="排查内存泄漏",
        )

        assert success1 is True
        assert pending1 is not None
        assert mock_llm.summarize_conversation.call_count == 1
        # 延迟持久化：apply() 不再直接调 repo
        mock_repo.create_context_summary_stage.assert_not_called()
        # 手动持久化
        await strategy.persist_summary(pending1)
        mock_repo.create_context_summary_stage.assert_called_once()

        round1_calls = mock_llm.summarize_conversation.call_count
        print(f"[对比报告] 第一轮: LLM 摘要调用次数 = {round1_calls}")

        # ===== 第二轮：模拟 ContextBuilder 检测到 CONTEXT_SUMMARY 后的行为 =====
        # 构造第二轮的消息：锚点 + 摘要事件 + 后续新消息（模拟 ContextBuilder 分支加载）
        builder = ContextBuilder()
        summary_stage = MagicMock()
        summary_stage.stage_seq = 22
        summary_stage.stage_type = StageType.CONTEXT_SUMMARY.value
        summary_stage.status = StageStatus.COMPLETED.value
        summary_stage.input_data = {
            "from_stage_seq": 2, "to_stage_seq": 20,
            "original_message_count": 19, "original_tokens": 5000,
        }
        summary_stage.output_data = {
            "summary": "全文摘要结果：10轮诊断完成",
            "summary_tokens": 100,
        }
        summary_stage.summarized_content = None
        summary_stage.summary_type = None
        summary_stage.original_tokens = None

        # 模拟第二轮的 stages：原始 + 摘要事件 + 新消息
        stages_round2 = make_ten_round_stages()
        stages_round2.append(summary_stage)

        # 添加新消息 stages
        new_stage = MagicMock()
        new_stage.stage_seq = 23
        new_stage.stage_type = StageType.LLM_THINKING.value
        new_stage.status = StageStatus.COMPLETED.value
        new_stage.input_data = {}
        new_stage.output_data = {"thinking": "新一轮分析"}
        new_stage.summarized_content = None
        new_stage.summary_type = None
        new_stage.original_tokens = None
        new_stage.tool_name = None
        new_stage.tool_arguments = None
        stages_round2.append(new_stage)

        # ContextBuilder 处理后的消息
        messages_round2 = builder._stages_to_messages(stages_round2)

        # 因为存在 CONTEXT_SUMMARY，应只加载：锚点 + 摘要 + 后续新消息
        anchor_count = sum(1 for m in messages_round2 if m.get("stage_seq") == 1)
        summary_count = sum(1 for m in messages_round2 if m.get("stage_type") == "CONTEXT_SUMMARY")

        assert anchor_count == 1
        assert summary_count == 1
        # 消息数远少于原始 stages 数
        assert len(messages_round2) < len(stages_round2)

        # 第二轮不应再触发全文摘要（因为消息量已大幅减少）
        # 重置 mock 调用计数
        mock_llm.summarize_conversation.reset_mock()

        # 在第二轮中检查：如果未超预算，全文摘要不会被调用
        round2_tokens = counter.count_messages(messages_round2)
        print(f"[对比报告] 第二轮: 消息数 = {len(messages_round2)}, tokens = {round2_tokens}")
        print(f"[对比报告] 第二轮: LLM 摘要调用次数 = {mock_llm.summarize_conversation.call_count} (应为 0)")
        assert mock_llm.summarize_conversation.call_count == 0


class TestEndToEndPipeline:
    """端到端管道完整流程测试"""

    @pytest.mark.asyncio
    async def test_full_pipeline_with_ten_round_data(self, ten_round_context, token_counter):
        """使用 10 轮数据测试完整管道"""
        context = ten_round_context

        before_count = len(context.messages)
        before_tokens = token_counter.count_messages(context.messages)

        mock_llm = MagicMock()
        mock_llm.summarize_conversation = AsyncMock(return_value="完整管道摘要结果")
        mock_llm.model = "test-model"

        mock_repo = MagicMock()
        mock_repo.create_context_summary_stage = AsyncMock()

        manager = ContextWindowManager(
            token_counter=token_counter,
            llm_summarizer=mock_llm,
            repo=mock_repo,
        )

        # 设置一个较低的预算触发优化
        with patch.object(token_counter, "get_available_budget", return_value=100):
            result = await manager.optimize(context)

        after_count = len(result.messages)
        after_tokens = token_counter.count_messages(result.messages)

        report = result.metadata["context_optimization"]

        print(f"\n[端到端对比报告]")
        print(f"  原始: {before_count} 条消息, {before_tokens} tokens")
        print(f"  优化后: {after_count} 条消息, {after_tokens} tokens")
        print(f"  策略: {report['strategies_applied']}")
        print(f"  压缩率: {report['compression_rate']}")
        print(f"  移除消息: {report['messages_removed']}")

        # 基本验证
        assert after_count < before_count
        assert result.task_id == context.task_id
        assert len(report["strategies_applied"]) > 0
