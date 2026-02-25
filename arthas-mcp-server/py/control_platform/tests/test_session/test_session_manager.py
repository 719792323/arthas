"""
会话管理器（SessionManager）测试

测试会话注册、注销、查询、初始化标记和统计。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from control_platform.session.client_session import ClientSession
from control_platform.session.session_manager import SessionManager
from control_platform.protocol.mcp_handler import McpHandler


class TestSessionManager:
    """会话管理器测试"""

    @pytest.fixture
    def manager(self):
        """提供基于 Mock McpHandler 的 SessionManager"""
        mock_mcp = MagicMock(spec=McpHandler)
        return SessionManager(mock_mcp)

    @pytest.fixture
    def mock_ws(self):
        ws = AsyncMock()
        ws.send_text = AsyncMock()
        return ws

    @pytest.mark.asyncio
    async def test_register_new_session(self, manager: SessionManager, mock_ws):
        """测试目的：注册新会话后，session 应被添加到管理器中"""
        session = await manager.register("session-001", mock_ws)
        assert session is not None
        assert session.session_id == "session-001"
        assert manager.session_count == 1

    @pytest.mark.asyncio
    async def test_register_replaces_old_session(self, manager: SessionManager, mock_ws):
        """测试目的：重复注册同一 session_id 时，旧 session 应被关闭并替换"""
        old_session = await manager.register("session-001", mock_ws)

        new_ws = AsyncMock()
        new_ws.send_text = AsyncMock()
        new_session = await manager.register("session-001", new_ws)

        assert old_session.active is False  # 旧的应被关闭
        assert new_session is not old_session
        assert manager.session_count == 1  # 仍然只有 1 个

    @pytest.mark.asyncio
    async def test_unregister_removes_session(self, manager: SessionManager, mock_ws):
        """测试目的：注销会话后，session 应从管理器中移除并被关闭"""
        session = await manager.register("session-001", mock_ws)
        await manager.unregister("session-001")

        assert session.active is False
        assert manager.session_count == 0

    @pytest.mark.asyncio
    async def test_unregister_nonexistent_session(self, manager: SessionManager):
        """测试目的：注销不存在的 session_id 不应报错"""
        await manager.unregister("non-existent")  # 不应抛异常

    @pytest.mark.asyncio
    async def test_get_session_active_and_initialized(self, manager: SessionManager, mock_ws):
        """测试目的：get_session 只返回 active 且 initialized 的会话"""
        session = await manager.register("session-001", mock_ws)
        # 未初始化，应返回 None
        result = await manager.get_session("session-001")
        assert result is None

        # 标记初始化后应能获取
        await manager.mark_initialized("session-001")
        result = await manager.get_session("session-001")
        assert result is session

    @pytest.mark.asyncio
    async def test_get_session_inactive_returns_none(self, manager: SessionManager, mock_ws):
        """测试目的：不活跃的 session 调用 get_session 应返回 None"""
        session = await manager.register("session-001", mock_ws)
        await manager.mark_initialized("session-001")
        session.active = False

        result = await manager.get_session("session-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_raw_session_skips_initialized_check(self, manager: SessionManager, mock_ws):
        """测试目的：get_raw_session 只检查 active 状态，不检查 initialized"""
        session = await manager.register("session-001", mock_ws)
        # 未初始化
        result = await manager.get_raw_session("session-001")
        assert result is session  # 应能获取

    @pytest.mark.asyncio
    async def test_get_raw_session_inactive_returns_none(self, manager: SessionManager, mock_ws):
        """测试目的：不活跃的 session 调用 get_raw_session 也应返回 None"""
        session = await manager.register("session-001", mock_ws)
        session.active = False
        result = await manager.get_raw_session("session-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_mark_initialized(self, manager: SessionManager, mock_ws):
        """测试目的：mark_initialized 应将 session.initialized 设为 True"""
        session = await manager.register("session-001", mock_ws)
        assert session.initialized is False

        await manager.mark_initialized("session-001")
        assert session.initialized is True

    @pytest.mark.asyncio
    async def test_session_count(self, manager: SessionManager, mock_ws):
        """测试目的：session_count 应返回管理器中所有 session 的总数"""
        ws1, ws2, ws3 = AsyncMock(), AsyncMock(), AsyncMock()
        await manager.register("s1", ws1)
        await manager.register("s2", ws2)
        await manager.register("s3", ws3)
        assert manager.session_count == 3

    @pytest.mark.asyncio
    async def test_active_session_count(self, manager: SessionManager, mock_ws):
        """测试目的：active_session_count 只计算 active 且 initialized 的 session"""
        ws1, ws2, ws3 = AsyncMock(), AsyncMock(), AsyncMock()
        await manager.register("s1", ws1)
        await manager.register("s2", ws2)
        await manager.register("s3", ws3)

        await manager.mark_initialized("s1")
        await manager.mark_initialized("s2")
        # s3 未初始化

        assert manager.active_session_count == 2

    @pytest.mark.asyncio
    async def test_get_all_active_sessions(self, manager: SessionManager):
        """测试目的：get_all_active_sessions 应只返回活跃且已初始化的会话"""
        ws1, ws2 = AsyncMock(), AsyncMock()
        await manager.register("s1", ws1)
        await manager.register("s2", ws2)
        await manager.mark_initialized("s1")

        sessions = await manager.get_all_active_sessions()
        assert len(sessions) == 1
        assert sessions[0].session_id == "s1"

    @pytest.mark.asyncio
    async def test_get_all_session_ids(self, manager: SessionManager):
        """测试目的：get_all_session_ids 应只返回活跃且已初始化的 session ID"""
        ws1, ws2 = AsyncMock(), AsyncMock()
        await manager.register("s1", ws1)
        await manager.register("s2", ws2)
        await manager.mark_initialized("s1")
        await manager.mark_initialized("s2")

        ids = await manager.get_all_session_ids()
        assert set(ids) == {"s1", "s2"}

    @pytest.mark.asyncio
    async def test_close_all(self, manager: SessionManager):
        """测试目的：close_all 应关闭所有会话并清空管理器"""
        ws1, ws2 = AsyncMock(), AsyncMock()
        s1 = await manager.register("s1", ws1)
        s2 = await manager.register("s2", ws2)

        await manager.close_all()

        assert s1.active is False
        assert s2.active is False
        assert manager.session_count == 0
