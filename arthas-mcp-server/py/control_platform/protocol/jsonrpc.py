"""
JSON-RPC 2.0 协议工具函数

提供 JSON-RPC 2.0 消息的构建与解析能力，与 Arthas MCP Client 的 JSON-RPC 交互对齐。

规范参考: https://www.jsonrpc.org/specification
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Dict, List, Optional, Union


class RequestIdGenerator:
    """
    请求 ID 生成器（每个 session 一个实例）

    与 Java 端 McpStreamableServerSession.generateRequestId() 对齐，
    生成格式为 "sessionId-N" 的请求 ID，每个 session 独立计数。
    """

    def __init__(self, session_id: str):
        self._session_id = session_id
        self._counter = itertools.count(0)

    def next_id(self) -> str:
        """生成下一个请求 ID，格式: sessionId-N"""
        return f"{self._session_id}-{next(self._counter)}"


class JsonRpcErrorCode(IntEnum):
    """JSON-RPC 2.0 标准错误码"""
    PARSE_ERROR = -32700       # 解析错误
    INVALID_REQUEST = -32600   # 无效请求
    METHOD_NOT_FOUND = -32601  # 方法未找到
    INVALID_PARAMS = -32602    # 无效参数
    INTERNAL_ERROR = -32603    # 内部错误


@dataclass
class JsonRpcMessage:
    """
    解析后的 JSON-RPC 消息

    Attributes:
        raw: 原始消息字典
        is_request: 是否是请求（有 method 和 id）
        is_notification: 是否是通知（有 method 但无 id）
        is_response: 是否是响应（有 result 或 error）
        method: 方法名（仅请求/通知）
        params: 参数（仅请求/通知）
        request_id: 请求 ID（仅请求/响应）
        result: 结果（仅成功响应）
        error: 错误信息（仅错误响应）
    """
    raw: Dict[str, Any]
    is_request: bool = False
    is_notification: bool = False
    is_response: bool = False
    method: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    request_id: Optional[Union[int, str]] = None
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None





def build_request(
    method: str,
    params: Optional[Dict[str, Any]] = None,
    request_id: Optional[Union[int, str]] = None,
) -> Dict[str, Any]:
    """
    构建 JSON-RPC 2.0 请求消息

    Args:
        method: 方法名
        params: 参数字典
        request_id: 请求 ID，为 None 时自动生成

    Returns:
        JSON-RPC 请求消息字典
    """
    if request_id is None:
        raise ValueError("request_id 不能为 None，请通过 session 的 ID 生成器生成")

    message: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params is not None:
        message["params"] = params
    return message


def build_response(
    request_id: Union[int, str],
    result: Any,
) -> Dict[str, Any]:
    """
    构建 JSON-RPC 2.0 成功响应消息

    Args:
        request_id: 对应请求的 ID
        result: 响应结果

    Returns:
        JSON-RPC 响应消息字典
    """
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def build_error(
    request_id: Optional[Union[int, str]],
    code: int,
    message: str,
    data: Any = None,
) -> Dict[str, Any]:
    """
    构建 JSON-RPC 2.0 错误响应消息

    Args:
        request_id: 对应请求的 ID（可为 None）
        code: 错误码
        message: 错误信息
        data: 附加错误数据

    Returns:
        JSON-RPC 错误响应消息字典
    """
    error_obj: Dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if data is not None:
        error_obj["data"] = data

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": error_obj,
    }


def build_notification(
    method: str,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    构建 JSON-RPC 2.0 通知消息（无 id，不期望响应）

    Args:
        method: 方法名
        params: 参数字典

    Returns:
        JSON-RPC 通知消息字典
    """
    message: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": method,
    }
    if params is not None:
        message["params"] = params
    return message


def parse_message(raw: Union[str, Dict[str, Any]]) -> JsonRpcMessage:
    """
    解析 JSON-RPC 2.0 消息

    Args:
        raw: 原始消息（JSON 字符串或已解析的字典）

    Returns:
        解析后的 JsonRpcMessage 对象

    Raises:
        ValueError: 消息格式无效
    """
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"无效的 JSON 格式: {e}")
    elif isinstance(raw, dict):
        data = raw
    else:
        raise ValueError(f"不支持的消息类型: {type(raw)}")

    if not isinstance(data, dict):
        raise ValueError(f"JSON-RPC 消息必须是对象，收到: {type(data)}")

    msg = JsonRpcMessage(raw=data)

    # 判断消息类型
    has_method = "method" in data
    has_id = "id" in data
    has_result = "result" in data
    has_error = "error" in data

    if has_method and has_id:
        # 请求：有 method 和 id
        msg.is_request = True
        msg.method = data["method"]
        msg.params = data.get("params")
        msg.request_id = data["id"]
    elif has_method and not has_id:
        # 通知：有 method 但无 id
        msg.is_notification = True
        msg.method = data["method"]
        msg.params = data.get("params")
    elif has_result or has_error:
        # 响应：有 result 或 error
        msg.is_response = True
        msg.request_id = data.get("id")
        msg.result = data.get("result")
        msg.error = data.get("error")
    else:
        raise ValueError(f"无法识别的 JSON-RPC 消息格式: {data}")

    return msg


def serialize(message: Dict[str, Any]) -> str:
    """
    将消息字典序列化为 JSON 字符串

    Args:
        message: 消息字典

    Returns:
        JSON 字符串
    """
    return json.dumps(message, ensure_ascii=False)
