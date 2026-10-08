"""LangGraph nodes and injectable LLM integration."""

from __future__ import annotations

import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from backend.agent.routing import normalize_intent, select_image_backend
from backend.agent.state import AgentState
from backend.tools.tryon_tools import ToolResult, TryonTools


class IntentOutput(BaseModel):
    mode: Literal["A", "B", "video", "partial"] = "B"
    garment_part: Literal["top", "bottom", "one_piece", "full"] | None = None
    scenes: list[str] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=list)
    variants: int = Field(default=1, ge=1, le=9)
    has_garment_image: bool = False
    has_model_image: bool = False
    angle_preset: str | None = None
    replace: Literal["upper", "lower"] = "upper"
    get_bbox: bool = False


class PromptOutput(BaseModel):
    prompt: str
    consistency_constraints: list[str] = Field(default_factory=list)


class AgentLLMGateway(Protocol):
    async def parse_intent(self, state: Mapping[str, Any]) -> dict[str, Any]: ...

    async def engineer_prompt(self, state: Mapping[str, Any]) -> dict[str, Any]: ...


class LangChainAgentLLM:
    """Production structured-output gateway backed by ChatOpenAI."""

    def __init__(self, llm: ChatOpenAI) -> None:
        self.intent_llm = llm.with_structured_output(IntentOutput)
        self.prompt_llm = llm.with_structured_output(PromptOutput)

    @classmethod
    def from_env(cls) -> "LangChainAgentLLM":
        model = os.getenv("AGENT_LLM_MODEL", "").strip()
        if not model:
            raise RuntimeError("AGENT_LLM_MODEL is required to build the production graph")
        return cls(
            ChatOpenAI(
                model=model,
                api_key=os.getenv("OPENAI_API_KEY") or None,
                base_url=os.getenv("OPENAI_BASE_URL") or None,
                temperature=0,
            )
        )

    async def parse_intent(self, state: Mapping[str, Any]) -> dict[str, Any]:
        history = _history_text(state.get("messages") or [])
        response = await self.intent_llm.ainvoke(
            [
                SystemMessage(
                    content=(
                        "你是电商虚拟试衣意图解析器。严格提取模式、服装部位、场景、"
                        "颜色、变体数量、图片存在性、多角度预设和局部替换部位。"
                        "服装部位不明确时必须返回 null，禁止猜测默认值。"
                    )
                ),
                HumanMessage(
                    content=f"历史：\n{history}\n当前输入：{state.get('user_input', '')}"
                ),
            ]
        )
        return response.model_dump() if isinstance(response, BaseModel) else dict(response)

    async def engineer_prompt(self, state: Mapping[str, Any]) -> dict[str, Any]:
        response = await self.prompt_llm.ainvoke(
            [
                SystemMessage(
                    content=(
                        "你是电商试衣提示词工程师。生成清晰的结构化摄影提示词，并给出"
                        "人物身份、脸部、体型、服装颜色/版型/纹理/Logo、构图和光线的"
                        "一致性锁定约束。不得改变参考图里的商品细节。"
                    )
                ),
                HumanMessage(
                    content=(
                        f"意图：{state.get('parsed_intent', {})}\n"
                        f"服装信息：{state.get('garment_info', {})}\n"
                        f"候选模特：{state.get('model_candidates', [])}"
                    )
                ),
            ]
        )
        return response.model_dump() if isinstance(response, BaseModel) else dict(response)


def _history_text(messages: list[Any]) -> str:
    parts = []
    for message in messages:
        if isinstance(message, BaseMessage):
            parts.append(str(message.content))
        else:
            parts.append(str(message))
    return "\n".join(parts[-10:])


def _merge_followup_intent(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    """Preserve prior fields when a follow-up only answers a clarification."""
    merged = dict(current)
    if not previous:
        return merged
    for field in ("scenes", "colors"):
        if not merged.get(field) and previous.get(field):
            merged[field] = previous[field]
    if int(merged.get("variants") or 1) == 1 and int(previous.get("variants") or 1) > 1:
        merged["variants"] = previous["variants"]
    for field in ("angle_preset", "replace"):
        if not merged.get(field) and previous.get(field):
            merged[field] = previous[field]
    if merged.get("mode") == "B" and previous.get("mode") in {"video", "partial"}:
        merged["mode"] = previous["mode"]
    return merged


class ToolExecutionError(RuntimeError):
    pass


class AgentNodes:
    def __init__(
        self,
        *,
        tools: TryonTools,
        llm: AgentLLMGateway,
        aliyun_configured: bool,
        jimeng_configured: bool,
    ) -> None:
        self.tools = tools
        self.llm = llm
        self.aliyun_configured = aliyun_configured
        self.jimeng_configured = jimeng_configured

    async def intent_parser(self, state: AgentState) -> dict[str, Any]:
        try:
            session_dir = state.get("session_dir")
            if not session_dir:
                session = await self.tools.get_session()
                session_dir = _require(session, "创建会话").get("session_dir")
                if not session_dir:
                    raise ToolExecutionError("会话工具未返回 session_dir")

            raw = await self.llm.parse_intent(state)
            raw = _merge_followup_intent(state.get("parsed_intent") or {}, raw)
            image_urls = state.get("image_urls") or {}
            raw["has_garment_image"] = bool(
                image_urls.get("garment") or raw.get("has_garment_image")
            )
            raw["has_model_image"] = bool(
                image_urls.get("model") or raw.get("has_model_image")
            )
            intent = normalize_intent(raw, aliyun_configured=self.aliyun_configured)
            return {
                "messages": [HumanMessage(content=state.get("user_input", ""))],
                "parsed_intent": intent,
                "session_dir": str(session_dir),
                "error": None,
            }
        except Exception as exc:
            return {"error": f"意图解析失败：{exc}"}

    async def garment_analyzer_node(self, state: AgentState) -> dict[str, Any]:
        garment = (state.get("image_urls") or {}).get("garment")
        if not garment:
            return {"error": "已判断有服装图，但状态中没有服装图片 URL/路径。"}
        result = await self.tools.analyze_garment(
            str(garment), session_dir=state["session_dir"]
        )
        if not result.ok:
            return {"error": _tool_error("服装分析", result)}
        return {"garment_info": result.data, "error": None}

    async def model_selector(self, state: AgentState) -> dict[str, Any]:
        intent = state.get("parsed_intent") or {}
        garment = state.get("garment_info") or {}
        description = str(
            garment.get("description")
            or " ".join(intent.get("scenes") or [])
            or state.get("user_input", "")
        )
        recommended = await self.tools.recommend_models(
            description, session_dir=state["session_dir"]
        )
        if not recommended.ok:
            return {"error": _tool_error("模特推荐", recommended)}
        candidates = _extract_candidates(recommended.data)

        if len(candidates) < 2:
            listed = await self.tools.list_models(session_dir=state["session_dir"])
            if listed.ok:
                candidates.extend(_extract_candidates(listed.data))

        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in candidates:
            marker = str(candidate.get("path") or candidate.get("name") or candidate)
            if marker not in seen:
                seen.add(marker)
                unique.append(candidate)
            if len(unique) == 3:
                break
        if not unique:
            return {"error": "模特推荐没有返回可用候选。"}
        return {"model_candidates": unique, "error": None}

    async def prompt_engineer(self, state: AgentState) -> dict[str, Any]:
        try:
            generated = await self.llm.engineer_prompt(state)
            prompt = str(generated.get("prompt") or "").strip()
            if not prompt:
                raise ValueError("LLM 未返回提示词")
            constraints = list(generated.get("consistency_constraints") or [])
            required = [
                "锁定同一模特的身份、脸部、发型和体型",
                "锁定服装颜色、版型、纹理、图案与 Logo，不得自由改款",
                "保持明亮电商棚拍光线与完整人体构图",
            ]
            for item in required:
                if item not in constraints:
                    constraints.append(item)

            intent = state.get("parsed_intent") or {}
            backend = select_image_backend(
                intent,
                state.get("image_urls") or {},
                jimeng_configured=self.jimeng_configured,
            )
            plan = {
                "mode": intent.get("mode"),
                "garment_part": intent.get("garment_part"),
                "prompt": prompt,
                "consistency_constraints": constraints,
                "variants": intent.get("variants", 1),
                "scenes": intent.get("scenes", []),
                "colors": intent.get("colors", []),
                "angle_preset": intent.get("angle_preset"),
                "image_backend": backend,
                "model_candidates": state.get("model_candidates") or [],
            }
            return {"confirmed_plan": plan, "error": None}
        except Exception as exc:
            return {"error": f"提示词生成失败：{exc}"}

    def ask_user(self, state: AgentState) -> dict[str, Any]:
        question = "请确认服装部位：上装、下装、连体单件，还是上下装整套？"
        answer = interrupt(
            {"type": "ask_user", "question": question, "reason": "garment_part_missing"}
        )
        if isinstance(answer, Mapping):
            answer = answer.get("answer") or answer.get("response") or ""
        answer_text = str(answer).strip()
        original = state.get("user_input", "").strip()
        combined_input = (
            f"{original}\n用户补充：{answer_text}" if original else answer_text
        )
        intent = dict(state.get("parsed_intent") or {})
        garment_part = _parse_garment_part(answer_text)
        if garment_part:
            intent["garment_part"] = garment_part
        return {
            "messages": [AIMessage(content=question)],
            "user_input": combined_input,
            "parsed_intent": intent,
            "error": None,
        }

    def plan_confirm(self, state: AgentState) -> dict[str, Any]:
        plan = deepcopy(state.get("confirmed_plan") or {})
        decision = interrupt(
            {
                "type": "plan_confirm",
                "message": "以下方案会调用可能产生费用的模型。请确认或直接输入修改要求。",
                "plan": plan,
            }
        )
        if isinstance(decision, Mapping):
            if decision.get("cancelled") or decision.get("approved") is False:
                return {"error": "用户取消了执行方案。"}
            selected_model = decision.get("selected_model")
            if selected_model:
                candidates = list(state.get("model_candidates") or [])
                candidates.sort(
                    key=lambda item: 0
                    if str(item.get("path") or item.get("name")) == str(selected_model)
                    else 1
                )
                plan["model_candidates"] = candidates
            if decision.get("prompt"):
                plan["prompt"] = str(decision["prompt"])
            if decision.get("changes"):
                plan["user_modification"] = str(decision["changes"])
                plan["prompt"] += f"\n用户修改要求：{decision['changes']}"
        else:
            text = str(decision).strip()
            if text.lower() not in {"确认", "同意", "执行", "yes", "y", "ok"}:
                plan["user_modification"] = text
                plan["prompt"] += f"\n用户修改要求：{text}"
        return {
            "messages": [AIMessage(content=_format_plan(plan))],
            "confirmed_plan": plan,
            "model_candidates": plan.get(
                "model_candidates", state.get("model_candidates") or []
            ),
            "error": None,
        }

    async def executor(self, state: AgentState) -> dict[str, Any]:
        if state.get("error"):
            return {}
        plan = state.get("confirmed_plan") or {}
        intent = state.get("parsed_intent") or {}
        images = dict(state.get("image_urls") or {})
        session_dir = state["session_dir"]
        total_cost = 0.0
        try:
            mode = plan.get("mode")
            if mode == "A":
                garment = _required_image(images, "garment")
                model = _model_image(images, state.get("model_candidates") or [])
                preprocessed = await self.tools.preprocess_garment(
                    garment, session_dir=session_dir
                )
                total_cost += preprocessed.cost
                processed_path = _result_location(_require(preprocessed, "服装预处理"))
                uploaded = await self.tools.upload_to_oss(
                    processed_path, session_dir=session_dir
                )
                total_cost += uploaded.cost
                garment_url = _result_location(_require(uploaded, "OSS 上传"))
                result = await self.tools.run_tryon(
                    garment_url,
                    model,
                    str(plan["garment_part"]),
                    "qwen",
                    session_dir=session_dir,
                )
            elif mode == "B":
                result = await self.tools.generate_variants(
                    str(plan["prompt"]),
                    image_backend=str(plan["image_backend"]),
                    variants=int(plan.get("variants") or 1),
                    angle_preset=plan.get("angle_preset"),
                    garment_img=images.get("garment"),
                    model_img=images.get("model") or _optional_model(state),
                    session_dir=session_dir,
                )
            elif mode == "partial":
                result = await self.tools.partial_tryon(
                    _required_image(images, "model"),
                    _required_image(images, "garment"),
                    intent.get("replace", "upper"),
                    bool(intent.get("get_bbox")),
                    session_dir=session_dir,
                )
            elif mode == "video":
                video_images = _video_images(images)
                result = await self.tools.generate_video(
                    video_images,
                    str(plan["prompt"]),
                    session_dir=session_dir,
                )
            else:
                raise ValueError(f"不支持的执行模式：{mode}")

            total_cost += result.cost
            data = _require(result, f"执行模式 {mode}")
            locations = _result_locations(data)
            if locations:
                images["results"] = locations
            return {
                "messages": [
                    AIMessage(
                        content=f"任务执行完成，模式 {mode}，估算费用 ¥{total_cost:.4f}。"
                    )
                ],
                "confirmed_plan": {**plan, "actual_cost": total_cost},
                "image_urls": images,
                "error": None,
            }
        except Exception as exc:
            return {"error": f"执行失败：{exc}"}

    def error_handler(self, state: AgentState) -> dict[str, Any]:
        detail = state.get("error") or "未知错误"
        return {
            "messages": [
                AIMessage(
                    content=(
                        f"抱歉，本次试衣任务未能完成：{detail}\n"
                        "请检查图片、服务配置与账户额度后重试；未确认的付费步骤不会执行。"
                    )
                )
            ]
        }


def _require(result: ToolResult, label: str) -> dict[str, Any]:
    if not result.ok:
        raise ToolExecutionError(_tool_error(label, result))
    return result.data


def _tool_error(label: str, result: ToolResult) -> str:
    detail = result.stderr or result.data.get("error") or "未知错误"
    return f"{label}失败：{detail}"


def _extract_candidates(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    structured = data.get("candidates") or data.get("models")
    if isinstance(structured, list):
        return [dict(item) if isinstance(item, Mapping) else {"name": str(item)} for item in structured]
    stdout = str(data.get("stdout") or "")
    candidates: list[dict[str, Any]] = []
    pending_name = ""
    for line in stdout.splitlines():
        name_match = re.search(r"(?:推荐模特：|[👩👨]\s*)([^（(\s]+)", line)
        if name_match:
            name = name_match.group(1).strip()
            if name.endswith("模特："):
                continue
            if pending_name:
                candidates.append({"name": pending_name})
            pending_name = name
            continue
        path_match = re.search(r"(?:图片路径：|路径:?)\s*(.+)$", line)
        if path_match:
            candidates.append({"name": pending_name or "候选模特", "path": path_match.group(1).strip()})
            pending_name = ""
    if pending_name:
        candidates.append({"name": pending_name})
    return candidates


def _format_plan(plan: Mapping[str, Any]) -> str:
    return (
        f"执行方案：模式 {plan.get('mode')}，服装部位 {plan.get('garment_part')}，"
        f"变体 {plan.get('variants', 1)} 张。\n提示词：{plan.get('prompt', '')}"
    )


def _parse_garment_part(answer: str) -> str | None:
    lowered = answer.lower()
    if any(token in lowered for token in ("上装", "上衣", "top")):
        return "top"
    if any(token in lowered for token in ("下装", "裤", "裙", "bottom", "lower")):
        return "bottom"
    if any(token in lowered for token in ("连体", "连衣", "one_piece", "one-piece")):
        return "one_piece"
    if any(token in lowered for token in ("整套", "上下装", "全套", "full")):
        return "full"
    return None


def _required_image(images: Mapping[str, Any], key: str) -> str:
    value = images.get(key)
    if not value:
        raise ValueError(f"缺少 {key} 图片 URL/路径")
    return str(value)


def _model_image(images: Mapping[str, Any], candidates: list[dict[str, Any]]) -> str:
    if images.get("model"):
        return str(images["model"])
    for candidate in candidates:
        if candidate.get("path"):
            return str(candidate["path"])
    raise ValueError("精准试穿需要模特图片，但候选中没有可用路径")


def _optional_model(state: Mapping[str, Any]) -> str | None:
    for candidate in state.get("model_candidates") or []:
        if candidate.get("path"):
            return str(candidate["path"])
    return None


def _result_location(data: Mapping[str, Any]) -> str:
    for key in ("url", "path", "output", "result"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    stdout = str(data.get("stdout") or "")
    urls = re.findall(r"https?://\S+", stdout)
    if urls:
        return urls[-1].rstrip(".,")
    for line in reversed(stdout.splitlines()):
        if ":" in line:
            value = line.split(":", 1)[1].strip()
            if value:
                return value
    raise ToolExecutionError("工具成功但没有返回输出 URL/路径")


def _result_locations(data: Mapping[str, Any]) -> list[str]:
    value = data.get("urls") or data.get("paths") or data.get("results")
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        return [_result_location(data)]
    except ToolExecutionError:
        return []


def _video_images(images: Mapping[str, Any]) -> list[str]:
    results = images.get("results")
    if isinstance(results, list) and results:
        return [str(item) for item in results]
    return [str(value) for key in ("model", "garment") if (value := images.get(key))]
