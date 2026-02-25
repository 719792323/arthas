"""
Pydantic Schema 测试

测试 API 序列化 schema 的验证规则、默认值和 ORM 转换。
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from control_platform.models.action import ActionType, DecisionResult
from control_platform.models.task import CreateDiagnosisRequest, TaskStatusResponse
from control_platform.models.event import (
    DiagnosisStageSchema,
    DiagnosisTaskSchema,
    DiagnosisTaskSummarySchema,
    DiagnosisProgressSchema,
)


# ======================== Action Schema ========================

class TestActionType:
    """ActionType 枚举测试"""

    def test_tool_call_value(self):
        """测试目的：验证 ActionType.TOOL_CALL 的字符串值"""
        assert ActionType.TOOL_CALL.value == "tool_call"

    def test_conclude_value(self):
        """测试目的：验证 ActionType.CONCLUDE 的字符串值"""
        assert ActionType.CONCLUDE.value == "conclude"


class TestDecisionResult:
    """DecisionResult Pydantic 模型测试"""

    def test_tool_call_result(self):
        """测试目的：验证 TOOL_CALL 类型的 DecisionResult 能正确序列化"""
        result = DecisionResult(
            action_type=ActionType.TOOL_CALL,
            tool_name="jvm",
            tool_arguments={"verbose": True},
            thinking="需要查看 JVM 状态",
        )
        assert result.action_type == ActionType.TOOL_CALL
        assert result.tool_name == "jvm"
        assert result.tool_arguments == {"verbose": True}
        assert result.conclusion is None

    def test_conclude_result(self):
        """测试目的：验证 CONCLUDE 类型的 DecisionResult 能正确序列化"""
        result = DecisionResult(
            action_type=ActionType.CONCLUDE,
            conclusion="诊断完成",
            thinking="分析结果",
        )
        assert result.action_type == ActionType.CONCLUDE
        assert result.conclusion == "诊断完成"
        assert result.tool_name is None

    def test_action_type_required(self):
        """测试目的：action_type 是必填字段，缺失时应抛出 ValidationError"""
        with pytest.raises(ValidationError):
            DecisionResult()

    def test_json_serialization(self):
        """测试目的：DecisionResult 应能正确序列化为 JSON"""
        result = DecisionResult(
            action_type=ActionType.TOOL_CALL,
            tool_name="gc",
            thinking="检查 GC",
        )
        data = result.model_dump()
        assert data["action_type"] == "tool_call"
        assert data["tool_name"] == "gc"


# ======================== Task Schema ========================

class TestCreateDiagnosisRequest:
    """创建诊断任务请求 Schema 测试"""

    def test_valid_request(self):
        """测试目的：正常参数创建请求应成功"""
        req = CreateDiagnosisRequest(
            session_id="session-001",
            user_query="帮我排查内存泄漏",
        )
        assert req.session_id == "session-001"
        assert req.user_query == "帮我排查内存泄漏"
        assert req.metadata is None

    def test_user_query_min_length(self):
        """测试目的：user_query 不能为空字符串（min_length=1 校验）"""
        with pytest.raises(ValidationError):
            CreateDiagnosisRequest(
                session_id="session-001",
                user_query="",  # 空字符串应失败
            )

    def test_with_metadata(self):
        """测试目的：metadata 可选字段能正确传入"""
        req = CreateDiagnosisRequest(
            session_id="s1",
            user_query="测试",
            metadata={"source": "web"},
        )
        assert req.metadata == {"source": "web"}

    def test_missing_session_id(self):
        """测试目的：session_id 是必填字段，缺失时应抛出 ValidationError"""
        with pytest.raises(ValidationError):
            CreateDiagnosisRequest(user_query="测试")

    def test_missing_user_query(self):
        """测试目的：user_query 是必填字段，缺失时应抛出 ValidationError"""
        with pytest.raises(ValidationError):
            CreateDiagnosisRequest(session_id="s1")


class TestTaskStatusResponse:
    """任务状态响应 Schema 测试"""

    def test_valid_response(self):
        """测试目的：TaskStatusResponse 能正确构造"""
        resp = TaskStatusResponse(
            task_id="task-001",
            status="running",
            message="任务创建成功",
        )
        assert resp.task_id == "task-001"
        assert resp.status == "running"

    def test_default_message(self):
        """测试目的：message 有默认空字符串"""
        resp = TaskStatusResponse(task_id="t1", status="running")
        assert resp.message == ""


# ======================== Event Schema ========================

class TestDiagnosisTaskSchema:
    """诊断任务 API Schema 测试"""

    def test_from_dict(self):
        """测试目的：DiagnosisTaskSchema 能从字典正确构造"""
        now = datetime.now()
        schema = DiagnosisTaskSchema(
            task_id="task-001",
            session_id="session-001",
            user_query="测试",
            status="running",
            current_stage_seq=1,
            created_at=now,
            updated_at=now,
            stages=[],
        )
        assert schema.task_id == "task-001"
        assert schema.stages == []

    def test_metadata_alias(self):
        """测试目的：metadata 字段使用 alias='metadata_' 能正确解析（兼容 ORM 属性名）"""
        now = datetime.now()
        schema = DiagnosisTaskSchema(
            task_id="t1",
            session_id="s1",
            user_query="q",
            status="running",
            current_stage_seq=1,
            created_at=now,
            updated_at=now,
            metadata_={"key": "value"},
        )
        assert schema.metadata == {"key": "value"}


class TestDiagnosisProgressSchema:
    """诊断任务进度 Schema 测试"""

    def test_default_optional_fields(self):
        """测试目的：DiagnosisProgressSchema 的可选字段应默认为 None"""
        schema = DiagnosisProgressSchema(
            task_id="t1",
            status="running",
            total_stages=3,
            completed_stages=1,
            current_stage_seq=2,
        )
        assert schema.current_stage_type is None
        assert schema.current_stage_status is None
