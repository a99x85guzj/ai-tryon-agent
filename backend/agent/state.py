"""Shared LangGraph state definitions."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    user_input: str
    parsed_intent: dict[str, Any]
    garment_info: dict[str, Any]
    model_candidates: list[dict[str, Any]]
    confirmed_plan: dict[str, Any]
    image_urls: dict[str, Any]
    session_dir: str
    error: str | None

