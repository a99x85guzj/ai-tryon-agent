"""Construction of the interruptible LangGraph try-on workflow."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from backend.agent.nodes import AgentLLMGateway, AgentNodes, LangChainAgentLLM
from backend.agent.routing import (
    route_after_executor,
    route_after_garment_analysis,
    route_after_intent,
    route_after_model_selection,
    route_after_prompt_engineer,
)
from backend.agent.state import AgentState
from backend.tools.tryon_tools import TryonTools


def build_graph(
    *,
    tools: TryonTools | None = None,
    llm: AgentLLMGateway | None = None,
    checkpointer=None,
    aliyun_configured: bool | None = None,
    jimeng_configured: bool | None = None,
):
    load_dotenv(override=False)
    tools = tools or TryonTools()
    llm = llm or LangChainAgentLLM.from_env()
    nodes = AgentNodes(
        tools=tools,
        llm=llm,
        aliyun_configured=(
            bool(os.getenv("ALIYUN_API_KEY", "").strip())
            if aliyun_configured is None
            else aliyun_configured
        ),
        jimeng_configured=(
            bool(
                os.getenv("JIMENG_ACCESS_KEY", "").strip()
                and os.getenv("JIMENG_SECRET_KEY", "").strip()
            )
            if jimeng_configured is None
            else jimeng_configured
        ),
    )

    graph = StateGraph(AgentState)
    graph.add_node("intent_parser", nodes.intent_parser)
    graph.add_node("ask_user", nodes.ask_user)
    graph.add_node("garment_analyzer", nodes.garment_analyzer_node)
    graph.add_node("model_selector", nodes.model_selector)
    graph.add_node("prompt_engineer", nodes.prompt_engineer)
    graph.add_node("plan_confirm", nodes.plan_confirm)
    graph.add_node("executor", nodes.executor)
    graph.add_node("error_handler", nodes.error_handler)

    graph.add_edge(START, "intent_parser")
    graph.add_conditional_edges(
        "intent_parser",
        route_after_intent,
        {
            "ask_user": "ask_user",
            "garment_analyzer": "garment_analyzer",
            "model_selector": "model_selector",
            "prompt_engineer": "prompt_engineer",
            "error_handler": "error_handler",
        },
    )
    graph.add_conditional_edges(
        "ask_user",
        route_after_intent,
        {
            "ask_user": "ask_user",
            "garment_analyzer": "garment_analyzer",
            "model_selector": "model_selector",
            "prompt_engineer": "prompt_engineer",
            "error_handler": "error_handler",
        },
    )
    graph.add_conditional_edges(
        "garment_analyzer",
        route_after_garment_analysis,
        {
            "model_selector": "model_selector",
            "prompt_engineer": "prompt_engineer",
            "error_handler": "error_handler",
        },
    )
    graph.add_conditional_edges(
        "model_selector",
        route_after_model_selection,
        {"prompt_engineer": "prompt_engineer", "error_handler": "error_handler"},
    )
    graph.add_conditional_edges(
        "prompt_engineer",
        route_after_prompt_engineer,
        {"plan_confirm": "plan_confirm", "error_handler": "error_handler"},
    )
    graph.add_edge("plan_confirm", "executor")
    graph.add_conditional_edges(
        "executor",
        route_after_executor,
        {"error_handler": "error_handler", "end": END},
    )
    graph.add_edge("error_handler", END)
    return graph.compile(checkpointer=checkpointer or MemorySaver())
