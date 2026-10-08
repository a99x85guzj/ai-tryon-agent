"""Pure routing decisions for the try-on graph."""

from __future__ import annotations

from typing import Any, Literal, Mapping


Mode = Literal["A", "B", "video", "partial"]


def resolve_mode(intent: Mapping[str, Any], *, aliyun_configured: bool) -> Mode:
    """Resolve the execution mode from structured LLM output and hard rules."""
    requested = intent.get("mode")
    if requested == "partial":
        return "partial"
    if requested == "video":
        return "video"
    if intent.get("angle_preset"):
        return "B"
    if int(intent.get("variants") or 1) > 1 or intent.get("scenes") or intent.get("colors"):
        return "B"
    if intent.get("has_garment_image") and aliyun_configured:
        return "A"
    return "B"


def normalize_intent(
    intent: Mapping[str, Any], *, aliyun_configured: bool
) -> dict[str, Any]:
    """Return a normalized copy without mutating the LLM result."""
    normalized = dict(intent)
    normalized["mode"] = resolve_mode(normalized, aliyun_configured=aliyun_configured)
    normalized["variants"] = max(1, int(normalized.get("variants") or 1))
    normalized["scenes"] = list(normalized.get("scenes") or [])
    normalized["colors"] = list(normalized.get("colors") or [])
    return normalized


def select_image_backend(
    intent: Mapping[str, Any],
    image_urls: Mapping[str, Any],
    *,
    jimeng_configured: bool,
) -> Literal["douban", "jimeng"]:
    """Reference images always force Doubao; Jimeng is text-only."""
    has_reference = bool(
        intent.get("has_garment_image")
        or intent.get("has_model_image")
        or image_urls.get("garment")
        or image_urls.get("model")
    )
    if has_reference:
        return "douban"
    return "jimeng" if jimeng_configured else "douban"


def needs_model_selection(intent: Mapping[str, Any]) -> bool:
    if intent.get("has_model_image"):
        return False
    return intent.get("mode") == "A" or bool(intent.get("angle_preset"))


def route_after_intent(state: Mapping[str, Any]) -> str:
    if state.get("error"):
        return "error_handler"
    intent = state.get("parsed_intent") or {}
    if not intent.get("garment_part"):
        return "ask_user"
    if intent.get("mode") == "video":
        return "prompt_engineer"
    if intent.get("has_garment_image"):
        return "garment_analyzer"
    if needs_model_selection(intent):
        return "model_selector"
    return "prompt_engineer"


def route_after_garment_analysis(state: Mapping[str, Any]) -> str:
    if state.get("error"):
        return "error_handler"
    if needs_model_selection(state.get("parsed_intent") or {}):
        return "model_selector"
    return "prompt_engineer"


def route_after_model_selection(state: Mapping[str, Any]) -> str:
    return "error_handler" if state.get("error") else "prompt_engineer"


def route_after_prompt_engineer(state: Mapping[str, Any]) -> str:
    return "error_handler" if state.get("error") else "plan_confirm"


def route_after_executor(state: Mapping[str, Any]) -> str:
    return "error_handler" if state.get("error") else "end"

