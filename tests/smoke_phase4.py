"""Phase-four TestClient smoke: submit, SSE, confirm, and retrieve artifacts."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.main import create_app
from backend.services.task_manager import TaskManager, TaskRunResult
from backend.tools.tryon_tools import ToolResult


class SmokeRunner:
    async def __call__(
        self, task_id: str, state: dict[str, Any], resume: Any = None
    ) -> TaskRunResult:
        if resume is None:
            return TaskRunResult(
                status="waiting_confirm",
                state={
                    **state,
                    "confirmed_plan": {
                        "mode": "B",
                        "garment_part": "top",
                        "variants": 1,
                        "prompt": "白色T恤电商棚拍",
                    },
                },
                interrupt={
                    "type": "plan_confirm",
                    "plan": {"mode": "B", "garment_part": "top", "variants": 1},
                },
            )
        assert resume == "将背景改成浅灰色"
        result_path = Path(state["session_dir"]) / "result.png"
        result_path.write_bytes(b"mock-png-content")
        return TaskRunResult(
            status="completed",
            state={
                **state,
                "pending_interrupt": None,
                "image_urls": {
                    **(state.get("image_urls") or {}),
                    "results": [str(result_path)],
                },
                "error": None,
            },
            cost=0.2,
        )


class SmokeTools:
    async def list_models(self, *, session_dir):
        return ToolResult(
            True, {"candidates": [{"name": "柔妍", "path": "model.jpg"}]}, 0, 0, ""
        )


def wait_for_status(client: TestClient, task_id: str, status: str) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f"/api/tasks/{task_id}")
        response.raise_for_status()
        payload = response.json()
        if payload["status"] == status:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"Task did not reach {status}")


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        manager = TaskManager(root / "tasks.db", SmokeRunner())
        app = create_app(
            task_manager=manager,
            tryon_tools=SmokeTools(),
            sessions_root=root / "sessions",
            sync_vendor_env=False,
        )
        with TestClient(app) as client:
            submitted = client.post(
                "/api/generate",
                data={"description": "白色T恤电商棚拍"},
                files={
                    "garment_image": ("garment.png", b"\x89PNG\r\n\x1a\nfake-garment", "image/png"),
                    "model_image": ("model.jpg", b"\xff\xd8\xfffake-model", "image/jpeg"),
                },
            )
            assert submitted.status_code == 202, submitted.text
            task_id = submitted.json()["task_id"]
            waiting = wait_for_status(client, task_id, "waiting_confirm")
            assert Path(waiting["state"]["image_urls"]["garment"]).is_file()
            assert Path(waiting["state"]["image_urls"]["model"]).is_file()

            with client.stream("GET", f"/api/tasks/{task_id}/events") as stream:
                first_events = "".join(stream.iter_text())
            assert "event: submitted" in first_events
            assert "event: running" in first_events
            assert "event: waiting_confirm" in first_events
            event_ids = [int(value) for value in re.findall(r"^id: (\d+)$", first_events, re.M)]

            confirmed = client.post(
                f"/api/tasks/{task_id}/confirm",
                json={"action": "modify", "payload": "将背景改成浅灰色"},
            )
            assert confirmed.status_code == 202, confirmed.text
            completed = wait_for_status(client, task_id, "completed")
            assert completed["artifacts"][0].startswith("/api/files/")
            assert completed["cost"] == 0.2
            artifact = client.get(completed["artifacts"][0])
            assert artifact.status_code == 200
            assert artifact.content == b"mock-png-content"

            with client.stream(
                "GET",
                f"/api/tasks/{task_id}/events",
                headers={"Last-Event-ID": str(event_ids[-1])},
            ) as stream:
                resumed_events = "".join(stream.iter_text())
            assert "event: confirmation_received" in resumed_events
            assert "event: completed" in resumed_events

            models = client.get("/api/models")
            assert models.status_code == 200
            assert models.json()["models"]["candidates"][0]["name"] == "柔妍"

            print(json.dumps(completed, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
