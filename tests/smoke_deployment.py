"""HTTP smoke for a running Compose stack with APP_MOCK_MODE=true."""

from __future__ import annotations

import base64
import os
import time

import httpx


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def wait_status(client: httpx.Client, task_id: str, expected: str) -> dict:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(f"/api/tasks/{task_id}")
        response.raise_for_status()
        payload = response.json()
        if payload["status"] == expected:
            return payload
        if payload["status"] == "failed":
            raise RuntimeError(payload)
        time.sleep(0.1)
    raise TimeoutError(f"task did not reach {expected}")


def main() -> None:
    base_url = os.getenv("SMOKE_BASE_URL", "http://localhost:8080")
    api_key = os.getenv("API_KEY", "")
    headers = {"X-API-Key": api_key} if api_key else {}
    with httpx.Client(base_url=base_url, headers=headers, timeout=30) as client:
        health = client.get("/api/health")
        health.raise_for_status()
        submitted = client.post(
            "/api/generate",
            data={"description": "上装，白色 T 恤电商棚拍 mock"},
            files={"garment_image": ("garment.png", PNG, "image/png")},
        )
        submitted.raise_for_status()
        task_id = submitted.json()["task_id"]
        wait_status(client, task_id, "waiting_confirm")
        confirmed = client.post(
            f"/api/tasks/{task_id}/confirm",
            json={"action": "approve", "payload": None},
        )
        confirmed.raise_for_status()
        completed = wait_status(client, task_id, "completed")
        artifact = client.get(completed["artifacts"][0])
        artifact.raise_for_status()
        assert artifact.content.startswith(b"\x89PNG")
        stats = client.get("/api/stats/daily")
        stats.raise_for_status()
        print({"task_id": task_id, "artifact": completed["artifacts"][0], "stats": stats.json()})


if __name__ == "__main__":
    main()
