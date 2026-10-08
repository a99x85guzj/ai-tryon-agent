"""Delete expired local session caches; safe to run from Task Scheduler or cron."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.cache_cleanup import cleanup_sessions


def main() -> None:
    parser = argparse.ArgumentParser(description="清理过期试衣会话缓存")
    parser.add_argument(
        "--root",
        default=str(PROJECT_ROOT / "data" / "sessions"),
        help="会话缓存根目录",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=int(os.getenv("CACHE_RETENTION_DAYS", "7")),
        help="保留天数，默认 7",
    )
    args = parser.parse_args()
    removed = cleanup_sessions(args.root, retention_days=args.days)
    print(json.dumps({"removed": removed, "count": len(removed)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
