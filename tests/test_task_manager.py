from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from backend.services.task_manager import TaskManager, TaskRunResult


class ImmediateRunner:
    async def __call__(self, task_id: str, state: dict, resume: Any = None):
        return TaskRunResult(
            "completed",
            {**state, "image_urls": {"results": [f"mock://{task_id}.png"]}},
            cost=0.25,
        )


@pytest.mark.asyncio
async def test_submit_persists_and_completes(tmp_path: Path) -> None:
    manager = TaskManager(tmp_path / "tasks.db", ImmediateRunner())
    manager.init_db()
    submitted = await manager.submit(
        {"user_input": "white shirt"}, tmp_path / "sessions" / "task-one"
    )
    assert submitted.status == "pending"

    completed = await manager.wait_for_status(
        submitted.task_id, {"completed"}, timeout=2
    )
    assert completed.cost == pytest.approx(0.25)
    assert completed.state["image_urls"]["results"][0].startswith("mock://")
    events = [event async for event in manager.stream_events(submitted.task_id)]
    assert [event["event"] for event in events] == [
        "submitted",
        "running",
        "completed",
    ]


@pytest.mark.asyncio
async def test_semaphore_limits_execution_to_two(tmp_path: Path) -> None:
    active = 0
    maximum = 0
    lock = asyncio.Lock()

    async def runner(task_id: str, state: dict, resume: Any = None):
        nonlocal active, maximum
        async with lock:
            active += 1
            maximum = max(maximum, active)
        await asyncio.sleep(0.08)
        async with lock:
            active -= 1
        return TaskRunResult("completed", state)

    manager = TaskManager(tmp_path / "tasks.db", runner, max_concurrency=2)
    manager.init_db()
    records = [
        await manager.submit(
            {"index": index}, tmp_path / "sessions" / f"task-{index}"
        )
        for index in range(5)
    ]
    await asyncio.gather(
        *(
            manager.wait_for_status(record.task_id, {"completed"}, timeout=3)
            for record in records
        )
    )
    assert maximum == 2


@pytest.mark.asyncio
async def test_cancel_stops_running_task(tmp_path: Path) -> None:
    started = asyncio.Event()

    async def runner(task_id: str, state: dict, resume: Any = None):
        started.set()
        await asyncio.Event().wait()
        return TaskRunResult("completed", state)

    manager = TaskManager(tmp_path / "tasks.db", runner)
    manager.init_db()
    record = await manager.submit({}, tmp_path / "sessions" / "cancel")
    await asyncio.wait_for(started.wait(), timeout=1)

    cancelled = await manager.cancel(record.task_id)
    assert cancelled.status == "failed"
    assert "取消" in cancelled.state["error"]
    events = [event async for event in manager.stream_events(record.task_id)]
    assert events[-1]["event"] == "cancelled"


@pytest.mark.asyncio
async def test_restart_recovers_pending_and_fails_orphaned_running(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "tasks.db"
    old = TaskManager(db_path, ImmediateRunner())
    old.init_db()
    pending = await old.submit(
        {"kind": "pending"},
        tmp_path / "sessions" / "pending",
        schedule=False,
    )
    running = await old.submit(
        {"kind": "running"},
        tmp_path / "sessions" / "running",
        schedule=False,
    )
    old._update_task(running.task_id, status="running")

    restarted = TaskManager(db_path, ImmediateRunner())
    restarted.init_db()
    summary = await restarted.recover_tasks()
    assert summary == {"rescheduled": 1, "failed_orphans": 1}

    completed = await restarted.wait_for_status(
        pending.task_id, {"completed"}, timeout=2
    )
    orphaned = restarted.get(running.task_id)
    assert completed.status == "completed"
    assert orphaned.status == "failed"
    assert orphaned.state["retryable"] is True


@pytest.mark.asyncio
async def test_waiting_task_can_be_confirmed_and_resumed(tmp_path: Path) -> None:
    resumes: list[Any] = []

    async def runner(task_id: str, state: dict, resume: Any = None):
        resumes.append(resume)
        if resume is None:
            return TaskRunResult(
                "waiting_confirm",
                state,
                interrupt={"type": "plan_confirm", "plan": {"mode": "B"}},
            )
        return TaskRunResult(
            "completed", {**state, "image_urls": {"results": ["mock://done.png"]}}
        )

    manager = TaskManager(tmp_path / "tasks.db", runner)
    manager.init_db()
    record = await manager.submit({}, tmp_path / "sessions" / "confirm")
    waiting = await manager.wait_for_status(
        record.task_id, {"waiting_confirm"}, timeout=2
    )
    assert waiting.state["pending_interrupt"]["type"] == "plan_confirm"

    await manager.confirm(record.task_id, {"changes": "blue"})
    completed = await manager.wait_for_status(
        record.task_id, {"completed"}, timeout=2
    )
    assert completed.status == "completed"
    assert resumes == [None, {"changes": "blue"}]

