"""Seven-day local session cache retention."""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path


def cleanup_sessions(root: str | Path, *, retention_days: int = 7, now: float | None = None) -> list[str]:
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1")
    sessions_root = Path(root).resolve()
    if not sessions_root.exists():
        return []
    cutoff = (now or time.time()) - retention_days * 86400
    removed: list[str] = []
    for candidate in sessions_root.iterdir():
        resolved = candidate.resolve()
        try:
            resolved.relative_to(sessions_root)
        except ValueError:
            continue
        if not candidate.is_dir() or candidate.stat().st_mtime >= cutoff:
            continue
        shutil.rmtree(candidate)
        removed.append(str(resolved))
    return removed


async def cleanup_loop(root: str | Path, *, retention_days: int, interval_hours: float) -> None:
    while True:
        await asyncio.to_thread(cleanup_sessions, root, retention_days=retention_days)
        await asyncio.sleep(max(interval_hours, 0.1) * 3600)
