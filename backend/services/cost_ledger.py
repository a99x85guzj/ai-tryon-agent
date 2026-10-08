"""SQLite-backed attempt ledger and atomic daily budget reservations."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class BudgetExceededError(RuntimeError):
    """Raised before a request that would exceed the configured daily budget."""


@dataclass(frozen=True)
class DailyUsage:
    day: str
    attempts: int
    successful_calls: int
    estimated_cost: float


class CostLedger:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

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

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS doubao_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    requested_at TEXT NOT NULL,
                    usage_day TEXT NOT NULL,
                    model TEXT NOT NULL,
                    elapsed_seconds REAL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'success', 'failed')),
                    estimated_cost REAL NOT NULL CHECK(estimated_cost >= 0),
                    error TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_doubao_calls_day "
                "ON doubao_calls(usage_day)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    requested_at TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    elapsed_seconds REAL NOT NULL,
                    success INTEGER NOT NULL CHECK(success IN (0, 1)),
                    cost REAL NOT NULL CHECK(cost >= 0),
                    stderr TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _today() -> str:
        return datetime.now().astimezone().date().isoformat()

    def reserve_attempt(
        self,
        *,
        model: str,
        estimated_cost: float,
        daily_limit: float,
    ) -> int:
        """Atomically reserve one request's projected cost and return its row id."""
        if estimated_cost < 0 or daily_limit < 0:
            raise ValueError("Costs and budget limits must be non-negative")

        usage_day = self._today()
        timestamp = datetime.now(timezone.utc).isoformat()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT COALESCE(SUM(estimated_cost), 0) AS reserved
                FROM doubao_calls
                WHERE usage_day = ? AND status IN ('pending', 'success')
                """,
                (usage_day,),
            ).fetchone()
            reserved = float(row["reserved"])
            if reserved + estimated_cost > daily_limit:
                raise BudgetExceededError(
                    "Doubao daily budget exceeded: "
                    f"reserved CNY {reserved:.4f}, next CNY {estimated_cost:.4f}, "
                    f"limit CNY {daily_limit:.4f}."
                )
            cursor = connection.execute(
                """
                INSERT INTO doubao_calls
                    (requested_at, usage_day, model, status, estimated_cost)
                VALUES (?, ?, ?, 'pending', ?)
                """,
                (timestamp, usage_day, model, estimated_cost),
            )
            connection.commit()
            return int(cursor.lastrowid)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def finish_attempt(
        self,
        attempt_id: int,
        *,
        success: bool,
        elapsed_seconds: float,
        error: str | None = None,
    ) -> None:
        """Finalize an attempt; failed calls release their cost reservation."""
        status = "success" if success else "failed"
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE doubao_calls
                SET status = ?, elapsed_seconds = ?, error = ?,
                    estimated_cost = CASE WHEN ? THEN estimated_cost ELSE 0 END
                WHERE id = ? AND status = 'pending'
                """,
                (status, elapsed_seconds, error, success, attempt_id),
            )

    def daily_usage(self, day: str | None = None) -> DailyUsage:
        day = day or self._today()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS attempts,
                       SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
                       COALESCE(SUM(
                           CASE WHEN status IN ('pending', 'success')
                                THEN estimated_cost ELSE 0 END
                       ), 0) AS cost
                FROM doubao_calls
                WHERE usage_day = ?
                """,
                (day,),
            ).fetchone()
        return DailyUsage(
            day=day,
            attempts=int(row["attempts"]),
            successful_calls=int(row["successes"] or 0),
            estimated_cost=float(row["cost"]),
        )

    def record_tool_call(
        self,
        *,
        tool_name: str,
        elapsed_seconds: float,
        success: bool,
        cost: float,
        stderr: str = "",
    ) -> int:
        """Persist one CLI invocation, including zero-cost local operations."""
        if cost < 0:
            raise ValueError("Tool cost must be non-negative")
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO tool_calls
                    (requested_at, tool_name, elapsed_seconds, success, cost, stderr)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    tool_name,
                    elapsed_seconds,
                    success,
                    cost,
                    stderr[:4000],
                ),
            )
            return int(cursor.lastrowid)

    def tool_call_count(self) -> int:
        """Return the number of recorded CLI invocations (primarily for diagnostics)."""
        with self._connection() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM tool_calls").fetchone()
        return int(row["count"])

    def daily_stats(self, day: str | None = None) -> dict:
        """Aggregate cost, reliability, and latency by CLI tool for one UTC day."""
        day = day or datetime.now(timezone.utc).date().isoformat()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT tool_name,
                       COUNT(*) AS calls,
                       SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failures,
                       COALESCE(SUM(cost), 0) AS cost,
                       COALESCE(AVG(elapsed_seconds), 0) AS average_elapsed
                FROM tool_calls
                WHERE substr(requested_at, 1, 10) = ?
                GROUP BY tool_name
                ORDER BY tool_name
                """,
                (day,),
            ).fetchall()
        groups = [
            {
                "tool": row["tool_name"],
                "calls": int(row["calls"]),
                "failures": int(row["failures"] or 0),
                "failure_rate": (
                    float(row["failures"] or 0) / int(row["calls"])
                    if row["calls"]
                    else 0.0
                ),
                "average_elapsed_seconds": float(row["average_elapsed"]),
                "cost": float(row["cost"]),
            }
            for row in rows
        ]
        calls = sum(item["calls"] for item in groups)
        failures = sum(item["failures"] for item in groups)
        weighted_elapsed = sum(
            item["average_elapsed_seconds"] * item["calls"] for item in groups
        )
        return {
            "day": day,
            "timezone": "UTC",
            "calls": calls,
            "failures": failures,
            "failure_rate": failures / calls if calls else 0.0,
            "average_elapsed_seconds": weighted_elapsed / calls if calls else 0.0,
            "cost": sum(item["cost"] for item in groups),
            "by_tool": groups,
        }
