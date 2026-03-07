"""
异步数据库引擎初始化与 Session 管理

提供：
- 异步 SQLAlchemy Engine 和 SessionFactory
- 自动建表逻辑
- 异步 Session 上下文管理器
- 基于 contextvars 的 Session 共享机制（shared_session）
"""

from contextlib import asynccontextmanager
from contextvars import ContextVar
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

# 基于 contextvars 的 Session 共享机制
# 当 _current_session 有值时，get_session() 会复用该 session 而非创建新的
_current_session: ContextVar[AsyncSession | None] = ContextVar(
    "_current_session", default=None
)


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


def _build_engine_kwargs() -> dict:
    """
    根据数据库 URL 动态构建引擎参数。

    - SQLite：仅设置 check_same_thread=False（兼容异步）
    - MySQL/PostgreSQL 等生产数据库：配置连接池参数，防止长连接超时断开
    """
    kwargs: dict = {
        "echo": settings.debug,
    }

    if "sqlite" in settings.db_url:
        # SQLite 不支持连接池参数，仅需解除线程限制
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # MySQL / PostgreSQL 等生产数据库的连接池配置
        kwargs.update({
            "pool_size": 10,           # 连接池常驻连接数
            "max_overflow": 20,        # 超出 pool_size 后允许的最大临时连接数
            "pool_recycle": 3600,      # 连接最大存活秒数（防止 MySQL 8h 超时断开）
            "pool_pre_ping": True,     # 每次取连接前发送 ping，自动剔除失效连接
        })

        # MySQL 专属：强制使用 utf8mb4 字符集
        if "mysql" in settings.db_url:
            kwargs["connect_args"] = {"charset": "utf8mb4"}

    return kwargs


async def init_db() -> None:
    """
    初始化数据库引擎并自动建表。

    在应用启动时（lifespan）调用一次即可。
    根据 settings.db_url 自动识别数据库类型，动态配置引擎参数：
    - SQLite：轻量模式，无连接池
    - MySQL：连接池 + utf8mb4 字符集 + 超时保活
    - PostgreSQL：连接池 + 超时保活
    """
    global _engine, _session_factory

    _engine = create_async_engine(
        settings.db_url,
        **_build_engine_kwargs(),
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

    自动感知 shared_session 上下文：
    - 如果当前协程处于 shared_session() 作用域内，则复用共享 session（不提交、不回滚）
    - 否则创建独立 session（退出时自动 commit/rollback）

    用法::

        async with get_session() as session:
            result = await session.execute(...)
    """
    # 检查是否处于 shared_session 作用域内
    shared = _current_session.get()
    if shared is not None:
        # 复用共享 session，不管理事务生命周期（由 shared_session 统一管理）
        yield shared
        return

    # 独立 session 模式（向后兼容原有行为）
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def shared_session() -> AsyncGenerator[AsyncSession, None]:
    """
    创建共享 Session 作用域的上下文管理器。

    在此作用域内，所有通过 get_session() 获取的 session 都将复用同一个 session，
    保证多个 Repository 方法在同一个数据库事务中执行。

    - 作用域结束时自动 commit（成功）或 rollback（异常）
    - 支持嵌套调用：内层 shared_session 直接复用外层的 session
    - Repository 层代码完全不需要修改

    用法::

        async with shared_session():
            # 以下操作在同一个事务中执行
            task = await repo.get_task(task_id)
            await repo.complete_stage(stage.id, output_data)
            await repo.complete_task(task_id, conclusion)

    原理::

        shared_session()       →  创建 session，设置到 ContextVar
            ├── repo.get_task()     →  get_session() 检测到 ContextVar，复用 session
            ├── repo.complete_stage() → 同上，复用同一个 session
            └── repo.complete_task()  → 同上
        作用域结束              →  统一 commit 或 rollback，清除 ContextVar
    """
    # 支持嵌套：如果已经在 shared_session 作用域内，直接复用
    existing = _current_session.get()
    if existing is not None:
        yield existing
        return

    # 创建新的共享 session
    # 如果数据库未初始化（如纯 mock 测试场景），优雅降级为无共享 session
    if _session_factory is None:
        yield None  # type: ignore[arg-type]
        return

    factory = get_session_factory()
    async with factory() as session:
        token = _current_session.set(session)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            _current_session.reset(token)