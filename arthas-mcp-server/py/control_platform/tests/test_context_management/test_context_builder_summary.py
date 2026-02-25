"""
ContextBuilder 摘要缓存读取与 CONTEXT_SUMMARY 事件测试
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from control_platform.decision.context_builder import ContextBuilder
from control_platform.db.models import StageStatus, StageType


def _make_stage(
    stage_seq: int,
    stage_type: str,
    status: str = StageStatus.COMPLETED.value,
    input_data: dict = None,
    output_data: dict = None,
    tool_result: str = None,
    tool_name: str = None,
    summarized_content: str = None,
    summary_type: str = None,
    original_tokens: int = None,
    summary_tokens: int = None,
):
    stage = MagicMock()
    stage.stage_seq = stage_seq
    stage.stage_type = stage_type
    stage.status = status
    stage.input_data = input_data or {}
    stage.output_data = output_data or {}
    stage.tool_result = tool_result
    stage.tool_name = tool_name
    stage.tool_arguments = None
    stage.summarized_content = summarized_content
    stage.summary_type = summary_type
    stage.original_tokens = original_tokens
    stage.summary_tokens = summary_tokens
    return stage


class TestSummarizedContentCache:
    """摘要缓存读取测试"""

    def test_tool_call_uses_summarized_content(self):
        """TOOL_CALL stage 有 summarized_content 时优先使用"""
        builder = ContextBuilder()
        stage = _make_stage(
            stage_seq=3,
            stage_type=StageType.TOOL_CALL.value,
            tool_result="原始巨大输出...",
            tool_name="jstack",
            summarized_content="摘要：线程阻塞",
            summary_type="llm",
            original_tokens=5000,
        )
        msg = builder._stage_to_message(stage)
        assert msg["content"] == "摘要：线程阻塞"
        assert msg["summarized"] is True
        assert msg["summary_type"] == "llm"

    def test_tool_call_uses_original_when_no_summary(self):
        """TOOL_CALL stage 无 summarized_content 时使用原始内容"""
        builder = ContextBuilder()
        stage = _make_stage(
            stage_seq=3,
            stage_type=StageType.TOOL_CALL.value,
            tool_result="原始输出",
            tool_name="jstack",
        )
        msg = builder._stage_to_message(stage)
        assert msg["content"] == "原始输出"
        assert "summarized" not in msg

    def test_tool_result_uses_summarized_content(self):
        """TOOL_RESULT stage 有 summarized_content 时优先使用"""
        builder = ContextBuilder()
        stage = _make_stage(
            stage_seq=4,
            stage_type=StageType.TOOL_RESULT.value,
            input_data={"tool_name": "jstack", "tool_result": "原始内容"},
            summarized_content="摘要内容",
            summary_type="rule",
            original_tokens=3000,
        )
        msg = builder._stage_to_message(stage)
        assert msg["content"] == "摘要内容"
        assert msg["summarized"] is True


class TestContextSummaryEvent:
    """CONTEXT_SUMMARY 事件检测与分支加载逻辑测试"""

    def test_context_summary_converts_to_system_message(self):
        """CONTEXT_SUMMARY 事件转为 system 角色消息"""
        builder = ContextBuilder()
        stage = _make_stage(
            stage_seq=10,
            stage_type=StageType.CONTEXT_SUMMARY.value,
            input_data={
                "from_stage_seq": 2,
                "to_stage_seq": 8,
                "original_message_count": 6,
                "original_tokens": 5000,
            },
            output_data={
                "summary": "### 已执行的工具\n- jstack → 线程阻塞\n### 关键发现\n- 死锁",
                "summary_tokens": 100,
            },
        )
        msg = builder._stage_to_message(stage)
        assert msg["role"] == "system"
        assert "诊断历史摘要" in msg["content"]
        assert msg["summary_type"] == "full"
        assert msg["from_stage_seq"] == 2
        assert msg["to_stage_seq"] == 8

    def test_branch_loading_with_summary_event(self):
        """存在 CONTEXT_SUMMARY 时，分支加载：锚点 + 摘要 + 后续新消息"""
        builder = ContextBuilder()
        stages = [
            _make_stage(1, StageType.USER_QUERY.value, input_data={"user_query": "排查内存"}),
            _make_stage(2, StageType.LLM_THINKING.value, output_data={"thinking": "分析中"}),
            _make_stage(3, StageType.TOOL_CALL.value, tool_result="大量输出", tool_name="jstack"),
            _make_stage(4, StageType.TOOL_RESULT.value, input_data={"tool_name": "jstack", "tool_result": "结果"}),
            _make_stage(5, StageType.LLM_THINKING.value, output_data={"thinking": "继续"}),
            # CONTEXT_SUMMARY 事件
            _make_stage(
                6,
                StageType.CONTEXT_SUMMARY.value,
                input_data={"from_stage_seq": 2, "to_stage_seq": 5, "original_message_count": 4, "original_tokens": 3000},
                output_data={"summary": "摘要内容"},
            ),
            # 摘要之后的新消息
            _make_stage(7, StageType.LLM_THINKING.value, output_data={"thinking": "新的分析"}),
            _make_stage(8, StageType.TOOL_CALL.value, tool_result="新结果", tool_name="heapdump"),
        ]
        messages = builder._stages_to_messages(stages)

        # 应该只有 3 条消息：锚点(seq=1) + 摘要(seq=6) + 新消息(seq=7, seq=8)
        assert len(messages) == 4
        assert messages[0]["role"] == "user"  # 锚点
        assert messages[0]["stage_seq"] == 1
        assert messages[1]["role"] == "system"  # 摘要事件
        assert messages[1]["stage_seq"] == 6
        assert messages[2]["stage_seq"] == 7
        assert messages[3]["stage_seq"] == 8

    def test_full_loading_without_summary_event(self):
        """无 CONTEXT_SUMMARY 时，全量加载"""
        builder = ContextBuilder()
        stages = [
            _make_stage(1, StageType.USER_QUERY.value, input_data={"user_query": "排查"}),
            _make_stage(2, StageType.LLM_THINKING.value, output_data={"thinking": "分析"}),
            _make_stage(3, StageType.TOOL_CALL.value, tool_result="结果", tool_name="jstack"),
        ]
        messages = builder._stages_to_messages(stages)
        assert len(messages) == 3

    def test_pending_stages_are_excluded(self):
        """PENDING 状态的 stage 被排除"""
        builder = ContextBuilder()
        stages = [
            _make_stage(1, StageType.USER_QUERY.value, input_data={"user_query": "排查"}),
            _make_stage(2, StageType.LLM_THINKING.value, status="pending", output_data={"thinking": "分析"}),
        ]
        messages = builder._stages_to_messages(stages)
        assert len(messages) == 1
