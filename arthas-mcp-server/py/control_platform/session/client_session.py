"""
客户端会话封装

ClientSession 封装单个 WebSocket 连接，管理连接状态、心跳时间戳和请求等待队列。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

from control_platform.protocol.jsonrpc import RequestIdGenerator

logger = logging.getLogger(__name__)


class ClientSession:
    """
    WebSocket 客户端会话

    封装一个 WebSocket 连接，提供消息发送、请求-响应匹配和状态管理。

    Attributes:
        session_id: 会话唯一标识（由客户端提供的 sessionId）
        websocket: FastAPI WebSocket 连接对象
        initialized: 是否已完成 MCP 初始化握手
        active: 连接是否活跃
        last_heartbeat_at: 最后一次心跳响应时间
        client_info: 客户端信息（从 initialize 请求中获取）
        created_at: 会话创建时间
        pending_futures: 请求 ID → Future 映射，用于请求-响应匹配
    """

    def __init__(self, session_id: str, websocket):
        self.session_id = session_id
        self.websocket = websocket
        self.initialized = False
        self.active = True
        self.last_heartbeat_at: float = time.time()
        self.client_info: Dict[str, Any] = {}
        self.created_at: float = time.time()

        # 请求-响应匹配（每个 session 独立的 ID 生成器，与 Java 端 McpStreamableServerSession 对齐）
        self._id_generator = RequestIdGenerator(session_id)
        self.pending_futures: Dict[Any, asyncio.Future] = {}

        # 发送锁，防止并发写入 WebSocket
        self._send_lock = asyncio.Lock()

    def next_request_id(self) -> str:
        """生成下一个请求 ID，格式: sessionId-N，与 Java 端对齐"""
        return self._id_generator.next_id()

    async def send_message(self, message: Dict[str, Any]) -> bool:
        """
        通过 WebSocket 发送消息

        Args:
            message: 要发送的消息字典

        Returns:
            是否发送成功
        """
        if not self.active or not self.websocket:
            logger.warning(f"会话不活跃，跳过发送 (session={self.session_id[:8]})")
            return False

        try:
            async with self._send_lock:
                data = json.dumps(message, ensure_ascii=False)
                await self.websocket.send_text(data)
            logger.debug(
                f"📤 发送消息: {message.get('method', 'response')} "
                f"(session={self.session_id[:8]})"
            )
            return True
        except Exception as e:
            logger.error(f"发送消息失败: {e} (session={self.session_id[:8]})")
            self.active = False
            return False

    async def send_and_wait(
        self,
        message: Dict[str, Any],
        timeout: float = 30.0,
    ) -> Optional[Dict[str, Any]]:
        """
        发送请求并等待响应

        Args:
            message: 要发送的请求消息（必须包含 id）
            timeout: 超时时间（秒）

        Returns:
            响应消息字典，超时返回 None
        """
        request_id = message.get("id")
        if request_id is None:
            logger.error("send_and_wait 需要消息包含 id 字段")
            return None

        # 创建 Future
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self.pending_futures[request_id] = future

        # 发送请求
        success = await self.send_message(message)
        if not success:
            self.pending_futures.pop(request_id, None)
            return None

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self.pending_futures.pop(request_id, None)
            logger.warning(
                f"请求超时: id={request_id}, timeout={timeout}s "
                f"(session={self.session_id[:8]})"
            )
            return None

    def resolve_response(self, request_id: Any, response: Dict[str, Any]) -> bool:
        """
        解析收到的响应，匹配到对应的 pending future

        Args:
            request_id: 响应的请求 ID
            response: 响应消息字典

        Returns:
            是否匹配成功
        """
        future = self.pending_futures.pop(request_id, None)
        if future and not future.done():
            future.set_result(response)
            return True
        return False

    def update_heartbeat(self) -> None:
        """更新心跳时间戳"""
        self.last_heartbeat_at = time.time()

    def close(self) -> None:
        """
        关闭会话

        取消所有 pending future 并标记为不活跃。
        """
        self.active = False

        # 取消所有 pending future
        for future in self.pending_futures.values():
            if not future.done():
                future.set_exception(Exception("Session closed"))
        self.pending_futures.clear()

        logger.info(f"会话已关闭: {self.session_id[:8]}")

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典，用于 REST API 返回"""
        return {
            "session_id": self.session_id,
            "initialized": self.initialized,
            "active": self.active,
            "client_info": self.client_info,
            "created_at": self.created_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "pending_requests": len(self.pending_futures),
        }
