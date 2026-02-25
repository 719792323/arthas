"""
JSON-RPC 2.0 协议工具函数测试

测试消息构建（request/response/error/notification）和消息解析。
"""

import pytest

from control_platform.protocol.jsonrpc import (
    JsonRpcErrorCode,
    JsonRpcMessage,
    RequestIdGenerator,
    build_error,
    build_notification,
    build_request,
    build_response,
    parse_message,
    serialize,
)


class TestBuildRequest:
    """JSON-RPC 请求构建测试"""

    def test_basic_request(self):
        """测试目的：build_request 应生成包含 jsonrpc、id、method 的标准请求"""
        req = build_request("tools/list", request_id=1)
        assert req["jsonrpc"] == "2.0"
        assert req["id"] == 1
        assert req["method"] == "tools/list"
        assert "params" not in req

    def test_request_with_params(self):
        """测试目的：带 params 的请求应包含 params 字段"""
        req = build_request("tools/call", params={"name": "jvm"}, request_id=2)
        assert req["params"] == {"name": "jvm"}

    def test_auto_generate_request_id(self):
        """测试目的：不指定 request_id 时应抛出 ValueError"""
        with pytest.raises(ValueError, match="request_id 不能为 None"):
            build_request("ping")


class TestRequestIdGenerator:
    """请求 ID 生成器测试"""

    def test_generates_session_prefixed_ids(self):
        """测试目的：生成器应产生 sessionId-N 格式的 ID"""
        gen = RequestIdGenerator("session-abc")
        assert gen.next_id() == "session-abc-0"
        assert gen.next_id() == "session-abc-1"
        assert gen.next_id() == "session-abc-2"

    def test_independent_generators(self):
        """测试目的：不同 session 的生成器应独立计数"""
        gen1 = RequestIdGenerator("s1")
        gen2 = RequestIdGenerator("s2")
        assert gen1.next_id() == "s1-0"
        assert gen2.next_id() == "s2-0"
        assert gen1.next_id() == "s1-1"
        assert gen2.next_id() == "s2-1"


class TestBuildResponse:
    """JSON-RPC 响应构建测试"""

    def test_success_response(self):
        """测试目的：build_response 应生成标准成功响应"""
        resp = build_response(1, {"tools": []})
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        assert resp["result"] == {"tools": []}

    def test_empty_result(self):
        """测试目的：result 为空对象时也应正确构建"""
        resp = build_response(2, {})
        assert resp["result"] == {}


class TestBuildError:
    """JSON-RPC 错误响应构建测试"""

    def test_error_response(self):
        """测试目的：build_error 应生成包含 error 对象的标准错误响应"""
        resp = build_error(1, JsonRpcErrorCode.METHOD_NOT_FOUND, "Method not found")
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        assert resp["error"]["code"] == -32601
        assert resp["error"]["message"] == "Method not found"

    def test_error_with_data(self):
        """测试目的：error 可以携带额外的 data 字段"""
        resp = build_error(1, -32000, "Custom error", data={"detail": "info"})
        assert resp["error"]["data"] == {"detail": "info"}

    def test_error_without_data(self):
        """测试目的：不传 data 时，error 对象中不应包含 data 字段"""
        resp = build_error(1, -32000, "Error")
        assert "data" not in resp["error"]


class TestBuildNotification:
    """JSON-RPC 通知构建测试"""

    def test_basic_notification(self):
        """测试目的：build_notification 应生成无 id 字段的通知消息"""
        notif = build_notification("notifications/initialized")
        assert notif["jsonrpc"] == "2.0"
        assert notif["method"] == "notifications/initialized"
        assert "id" not in notif

    def test_notification_with_params(self):
        """测试目的：带 params 的通知应包含 params 字段"""
        notif = build_notification("log", params={"level": "info"})
        assert notif["params"] == {"level": "info"}


class TestParseMessage:
    """JSON-RPC 消息解析测试"""

    def test_parse_request(self):
        """测试目的：有 method 和 id 的消息应解析为 request"""
        raw = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {}}}
        msg = parse_message(raw)
        assert msg.is_request is True
        assert msg.is_notification is False
        assert msg.is_response is False
        assert msg.method == "initialize"
        assert msg.request_id == 1
        assert msg.params == {"clientInfo": {}}

    def test_parse_notification(self):
        """测试目的：有 method 但无 id 的消息应解析为 notification"""
        raw = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        msg = parse_message(raw)
        assert msg.is_notification is True
        assert msg.is_request is False
        assert msg.method == "notifications/initialized"

    def test_parse_success_response(self):
        """测试目的：有 result 的消息应解析为 success response"""
        raw = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
        msg = parse_message(raw)
        assert msg.is_response is True
        assert msg.result == {"tools": []}
        assert msg.error is None

    def test_parse_error_response(self):
        """测试目的：有 error 的消息应解析为 error response"""
        raw = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Not found"}}
        msg = parse_message(raw)
        assert msg.is_response is True
        assert msg.error["code"] == -32601
        assert msg.result is None

    def test_parse_json_string(self):
        """测试目的：parse_message 应能直接解析 JSON 字符串"""
        json_str = '{"jsonrpc": "2.0", "id": 1, "method": "ping"}'
        msg = parse_message(json_str)
        assert msg.is_request is True
        assert msg.method == "ping"

    def test_parse_invalid_json_raises(self):
        """测试目的：无效 JSON 字符串应抛出 ValueError"""
        with pytest.raises(ValueError, match="无效的 JSON 格式"):
            parse_message("{invalid json}")

    def test_parse_unrecognized_format_raises(self):
        """测试目的：无法识别的消息格式应抛出 ValueError"""
        with pytest.raises(ValueError):
            parse_message({"jsonrpc": "2.0"})  # 既无 method 也无 result/error

    def test_parse_non_dict_raises(self):
        """测试目的：非 dict 类型的输入应抛出 ValueError"""
        with pytest.raises(ValueError):
            parse_message(42)


class TestSerialize:
    """消息序列化测试"""

    def test_serialize_to_json(self):
        """测试目的：serialize 应将消息字典转为 JSON 字符串"""
        msg = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        result = serialize(msg)
        assert '"jsonrpc": "2.0"' in result
        assert '"method": "ping"' in result

    def test_serialize_chinese_no_escape(self):
        """测试目的：序列化中文时不应转义为 Unicode（ensure_ascii=False）"""
        msg = {"content": "中文内容"}
        result = serialize(msg)
        assert "中文内容" in result
        assert "\\u" not in result
