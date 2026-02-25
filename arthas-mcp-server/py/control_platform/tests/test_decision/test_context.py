"""
DecisionContext 数据模型测试

测试 DecisionContext 的默认值、字段赋值和数据结构。
"""

import pytest

from control_platform.decision.context import DecisionContext


class TestDecisionContext:
    """DecisionContext 数据类测试"""

    def test_default_values(self):
        """测试目的：验证 DecisionContext 不传参数时所有字段使用正确的默认值"""
        ctx = DecisionContext()
        assert ctx.task_id == ""
        assert ctx.session_id == ""
        assert ctx.user_query == ""
        assert ctx.messages == []
        assert ctx.available_tools == []
        assert ctx.current_stage_seq == 0
        assert ctx.rag_context is None
        assert ctx.metadata == {}

    def test_field_assignment(self):
        """测试目的：验证 DecisionContext 所有字段可正确赋值和读取"""
        messages = [{"role": "user", "content": "测试消息"}]
        tools = [{"name": "jvm", "description": "JVM 诊断"}]
        ctx = DecisionContext(
            task_id="task-001",
            session_id="session-001",
            user_query="帮我查看 JVM 状态",
            messages=messages,
            available_tools=tools,
            current_stage_seq=3,
            rag_context={"key": "value"},
            metadata={"source": "test"},
        )
        assert ctx.task_id == "task-001"
        assert ctx.session_id == "session-001"
        assert ctx.user_query == "帮我查看 JVM 状态"
        assert ctx.messages == messages
        assert ctx.available_tools == tools
        assert ctx.current_stage_seq == 3
        assert ctx.rag_context == {"key": "value"}
        assert ctx.metadata == {"source": "test"}

    def test_messages_list_is_independent(self):
        """测试目的：验证不同 DecisionContext 实例的 messages 列表互不干扰"""
        ctx1 = DecisionContext()
        ctx2 = DecisionContext()
        ctx1.messages.append({"role": "user", "content": "msg1"})
        assert len(ctx2.messages) == 0  # 不应被影响
