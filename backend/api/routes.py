"""HTTP and SSE endpoints for asynchronous try-on tasks."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from backend.services.task_manager import (
    InvalidTaskTransitionError,
    TaskManager,
    TaskNotFoundError,
    TaskRecord,
)


router = APIRouter(prefix="/api")
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


class ConfirmRequest(BaseModel):
    action: Literal["approve", "modify"]
    payload: Any | None = None


class Region(BaseModel):
    x: float
    y: float
    width: float
    height: float


def _manager(request: Request) -> TaskManager:
    return request.app.state.task_manager


@router.post("/generate", status_code=202)
async def generate(
    request: Request,
    description: Annotated[str, Form(min_length=1)],
    garment_image: Annotated[UploadFile | None, File()] = None,
    model_image: Annotated[UploadFile | None, File()] = None,
) -> dict[str, Any]:
    task_id = uuid.uuid4().hex
    sessions_root: Path = request.app.state.sessions_root
    session_dir = sessions_root / f"task_{task_id}"
    upload_dir = session_dir / "uploads"
    image_urls: dict[str, Any] = {}
    if garment_image is not None:
        image_urls["garment"] = str(
            await _save_upload(garment_image, upload_dir, "garment")
        )
    if model_image is not None:
        image_urls["model"] = str(await _save_upload(model_image, upload_dir, "model"))

    state = {
        "messages": [],
        "user_input": description.strip(),
        "image_urls": image_urls,
        "session_dir": str(session_dir),
        "error": None,
    }
    record = await _manager(request).submit(
        state, session_dir, task_id=task_id, schedule=True
    )
    return {"task_id": record.task_id, "status": record.status}


@router.post("/preflight")
async def preflight_image(
    request: Request,
    image: Annotated[UploadFile, File()],
    kind: Annotated[Literal["garment", "model"], Form()],
) -> dict[str, Any]:
    """Run the relevant upstream CLI validation before a task is submitted."""
    session_dir = request.app.state.sessions_root / f"preflight_{uuid.uuid4().hex}"
    image_path = await _save_upload(image, session_dir / "uploads", kind)
    tools = request.app.state.tryon_tools
    if kind == "garment":
        result = await tools.preprocess_garment(
            str(image_path), session_dir=session_dir
        )
    else:
        result = await tools.validate_model_image(
            str(image_path), session_dir=session_dir
        )
    if not result.ok:
        detail = result.stderr or result.data.get("error") or "图片预检未通过"
        raise HTTPException(status_code=422, detail=detail)
    return {
        "ok": True,
        "kind": kind,
        "data": result.data,
        "elapsed": result.elapsed,
    }


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request) -> dict[str, Any]:
    return _task_response(_manager(request).get(task_id), request.app.state.sessions_root)


@router.post("/tasks/{task_id}/confirm", status_code=202)
async def confirm_task(
    task_id: str, body: ConfirmRequest, request: Request
) -> dict[str, Any]:
    if body.action == "approve":
        resume: Any = "确认"
        if isinstance(body.payload, dict):
            resume = {"approved": True, **body.payload}
    else:
        if body.payload is None or body.payload == "":
            raise HTTPException(status_code=422, detail="modify 操作必须提供 payload")
        resume = body.payload
    try:
        record = await _manager(request).confirm(task_id, resume)
    except InvalidTaskTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"task_id": record.task_id, "status": record.status}


@router.get("/tasks/{task_id}/events")
async def task_events(
    task_id: str,
    request: Request,
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    manager = _manager(request)
    manager.get(task_id)

    async def event_stream():
        async for event in manager.stream_events(task_id, after_id=last_event_id or 0):
            payload = json.dumps(event, ensure_ascii=False)
            yield f"id: {event['id']}\nevent: {event['event']}\ndata: {payload}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request) -> dict[str, Any]:
    record = await _manager(request).cancel(task_id)
    return _task_response(record, request.app.state.sessions_root)


@router.get("/models")
async def list_models(request: Request) -> dict[str, Any]:
    session_dir = request.app.state.sessions_root / "model_catalog"
    result = await request.app.state.tryon_tools.list_models(session_dir=session_dir)
    if not result.ok:
        raise HTTPException(
            status_code=502,
            detail=result.stderr or result.data.get("error") or "模特列表读取失败",
        )
    return {"models": result.data, "cost": result.cost}


@router.get("/stats/daily")
async def daily_stats(request: Request, day: str | None = None) -> dict[str, Any]:
    if day is not None:
        try:
            date.fromisoformat(day)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="day 必须为 YYYY-MM-DD") from exc
    ledger = getattr(request.app.state.tryon_tools, "ledger", None)
    if ledger is None:
        raise HTTPException(status_code=503, detail="费用账本未配置")
    return ledger.daily_stats(day)


@router.get("/edit/bbox")
async def edit_bbox(image: str, request: Request) -> dict[str, Any]:
    """Return upstream upper/lower garment boxes for a result image."""
    source = _resolve_edit_image(image, request.app.state.sessions_root)
    task_id = uuid.uuid4().hex
    session_dir = request.app.state.sessions_root / f"bbox_{task_id}"
    await _manager(request).submit(
        {
            "task_type": "edit_bbox",
            "source_images": [source],
            "session_dir": str(session_dir),
            "error": None,
        },
        session_dir,
        task_id=task_id,
        schedule=True,
    )
    try:
        record = await _manager(request).wait_for_status(
            task_id, {"completed", "failed"}, timeout=300
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="服饰区域识别超时") from exc
    if record.status == "failed":
        raise HTTPException(
            status_code=422, detail=record.state.get("error") or "未识别到服饰区域"
        )
    return {"task_id": task_id, "image": image, "regions": record.state["regions"]}


@router.post("/edit/replace", status_code=202)
async def edit_replace(
    request: Request,
    image: Annotated[str, Form(min_length=1)],
    region: Annotated[str, Form(min_length=1)],
    description: Annotated[str, Form()] = "",
    replace: Annotated[Literal["upper", "lower"], Form()] = "upper",
    other_images: Annotated[str, Form()] = "[]",
    garment_image: Annotated[UploadFile | None, File()] = None,
) -> dict[str, Any]:
    try:
        parsed_region = Region.model_validate_json(region)
        parsed_others = json.loads(other_images)
        if not isinstance(parsed_others, list):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="region 或 other_images 格式错误") from exc
    if not description.strip() and garment_image is None:
        raise HTTPException(status_code=422, detail="请填写替换描述或上传替换服装图")

    task_id = uuid.uuid4().hex
    session_dir = request.app.state.sessions_root / f"edit_{task_id}"
    garment_path = None
    if garment_image is not None:
        garment_path = await _save_upload(
            garment_image, session_dir / "uploads", "replacement"
        )
    source_images = [image, *[str(item) for item in parsed_others]]
    resolved = [
        _resolve_edit_image(item, request.app.state.sessions_root)
        for item in dict.fromkeys(source_images)
    ]
    state = {
        "task_type": "edit_replace",
        "source_images": resolved,
        "region": parsed_region.model_dump(),
        "description": description.strip(),
        "replace": replace,
        "garment_image": str(garment_path) if garment_path else None,
        "session_dir": str(session_dir),
        "error": None,
    }
    record = await _manager(request).submit(
        state, session_dir, task_id=task_id, schedule=True
    )
    return {"task_id": record.task_id, "status": record.status}


@router.post("/edit/video", status_code=202)
async def edit_video(request: Request, images: list[str]) -> dict[str, Any]:
    if not 1 <= len(images) <= 4:
        raise HTTPException(status_code=422, detail="请选择 1 至 4 张成品图")
    task_id = uuid.uuid4().hex
    session_dir = request.app.state.sessions_root / f"video_{task_id}"
    sources = [
        _resolve_edit_image(item, request.app.state.sessions_root)
        for item in images
    ]
    record = await _manager(request).submit(
        {
            "task_type": "edit_video",
            "source_images": sources,
            "session_dir": str(session_dir),
            "error": None,
        },
        session_dir,
        task_id=task_id,
        schedule=True,
    )
    return {"task_id": record.task_id, "status": record.status}


@router.get("/files/{path:path}")
async def local_file(path: str, request: Request) -> FileResponse:
    root = request.app.state.sessions_root.resolve()
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(target)


async def _save_upload(upload: UploadFile, directory: Path, prefix: str) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise HTTPException(status_code=415, detail="仅支持 JPG、PNG、WEBP 图片")
    if upload.content_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise HTTPException(status_code=415, detail="图片 MIME 类型不受支持")
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]", "_", Path(upload.filename or prefix).stem)
    destination = directory / f"{prefix}_{safe_stem}{suffix}"
    directory.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with destination.open("wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="图片不得超过 10MB")
                handle.write(chunk)
        if not _has_valid_image_signature(destination, suffix):
            raise HTTPException(status_code=415, detail="图片内容与声明格式不匹配")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return destination.resolve()


def _has_valid_image_signature(path: Path, suffix: str) -> bool:
    with path.open("rb") as handle:
        header = handle.read(12)
    if suffix in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if suffix == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == ".webp":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    return False


def _task_response(record: TaskRecord, sessions_root: Path) -> dict[str, Any]:
    state = record.state
    raw_artifacts = (state.get("image_urls") or {}).get("results") or []
    if isinstance(raw_artifacts, str):
        raw_artifacts = [raw_artifacts]
    artifacts = [_public_artifact(str(item), sessions_root) for item in raw_artifacts]
    return {
        **record.to_dict(),
        "artifacts": artifacts,
        "interrupt": state.get("pending_interrupt"),
        "error": state.get("error"),
    }


def _public_artifact(value: str, sessions_root: Path) -> str:
    if value.startswith(("http://", "https://", "mock://")):
        return value
    candidate = Path(value)
    if not candidate.is_absolute():
        return value
    try:
        relative = candidate.resolve().relative_to(sessions_root.resolve())
    except ValueError:
        return value
    return "/api/files/" + "/".join(relative.parts)


def _resolve_edit_image(value: str, sessions_root: Path) -> str:
    if value.startswith(("http://", "https://")):
        return value
    prefix = "/api/files/"
    if value.startswith(prefix):
        candidate = (sessions_root / value[len(prefix) :]).resolve()
    else:
        candidate = Path(value).resolve()
    try:
        candidate.relative_to(sessions_root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="图片不在可编辑会话目录中") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="待编辑图片不存在")
    return str(candidate)
