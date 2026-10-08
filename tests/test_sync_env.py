from pathlib import Path

from sync_env import sync_env


def test_sync_env_adds_vendor_ark_alias(tmp_path: Path) -> None:
    source = tmp_path / ".env"
    destination = tmp_path / "vendor" / "scripts" / ".env"
    source.write_text(
        "ARK_API_KEY=secret\nARK_ENDPOINT=https://ark.example.test/api/v3\n",
        encoding="utf-8",
    )

    assert sync_env(source, destination) == destination.resolve()
    synchronized = destination.read_text(encoding="utf-8")
    assert "ARK_API_KEY=secret" in synchronized
    assert "ARK_BASE_URL=https://ark.example.test/api/v3" in synchronized

