"""Deterministic no-network runtime for deployment smoke tests."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from backend.services.cost_ledger import CostLedger
from backend.services.task_manager import TaskRunResult
from backend.tools.tryon_tools import ToolResult


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class MockAgentRunner:
    async def __call__(self, task_id: str, state: dict[str, Any], resume=None):
        del task_id
        if resume is None:
            plan = {
                "mode": "B",
                "garment_part": "top",
                "variants": 1,
                "prompt": state.get("user_input", "mock try-on"),
                "image_backend": "mock",
            }
            return TaskRunResult(
                status="waiting_confirm",
                state={**state, "confirmed_plan": plan},
                interrupt={"type": "plan_confirm", "plan": plan},
            )
        output = Path(state["session_dir"]) / "mock_result.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(_PNG)
        return TaskRunResult(
            status="completed",
            state={
                **state,
                "pending_interrupt": None,
                "image_urls": {
                    **(state.get("image_urls") or {}),
                    "results": [str(output)],
                },
                "error": None,
            },
        )


class MockTryonTools:
    def __init__(self, data_root: Path) -> None:
        self.ledger = CostLedger(data_root / "cost_ledger.db")

    def _result(self, data: dict[str, Any]) -> ToolResult:
        return ToolResult(True, data, 0, 0.01, "")

    async def preprocess_garment(self, path, *, session_dir):
        return self._result({"path": str(path)})

    async def validate_model_image(self, path, *, session_dir):
        return self._result({"valid": True, "path": str(path)})

    async def list_models(self, *, session_dir):
        return self._result({"models": [{"name": "Mock 模特", "group": "测试"}]})

    async def partial_tryon(self, model, new_garment, replace, get_bbox=False, *, session_dir):
        if get_bbox:
            bbox = [20, 20, 180, 150] if replace == "upper" else [20, 145, 180, 300]
            return self._result({"bbox": bbox})
        output = Path(session_dir) / f"mock_{replace}_{Path(model).stem}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(_PNG)
        return self._result({"path": str(output)})

    async def generate_variants(self, *args, **kwargs):
        output = Path(kwargs["session_dir"]) / "mock_garment.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(_PNG)
        return self._result({"path": str(output)})

    async def generate_video(self, images, prompt, ratio, *, session_dir):
        output = Path(session_dir) / "mock_video.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mock-video")
        return self._result({"path": str(output)})

    async def upload_to_oss(self, path, *, session_dir):
        self.ledger.record_tool_call(
            tool_name="upload_to_oss", elapsed_seconds=0.01, success=True, cost=0
        )
        return self._result({"url": f"https://mock-oss.invalid/{Path(path).name}"})
