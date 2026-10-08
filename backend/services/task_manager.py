"""Durable single-process task scheduling and SSE event persistence.

This manager deliberately assumes ``uvicorn --workers 1``. The semaphore and
``asyncio.Task`` registry live in process memory; multiple workers would split
both across processes even though they share the SQLite file. Production
multi-worker deployments must move execution and concurrency limits to an
external queue such as Redis/Celery/Arq.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, Protocol

from langchain_core.messages import BaseMessage, message_to_dict


TaskStatus = Literal[
    "pending", "running", "waiting_confirm", "completed", "failed"
]
TERMINAL_STATUSES = {"completed", "failed"}
SSE_STOP_STATUSES = {"waiting_confirm", *TERMINAL_STATUSES}


class TaskNotFoundError(LookupError):
    pass


class InvalidTaskTransitionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TaskRecord:
    task_id: str
    state: dict[str, Any]
    status: TaskStatus
    session_dir: str
    cost: float
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TaskRunResult:
    status: Literal["waiting_confirm", "completed", "failed"]
    state: dict[str, Any]
    cost: float = 0.0
    interrupt: dict[str, Any] | None = None


class TaskRunner(Protocol):
    async def __call__(
        self,
        task_id: str,
        state: dict[str, Any],
        resume: Any | None = None,
    ) -> TaskRunResult: ...


class TaskManager:
    def __init__(
        self,
        db_path: str | Path,
        runner: TaskRunner,
        *,
        max_concurrency: int = 2,
        event_poll_seconds: float = 0.05,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self.db_path = Path(db_path)
        self.runner = runner
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.event_poll_seconds = event_poll_seconds
        self._active: dict[str, asyncio.Task[None]] = {}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    state_snapshot TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'pending', 'running', 'waiting_confirm', 'completed', 'failed'
                    )),
                    session_dir TEXT NOT NULL,
                    cost REAL NOT NULL DEFAULT 0 CHECK(cost >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_events_task "
                "ON task_events(task_id, id)"
            )

    async def submit(
        self,
        state: dict[str, Any],
        session_dir: str | Path,
        *,
        task_id: str | None = None,
        schedule: bool = True,
    ) -> TaskRecord:
        task_id = task_id or uuid.uuid4().hex
        timestamp = _utc_now()
        session = str(Path(session_dir).resolve())
        Path(session).mkdir(parents=True, exist_ok=True)
        snapshot = dict(state)
        snapshot["session_dir"] = session
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO tasks
                    (task_id, state_snapshot, status, session_dir, cost, created_at, updated_at)
                VALUES (?, ?, 'pending', ?, 0, ?, ?)
                """,
                (task_id, _dumps(snapshot), session, timestamp, timestamp),
            )
        self._append_event(task_id, "submitted", "pending", {"progress": 0})
        if schedule:
            self._schedule(task_id)
        return self.get(task_id)

    def get(self, task_id: str) -> TaskRecord:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise TaskNotFoundError(task_id)
        return _row_to_record(row)

    def list_by_status(self, statuses: set[str]) -> list[TaskRecord]:
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM tasks WHERE status IN ({placeholders}) ORDER BY created_at",
                tuple(sorted(statuses)),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    async def confirm(self, task_id: str, resume: Any) -> TaskRecord:
        record = self.get(task_id)
        if record.status != "waiting_confirm":
            raise InvalidTaskTransitionError(
                f"Task {task_id} is {record.status}, not waiting_confirm"
            )
        state = dict(record.state)
        state.pop("pending_interrupt", None)
        self._update_task(task_id, status="pending", state=state)
        self._append_event(task_id, "confirmation_received", "pending", {"progress": 55})
        self._schedule(task_id, resume=resume)
        return self.get(task_id)

    async def cancel(self, task_id: str) -> TaskRecord:
        record = self.get(task_id)
        if record.status in TERMINAL_STATUSES:
            return record
        active = self._active.get(task_id)
        if active and not active.done():
            active.cancel()
            try:
                await active
            except asyncio.CancelledError:
                pass
        else:
            state = {**record.state, "error": "任务已由用户取消，可重新提交。"}
            self._update_task(task_id, status="failed", state=state)
            self._append_event(task_id, "cancelled", "failed", {"progress": 100})
        return self.get(task_id)

    async def recover_tasks(self) -> dict[str, int]:
        """Reschedule pending work and fail orphaned in-process running work."""
        pending = self.list_by_status({"pending"})
        running = self.list_by_status({"running"})
        for record in running:
            state = {
                **record.state,
                "error": "服务重启时任务仍为 running，原执行进程已不存在；任务可重试。",
                "retryable": True,
            }
            self._update_task(record.task_id, status="failed", state=state)
            self._append_event(
                record.task_id,
                "recovery_failed",
                "failed",
                {"progress": 100, "retryable": True},
            )
        for record in pending:
            self._schedule(record.task_id)
        return {"rescheduled": len(pending), "failed_orphans": len(running)}

    async def shutdown(self) -> None:
        tasks = [task for task in self._active.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def wait_for_status(
        self,
        task_id: str,
        statuses: set[str],
        *,
        timeout: float = 5.0,
    ) -> TaskRecord:
        async def poll() -> TaskRecord:
            while True:
                record = self.get(task_id)
                if record.status in statuses:
                    return record
                await asyncio.sleep(self.event_poll_seconds)

        return await asyncio.wait_for(poll(), timeout=timeout)

    async def stream_events(
        self, task_id: str, *, after_id: int = 0
    ) -> AsyncIterator[dict[str, Any]]:
        self.get(task_id)
        last_id = max(0, after_id)
        while True:
            events = self._events_after(task_id, last_id)
            for event in events:
                last_id = event["id"]
                yield event
                if event["status"] in SSE_STOP_STATUSES:
                    return
            record = self.get(task_id)
            if record.status in SSE_STOP_STATUSES and not events:
                return
            await asyncio.sleep(self.event_poll_seconds)

    def _schedule(self, task_id: str, *, resume: Any | None = None) -> None:
        active = self._active.get(task_id)
        if active and not active.done():
            raise InvalidTaskTransitionError(f"Task {task_id} is already scheduled")
        task = asyncio.create_task(
            self._execute(task_id, resume=resume), name=f"tryon-task-{task_id}"
        )
        self._active[task_id] = task
        task.add_done_callback(lambda _: self._active.pop(task_id, None))

    async def _execute(self, task_id: str, *, resume: Any | None = None) -> None:
        try:
            async with self.semaphore:
                record = self.get(task_id)
                if record.status not in {"pending", "waiting_confirm"}:
                    return
                self._update_task(task_id, status="running")
                self._append_event(task_id, "running", "running", {"progress": 10})
                try:
                    outcome = await self.runner(task_id, record.state, resume)
                except asyncio.CancelledError:
                    current = self.get(task_id)
                    state = {**current.state, "error": "任务已由用户取消，可重新提交。"}
                    self._update_task(task_id, status="failed", state=state)
                    self._append_event(
                        task_id, "cancelled", "failed", {"progress": 100}
                    )
                    raise
                except Exception as exc:
                    outcome = TaskRunResult(
                        status="failed",
                        state={**record.state, "error": f"后台执行异常：{exc}"},
                    )

                state = dict(outcome.state)
                if outcome.interrupt:
                    state["pending_interrupt"] = outcome.interrupt
                total_cost = self.get(task_id).cost + max(0.0, outcome.cost)
                self._update_task(
                    task_id,
                    status=outcome.status,
                    state=state,
                    cost=total_cost,
                )
                progress = 50 if outcome.status == "waiting_confirm" else 100
                event_type = {
                    "waiting_confirm": "waiting_confirm",
                    "completed": "completed",
                    "failed": "failed",
                }[outcome.status]
                event_data: dict[str, Any] = {"progress": progress}
                if outcome.interrupt:
                    event_data["interrupt"] = outcome.interrupt
                self._append_event(task_id, event_type, outcome.status, event_data)
        finally:
            pass

    def _update_task(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        state: dict[str, Any] | None = None,
        cost: float | None = None,
    ) -> None:
        assignments = ["updated_at = ?"]
        values: list[Any] = [_utc_now()]
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if state is not None:
            assignments.append("state_snapshot = ?")
            values.append(_dumps(state))
        if cost is not None:
            assignments.append("cost = ?")
            values.append(cost)
        values.append(task_id)
        with self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE tasks SET {', '.join(assignments)} WHERE task_id = ?", values
            )
            if cursor.rowcount != 1:
                raise TaskNotFoundError(task_id)

    def _append_event(
        self,
        task_id: str,
        event_type: str,
        status: str,
        data: dict[str, Any],
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO task_events(task_id, event_type, status, data, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, event_type, status, _dumps(data), _utc_now()),
            )

    def _events_after(self, task_id: str, last_id: int) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, event_type, status, data, created_at
                FROM task_events WHERE task_id = ? AND id > ? ORDER BY id
                """,
                (task_id, last_id),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "event": row["event_type"],
                "status": row["status"],
                "data": json.loads(row["data"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseMessage):
        return message_to_dict(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _row_to_record(row: sqlite3.Row) -> TaskRecord:
    return TaskRecord(
        task_id=row["task_id"],
        state=json.loads(row["state_snapshot"]),
        status=row["status"],
        session_dir=row["session_dir"],
        cost=float(row["cost"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
