"""Async Doubao Seedream client with retries, budget enforcement, and logging."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from backend.services.cost_ledger import BudgetExceededError, CostLedger


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "data" / "cost_ledger.db"


class DoubaoConfigurationError(RuntimeError):
    """Raised when required runtime configuration is absent or invalid."""


class DoubaoResponseError(RuntimeError):
    """Raised when Ark returns a successful but malformed response."""


SleepCallable = Callable[[float], Awaitable[None]]


class DoubaoClient:
    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str,
        model: str,
        daily_limit: float,
        estimated_cost: float,
        ledger: CostLedger,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: SleepCallable = asyncio.sleep,
        max_attempts: int = 3,
        backoff_base_seconds: float = 1.0,
        timeout_seconds: float = 90.0,
    ) -> None:
        if not api_key.strip():
            raise DoubaoConfigurationError("ARK_API_KEY is required")
        if max_attempts < 1 or max_attempts > 3:
            raise ValueError("max_attempts must be between 1 and 3")
        self.api_key = api_key
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.daily_limit = daily_limit
        self.estimated_cost = estimated_cost
        self.ledger = ledger
        self.transport = transport
        self.sleep = sleep
        self.max_attempts = max_attempts
        self.backoff_base_seconds = backoff_base_seconds
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(
        cls,
        *,
        env_file: str | Path = PROJECT_ROOT / ".env",
        ledger_path: str | Path = DEFAULT_LEDGER_PATH,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: SleepCallable = asyncio.sleep,
    ) -> "DoubaoClient":
        load_dotenv(dotenv_path=env_file, override=False)
        try:
            daily_limit = float(os.getenv("BUDGET_DAILY_LIMIT", ""))
            estimated_cost = float(os.getenv("ARK_IMAGE_COST_ESTIMATE", "0.20"))
        except ValueError as exc:
            raise DoubaoConfigurationError(
                "BUDGET_DAILY_LIMIT and ARK_IMAGE_COST_ESTIMATE must be numbers"
            ) from exc

        return cls(
            api_key=os.getenv("ARK_API_KEY", ""),
            endpoint=os.getenv(
                "ARK_ENDPOINT", "https://ark.cn-beijing.volces.com/api/v3"
            ),
            model=os.getenv("ARK_IMAGE_MODEL", "doubao-seedream-5-0-260128"),
            daily_limit=daily_limit,
            estimated_cost=estimated_cost,
            ledger=CostLedger(ledger_path),
            transport=transport,
            sleep=sleep,
        )

    async def generate_image(
        self, prompt: str, ref_images: Sequence[str] | None = None
    ) -> str:
        """Generate one image and return its public URL."""
        if not prompt.strip():
            raise ValueError("prompt must not be empty")

        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "size": "2K",
            "response_format": "url",
            "watermark": False,
        }
        if ref_images:
            payload["image"] = list(ref_images)

        last_error: Exception | None = None
        for attempt_number in range(1, self.max_attempts + 1):
            attempt_id = self.ledger.reserve_attempt(
                model=self.model,
                estimated_cost=self.estimated_cost,
                daily_limit=self.daily_limit,
            )
            started = time.perf_counter()
            try:
                async with httpx.AsyncClient(
                    transport=self.transport,
                    timeout=self.timeout_seconds,
                ) as client:
                    response = await client.post(
                        f"{self.endpoint}/images/generations",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=payload,
                    )
                    response.raise_for_status()
                    url = self._extract_url(response.json())
                elapsed = time.perf_counter() - started
                self.ledger.finish_attempt(
                    attempt_id, success=True, elapsed_seconds=elapsed
                )
                logger.info(
                    "Doubao image call succeeded",
                    extra={
                        "model": self.model,
                        "elapsed_seconds": elapsed,
                        "success": True,
                        "attempt": attempt_number,
                    },
                )
                return url
            except Exception as exc:
                elapsed = time.perf_counter() - started
                self.ledger.finish_attempt(
                    attempt_id,
                    success=False,
                    elapsed_seconds=elapsed,
                    error=str(exc)[:1000],
                )
                logger.warning(
                    "Doubao image call failed",
                    extra={
                        "model": self.model,
                        "elapsed_seconds": elapsed,
                        "success": False,
                        "attempt": attempt_number,
                    },
                    exc_info=True,
                )
                last_error = exc
                if attempt_number >= self.max_attempts or not self._is_retryable(exc):
                    raise
                await self.sleep(self.backoff_base_seconds * (2 ** (attempt_number - 1)))

        raise RuntimeError("Doubao request failed without an exception") from last_error

    @staticmethod
    def _extract_url(payload: Any) -> str:
        try:
            url = payload["data"][0]["url"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DoubaoResponseError("Ark response did not contain data[0].url") from exc
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise DoubaoResponseError("Ark returned an invalid image URL")
        return url

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        if isinstance(error, httpx.TransportError):
            return True
        if isinstance(error, httpx.HTTPStatusError):
            return error.response.status_code in {408, 409, 429} or (
                error.response.status_code >= 500
            )
        return False


async def generate_image(
    prompt: str, ref_images: Sequence[str] | None = None
) -> str:
    """Convenience entry point using configuration from the project .env file."""
    return await DoubaoClient.from_env().generate_image(prompt, ref_images)


__all__ = [
    "BudgetExceededError",
    "DoubaoClient",
    "DoubaoConfigurationError",
    "DoubaoResponseError",
    "generate_image",
]

