"""Synchronize the application .env to the vendored skill's scripts directory."""

from __future__ import annotations

import argparse
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = PROJECT_ROOT / ".env"
DEFAULT_DESTINATION = (
    PROJECT_ROOT / "vendor" / "shop-tryon-skill" / "scripts" / ".env"
)


def sync_env(source: Path = DEFAULT_SOURCE, destination: Path = DEFAULT_DESTINATION) -> Path:
    """Copy an env file and add the ARK_BASE_URL alias expected by the skill."""
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise FileNotFoundError(
            f"Missing {source}. Copy .env.example to .env and fill in your keys first."
        )

    text = source.read_text(encoding="utf-8")
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    if "ARK_BASE_URL" not in values and values.get("ARK_ENDPOINT"):
        text = text.rstrip() + f"\nARK_BASE_URL={values['ARK_ENDPOINT']}\n"
    elif not text.endswith("\n"):
        text += "\n"

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    destination = sync_env(args.source, args.destination)
    print(f"Environment synchronized to {destination}")


if __name__ == "__main__":
    main()

