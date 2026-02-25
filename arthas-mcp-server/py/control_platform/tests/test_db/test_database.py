"""
数据库初始化测试

测试 init_db / close_db / get_session 的行为，
包括正常流程、未初始化时的错误处理和事务语义。
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from control_platform.db import database as db_module
from control_platform.db.models import Base, DiagnosisTask


class TestDatabaseInit:
    """数据库引擎初始化与关闭测试"""

    @pytest.mark.asyncio
    async def test_get_engine_before_init_raises(self):
        """测试目的：未调用 init_db() 直接调用 get_engine() 应抛出 RuntimeError"""
        # 保存原始状态
        original_engine = db_module._engine
        original_factory = db_module._session_factory
        db_module._engine = None
        db_module._session_factory = None

        try:
            with pytest.raises(RuntimeError, match="数据库尚未初始化"):
                db_module.get_engine()
        finally:
            db_module._engine = original_engine
            db_module._session_factory = original_factory

    @pytest.mark.asyncio
    async def test_get_session_factory_before_init_raises(self):
        """测试目的：未调用 init_db() 直接调用 get_session_factory() 应抛出 RuntimeError"""
        original_engine = db_module._engine
        original_factory = db_module._session_factory
        db_module._engine = None
        db_module._session_factory = None

        try:
            with pytest.raises(RuntimeError, match="数据库尚未初始化"):
                db_module.get_session_factory()
        finally:
            db_module._engine = original_engine
            db_module._session_factory = original_factory

    @pytest.mark.asyncio
    async def test_close_db_resets_global_state(self):
        """测试目的：close_db() 调用后，全局 _engine 和 _session_factory 应重置为 None"""
        # 先初始化一个测试引擎
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        original_engine = db_module._engine
        original_factory = db_module._session_factory
        db_module._engine = engine
        db_module._session_factory = factory

        try:
            await db_module.close_db()
            assert db_module._engine is None
            assert db_module._session_factory is None
        finally:
            db_module._engine = original_engine
            db_module._session_factory = original_factory


class TestGetSession:
    """get_session() 上下文管理器测试"""

    @pytest.mark.asyncio
    async def test_session_commit_on_success(self, memory_db):
        """测试目的：验证 get_session 上下文正常退出时数据自动 commit 持久化"""
        async with db_module.get_session() as session:
            task = DiagnosisTask(
                session_id="test-commit",
                user_query="测试提交",
            )
            session.add(task)
        # commit 成功，数据应该持久化

        # 使用新的 session 验证数据确实存在
        async with db_module.get_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(DiagnosisTask).where(DiagnosisTask.session_id == "test-commit")
            )
            loaded = result.scalar_one_or_none()
            assert loaded is not None
            assert loaded.user_query == "测试提交"

    @pytest.mark.asyncio
    async def test_session_rollback_on_exception(self, memory_db):
        """测试目的：验证 get_session 上下文异常退出时数据自动 rollback"""
        task_id_holder = []

        with pytest.raises(ValueError):
            async with db_module.get_session() as session:
                task = DiagnosisTask(
                    session_id="test-rollback",
                    user_query="测试回滚",
                )
                session.add(task)
                await session.flush()
                task_id_holder.append(task.task_id)
                # 手动抛出异常，触发 rollback
                raise ValueError("故意抛出的异常")

        # rollback 后，数据不应该存在
        async with db_module.get_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(DiagnosisTask).where(DiagnosisTask.session_id == "test-rollback")
            )
            loaded = result.scalar_one_or_none()
            assert loaded is None
