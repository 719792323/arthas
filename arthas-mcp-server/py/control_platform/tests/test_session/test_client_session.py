"""
客户端会话（ClientSession）测试

测试消息发送、请求-响应匹配、超时处理和会话关闭。
"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from control_platform.session.client_session import ClientSession


class TestClientSessionBasic:
    """ClientSession 基础功能测试"""

    @pytest.fixture
    def mock_ws(self):
        """提供 Mock WebSocket"""
        ws = AsyncMock()
        ws.send_text = AsyncMock()
        return ws

    @pytest.fixture
    def session(self, mock_ws):
        """提供基于 Mock WebSocket 的 ClientSession"""
        return ClientSession("test-session-id", mock_ws)

    def test_initial_state(self, session: ClientSession):
        """测试目的：新创建的 ClientSession 应有正确的初始状态"""
        assert session.session_id == "test-session-id"
        assert session.active is True
        assert session.initialized is False
        assert session.pending_futures == {}

    def test_next_request_id_increments(self, session: ClientSession):
        """测试目的：next_request_id 应生成 sessionId-N 格式的自增 ID"""
        id1 = session.next_request_id()
        id2 = session.next_request_id()
        id3 = session.next_request_id()
        assert id1 == "test-session-id-0"
        assert id2 == "test-session-id-1"
        assert id3 == "test-session-id-2"


class TestClientSessionSendMessage:
    """消息发送测试"""

    @pytest.fixture
    def mock_ws(self):
        ws = AsyncMock()
        ws.send_text = AsyncMock()
        return ws

    @pytest.fixture
    def session(self, mock_ws):
        return ClientSession("test-session-id", mock_ws)

    @pytest.mark.asyncio
    async def test_send_message_success(self, session: ClientSession, mock_ws):
        """测试目的：send_message 正常发送应调用 websocket.send_text 并返回 True"""
        result = await session.send_message({"method": "ping"})
        assert result is True
        mock_ws.send_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_message_serializes_json(self, session: ClientSession, mock_ws):
        """测试目的：send_message 应将消息字典序列化为 JSON 字符串"""
        msg = {"method": "test", "params": {"key": "值"}}
        await session.send_message(msg)

        call_args = mock_ws.send_text.call_args[0][0]
        parsed = json.loads(call_args)
        assert parsed["method"] == "test"
        assert parsed["params"]["key"] == "值"

    @pytest.mark.asyncio
    async def test_send_message_failure_returns_false(self, session: ClientSession, mock_ws):
        """测试目的：send_message 发送失败（异常）应返回 False 并标记 active=False"""
        mock_ws.send_text.side_effect = Exception("Connection lost")
        result = await session.send_message({"method": "ping"})
        assert result is False
        assert session.active is False

    @pytest.mark.asyncio
    async def test_send_message_inactive_session(self, session: ClientSession):
        """测试目的：不活跃的 session 发送消息应直接返回 False"""
        session.active = False
        result = await session.send_message({"method": "ping"})
        assert result is False


class TestClientSessionSendAndWait:
    """请求-响应匹配测试"""

    @pytest.fixture
    def mock_ws(self):
        ws = AsyncMock()
        ws.send_text = AsyncMock()
        return ws

    @pytest.fixture
    def session(self, mock_ws):
        return ClientSession("test-session-id", mock_ws)

    @pytest.mark.asyncio
    async def test_send_and_wait_success(self, session: ClientSession):
        """测试目的：send_and_wait 发送后通过 resolve_response 匹配应返回正确响应"""
        msg = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        expected_response = {"jsonrpc": "2.0", "id": 1, "result": {}}

        # 在后台任务中模拟收到响应
        async def simulate_response():
            await asyncio.sleep(0.05)
            session.resolve_response(1, expected_response)

        asyncio.create_task(simulate_response())
        result = await session.send_and_wait(msg, timeout=5.0)

        assert result == expected_response

    @pytest.mark.asyncio
    async def test_send_and_wait_timeout(self, session: ClientSession):
        """测试目的：send_and_wait 超时时应返回 None"""
        msg = {"jsonrpc": "2.0", "id": 99, "method": "slow_call"}

        result = await session.send_and_wait(msg, timeout=0.1)
        assert result is None

    @pytest.mark.asyncio
    async def test_send_and_wait_no_id_returns_none(self, session: ClientSession):
        """测试目的：消息没有 id 字段时，send_and_wait 应返回 None"""
        msg = {"jsonrpc": "2.0", "method": "notification"}
        result = await session.send_and_wait(msg, timeout=1.0)
        assert result is None

    @pytest.mark.asyncio
    async def test_send_and_wait_send_failure(self, session: ClientSession, mock_ws):
        """测试目的：发送失败时，send_and_wait 应返回 None 并清理 pending future"""
        mock_ws = session.websocket
        mock_ws.send_text.side_effect = Exception("Send failed")

        msg = {"jsonrpc": "2.0", "id": 5, "method": "test"}
        result = await session.send_and_wait(msg, timeout=1.0)

        assert result is None
        assert 5 not in session.pending_futures


class TestClientSessionResolveResponse:
    """响应匹配测试"""

    @pytest.fixture
    def mock_ws(self):
        ws = AsyncMock()
        ws.send_text = AsyncMock()
        return ws

    @pytest.fixture
    def session(self, mock_ws):
        return ClientSession("test-session-id", mock_ws)

    @pytest.mark.asyncio
    async def test_resolve_response_matches(self, session: ClientSession):
        """测试目的：resolve_response 匹配到 pending future 时应返回 True"""
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        session.pending_futures[1] = future

        matched = session.resolve_response(1, {"result": "ok"})
        assert matched is True
        assert future.result() == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_resolve_response_no_match(self, session: ClientSession):
        """测试目的：resolve_response 没有匹配的 pending future 时应返回 False"""
        matched = session.resolve_response(999, {"result": "ok"})
        assert matched is False


class TestClientSessionClose:
    """会话关闭测试"""

    @pytest.fixture
    def mock_ws(self):
        ws = AsyncMock()
        ws.send_text = AsyncMock()
        return ws

    @pytest.fixture
    def session(self, mock_ws):
        return ClientSession("test-session-id", mock_ws)

    @pytest.mark.asyncio
    async def test_close_sets_inactive(self, session: ClientSession):
        """测试目的：close() 应将 active 设为 False"""
        session.close()
        assert session.active is False

    @pytest.mark.asyncio
    async def test_close_cancels_pending_futures(self, session: ClientSession):
        """测试目的：close() 应取消所有 pending future"""
        loop = asyncio.get_event_loop()
        future1 = loop.create_future()
        future2 = loop.create_future()
        session.pending_futures[1] = future1
        session.pending_futures[2] = future2

        session.close()

        assert len(session.pending_futures) == 0
        # future 应该已经完成（通过 set_exception）
        assert future1.done()
        assert future2.done()

    def test_to_dict(self, session: ClientSession):
        """测试目的：to_dict 应序列化所有关键字段"""
        data = session.to_dict()
        assert data["session_id"] == "test-session-id"
        assert data["initialized"] is False
        assert data["active"] is True
        assert "created_at" in data
        assert "pending_requests" in data
