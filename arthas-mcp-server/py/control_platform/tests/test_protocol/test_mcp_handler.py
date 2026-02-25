"""
MCP 协议处理器（McpHandler）测试

测试请求处理、通知处理、请求构建和响应解析。
"""

from unittest.mock import MagicMock

import pytest

from control_platform.protocol.mcp_handler import McpHandler, MCP_PROTOCOL_VERSION, SERVER_INFO
from control_platform.protocol.jsonrpc import (
    JsonRpcMessage,
    JsonRpcErrorCode,
    parse_message,
    build_request,
    build_response,
)


class TestMcpHandlerRequests:
    """MCP 请求处理测试"""

    @pytest.fixture
    def handler(self):
        return McpHandler()

    def test_handle_initialize(self, handler: McpHandler):
        """测试目的：initialize 请求应返回包含 protocolVersion、capabilities 和 serverInfo 的响应"""
        msg = JsonRpcMessage(
            raw={},
            is_request=True,
            method="initialize",
            params={"clientInfo": {"name": "Arthas", "version": "1.0"}},
            request_id=1,
        )

        response = handler.handle_request(msg, "session-001")

        assert response is not None
        result = response["result"]
        assert result["protocolVersion"] == MCP_PROTOCOL_VERSION
        assert "capabilities" in result
        assert result["serverInfo"] == SERVER_INFO

    def test_handle_ping(self, handler: McpHandler):
        """测试目的：ping 请求应返回空 result 的响应"""
        msg = JsonRpcMessage(
            raw={},
            is_request=True,
            method="ping",
            params={},
            request_id=2,
        )

        response = handler.handle_request(msg, "session-001")

        assert response is not None
        assert response["result"] == {}
        assert response["id"] == 2

    def test_handle_unknown_method(self, handler: McpHandler):
        """测试目的：未知方法应返回 METHOD_NOT_FOUND 错误响应"""
        msg = JsonRpcMessage(
            raw={},
            is_request=True,
            method="unknown/method",
            params={},
            request_id=3,
        )

        response = handler.handle_request(msg, "session-001")

        assert response is not None
        assert "error" in response
        assert response["error"]["code"] == JsonRpcErrorCode.METHOD_NOT_FOUND


class TestMcpHandlerNotifications:
    """MCP 通知处理测试"""

    def test_notifications_initialized_callback(self):
        """测试目的：收到 notifications/initialized 通知时，应触发 on_initialized 回调"""
        callback = MagicMock()
        handler = McpHandler(on_initialized=callback)

        msg = JsonRpcMessage(
            raw={},
            is_notification=True,
            method="notifications/initialized",
            params={},
        )

        handler.handle_notification(msg, "session-001")
        callback.assert_called_once_with("session-001")

    def test_notifications_initialized_no_callback(self):
        """测试目的：没有设置回调时，收到通知不应报错"""
        handler = McpHandler()
        msg = JsonRpcMessage(
            raw={},
            is_notification=True,
            method="notifications/initialized",
            params={},
        )
        handler.handle_notification(msg, "session-001")  # 不应报错


class TestMcpHandlerRequestBuilders:
    """服务端请求构建测试"""

    def test_build_tools_list_request(self):
        """测试目的：build_tools_list_request 应生成 method=tools/list 的 JSON-RPC 请求"""
        req = McpHandler.build_tools_list_request(request_id=10)
        assert req["method"] == "tools/list"
        assert req["id"] == 10
        assert req["jsonrpc"] == "2.0"

    def test_build_tools_call_request_with_meta(self):
        """测试目的：build_tools_call_request 应在 _meta 中注入 taskId 和 stageId"""
        req = McpHandler.build_tools_call_request(
            tool_name="jvm",
            arguments={"verbose": True},
            task_id="task-001",
            stage_id="3",
            request_id=20,
        )

        assert req["method"] == "tools/call"
        params = req["params"]
        assert params["name"] == "jvm"
        assert params["arguments"] == {"verbose": True}
        assert params["_meta"]["taskId"] == "task-001"
        assert params["_meta"]["stageId"] == "3"

    def test_build_tools_call_request_without_meta(self):
        """测试目的：不传 task_id 和 stage_id 时，_meta 不应存在"""
        req = McpHandler.build_tools_call_request(
            tool_name="gc",
            arguments={},
            request_id=21,
        )

        params = req["params"]
        assert "_meta" not in params

    def test_build_ping_request(self):
        """测试目的：build_ping_request 应生成 method=ping 的 JSON-RPC 请求"""
        req = McpHandler.build_ping_request(request_id=30)
        assert req["method"] == "ping"


class TestMcpHandlerResponseParsing:
    """响应解析测试"""

    def test_handle_success_response(self):
        """测试目的：处理成功响应应返回 (result, None)"""
        handler = McpHandler()
        msg = JsonRpcMessage(
            raw={},
            is_response=True,
            request_id=1,
            result={"tools": [{"name": "jvm"}]},
        )

        result, error = handler.handle_response(msg, "session-001")
        assert result == {"tools": [{"name": "jvm"}]}
        assert error is None

    def test_handle_error_response(self):
        """测试目的：处理错误响应应返回 (None, error)"""
        handler = McpHandler()
        msg = JsonRpcMessage(
            raw={},
            is_response=True,
            request_id=1,
            error={"code": -32000, "message": "Internal error"},
        )

        result, error = handler.handle_response(msg, "session-001")
        assert result is None
        assert error["code"] == -32000

    def test_extract_task_stage_from_response(self):
        """测试目的：从 tools/call 响应的 _meta 中正确提取 taskId 和 stageId"""
        result = {
            "_meta": {"taskId": "task-123", "stageId": "5"},
            "content": [{"type": "text", "text": "result"}],
        }
        task_id, stage_id = McpHandler.extract_task_stage_from_response(result)
        assert task_id == "task-123"
        assert stage_id == "5"

    def test_extract_task_stage_no_meta(self):
        """测试目的：响应中没有 _meta 时应返回 (None, None)"""
        result = {"content": []}
        task_id, stage_id = McpHandler.extract_task_stage_from_response(result)
        assert task_id is None
        assert stage_id is None

    def test_extract_task_stage_none_result(self):
        """测试目的：result 为 None 时应返回 (None, None)"""
        task_id, stage_id = McpHandler.extract_task_stage_from_response(None)
        assert task_id is None
        assert stage_id is None

    def test_extract_task_stage_from_meta_without_underscore(self):
        """测试目的：兼容 Java 端 Jackson 序列化 getMeta() 生成的 "meta" key（非 "_meta"）"""
        result = {
            "meta": {"taskId": "task-456", "stageId": "3"},
            "content": [{"type": "text", "text": "result"}],
        }
        task_id, stage_id = McpHandler.extract_task_stage_from_response(result)
        assert task_id == "task-456"
        assert stage_id == "3"
