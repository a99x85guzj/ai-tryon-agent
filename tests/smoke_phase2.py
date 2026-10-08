"""Optional paid phase-two smoke test. Run manually with a configured .env."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.tools.tryon_tools import TryonTools


async def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    if not os.getenv("ARK_API_KEY", "").strip():
        print("跳过：请先配置 ARK_API_KEY 并运行 uv run python sync_env.py")
        return

    tools = TryonTools()
    session_result = await tools.new_session()
    if not session_result.ok:
        raise RuntimeError(session_result.stderr or session_result.data)
    session_dir = session_result.data["session_dir"]
    result = await tools.generate_variants(
        "一个穿白色T恤的亚洲女模特，纯白棚拍",
        image_backend="douban",
        variants=1,
        session_dir=session_dir,
    )
    print(result)
    if not result.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())

