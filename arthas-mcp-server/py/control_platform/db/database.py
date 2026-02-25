"""
异步数据库引擎初始化与 Session 管理

提供：
- 异步 SQLAlchemy Engine 和 SessionFactory
- 自动建表逻辑
- 异步 Session 上下文管理器
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from control_platform.config import settings

# 全局异步引擎（惰性初始化）
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """获取全局异步引擎（需先调用 init_db）"""
    if _engine is None:
        raise RuntimeError("数据库尚未初始化，请先调用 init_db()")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取全局 Session 工厂（需先调用 init_db）"""
    if _session_factory is None:
        raise RuntimeError("数据库尚未初始化，请先调用 init_db()")
    return _session_factory


async def init_db() -> None:
    """
    初始化数据库引擎并自动建表。

    在应用启动时（lifespan）调用一次即可。
    """
    global _engine, _session_factory

    _engine = create_async_engine(
        settings.db_url,
        echo=settings.debug,
        # SQLite 需要开启 WAL 模式以支持并发读
        connect_args={"check_same_thread": False} if "sqlite" in settings.db_url else {},
    )

    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # 自动建表（导入 models 确保所有表被注册到 Base.metadata）
    from control_platform.db.models import Base  # noqa: F811

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """关闭数据库引擎，释放连接池资源。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    获取异步数据库 Session 的上下文管理器。

    用法::

        async with get_session() as session:
            result = await session.execute(...)
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
