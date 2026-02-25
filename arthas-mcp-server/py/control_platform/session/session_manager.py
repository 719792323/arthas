"""
会话管理器

SessionManager 负责管理所有 WebSocket 客户端会话，包括注册、注销、查询和心跳检测。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Dict, List, Optional

from control_platform.session.client_session import ClientSession
from control_platform.protocol.mcp_handler import McpHandler
from control_platform.config import settings

logger = logging.getLogger(__name__)


class SessionManager:
    """
    WebSocket 会话管理器

    管理所有客户端会话的生命周期，提供注册、注销、查询和心跳检测能力。
    通过 sessionId 唯一标识每个会话，仅返回活跃且已初始化的会话。

    Attributes:
        _sessions: sessionId → ClientSession 映射
        _lock: 异步锁，保护 sessions 字典的并发访问
        _mcp_handler: MCP 协议处理器（用于发送心跳 ping）
        _heartbeat_task: 心跳检测后台任务
    """

    def __init__(self, mcp_handler: McpHandler):
        self._sessions: Dict[str, ClientSession] = {}
        self._lock = asyncio.Lock()
        self._mcp_handler = mcp_handler
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._on_unregister_callbacks: List[Callable[[str], None]] = []

    def add_on_unregister_callback(self, callback: Callable[[str], None]) -> None:
        """
        注册 session 注销时的回调函数

        回调函数接收 session_id 作为参数，在 session 被移除后同步调用。
        用于清理与 session 关联的外部资源（如工具列表缓存）。

        Args:
            callback: 回调函数，签名为 (session_id: str) -> None
        """
        self._on_unregister_callbacks.append(callback)

    async def register(self, session_id: str, websocket) -> ClientSession:
        """
        注册新会话

        如果 session_id 已存在，则关闭旧会话并替换。

        Args:
            session_id: 会话 ID
            websocket: FastAPI WebSocket 连接对象

        Returns:
            新创建的 ClientSession
        """
        async with self._lock:
            # 如果已存在同 session_id 的会话，先关闭旧会话
            old_session = self._sessions.get(session_id)
            if old_session:
                logger.info(f"替换已有会话: {session_id[:8]}")
                old_session.close()

            session = ClientSession(session_id, websocket)
            self._sessions[session_id] = session
            logger.info(
                f"✅ 注册新会话: {session_id[:8]} "
                f"(当前会话数: {len(self._sessions)})"
            )
            return session

    async def unregister(self, session_id: str) -> None:
        """
        注销会话

        从管理器中移除并关闭会话，并触发注销回调清理关联资源。

        Args:
            session_id: 要移除的会话 ID
        """
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session:
                session.close()
                logger.info(
                    f"📪 注销会话: {session_id[:8]} "
                    f"(剩余会话数: {len(self._sessions)})"
                )
                # 触发注销回调，清理与 session 关联的外部资源
                for callback in self._on_unregister_callbacks:
                    try:
                        callback(session_id)
                    except Exception as e:
                        logger.error(f"注销回调执行失败: {e}", exc_info=True)

    async def get_session(self, session_id: str) -> Optional[ClientSession]:
        """
        获取会话

        仅返回活跃且已初始化的会话，否则返回 None。

        Args:
            session_id: 会话 ID

        Returns:
            ClientSession 或 None
        """
        session = self._sessions.get(session_id)
        if session and session.active and session.initialized:
            return session
        return None

    async def get_raw_session(self, session_id: str) -> Optional[ClientSession]:
        """
        获取原始会话（不检查初始化状态）

        用于连接建立初期尚未完成初始化的场景。

        Args:
            session_id: 会话 ID

        Returns:
            ClientSession 或 None
        """
        session = self._sessions.get(session_id)
        if session and session.active:
            return session
        return None

    async def get_all_active_sessions(self) -> List[ClientSession]:
        """
        获取所有活跃且已初始化的会话

        Returns:
            活跃会话列表
        """
        return [
            s for s in self._sessions.values()
            if s.active and s.initialized
        ]

    async def get_all_session_ids(self) -> List[str]:
        """
        获取所有活跃且已初始化的会话 ID

        Returns:
            会话 ID 列表
        """
        return [
            sid for sid, s in self._sessions.items()
            if s.active and s.initialized
        ]

    async def mark_initialized(self, session_id: str) -> None:
        """
        将会话标记为已初始化

        Args:
            session_id: 会话 ID
        """
        session = self._sessions.get(session_id)
        if session:
            session.initialized = True
            logger.info(f"✅ 会话已初始化: {session_id[:8]}")

    def start_heartbeat(self) -> None:
        """启动心跳检测后台任务"""
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            logger.info("💓 心跳检测已启动")

    def stop_heartbeat(self) -> None:
        """停止心跳检测后台任务"""
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            logger.info("💓 心跳检测已停止")

    async def _heartbeat_loop(self) -> None:
        """
        心跳检测协程

        定时遍历所有会话，发送 MCP ping 请求。
        超时未响应的会话将被移除。
        """
        interval = settings.mcp_heartbeat_interval
        timeout = settings.mcp_heartbeat_timeout

        logger.info(f"💓 心跳检测循环启动 (间隔={interval}s, 超时={timeout}s)")

        while True:
            try:
                await asyncio.sleep(interval)
                await self._check_heartbeats(timeout)
            except asyncio.CancelledError:
                logger.info("💓 心跳检测循环已取消")
                break
            except Exception as e:
                logger.error(f"心跳检测异常: {e}", exc_info=True)
                await asyncio.sleep(5)  # 异常后短暂等待再重试

    async def _check_heartbeats(self, timeout: float) -> None:
        """
        检查所有会话的心跳

        向每个活跃已初始化的会话发送 ping 请求，超时则移除。

        Args:
            timeout: ping 超时时间（秒）
        """
        # 获取快照，避免迭代时修改
        sessions_snapshot = list(self._sessions.items())
        stale_session_ids = []

        for session_id, session in sessions_snapshot:
            if not session.active:
                stale_session_ids.append(session_id)
                continue

            if not session.initialized:
                # 未初始化的会话检查是否过期（如连接后长时间未握手）
                if time.time() - session.created_at > 60:
                    logger.warning(f"会话初始化超时，移除: {session_id[:8]}")
                    stale_session_ids.append(session_id)
                continue

            # 发送 MCP ping
            try:
                ping_msg = self._mcp_handler.build_ping_request(request_id=session.next_request_id())
                response = await session.send_and_wait(ping_msg, timeout=timeout)
                if response is None:
                    logger.warning(f"心跳超时，移除会话: {session_id[:8]}")
                    stale_session_ids.append(session_id)
                else:
                    session.update_heartbeat()
            except Exception as e:
                logger.warning(f"心跳异常: {e}，移除会话: {session_id[:8]}")
                stale_session_ids.append(session_id)

        # 移除失效会话
        for session_id in stale_session_ids:
            await self.unregister(session_id)

    async def close_all(self) -> None:
        """关闭所有会话"""
        self.stop_heartbeat()
        async with self._lock:
            for session in self._sessions.values():
                session.close()
            count = len(self._sessions)
            self._sessions.clear()
            logger.info(f"已关闭所有会话 (共 {count} 个)")

    @property
    def session_count(self) -> int:
        """当前会话总数"""
        return len(self._sessions)

    @property
    def active_session_count(self) -> int:
        """活跃且已初始化的会话数"""
        return sum(
            1 for s in self._sessions.values()
            if s.active and s.initialized
        )
