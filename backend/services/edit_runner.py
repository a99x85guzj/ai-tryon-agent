"""Durable edit/video task runners built on the upstream CLI tools."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Mapping

from backend.services.task_manager import TaskRunResult
from backend.tools.tryon_tools import ToolResult, TryonTools


VIDEO_LOCK_PROMPT = """full body always visible from head to toe throughout entire video,
model centered in frame with 10% margin top and bottom,
camera stays wide, no zoom in, no close-up, no cropping,
9:16 vertical format maintained,
smooth natural human motion only, physically realistic movement,
head and body rotate naturally — never reversed or counterclockwise,
no unnatural body distortion, no impossible physics,
clean white or light studio background, no black or dark background,
clothing details clearly visible throughout"""


class EditTaskRunner:
    def __init__(self, tools: TryonTools) -> None:
        self.tools = tools

    async def __call__(
        self, task_id: str, state: dict[str, Any], resume: Any = None
    ) -> TaskRunResult:
        del task_id, resume
        try:
            task_type = state.get("task_type")
            if task_type == "edit_bbox":
                return await self._bbox(state)
            if task_type == "edit_replace":
                return await self._replace(state)
            if task_type == "edit_video":
                return await self._video(state)
            raise ValueError(f"未知编辑任务类型：{task_type}")
        except Exception as exc:
            return TaskRunResult(
                status="failed", state={**state, "error": f"编辑任务失败：{exc}"}
            )

    async def _bbox(self, state: dict[str, Any]) -> TaskRunResult:
        source = str((state.get("source_images") or [""])[0])
        if not source:
            raise ValueError("缺少待识别图片")
        session_dir = str(state["session_dir"])
        upper, lower = await asyncio.gather(
            self.tools.partial_tryon(
                source, "", "upper", True, session_dir=session_dir
            ),
            self.tools.partial_tryon(
                source, "", "lower", True, session_dir=session_dir
            ),
        )
        regions: list[dict[str, Any]] = []
        total_cost = 0.0
        for part, result in (("upper", upper), ("lower", lower)):
            total_cost += result.cost
            if not result.ok:
                continue
            bbox = _extract_bbox(result.data, part)
            if bbox:
                x1, y1, x2, y2 = bbox
                regions.append(
                    {
                        "replace": part,
                        "bbox": bbox,
                        "x": x1,
                        "y": y1,
                        "width": x2 - x1,
                        "height": y2 - y1,
                    }
                )
        if not regions:
            raise RuntimeError(upper.stderr or lower.stderr or "未识别到服饰区域")
        return TaskRunResult(
            status="completed",
            state={**state, "regions": regions, "error": None},
            cost=total_cost,
        )

    async def _replace(self, state: dict[str, Any]) -> TaskRunResult:
        session_dir = str(state["session_dir"])
        images = [str(item) for item in state.get("source_images") or []]
        if not images:
            raise ValueError("至少需要一张待编辑图片")
        replacement = str(state.get("garment_image") or "")
        total_cost = 0.0
        if not replacement:
            description = str(state.get("description") or "").strip()
            if not description:
                raise ValueError("未上传替换服装时必须填写替换描述")
            generated = await self.tools.generate_variants(
                f"纯白背景单件服装商品图，无人物：{description}",
                image_backend="douban",
                variants=1,
                session_dir=session_dir,
            )
            total_cost += generated.cost
            replacement = _result_location(_require(generated, "生成替换服装参考图"))

        results: list[str] = []
        for source in images:
            result = await self.tools.partial_tryon(
                source,
                replacement,
                str(state.get("replace") or "upper"),
                False,
                session_dir=session_dir,
            )
            total_cost += result.cost
            results.append(_result_location(_require(result, "局部试穿")))

        next_state = {
            **state,
            "image_urls": {"results": results},
            "replacement_garment": replacement,
            "edit_params": {
                "region": state.get("region"),
                "description": state.get("description"),
                "replace": state.get("replace"),
                "sync_count": len(images),
            },
            "error": None,
        }
        return TaskRunResult(status="completed", state=next_state, cost=total_cost)

    async def _video(self, state: dict[str, Any]) -> TaskRunResult:
        images = [str(item) for item in state.get("source_images") or []]
        if not 1 <= len(images) <= 4:
            raise ValueError("展示视频必须选择 1 至 4 张图片")
        motion = (
            "model slowly turns clockwise to show front and side, smooth catwalk, "
            "bright clean studio background"
            if len(images) == 1
            else "each scene shows the complete outfit from head to toe, smooth transition, bright background"
        )
        prompt = f"{VIDEO_LOCK_PROMPT},\n{motion}"
        result = await self.tools.generate_video(
            images, prompt, "9:16", session_dir=str(state["session_dir"])
        )
        data = _require(result, "生成展示视频")
        return TaskRunResult(
            status="completed",
            state={
                **state,
                "video_prompt": prompt,
                "image_urls": {"results": [_result_location(data)]},
                "error": None,
            },
            cost=result.cost,
        )


class RoutedTaskRunner:
    """Dispatch persisted jobs while keeping one TaskManager/semaphore."""

    def __init__(self, graph_runner, edit_runner: EditTaskRunner) -> None:
        self.graph_runner = graph_runner
        self.edit_runner = edit_runner

    async def __call__(self, task_id: str, state: dict[str, Any], resume: Any = None):
        if str(state.get("task_type") or "").startswith("edit_"):
            return await self.edit_runner(task_id, state, resume)
        return await self.graph_runner(task_id, state, resume)


def _require(result: ToolResult, label: str) -> Mapping[str, Any]:
    if not result.ok:
        raise RuntimeError(result.stderr or result.data.get("error") or f"{label}失败")
    return result.data


def _result_location(data: Mapping[str, Any]) -> str:
    for key in ("url", "path", "output", "result", "video_url"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    for key in ("urls", "paths", "results"):
        value = data.get(key)
        if isinstance(value, list) and value:
            return str(value[0])
    stdout = str(data.get("stdout") or "")
    urls = re.findall(r"https?://[^\s]+", stdout)
    if urls:
        return urls[-1].rstrip(".,")
    for line in reversed(stdout.splitlines()):
        if ":" in line and (candidate := line.split(":", 1)[1].strip()):
            if Path(candidate).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".mp4"}:
                return candidate
    raise RuntimeError("工具成功但没有返回产物 URL/路径")


def _extract_bbox(data: Mapping[str, Any], part: str) -> list[float] | None:
    direct = data.get("bbox")
    if isinstance(direct, list) and len(direct) == 4:
        return [float(value) for value in direct]
    candidates = data.get("results") or data.get("result")
    if isinstance(candidates, list):
        for item in candidates:
            if isinstance(item, Mapping) and item.get("parse_type") == part:
                bbox = item.get("bbox")
                if isinstance(bbox, list) and len(bbox) == 4:
                    return [float(value) for value in bbox]
    stdout = str(data.get("stdout") or "")
    match = re.search(
        rf"{re.escape(part)}\s*:\s*\[\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*\]",
        stdout,
    )
    return [float(value) for value in match.groups()] if match else None
