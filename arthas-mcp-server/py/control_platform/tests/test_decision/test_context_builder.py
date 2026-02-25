"""
上下文构建器（ContextBuilder）测试

测试从数据库 stage 链构建 DecisionContext 的逻辑，
包括消息角色映射、只包含 completed stage、可用工具列表设置。
"""

import pytest
import pytest_asyncio

from control_platform.db.models import StageStatus, StageType
from control_platform.db.repository import DiagnosisRepository
from control_platform.decision.context_builder import ContextBuilder


class TestContextBuilder:
    """上下文构建器测试"""

    @pytest_asyncio.fixture
    async def builder(self):
        """提供默认的 ContextBuilder 实例"""
        b = ContextBuilder()
        b.set_available_tools("s1", [{"name": "jvm"}])
        return b

    @pytest.mark.asyncio
    async def test_build_context_from_user_query(self, repo: DiagnosisRepository, builder: ContextBuilder):
        """测试目的：只有 USER_QUERY(completed) 时，messages 应包含一条 role=user 的消息"""
        task = await repo.create_task(session_id="s1", user_query="测试问题")
        stages = await repo.get_task_stages(task.task_id)
        # 完成 USER_QUERY stage
        await repo.complete_stage(stages[0].id, output_data={"user_query": "测试问题"})

        ctx = await builder.build_context(task.task_id, repo)

        assert ctx.task_id == task.task_id
        assert ctx.user_query == "测试问题"
        assert len(ctx.messages) == 1
        assert ctx.messages[0]["role"] == "user"
        assert ctx.messages[0]["content"] == "测试问题"

    @pytest.mark.asyncio
    async def test_build_context_full_react_chain(self, repo: DiagnosisRepository, builder: ContextBuilder):
        """
        测试目的：验证完整 ReAct 链路下各 stage_type 到 message role 的映射正确性
        USER_QUERY→user, LLM_THINKING→assistant, TOOL_CALL→function_call, TOOL_RESULT→function_result
        """
        task = await repo.create_task(session_id="s1", user_query="查看 JVM")
        stages = await repo.get_task_stages(task.task_id)

        # USER_QUERY → LLM_THINKING
        llm = await repo.complete_and_next(
            stage_id=stages[0].id,
            output_data={"user_query": "查看 JVM"},
            next_stage_type=StageType.LLM_THINKING.value,
            next_input_data={"instruction": "分析"},
        )

        # LLM_THINKING → TOOL_CALL
        tc = await repo.complete_and_next(
            stage_id=llm.id,
            output_data={"thinking": "需要调用 jvm", "action_type": "tool_call", "tool_name": "jvm"},
            next_stage_type=StageType.TOOL_CALL.value,
            next_tool_name="jvm",
            next_tool_arguments={},
        )

        # TOOL_CALL → TOOL_RESULT（同时在 complete_and_next 中设置 tool_result 字段）
        tr = await repo.complete_and_next(
            stage_id=tc.id,
            output_data={"tool_result": "JVM info"},
            next_stage_type=StageType.TOOL_RESULT.value,
            next_input_data={"tool_name": "jvm", "tool_result": "JVM info"},
            tool_result="JVM info",
        )

        # 完成 TOOL_RESULT
        await repo.complete_stage(tr.id, output_data={"forwarded_to_llm": True})

        ctx = await builder.build_context(task.task_id, repo)

        # 验证消息链
        roles = [m["role"] for m in ctx.messages]
        assert roles == ["user", "assistant", "function_call", "function_result"]

    @pytest.mark.asyncio
    async def test_build_context_excludes_pending_stages(self, repo: DiagnosisRepository, builder: ContextBuilder):
        """测试目的：pending 状态的 stage 不应出现在 messages 中"""
        task = await repo.create_task(session_id="s1", user_query="测试")
        # USER_QUERY 处于 pending，不应出现在 messages 中

        ctx = await builder.build_context(task.task_id, repo)
        assert len(ctx.messages) == 0

    @pytest.mark.asyncio
    async def test_build_context_excludes_failed_stages(self, repo: DiagnosisRepository, builder: ContextBuilder):
        """测试目的：failed 状态的 stage 不应出现在 messages 中"""
        task = await repo.create_task(session_id="s1", user_query="测试")
        stages = await repo.get_task_stages(task.task_id)
        # 将 stage 标记为 failed
        await repo.mark_failed(stages[0].id, "错误")
        await repo.mark_failed(stages[0].id, "错误")
        await repo.mark_failed(stages[0].id, "错误")

        ctx = await builder.build_context(task.task_id, repo)
        assert len(ctx.messages) == 0

    @pytest.mark.asyncio
    async def test_set_available_tools(self, builder: ContextBuilder):
        """测试目的：set_available_tools 应更新内部可用工具列表"""
        new_tools = [{"name": "gc"}, {"name": "thread"}]
        builder.set_available_tools("s1", new_tools)
        assert builder.get_available_tools("s1") == new_tools

    @pytest.mark.asyncio
    async def test_build_context_includes_available_tools(self, repo: DiagnosisRepository, builder: ContextBuilder):
        """测试目的：构建的 context 应包含 builder 中设置的 available_tools"""
        task = await repo.create_task(session_id="s1", user_query="测试")
        ctx = await builder.build_context(task.task_id, repo)
        assert ctx.available_tools == [{"name": "jvm"}]

    @pytest.mark.asyncio
    async def test_build_context_for_nonexistent_task(self, repo: DiagnosisRepository, builder: ContextBuilder):
        """测试目的：不存在的 task_id 构建上下文应返回空 messages 和空 session_id"""
        ctx = await builder.build_context("non-existent", repo)
        assert ctx.messages == []
        assert ctx.session_id == ""
