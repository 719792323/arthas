"""
测试公共 Fixture

提供内存 SQLite 数据库、DiagnosisRepository 实例、示例任务等测试基础设施。
所有 async 测试统一使用 pytest-asyncio。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from control_platform.db.models import Base
from control_platform.db import database as db_module
from control_platform.db.repository import DiagnosisRepository


# ======================== 数据库 Fixture ========================

@pytest_asyncio.fixture
async def memory_db():
    """
    测试目的：提供一个独立的内存 SQLite 数据库，每个测试用例完全隔离。
    自动建表、注入全局 engine/session_factory，测试结束后释放。
    """
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # 建表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 猴子补丁：替换全局数据库引擎和 Session 工厂
    original_engine = db_module._engine
    original_factory = db_module._session_factory
    db_module._engine = engine
    db_module._session_factory = factory

    yield factory

    # 恢复原始全局状态
    await engine.dispose()
    db_module._engine = original_engine
    db_module._session_factory = original_factory


@pytest_asyncio.fixture
async def repo(memory_db):
    """
    测试目的：提供一个基于内存数据库的 DiagnosisRepository 实例。
    """
    return DiagnosisRepository()


@pytest_asyncio.fixture
async def sample_task(repo: DiagnosisRepository):
    """
    测试目的：预创建一个 running 状态的诊断任务（包含初始 USER_QUERY stage）。
    返回 (task, initial_stage) 元组。
    """
    task = await repo.create_task(
        session_id="test-session-001",
        user_query="帮我排查 JVM 内存泄漏问题",
        metadata={"source": "unit_test"},
    )
    stages = await repo.get_task_stages(task.task_id)
    return task, stages[0]


# ======================== Mock Fixture ========================

@pytest.fixture
def mock_websocket():
    """
    测试目的：提供一个 Mock WebSocket 对象，模拟 FastAPI WebSocket 连接。
    """
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock(return_value='{}')
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def mock_session_manager():
    """
    测试目的：提供一个 Mock SessionManager，用于隔离会话管理依赖。
    """
    manager = AsyncMock()
    manager.get_session = AsyncMock(return_value=None)
    manager.get_all_active_sessions = AsyncMock(return_value=[])
    manager.register = AsyncMock()
    manager.unregister = AsyncMock()
    return manager


@pytest.fixture
def mock_decision_engine():
    """
    测试目的：提供一个可配置返回值的 Mock DecisionEngine。
    """
    engine = AsyncMock()
    engine.decide = AsyncMock()
    engine.engine_name = "MockDecisionEngine"
    return engine


@pytest.fixture
def mock_context_builder():
    """
    测试目的：提供一个 Mock ContextBuilder，返回预设的 DecisionContext。
    """
    from control_platform.decision.context import DecisionContext
    builder = AsyncMock()
    builder.build_context = AsyncMock(return_value=DecisionContext(
        task_id="test-task-id",
        session_id="test-session-001",
        user_query="测试问题",
        messages=[],
    ))
    return builder


@pytest.fixture
def mock_mcp_handler():
    """
    测试目的：提供一个 Mock McpHandler。
    """
    handler = MagicMock()
    return handler
