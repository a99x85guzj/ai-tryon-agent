"""Publish completed local artifacts through the vendored OSS CLI wrapper."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from backend.services.task_manager import TaskRunResult


class ArtifactPublisher:
    def __init__(self, tools, *, enabled: bool | None = None) -> None:
        self.tools = tools
        self.enabled = (
            os.getenv("OSS_PUBLISH_ENABLED", "false").lower() in {"1", "true", "yes"}
            if enabled is None
            else enabled
        )

    async def publish(self, paths: list[str], *, session_dir: str) -> tuple[list[str], float]:
        if not self.enabled:
            return paths, 0.0
        urls: list[str] = []
        cost = 0.0
        for value in paths:
            if value.startswith(("http://", "https://")):
                urls.append(value)
                continue
            path = Path(value)
            if not path.is_file():
                raise RuntimeError(f"OSS 发布产物不存在：{value}")
            result = await self.tools.upload_to_oss(value, session_dir=session_dir)
            cost += result.cost
            if not result.ok:
                raise RuntimeError(result.stderr or "OSS 产物上传失败")
            urls.append(_extract_url(result.data))
        return urls, cost


class PublishingTaskRunner:
    """Decorator that replaces completed local result paths with OSS URLs."""

    def __init__(self, runner, publisher: ArtifactPublisher) -> None:
        self.runner = runner
        self.publisher = publisher

    async def __call__(self, task_id: str, state: dict[str, Any], resume=None):
        outcome = await self.runner(task_id, state, resume)
        if outcome.status != "completed":
            return outcome
        image_urls = dict(outcome.state.get("image_urls") or {})
        results = image_urls.get("results") or []
        if isinstance(results, str):
            results = [results]
        if not results:
            return outcome
        try:
            published, upload_cost = await self.publisher.publish(
                [str(item) for item in results],
                session_dir=str(outcome.state.get("session_dir") or state.get("session_dir")),
            )
        except Exception as exc:
            return TaskRunResult(
                status="failed",
                state={**outcome.state, "error": f"产物上传 OSS 失败：{exc}"},
                cost=outcome.cost,
            )
        return TaskRunResult(
            status="completed",
            state={
                **outcome.state,
                "local_cache_artifacts": [str(item) for item in results],
                "image_urls": {**image_urls, "results": published},
            },
            cost=outcome.cost + upload_cost,
        )


def _extract_url(data: dict[str, Any]) -> str:
    for key in ("url", "result", "output"):
        value = data.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    matches = re.findall(r"https?://\S+", str(data.get("stdout") or ""))
    if not matches:
        raise RuntimeError("OSS 工具成功但没有返回 URL")
    return matches[-1].rstrip(".,")
