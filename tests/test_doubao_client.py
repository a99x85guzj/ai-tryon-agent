from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.services.cost_ledger import BudgetExceededError, CostLedger
from backend.services.doubao_client import DoubaoClient


def make_client(
    tmp_path: Path,
    transport: httpx.AsyncBaseTransport,
    *,
    daily_limit: float = 1.0,
    estimated_cost: float = 0.2,
    sleep=None,
) -> DoubaoClient:
    async def no_sleep(_: float) -> None:
        return None

    return DoubaoClient(
        api_key="test-key",
        endpoint="https://ark.example.test/api/v3",
        model="test-seedream",
        daily_limit=daily_limit,
        estimated_cost=estimated_cost,
        ledger=CostLedger(tmp_path / "ledger.db"),
        transport=transport,
        sleep=sleep or no_sleep,
    )


@pytest.mark.asyncio
async def test_generate_image_returns_url_and_records_cost(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["prompt"] == "white shirt"
        assert body["image"] == ["https://example.test/reference.png"]
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200, json={"data": [{"url": "https://example.test/result.png"}]}
        )

    client = make_client(tmp_path, httpx.MockTransport(handler))
    url = await client.generate_image(
        "white shirt", ["https://example.test/reference.png"]
    )

    assert url == "https://example.test/result.png"
    usage = client.ledger.daily_usage()
    assert usage.attempts == 1
    assert usage.successful_calls == 1
    assert usage.estimated_cost == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_transient_failure_retries_with_exponential_backoff(tmp_path: Path) -> None:
    calls = 0
    delays: list[float] = []

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(
            200, json={"data": [{"url": "https://example.test/result.png"}]}
        )

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    client = make_client(
        tmp_path, httpx.MockTransport(handler), sleep=record_sleep
    )
    assert await client.generate_image("retry me") == "https://example.test/result.png"
    assert calls == 3
    assert delays == [1.0, 2.0]
    usage = client.ledger.daily_usage()
    assert usage.attempts == 3
    assert usage.successful_calls == 1
    assert usage.estimated_cost == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_budget_is_checked_before_http_request(tmp_path: Path) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, json={"data": [{"url": "https://example.test/result.png"}]}
        )

    client = make_client(
        tmp_path,
        httpx.MockTransport(handler),
        daily_limit=0.1,
        estimated_cost=0.2,
    )
    with pytest.raises(BudgetExceededError):
        await client.generate_image("over budget")
    assert calls == 0

