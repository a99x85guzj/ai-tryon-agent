"""Adapter from a compiled LangGraph to the durable task-manager runner API."""

from __future__ import annotations

from typing import Any, Callable

from langgraph.types import Command

from backend.services.task_manager import TaskRunResult


class LangGraphTaskRunner:
    def __init__(self, graph_factory: Callable[[], Any]) -> None:
        self.graph_factory = graph_factory
        self._graph = None

    @property
    def graph(self):
        if self._graph is None:
            self._graph = self.graph_factory()
        return self._graph

    async def __call__(
        self,
        task_id: str,
        state: dict[str, Any],
        resume: Any | None = None,
    ) -> TaskRunResult:
        config = {"configurable": {"thread_id": task_id}}
        graph_input = Command(resume=resume) if resume is not None else state
        result = await self.graph.ainvoke(graph_input, config)

        interrupts = result.get("__interrupt__") or []
        if interrupts:
            interrupt_value = interrupts[0].value
            snapshot = await self.graph.aget_state(config)
            snapshot_state = dict(snapshot.values)
            return TaskRunResult(
                status="waiting_confirm",
                state=snapshot_state,
                cost=0.0,
                interrupt=interrupt_value,
            )

        state_result = dict(result)
        state_result.pop("__interrupt__", None)
        cost = float((state_result.get("confirmed_plan") or {}).get("actual_cost") or 0)
        status = "failed" if state_result.get("error") else "completed"
        return TaskRunResult(status=status, state=state_result, cost=cost)

