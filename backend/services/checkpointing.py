"""Configurable durable LangGraph checkpointer lifecycle."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path


@asynccontextmanager
async def checkpoint_saver(project_root: Path):
    backend = os.getenv("CHECKPOINT_BACKEND", "sqlite").strip().lower()
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    if backend == "postgres":
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            raise RuntimeError("CHECKPOINT_BACKEND=postgres 时必须配置 DATABASE_URL")
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(database_url) as saver:
            await saver.setup()
            yield saver
        return
    if backend != "sqlite":
        raise RuntimeError("CHECKPOINT_BACKEND 仅支持 sqlite 或 postgres")

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    configured = os.getenv("CHECKPOINT_SQLITE_PATH", "data/checkpoints.db")
    path = Path(configured)
    if not path.is_absolute():
        path = project_root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(path.resolve())) as saver:
        await saver.setup()
        yield saver
