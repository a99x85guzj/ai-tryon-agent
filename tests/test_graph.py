from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest
from langgraph.types import Command

from backend.agent.graph import build_graph
from backend.tools.tryon_tools import ToolResult


def ok(data: dict | None = None, cost: float = 0.0) -> ToolResult:
    return ToolResult(True, data or {}, cost, 0.01, "")


class FakeLLM:
    def __init__(self, intent: dict[str, Any], *, fill_part_on_answer: bool = False):
        self.intent = intent
        self.fill_part_on_answer = fill_part_on_answer

    async def parse_intent(self, state: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(self.intent)
        if self.fill_part_on_answer and "上装" in str(state.get("user_input")):
            result["garment_part"] = "top"
        return result

    async def engineer_prompt(self, state: Mapping[str, Any]) -> dict[str, Any]:
        intent = state["parsed_intent"]
        return {
            "prompt": f"电商棚拍，服装部位 {intent['garment_part']}",
            "consistency_constraints": ["锁定商品细节"],
        }


class FakeTools:
    def __init__(self, tmp_path: Path):
        self.session = str(tmp_path / "sessions" / "task_fake")
        self.calls: list[tuple[str, tuple, dict]] = []

    def _call(self, name: str, args: tuple, kwargs: dict, result: ToolResult):
        self.calls.append((name, args, kwargs))
        return result

    async def get_session(self):
        return self._call("get_session", (), {}, ok({"session_dir": self.session}))

    async def analyze_garment(self, *args, **kwargs):
        return self._call(
            "analyze_garment",
            args,
            kwargs,
            ok({"description": "白色棉质 T 恤", "category": "top"}, 0.1),
        )

    async def recommend_models(self, *args, **kwargs):
        return self._call(
            "recommend_models",
            args,
            kwargs,
            ok({"candidates": [{"name": "柔妍", "path": "model-a.jpg"}]}),
        )

    async def list_models(self, *args, **kwargs):
        return self._call(
            "list_models",
            args,
            kwargs,
            ok(
                {
                    "candidates": [
                        {"name": "雅琪", "path": "model-b.jpg"},
                        {"name": "宇菲", "path": "model-c.jpg"},
                    ]
                }
            ),
        )

    async def preprocess_garment(self, *args, **kwargs):
        return self._call(
            "preprocess_garment", args, kwargs, ok({"path": "processed.jpg"})
        )

    async def upload_to_oss(self, *args, **kwargs):
        return self._call(
            "upload_to_oss", args, kwargs, ok({"url": "https://oss.test/g.jpg"})
        )

    async def run_tryon(self, *args, **kwargs):
        return self._call(
            "run_tryon", args, kwargs, ok({"paths": ["tryon-result.jpg"]}, 1.0)
        )

    async def generate_variants(self, *args, **kwargs):
        return self._call(
            "generate_variants",
            args,
            kwargs,
            ok({"paths": ["variant-1.jpg", "variant-2.jpg"]}, 0.4),
        )

    async def partial_tryon(self, *args, **kwargs):
        return self._call(
            "partial_tryon", args, kwargs, ok({"path": "partial-result.jpg"}, 0.8)
        )

    async def generate_video(self, *args, **kwargs):
        return self._call(
            "generate_video", args, kwargs, ok({"path": "result.mp4"}, 2.0)
        )


def interrupted(result: dict, expected_type: str) -> dict:
    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["type"] == expected_type
    return payload


async def confirm(graph, config: dict, initial: dict, tools: FakeTools):
    paused = await graph.ainvoke(initial, config)
    interrupted(paused, "plan_confirm")
    paid_calls = {"run_tryon", "generate_variants", "partial_tryon", "generate_video"}
    assert not any(name in paid_calls for name, _, _ in tools.calls)
    return await graph.ainvoke(Command(resume="确认"), config)


@pytest.mark.asyncio
async def test_text_only_variant_chain(tmp_path: Path) -> None:
    tools = FakeTools(tmp_path)
    graph = build_graph(
        tools=tools,
        llm=FakeLLM(
            {
                "mode": "B",
                "garment_part": "top",
                "variants": 3,
                "colors": ["红", "蓝", "绿"],
                "has_garment_image": False,
                "has_model_image": False,
            }
        ),
        aliyun_configured=False,
        jimeng_configured=True,
    )
    config = {"configurable": {"thread_id": "text-variant"}}
    result = await confirm(
        graph,
        config,
        {"messages": [], "user_input": "白色T恤出3个颜色", "image_urls": {}},
        tools,
    )

    call = next(item for item in tools.calls if item[0] == "generate_variants")
    assert call[2]["image_backend"] == "jimeng"
    assert call[2]["variants"] == 3
    assert result["image_urls"]["results"] == ["variant-1.jpg", "variant-2.jpg"]


@pytest.mark.asyncio
async def test_garment_image_mode_a_chain(tmp_path: Path) -> None:
    tools = FakeTools(tmp_path)
    graph = build_graph(
        tools=tools,
        llm=FakeLLM(
            {
                "mode": "B",
                "garment_part": "top",
                "variants": 1,
                "has_garment_image": True,
                "has_model_image": True,
            }
        ),
        aliyun_configured=True,
        jimeng_configured=False,
    )
    result = await confirm(
        graph,
        {"configurable": {"thread_id": "mode-a"}},
        {
            "messages": [],
            "user_input": "精准试穿这件上衣",
            "image_urls": {"garment": "garment.jpg", "model": "model.jpg"},
        },
        tools,
    )

    names = [name for name, _, _ in tools.calls]
    assert names[-3:] == ["preprocess_garment", "upload_to_oss", "run_tryon"]
    run_call = tools.calls[-1]
    assert run_call[1][:4] == (
        "https://oss.test/g.jpg",
        "model.jpg",
        "top",
        "qwen",
    )
    assert result["image_urls"]["results"] == ["tryon-result.jpg"]


@pytest.mark.asyncio
async def test_partial_replacement_chain(tmp_path: Path) -> None:
    tools = FakeTools(tmp_path)
    graph = build_graph(
        tools=tools,
        llm=FakeLLM(
            {
                "mode": "partial",
                "garment_part": "bottom",
                "replace": "lower",
                "has_garment_image": True,
                "has_model_image": True,
            }
        ),
        aliyun_configured=True,
        jimeng_configured=False,
    )
    result = await confirm(
        graph,
        {"configurable": {"thread_id": "partial"}},
        {
            "messages": [],
            "user_input": "只替换下装",
            "image_urls": {"garment": "skirt.jpg", "model": "model.jpg"},
        },
        tools,
    )

    call = next(item for item in tools.calls if item[0] == "partial_tryon")
    assert call[1][:4] == ("model.jpg", "skirt.jpg", "lower", False)
    assert result["image_urls"]["results"] == ["partial-result.jpg"]


@pytest.mark.asyncio
async def test_missing_part_then_plan_edit_resumes_both_interrupts(tmp_path: Path) -> None:
    tools = FakeTools(tmp_path)
    graph = build_graph(
        tools=tools,
        llm=FakeLLM(
            {
                "mode": "B",
                "garment_part": None,
                "variants": 3,
                "colors": ["红", "蓝", "绿"],
                "has_garment_image": False,
                "has_model_image": False,
            },
            fill_part_on_answer=True,
        ),
        aliyun_configured=False,
        jimeng_configured=False,
    )
    config = {"configurable": {"thread_id": "interrupt-resume"}}
    first = await graph.ainvoke(
        {"messages": [], "user_input": "这件白色T恤出3个颜色", "image_urls": {}},
        config,
    )
    interrupted(first, "ask_user")

    second = await graph.ainvoke(Command(resume="上装"), config)
    plan_payload = interrupted(second, "plan_confirm")
    assert plan_payload["plan"]["garment_part"] == "top"

    final = await graph.ainvoke(Command(resume="把其中一个颜色改成海军蓝"), config)
    call = next(item for item in tools.calls if item[0] == "generate_variants")
    assert "海军蓝" in call[1][0]
    assert call[2]["variants"] == 3
    assert final["error"] is None
