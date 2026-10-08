"""Phase-one smoke test; uses a mock response when ARK_API_KEY is absent."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.cost_ledger import CostLedger
from backend.services.doubao_client import DoubaoClient


PROMPT = "一个穿白色T恤的亚洲女模特，纯白棚拍"


async def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    api_key = os.getenv("ARK_API_KEY", "").strip()
    transport = None
    if not api_key:
        api_key = "mock-key"

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"data": [{"url": "https://example.invalid/mock-tryon.png"}]},
            )

        transport = httpx.MockTransport(handler)
        print("ARK_API_KEY 未配置，使用 mock 响应进行冒烟测试。")

    client = DoubaoClient(
        api_key=api_key,
        endpoint=os.getenv(
            "ARK_ENDPOINT", "https://ark.cn-beijing.volces.com/api/v3"
        ),
        model=os.getenv("ARK_IMAGE_MODEL", "doubao-seedream-5-0-260128"),
        daily_limit=float(os.getenv("BUDGET_DAILY_LIMIT", "10.00")),
        estimated_cost=float(os.getenv("ARK_IMAGE_COST_ESTIMATE", "0.20")),
        ledger=CostLedger(PROJECT_ROOT / "data" / "smoke_cost_ledger.db"),
        transport=transport,
    )
    url = await client.generate_image(PROMPT)
    print(f"图片 URL: {url}")


if __name__ == "__main__":
    asyncio.run(main())
