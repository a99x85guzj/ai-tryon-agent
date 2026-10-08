from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from backend.services.cost_ledger import CostLedger
from backend.tools.tryon_tools import TryonTools


SCRIPT_NAMES = {
    "garment_analyzer.py",
    "model_manager.py",
    "preprocess.py",
    "oss_uploader.py",
    "tryon_runner.py",
    "image_gen_tryon.py",
    "partial_tryon.py",
    "video_gen.py",
    "output_manager.py",
}

FAKE_SCRIPT = r'''
import json
import os
from pathlib import Path
import sys
import time

record = {
    "script": Path(__file__).name,
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "output_root": os.environ.get("TRYON_OUTPUT_DIR"),
}
log_path = os.environ.get("FAKE_CLI_LOG")
if log_path:
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")

if "__timeout__" in sys.argv:
    time.sleep(5)
if "__stderr__" in sys.argv:
    print("simulated warning", file=sys.stderr)
if "__fail__" in sys.argv:
    print("simulated failure", file=sys.stderr)
    raise SystemExit(7)

if Path(__file__).name == "output_manager.py":
    session = Path(os.environ["TRYON_OUTPUT_DIR"]) / "task_fake"
    session.mkdir(parents=True, exist_ok=True)
    print(session.resolve())
else:
    print(json.dumps(record))
'''


@pytest.fixture
def fake_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    for name in SCRIPT_NAMES:
        (script_dir / name).write_text(FAKE_SCRIPT, encoding="utf-8")
    log_path = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLI_LOG", str(log_path))
    ledger = CostLedger(tmp_path / "ledger.db")
    tools = TryonTools(
        script_dir=script_dir,
        sessions_root=tmp_path / "sessions",
        ledger=ledger,
        python_executable=sys.executable,
        costs={
            "analyze_garment": 0.1,
            "generate_variants": 0.2,
            "run_tryon": 0.3,
        },
    )
    return tools, log_path, ledger, script_dir


def read_calls(log_path: Path) -> list[dict]:
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_all_wrappers_build_real_cli_arguments(fake_tools, tmp_path: Path) -> None:
    tools, log_path, ledger, script_dir = fake_tools
    session = tmp_path / "sessions" / "task_20260902_120000"
    session.mkdir(parents=True)

    results = [
        await tools.analyze_garment(
            "garment.jpg", "focus on fabric", session_dir=session
        ),
        await tools.list_models(session_dir=session),
        await tools.recommend_models("white tee", session_dir=session),
        await tools.validate_model_image("model.jpg", session_dir=session),
        await tools.preprocess_garment("garment.jpg", session_dir=session),
        await tools.upload_to_oss("garment.jpg", session_dir=session),
        await tools.run_tryon(
            "garment.jpg",
            "model.jpg",
            "top",
            "qwen",
            session_dir=session,
        ),
        await tools.generate_variants(
            "studio",
            "douban",
            2,
            "ecommerce",
            "garment.jpg",
            "model.jpg",
            session_dir=session,
        ),
        await tools.partial_tryon(
            "model.jpg", "top.jpg", "upper", False, session_dir=session
        ),
        await tools.partial_tryon(
            "model.jpg", "ignored.jpg", "lower", True, session_dir=session
        ),
        await tools.generate_video(
            ["one.jpg"], "walk", "9:16", session_dir=session
        ),
        await tools.generate_video(
            ["one.jpg", "two.jpg"], "turn", "1:1", session_dir=session
        ),
        await tools.new_session(),
        await tools.get_session(),
    ]

    assert all(result.ok for result in results)
    assert all(result.elapsed >= 0 for result in results)
    calls = read_calls(log_path)
    assert len(calls) == len(results)
    assert all(Path(call["cwd"]) == script_dir for call in calls)
    assert all(Path(call["output_root"]) == session.parent for call in calls[:12])

    expected = [
        ("garment_analyzer.py", ["garment.jpg", "--json", "--prompt", "focus on fabric"]),
        ("model_manager.py", ["list"]),
        ("model_manager.py", ["recommend", "white tee"]),
        ("model_manager.py", ["validate", "model.jpg"]),
        (
            "preprocess.py",
            ["garment.jpg", "--output", str(session / "garment_processed.jpg")],
        ),
        ("oss_uploader.py", ["garment.jpg"]),
        (
            "tryon_runner.py",
            [
                "--garment", "garment.jpg", "--model", "model.jpg",
                "--garment-part", "top", "--synthesis-method", "qwen",
                "--output-dir", str(session),
            ],
        ),
        (
            "image_gen_tryon.py",
            [
                "--desc", "studio", "--image-backend", "douban", "--variants", "2",
                "--output-dir", str(session), "--angle-preset", "ecommerce",
                "--garment-img", "garment.jpg", "--model-img", "model.jpg",
            ],
        ),
        (
            "partial_tryon.py",
            [
                "--model", "model.jpg", "--output-dir", str(session),
                "--new-garment", "top.jpg", "--replace", "upper",
            ],
        ),
        (
            "partial_tryon.py",
            [
                "--model", "model.jpg", "--output-dir", str(session),
                "--get-bbox", "lower",
            ],
        ),
        (
            "video_gen.py",
            [
                "--image", "one.jpg", "--prompt", "walk", "--ratio", "9:16",
                "--output", str(session / "video.mp4"),
            ],
        ),
        (
            "video_gen.py",
            [
                "--images", "one.jpg", "two.jpg", "--prompt", "turn",
                "--ratio", "1:1", "--output", str(session / "video.mp4"),
            ],
        ),
        ("output_manager.py", ["--new-session"]),
        ("output_manager.py", ["--get-session"]),
    ]
    assert [(call["script"], call["argv"]) for call in calls] == expected
    assert results[0].cost == pytest.approx(0.1)
    assert results[7].cost == pytest.approx(0.4)
    assert ledger.tool_call_count() == len(results)
    assert results[-2].data["session_dir"].endswith("task_fake")
    assert results[-1].data["session_dir"].endswith("task_fake")


@pytest.mark.asyncio
async def test_timeout_kills_process_and_records_failure(
    fake_tools, tmp_path: Path
) -> None:
    tools, _, ledger, _ = fake_tools
    tools.timeout_seconds = 0.05
    result = await tools.preprocess_garment(
        "__timeout__", session_dir=tmp_path / "session"
    )

    assert not result.ok
    assert result.data["error"] == "timeout"
    assert "Timed out after 0.05 seconds" in result.stderr
    assert result.elapsed < 2
    assert ledger.tool_call_count() == 1


@pytest.mark.asyncio
async def test_success_preserves_stderr(fake_tools, tmp_path: Path) -> None:
    tools, _, _, _ = fake_tools
    result = await tools.upload_to_oss(
        "__stderr__", session_dir=tmp_path / "session"
    )
    assert result.ok
    assert result.stderr == "simulated warning"


@pytest.mark.asyncio
async def test_nonzero_exit_returns_structured_error(fake_tools, tmp_path: Path) -> None:
    tools, _, _, _ = fake_tools
    result = await tools.validate_model_image(
        "__fail__", session_dir=tmp_path / "session"
    )
    assert not result.ok
    assert result.data["error"] == "process_failed"
    assert result.data["returncode"] == 7
    assert "simulated failure" in result.stderr

