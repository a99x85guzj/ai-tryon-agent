"""Interactive phase-three LangGraph demo (mock tools by default)."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import Any, Mapping

from langchain_core.messages import BaseMessage
from langgraph.types import Command

from backend.agent.graph import build_graph
from backend.tools.tryon_tools import ToolResult, TryonTools


PROJECT_ROOT = Path(__file__).resolve().parent


class DemoLLM:
    """Deterministic structured-output stand-in for an API-free CLI demo."""

    async def parse_intent(self, state: Mapping[str, Any]) -> dict[str, Any]:
        history = " ".join(
            str(message.content) if isinstance(message, BaseMessage) else str(message)
            for message in state.get("messages") or []
        )
        text = f"{history} {state.get('user_input', '')}"
        garment_part = None
        if "上装" in text:
            garment_part = "top"
        elif "下装" in text:
            garment_part = "bottom"
        elif "连体" in text or "连衣" in text:
            garment_part = "one_piece"
        elif "整套" in text or "上下装" in text:
            garment_part = "full"

        variant_match = re.search(r"(\d+)\s*个", text)
        variants = int(variant_match.group(1)) if variant_match else 1
        mode = "video" if "视频" in text else "partial" if "局部" in text else "B"
        colors = re.findall(r"([黑白红橙黄绿蓝紫粉灰]色)", text)
        return {
            "mode": mode,
            "garment_part": garment_part,
            "scenes": [],
            "colors": colors,
            "variants": variants,
            "has_garment_image": False,
            "has_model_image": False,
            "replace": "upper",
            "get_bbox": False,
        }

    async def engineer_prompt(self, state: Mapping[str, Any]) -> dict[str, Any]:
        intent = state["parsed_intent"]
        colors = "、".join(intent.get("colors") or []) or "协调配色"
        return {
            "prompt": (
                f"亚洲电商模特穿着{colors}{intent['garment_part']}，纯白棚拍，"
                "全身构图，真实面料纹理，商品细节清晰"
            ),
            "consistency_constraints": [
                "所有变体锁定同一人物身份、脸部、体型与发型",
                "服装版型、纹理、图案和 Logo 保持一致",
            ],
        }


class DemoTools:
    """No-network tool double used unless --real-tools is explicitly selected."""

    async def get_session(self) -> ToolResult:
        session = PROJECT_ROOT / "data" / "sessions" / "task_demo_cli"
        session.mkdir(parents=True, exist_ok=True)
        return ToolResult(True, {"session_dir": str(session)}, 0.0, 0.0, "")

    async def generate_variants(self, *args, **kwargs) -> ToolResult:
        count = int(kwargs.get("variants", 1))
        return ToolResult(
            True,
            {"paths": [f"mock://variant-{index + 1}.png" for index in range(count)]},
            0.0,
            0.01,
            "",
        )


def _show_interrupt(payload: Mapping[str, Any]) -> None:
    if payload.get("type") == "ask_user":
        print(f"\n[ask_user 中断] {payload['question']}")
    else:
        print("\n[plan_confirm 中断] 即将执行以下方案：")
        print(json.dumps(payload.get("plan", {}), ensure_ascii=False, indent=2))


async def run_demo(*, real_tools: bool, initial_text: str | None = None) -> None:
    tools = TryonTools() if real_tools else DemoTools()
    graph = build_graph(
        tools=tools,
        llm=DemoLLM(),
        aliyun_configured=False,
        jimeng_configured=False,
    )
    text = initial_text.strip() if initial_text else input("请输入试衣需求：").strip()
    config = {"configurable": {"thread_id": f"demo-{uuid.uuid4()}"}}
    result = await graph.ainvoke(
        {"messages": [], "user_input": text, "image_urls": {}}, config
    )

    while result.get("__interrupt__"):
        payload = result["__interrupt__"][0].value
        _show_interrupt(payload)
        response = input("你的回复：").strip()
        result = await graph.ainvoke(Command(resume=response), config)

    print("\n[完成]")
    messages = result.get("messages") or []
    if messages:
        print(messages[-1].content)
    if result.get("image_urls", {}).get("results"):
        print("输出：", result["image_urls"]["results"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real-tools",
        action="store_true",
        help="使用真实工具层（可能调用付费 API）；默认使用 mock",
    )
    parser.add_argument("--text", help="初始需求；省略时从交互输入读取")
    args = parser.parse_args()
    asyncio.run(run_demo(real_tools=args.real_tools, initial_text=args.text))


if __name__ == "__main__":
    main()
