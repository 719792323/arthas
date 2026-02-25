"""
MCP 协议处理器

处理 MCP 协议的标准交互：
- 客户端 → 服务端：initialize、ping、notifications/initialized
- 服务端 → 客户端：tools/list、tools/call（携带 taskId/stageId）

与 Arthas MCP Client（Java 端）的 McpClientProtocolHandler 对齐。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional, Tuple

from control_platform.protocol.jsonrpc import (
    JsonRpcMessage,
    build_response,
    build_error,
    build_request,
    JsonRpcErrorCode,
)

logger = logging.getLogger(__name__)

# MCP 协议版本
MCP_PROTOCOL_VERSION = "2025-03-26"

# 服务端信息
SERVER_INFO = {
    "name": "Arthas Control Platform",
    "version": "0.1.0",
}

# 服务端能力
SERVER_CAPABILITIES = {
    "tools": {"listChanged": True},
}


class McpHandler:
    """
    MCP 协议处理器

    处理来自客户端的 JSON-RPC 请求/通知，并提供构建服务端请求的方法。

    Attributes:
        _on_initialized: 客户端初始化完成的回调
    """

    def __init__(
        self,
        on_initialized: Optional[Callable[[str], None]] = None,
    ):
        """
        Args:
            on_initialized: 当客户端发送 notifications/initialized 时的回调，
                           参数为 session_id
        """
        self._on_initialized = on_initialized

    def handle_request(
        self,
        msg: JsonRpcMessage,
        session_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        处理客户端发来的请求（有 id，需要响应）

        Args:
            msg: 解析后的 JSON-RPC 消息
            session_id: 发送方的会话 ID

        Returns:
            响应消息字典，或 None（如果不需要响应）
        """
        method = msg.method
        params = msg.params or {}
        request_id = msg.request_id

        if method == "initialize":
            return self._handle_initialize(request_id, params, session_id)
        elif method == "ping":
            return self._handle_ping(request_id)
        else:
            logger.warning(f"未知请求方法: {method} (session={session_id})")
            return build_error(
                request_id,
                JsonRpcErrorCode.METHOD_NOT_FOUND,
                f"Method not found: {method}",
            )

    def handle_notification(
        self,
        msg: JsonRpcMessage,
        session_id: str,
    ) -> None:
        """
        处理客户端发来的通知（无 id，不需要响应）

        Args:
            msg: 解析后的 JSON-RPC 消息
            session_id: 发送方的会话 ID
        """
        method = msg.method

        if method == "notifications/initialized":
            logger.info(f"客户端初始化完成 (session={session_id})")
            if self._on_initialized:
                self._on_initialized(session_id)
        elif method == "notifications/cancelled":
            request_id = (msg.params or {}).get("requestId")
            logger.info(f"客户端取消请求: {request_id} (session={session_id})")
        else:
            logger.debug(f"收到通知: {method} (session={session_id})")

    def handle_response(
        self,
        msg: JsonRpcMessage,
        session_id: str,
    ) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
        """
        处理客户端发来的响应（对服务端请求的回复）

        Args:
            msg: 解析后的 JSON-RPC 消息
            session_id: 发送方的会话 ID

        Returns:
            (result, error) 元组：成功时 result 非空，失败时 error 非空
        """
        if msg.error:
            logger.warning(
                f"收到错误响应: id={msg.request_id}, "
                f"code={msg.error.get('code')}, "
                f"message={msg.error.get('message')} "
                f"(session={session_id})"
            )
            return None, msg.error
        else:
            return msg.result, None

    # ========== 服务端主动发送的请求构建 ==========

    @staticmethod
    def build_tools_list_request(request_id=None) -> Dict[str, Any]:
        """
        构建 tools/list 请求

        Args:
            request_id: 请求 ID，为 None 时自动生成

        Returns:
            JSON-RPC 请求消息字典
        """
        return build_request("tools/list", request_id=request_id)

    @staticmethod
    def build_tools_call_request(
        tool_name: str,
        arguments: Dict[str, Any] = None,
        task_id: str = None,
        stage_id: str = None,
        request_id=None,
    ) -> Dict[str, Any]:
        """
        构建 tools/call 请求

        在 params._meta 中注入 taskId 和 stageId，与 Java 端的
        TaskStageTracker 机制对齐。

        Args:
            tool_name: 工具名称
            arguments: 工具调用参数
            task_id: 任务 ID
            stage_id: 阶段 ID
            request_id: 请求 ID，为 None 时自动生成

        Returns:
            JSON-RPC 请求消息字典
        """
        params: Dict[str, Any] = {
            "name": tool_name,
            "arguments": arguments or {},
        }

        # 在 _meta 中注入 taskId 和 stageId
        if task_id or stage_id:
            meta: Dict[str, Any] = {}
            if task_id:
                meta["taskId"] = task_id
            if stage_id:
                meta["stageId"] = stage_id
            params["_meta"] = meta

        return build_request("tools/call", params=params, request_id=request_id)

    @staticmethod
    def build_ping_request(request_id=None) -> Dict[str, Any]:
        """
        构建 ping 请求

        Args:
            request_id: 请求 ID，为 None 时自动生成

        Returns:
            JSON-RPC 请求消息字典
        """
        return build_request("ping", request_id=request_id)

    # ========== 内部处理方法 ==========

    def _handle_initialize(
        self,
        request_id,
        params: Dict[str, Any],
        session_id: str,
    ) -> Dict[str, Any]:
        """处理 initialize 请求"""
        client_info = params.get("clientInfo", {})
        logger.info(
            f"客户端初始化: {client_info.get('name', 'unknown')} "
            f"v{client_info.get('version', 'unknown')} "
            f"(session={session_id})"
        )

        return build_response(request_id, {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": SERVER_CAPABILITIES,
            "serverInfo": SERVER_INFO,
        })

    @staticmethod
    def _handle_ping(request_id) -> Dict[str, Any]:
        """处理 ping 请求"""
        return build_response(request_id, {})

    @staticmethod
    def extract_task_stage_from_response(response_result: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        """
        从 tools/call 响应中提取 taskId 和 stageId

        兼容两种 key 格式：
        - "_meta": MCP 协议规范定义的标准 key
        - "meta": Java 端 Jackson 序列化 getMeta() 生成的 key

        Args:
            response_result: 响应的 result 字段

        Returns:
            (task_id, stage_id) 元组
        """
        if not response_result or not isinstance(response_result, dict):
            return None, None

        # 兼容 Java 端 Jackson 序列化: getMeta() → "meta", @JsonProperty("_meta") → "_meta"
        meta = response_result.get("_meta") or response_result.get("meta") or {}
        return meta.get("taskId"), meta.get("stageId")
