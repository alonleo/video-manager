"""数据库引擎与会话管理（SQLAlchemy 2.0 async + SQLite）。

SQLite 并发策略（重要）：
API 与 arq worker 是两个独立进程，都写同一个库文件。SQLite 只允许单写者，
因此这里做三件事：
  1. 开启 WAL 模式 —— 读不阻塞写，写不阻塞读
  2. 设置 busy_timeout —— 写冲突时排队等待而不是立刻抛 "database is locked"
  3. 设置 foreign_keys=ON —— SQLite 默认关闭外键约束，必须显式打开，
     否则 ondelete CASCADE 不生效
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

log = logging.getLogger(__name__)

engine = create_async_engine(
    settings.database_url,
    echo=False,
    # SQLite 用默认的 NullPool/StaticPool 语义即可；文件库不需要连接池调优
    pool_pre_ping=True,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _record) -> None:
    """每条新连接都要设置一遍 PRAGMA（PRAGMA 是连接级的）。"""
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute(f"PRAGMA busy_timeout={settings.sqlite_busy_timeout_ms}")
        cur.execute("PRAGMA foreign_keys=ON")
        # 性能：WAL 下 NORMAL 已足够安全（掉电最多丢最后一个事务，不会损坏库）
        cur.execute("PRAGMA synchronous=NORMAL")
        # 临时表/排序放内存，减少磁盘 IO
        cur.execute("PRAGMA temp_store=MEMORY")
        cur.execute("PRAGMA cache_size=-64000")  # 约 64MB
    finally:
        cur.close()


SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每请求一个会话。"""
    async with SessionLocal() as session:
        yield session
