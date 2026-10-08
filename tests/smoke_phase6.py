"""Phase-six mock smoke: bbox -> synced replacement -> refreshed files -> video."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.main import create_app
from backend.services.edit_runner import EditTaskRunner, VIDEO_LOCK_PROMPT
from backend.services.task_manager import TaskManager
from backend.tools.tryon_tools import ToolResult


class MockEditTools:
    def __init__(self) -> None:
        self.edits = 0

    async def partial_tryon(
        self, model, new_garment, replace, get_bbox=False, *, session_dir
    ):
        if get_bbox:
            bbox = [40, 60, 260, 310] if replace == "upper" else [45, 300, 265, 590]
            return ToolResult(True, {"bbox": bbox}, 0, 0.01, "")
        assert Path(model).is_file()
        assert Path(new_garment).is_file()
        self.edits += 1
        output = Path(session_dir) / f"edited_{self.edits}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(f"edited-{replace}-{self.edits}".encode())
        return ToolResult(True, {"path": str(output)}, 0.1, 0.01, "")

    async def generate_variants(self, *args, **kwargs):
        output = Path(kwargs["session_dir"]) / "generated_garment.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"generated-garment")
        return ToolResult(True, {"path": str(output)}, 0.05, 0.01, "")

    async def generate_video(self, images, prompt, ratio, *, session_dir):
        assert len(images) == 2
        assert prompt.startswith(VIDEO_LOCK_PROMPT)
        assert "no black or dark background" in prompt
        assert ratio == "9:16"
        output = Path(session_dir) / "video.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mock-video")
        return ToolResult(True, {"path": str(output)}, 0.3, 0.01, "")


def wait_completed(client: TestClient, task_id: str) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        payload = client.get(f"/api/tasks/{task_id}").json()
        if payload["status"] in {"completed", "failed"}:
            assert payload["status"] == "completed", payload
            return payload
        time.sleep(0.02)
    raise AssertionError("mock task timed out")


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        sessions = root / "sessions"
        base = sessions / "source"
        base.mkdir(parents=True)
        first = base / "first.png"
        second = base / "second.png"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        garment = b"\x89PNG\r\n\x1a\nreplacement-garment"
        tools = MockEditTools()
        manager = TaskManager(root / "tasks.db", EditTaskRunner(tools))
        app = create_app(
            task_manager=manager,
            tryon_tools=tools,
            sessions_root=sessions,
            sync_vendor_env=False,
        )
        with TestClient(app) as client:
            bbox = client.get(
                "/api/edit/bbox", params={"image": "/api/files/source/first.png"}
            )
            assert bbox.status_code == 200, bbox.text
            assert len(bbox.json()["regions"]) == 2

            submitted = client.post(
                "/api/edit/replace",
                data={
                    "image": "/api/files/source/first.png",
                    "region": json.dumps(
                        {"x": 40, "y": 60, "width": 220, "height": 250}
                    ),
                    "description": "换成红色百褶裙",
                    "replace": "lower",
                    "other_images": json.dumps(["/api/files/source/second.png"]),
                },
                files={"garment_image": ("skirt.png", garment, "image/png")},
            )
            assert submitted.status_code == 202, submitted.text
            edited = wait_completed(client, submitted.json()["task_id"])
            assert len(edited["artifacts"]) == 2
            assert edited["state"]["edit_params"]["region"]["width"] == 220
            assert client.get(edited["artifacts"][0]).content.startswith(b"edited-lower")

            video_submit = client.post("/api/edit/video", json=edited["artifacts"])
            assert video_submit.status_code == 202, video_submit.text
            video = wait_completed(client, video_submit.json()["task_id"])
            assert client.get(video["artifacts"][0]).content == b"mock-video"
            print(json.dumps({"bbox": bbox.json(), "edit": edited, "video": video}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
