"""FastAPI application assembly for the AI try-on service."""

from __future__ import annotations

import logging
import os
import asyncio
import secrets
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.agent.graph import build_graph
from backend.api.routes import router
from backend.services.graph_runner import LangGraphTaskRunner
from backend.services.edit_runner import EditTaskRunner, RoutedTaskRunner
from backend.services.artifact_publisher import ArtifactPublisher, PublishingTaskRunner
from backend.services.cache_cleanup import cleanup_loop
from backend.services.checkpointing import checkpoint_saver
from backend.services.task_manager import TaskManager, TaskNotFoundError
from backend.tools.tryon_tools import TryonTools
from sync_env import sync_env


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SESSIONS_ROOT = PROJECT_ROOT / "data" / "sessions"
DEFAULT_TASK_DB = PROJECT_ROOT / "data" / "tasks.db"


def create_app(
    *,
    task_manager: TaskManager | None = None,
    tryon_tools=None,
    sessions_root: str | Path = DEFAULT_SESSIONS_ROOT,
    sync_vendor_env: bool = True,
) -> FastAPI:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    if os.getenv("LANGSMITH_ENABLED", "false").lower() in {"1", "true", "yes"}:
        os.environ.setdefault("LANGSMITH_TRACING", "true")
    sessions_path = Path(sessions_root).resolve()
    mock_mode = os.getenv("APP_MOCK_MODE", "false").lower() in {"1", "true", "yes"}
    if tryon_tools is not None:
        tools = tryon_tools
    elif mock_mode:
        from backend.services.mock_runtime import MockTryonTools

        tools = MockTryonTools(PROJECT_ROOT / "data")
    else:
        tools = TryonTools(sessions_root=sessions_path)
    managed_checkpoint = task_manager is None
    checkpoint_holder: dict[str, object] = {}
    if task_manager is None:
        if mock_mode:
            from backend.services.mock_runtime import MockAgentRunner

            agent_runner = MockAgentRunner()
        else:
            agent_runner = LangGraphTaskRunner(
                lambda: build_graph(
                    tools=tools, checkpointer=checkpoint_holder["saver"]
                )
            )
        runner = PublishingTaskRunner(
            RoutedTaskRunner(
                agent_runner,
                EditTaskRunner(tools),
            ),
            ArtifactPublisher(tools),
        )
        task_manager = TaskManager(DEFAULT_TASK_DB, runner, max_concurrency=2)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # The upstream skill only reads scripts/.env. Keep it synchronized at
        # startup, but allow API-only/mock development before a local .env exists.
        if sync_vendor_env:
            try:
                sync_env()
            except FileNotFoundError:
                logger.warning("Root .env is absent; vendor env synchronization skipped")
        async with AsyncExitStack() as stack:
            if managed_checkpoint:
                checkpoint_holder["saver"] = await stack.enter_async_context(
                    checkpoint_saver(PROJECT_ROOT)
                )
            task_manager.init_db()
            recovery = await task_manager.recover_tasks()
            logger.info("Task recovery completed: %s", recovery)
            cleanup_task = asyncio.create_task(
                cleanup_loop(
                    sessions_path,
                    retention_days=int(os.getenv("CACHE_RETENTION_DAYS", "7")),
                    interval_hours=float(
                        os.getenv("CACHE_CLEANUP_INTERVAL_HOURS", "24")
                    ),
                ),
                name="session-cache-cleanup",
            )
            try:
                yield
            finally:
                cleanup_task.cancel()
                await asyncio.gather(cleanup_task, return_exceptions=True)
                await task_manager.shutdown()

    app = FastAPI(title="AI Virtual Try-on Agent", lifespan=lifespan)
    app.state.task_manager = task_manager
    app.state.tryon_tools = tools
    app.state.sessions_root = sessions_path

    origins = [
        item.strip()
        for item in os.getenv(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",")
        if item.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    auth_enabled = os.getenv("API_AUTH_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    api_key = os.getenv("API_KEY", "")
    if auth_enabled and not api_key:
        raise RuntimeError("API_AUTH_ENABLED=true 时必须配置 API_KEY")

    @app.middleware("http")
    async def api_key_auth(request: Request, call_next):
        if (
            auth_enabled
            and request.url.path.startswith("/api/")
            and request.url.path != "/api/health"
            and not request.url.path.startswith("/api/files/")
            and request.method != "OPTIONS"
            and not secrets.compare_digest(
                request.headers.get("X-API-Key", ""), api_key
            )
        ):
            return JSONResponse(status_code=401, content={"detail": "API Key 无效"})
        return await call_next(request)

    @app.exception_handler(TaskNotFoundError)
    async def task_not_found(_: Request, exc: TaskNotFoundError):
        return JSONResponse(
            status_code=404, content={"detail": f"任务不存在：{exc.args[0]}"}
        )

    @app.exception_handler(Exception)
    async def unhandled_error(_: Request, exc: Exception):
        logger.exception("Unhandled API error", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})

    app.include_router(router)
    return app


app = create_app()
