from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.services.artifact_publisher import ArtifactPublisher, PublishingTaskRunner
from backend.services.cache_cleanup import cleanup_sessions
from backend.services.checkpointing import checkpoint_saver
from backend.services.cost_ledger import CostLedger
from backend.services.task_manager import TaskManager, TaskRunResult
from backend.tools.tryon_tools import ToolResult


class FakeTools:
    def __init__(self, ledger: CostLedger) -> None:
        self.ledger = ledger

    async def upload_to_oss(self, path, *, session_dir):
        del session_dir
        return ToolResult(True, {"stdout": f"https://cdn.example/{Path(path).name}"}, 0.01, 0.1, "")

    async def preprocess_garment(self, path, *, session_dir):
        del path, session_dir
        return ToolResult(True, {"ok": True}, 0, 0.01, "")


class CompleteRunner:
    async def __call__(self, task_id, state, resume=None):
        del task_id, resume
        return TaskRunResult(status="completed", state=state)


def test_daily_stats_grouped_by_tool(tmp_path: Path):
    ledger = CostLedger(tmp_path / "cost.db")
    ledger.record_tool_call(tool_name="partial_tryon", elapsed_seconds=2, success=True, cost=0.2)
    ledger.record_tool_call(tool_name="partial_tryon", elapsed_seconds=4, success=False, cost=0.2)
    stats = ledger.daily_stats()
    assert stats["calls"] == 2
    assert stats["failure_rate"] == pytest.approx(0.5)
    assert stats["by_tool"][0]["average_elapsed_seconds"] == pytest.approx(3)


def test_cleanup_only_removes_expired_child_directories(tmp_path: Path):
    old = tmp_path / "old"
    recent = tmp_path / "recent"
    old.mkdir(); recent.mkdir()
    old_time = time.time() - 8 * 86400
    os.utime(old, (old_time, old_time))
    removed = cleanup_sessions(tmp_path, retention_days=7)
    assert removed == [str(old.resolve())]
    assert not old.exists()
    assert recent.exists()


@pytest.mark.asyncio
async def test_publisher_replaces_local_artifact_with_oss_url(tmp_path: Path):
    artifact = tmp_path / "result.png"
    artifact.write_bytes(b"png")
    tools = FakeTools(CostLedger(tmp_path / "cost.db"))

    class Runner:
        async def __call__(self, task_id, state, resume=None):
            del task_id, resume
            return TaskRunResult(
                status="completed",
                state={**state, "image_urls": {"results": [str(artifact)]}},
                cost=0.2,
            )

    outcome = await PublishingTaskRunner(
        Runner(), ArtifactPublisher(tools, enabled=True)
    )("task", {"session_dir": str(tmp_path)})
    assert outcome.state["image_urls"]["results"] == ["https://cdn.example/result.png"]
    assert outcome.state["local_cache_artifacts"] == [str(artifact)]
    assert outcome.cost == pytest.approx(0.21)


@pytest.mark.asyncio
async def test_sqlite_checkpoint_fallback(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CHECKPOINT_BACKEND", "sqlite")
    monkeypatch.setenv("CHECKPOINT_SQLITE_PATH", str(tmp_path / "checkpoint.db"))
    async with checkpoint_saver(tmp_path) as saver:
        assert saver is not None
    assert (tmp_path / "checkpoint.db").is_file()


def test_api_key_and_upload_signature(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_KEY", "test-secret")
    tools = FakeTools(CostLedger(tmp_path / "cost.db"))
    manager = TaskManager(tmp_path / "tasks.db", CompleteRunner())
    app = create_app(
        task_manager=manager,
        tryon_tools=tools,
        sessions_root=tmp_path / "sessions",
        sync_vendor_env=False,
    )
    with TestClient(app) as client:
        assert client.get("/api/tasks/missing").status_code == 401
        headers = {"X-API-Key": "test-secret"}
        rejected = client.post(
            "/api/preflight",
            data={"kind": "garment"},
            files={"image": ("fake.png", b"not-a-png", "image/png")},
            headers=headers,
        )
        assert rejected.status_code == 415
        valid_png = b"\x89PNG\r\n\x1a\n" + b"mock-content"
        accepted = client.post(
            "/api/preflight",
            data={"kind": "garment"},
            files={"image": ("valid.png", valid_png, "image/png")},
            headers=headers,
        )
        assert accepted.status_code == 200
