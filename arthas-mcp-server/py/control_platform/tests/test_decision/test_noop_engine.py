"""
Mock 决策引擎（MockDecisionEngine）测试

测试预设的关键词匹配逻辑、工具调用返回和结论生成。
"""

import pytest

from control_platform.decision.context import DecisionContext
from control_platform.decision.noop_engine import MockDecisionEngine
from control_platform.models.action import ActionType


class TestMockDecisionEngine:
    """Mock 决策引擎测试"""

    @pytest.fixture
    def engine(self):
        return MockDecisionEngine()

    def _make_context(self, user_query: str, messages=None) -> DecisionContext:
        """构造测试用的 DecisionContext"""
        return DecisionContext(
            task_id="test-task",
            session_id="test-session",
            user_query=user_query,
            messages=messages or [],
        )

    @pytest.mark.asyncio
    async def test_keyword_jvm_returns_tool_call(self, engine: MockDecisionEngine):
        """测试目的：用户提问包含 'jvm' 关键词时，应返回 TOOL_CALL + tool_name=jvm"""
        ctx = self._make_context("帮我查看 jvm 状态")
        result = await engine.decide(ctx)
        assert result.action_type == ActionType.TOOL_CALL
        assert result.tool_name == "jvm"

    @pytest.mark.asyncio
    async def test_keyword_gc_returns_tool_call(self, engine: MockDecisionEngine):
        """测试目的：用户提问包含 'gc' 关键词时，应返回 TOOL_CALL + tool_name=gc"""
        ctx = self._make_context("gc 日志分析")
        result = await engine.decide(ctx)
        assert result.action_type == ActionType.TOOL_CALL
        assert result.tool_name == "gc"

    @pytest.mark.asyncio
    async def test_keyword_thread_returns_tool_call(self, engine: MockDecisionEngine):
        """测试目的：用户提问包含 'thread' 关键词时，应返回 TOOL_CALL + tool_name=thread"""
        ctx = self._make_context("thread dump 分析")
        result = await engine.decide(ctx)
        assert result.action_type == ActionType.TOOL_CALL
        assert result.tool_name == "thread"

    @pytest.mark.asyncio
    async def test_keyword_cpu_returns_thread_with_args(self, engine: MockDecisionEngine):
        """测试目的：用户提问包含 'cpu' 关键词时，应返回 thread 工具并携带参数 n=5, i=1000"""
        ctx = self._make_context("cpu 使用率很高")
        result = await engine.decide(ctx)
        assert result.action_type == ActionType.TOOL_CALL
        assert result.tool_name == "thread"
        assert result.tool_arguments == {"n": 5, "i": 1000}

    @pytest.mark.asyncio
    async def test_no_keyword_returns_conclude(self, engine: MockDecisionEngine):
        """测试目的：用户提问不匹配任何关键词时，应直接返回 CONCLUDE"""
        ctx = self._make_context("今天天气怎么样？")
        result = await engine.decide(ctx)
        assert result.action_type == ActionType.CONCLUDE
        assert result.conclusion is not None

    @pytest.mark.asyncio
    async def test_has_tool_result_returns_conclude(self, engine: MockDecisionEngine):
        """测试目的：历史消息中已有 function_result 时，无论关键词如何都应返回 CONCLUDE"""
        messages = [
            {"role": "user", "content": "查看 jvm"},
            {"role": "function_result", "content": "JVM info", "tool_name": "jvm"},
        ]
        ctx = self._make_context("查看 jvm", messages=messages)
        result = await engine.decide(ctx)
        assert result.action_type == ActionType.CONCLUDE
        assert "jvm" in result.conclusion.lower() or "jvm" in result.thinking.lower()

    @pytest.mark.asyncio
    async def test_conclude_result_has_thinking(self, engine: MockDecisionEngine):
        """测试目的：所有返回结果都应包含 thinking 字段（推理过程文本）"""
        ctx = self._make_context("随便说点什么")
        result = await engine.decide(ctx)
        assert result.thinking is not None
        assert len(result.thinking) > 0

    @pytest.mark.asyncio
    async def test_chinese_keyword_thread(self, engine: MockDecisionEngine):
        """测试目的：中文关键词 '线程' 应匹配 thread 工具"""
        ctx = self._make_context("帮我看看线程状态")
        result = await engine.decide(ctx)
        assert result.action_type == ActionType.TOOL_CALL
        assert result.tool_name == "thread"

    @pytest.mark.asyncio
    async def test_chinese_keyword_memory(self, engine: MockDecisionEngine):
        """测试目的：中文关键词 '内存' 应匹配 memory 工具"""
        ctx = self._make_context("内存泄漏排查")
        result = await engine.decide(ctx)
        assert result.action_type == ActionType.TOOL_CALL
        assert result.tool_name == "memory"
