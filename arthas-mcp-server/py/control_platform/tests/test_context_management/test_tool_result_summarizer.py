"""
工具结果即时摘要测试

验证即时触发、字段回写、LLM 失败 fallback 到规则式裁剪、异常不阻断流程。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from control_platform.decision.context_management.tool_result_summarizer import ToolResultSummarizer
from control_platform.decision.context_management.token_counter import TokenCounter


def _make_stage(tool_result: str, tool_name: str = "jstack", stage_id: int = 1, summarized_content=None):
    """构造模拟的 DiagnosisStage 对象"""
    stage = MagicMock()
    stage.id = stage_id
    stage.tool_result = tool_result
    stage.tool_name = tool_name
    stage.summarized_content = summarized_content
    stage.stage_type = "TOOL_CALL"
    return stage


def _make_repo():
    """构造模拟的 DiagnosisRepository"""
    repo = MagicMock()
    repo.update_stage_summary = AsyncMock()
    return repo


class TestMaybeSummarize:
    """即时摘要触发逻辑测试"""

    @pytest.mark.asyncio
    @patch("control_platform.decision.context_management.tool_result_summarizer.settings")
    async def test_skip_when_disabled(self, mock_settings):
        """enable_tool_result_summary=False 时跳过"""
        mock_settings.enable_tool_result_summary = False
        summarizer = ToolResultSummarizer()
        stage = _make_stage("some result")
        repo = _make_repo()
        result = await summarizer.maybe_summarize(stage, repo)
        assert result is False

    @pytest.mark.asyncio
    @patch("control_platform.decision.context_management.tool_result_summarizer.settings")
    async def test_skip_when_under_threshold(self, mock_settings):
        """工具结果未超阈值时跳过"""
        mock_settings.enable_tool_result_summary = True
        mock_settings.tool_result_summary_threshold = 2000

        counter = TokenCounter()
        summarizer = ToolResultSummarizer(token_counter=counter)
        stage = _make_stage("short result")  # 远低于 2000 tokens
        repo = _make_repo()
        result = await summarizer.maybe_summarize(stage, repo)
        assert result is False
        repo.update_stage_summary.assert_not_called()

    @pytest.mark.asyncio
    @patch("control_platform.decision.context_management.tool_result_summarizer.settings")
    async def test_llm_summary_success(self, mock_settings):
        """LLM 摘要成功，验证字段回写"""
        mock_settings.enable_tool_result_summary = True
        mock_settings.tool_result_summary_threshold = 50  # 低阈值便于测试

        big_result = "java.lang.Thread.State: WAITING\n" * 200
        counter = TokenCounter()

        mock_llm = MagicMock()
        mock_llm.summarize_single = AsyncMock(return_value="摘要：线程阻塞")

        summarizer = ToolResultSummarizer(token_counter=counter, llm_summarizer=mock_llm)
        stage = _make_stage(big_result)
        repo = _make_repo()

        result = await summarizer.maybe_summarize(stage, repo, user_query="排查死锁")
        assert result is True

        # 验证 LLM 被调用
        mock_llm.summarize_single.assert_called_once()

        # 验证回写到数据库
        repo.update_stage_summary.assert_called_once()
        call_args = repo.update_stage_summary.call_args
        assert call_args.kwargs["summary_type"] == "llm"
        assert call_args.kwargs["summarized_content"] == "摘要：线程阻塞"

        original = call_args.kwargs["original_tokens"]
        summary = call_args.kwargs["summary_tokens"]
        print(f"[对比报告] LLM摘要 - 原始: {original} tokens, 摘要后: {summary} tokens")

    @pytest.mark.asyncio
    @patch("control_platform.decision.context_management.tool_result_summarizer.settings")
    async def test_llm_failure_fallback_to_rule(self, mock_settings):
        """LLM 失败时回退到规则式裁剪"""
        mock_settings.enable_tool_result_summary = True
        mock_settings.tool_result_summary_threshold = 50

        big_result = "java.lang.Thread.State: WAITING\n" * 200
        counter = TokenCounter()

        mock_llm = MagicMock()
        mock_llm.summarize_single = AsyncMock(side_effect=Exception("API Timeout"))

        summarizer = ToolResultSummarizer(token_counter=counter, llm_summarizer=mock_llm)
        stage = _make_stage(big_result)
        repo = _make_repo()

        result = await summarizer.maybe_summarize(stage, repo)
        assert result is True

        # 验证回退到规则式裁剪
        call_args = repo.update_stage_summary.call_args
        assert call_args.kwargs["summary_type"] == "rule"
        assert "已裁剪" in call_args.kwargs["summarized_content"]

        original = call_args.kwargs["original_tokens"]
        summary = call_args.kwargs["summary_tokens"]
        print(f"[对比报告] 规则裁剪 - 原始: {original} tokens, 裁剪后: {summary} tokens")

    @pytest.mark.asyncio
    @patch("control_platform.decision.context_management.tool_result_summarizer.settings")
    async def test_db_write_failure_does_not_block(self, mock_settings):
        """数据库回写失败不阻断流程"""
        mock_settings.enable_tool_result_summary = True
        mock_settings.tool_result_summary_threshold = 50

        big_result = "java.lang.Thread.State: WAITING\n" * 200
        counter = TokenCounter()

        mock_llm = MagicMock()
        mock_llm.summarize_single = AsyncMock(return_value="摘要")

        summarizer = ToolResultSummarizer(token_counter=counter, llm_summarizer=mock_llm)
        stage = _make_stage(big_result)
        repo = _make_repo()
        repo.update_stage_summary = AsyncMock(side_effect=Exception("DB Error"))

        # 不应抛出异常
        result = await summarizer.maybe_summarize(stage, repo)
        assert result is True

    @pytest.mark.asyncio
    @patch("control_platform.decision.context_management.tool_result_summarizer.settings")
    async def test_skip_when_no_tool_result(self, mock_settings):
        """tool_result 为空时跳过"""
        mock_settings.enable_tool_result_summary = True
        summarizer = ToolResultSummarizer()
        stage = _make_stage(tool_result="")
        repo = _make_repo()
        result = await summarizer.maybe_summarize(stage, repo)
        assert result is False


class TestRuleBasedTrim:
    """规则式裁剪测试"""

    def test_trim_preserves_head_and_tail(self):
        summarizer = ToolResultSummarizer()
        content = "A" * 10000
        trimmed = summarizer._rule_based_trim(content, original_tokens=3000)
        assert "已裁剪" in trimmed
        assert len(trimmed) < len(content)

    def test_short_content_not_trimmed(self):
        summarizer = ToolResultSummarizer()
        content = "short"
        trimmed = summarizer._rule_based_trim(content, original_tokens=10)
        assert trimmed == content
