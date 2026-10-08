"""Async, non-importing adapters for the shop-tryon-skill CLI scripts."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Sequence

from dotenv import load_dotenv

from backend.services.cost_ledger import CostLedger


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCRIPT_DIR = PROJECT_ROOT / "vendor" / "shop-tryon-skill" / "scripts"
DEFAULT_SESSIONS_ROOT = PROJECT_ROOT / "data" / "sessions"
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "data" / "cost_ledger.db"
DEFAULT_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    data: dict
    cost: float
    elapsed: float
    stderr: str


def _configured_costs() -> dict[str, float]:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    return {
        "analyze_garment": float(os.getenv("TRYON_COST_ANALYZE_GARMENT", "0")),
        "list_models": 0.0,
        "recommend_models": 0.0,
        "validate_model_image": 0.0,
        "preprocess_garment": 0.0,
        "upload_to_oss": 0.0,
        "run_tryon": float(os.getenv("TRYON_COST_RUN_TRYON", "0")),
        "generate_variants": float(os.getenv("TRYON_COST_GENERATE_VARIANT", "0")),
        "partial_tryon": float(os.getenv("TRYON_COST_PARTIAL_TRYON", "0")),
        "generate_video": float(os.getenv("TRYON_COST_GENERATE_VIDEO", "0")),
        "new_session": 0.0,
        "get_session": 0.0,
    }


class TryonTools:
    """Invoke upstream scripts strictly as child processes from their scripts cwd."""

    def __init__(
        self,
        *,
        script_dir: str | Path = DEFAULT_SCRIPT_DIR,
        sessions_root: str | Path = DEFAULT_SESSIONS_ROOT,
        ledger: CostLedger | None = None,
        python_executable: str | Path = sys.executable,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        costs: Mapping[str, float] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.script_dir = Path(script_dir).resolve()
        self.sessions_root = Path(sessions_root).resolve()
        self.ledger = ledger or CostLedger(DEFAULT_LEDGER_PATH)
        self.python_executable = str(python_executable)
        self.timeout_seconds = timeout_seconds
        self.costs = {**_configured_costs(), **(costs or {})}

    def _cost(self, tool_name: str, multiplier: int = 1) -> float:
        cost = float(self.costs.get(tool_name, 0.0)) * multiplier
        if cost < 0:
            raise ValueError(f"Configured cost for {tool_name} must be non-negative")
        return cost

    def _output_root(self, session_dir: Path) -> Path:
        # output_manager returns <root>/task_*. Passing that path back as its env
        # root would create task_*/task_* nesting, so preserve its parent root.
        if session_dir.name.startswith("task_"):
            return session_dir.parent
        return session_dir

    async def _run(
        self,
        tool_name: str,
        script_name: str,
        args: Sequence[str],
        *,
        session_dir: str | Path,
        cost: float | None = None,
    ) -> ToolResult:
        script_path = self.script_dir / script_name
        session_path = Path(session_dir).resolve()
        session_path.mkdir(parents=True, exist_ok=True)
        call_cost = self._cost(tool_name) if cost is None else cost
        started = time.perf_counter()
        stderr_text = ""

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["TRYON_OUTPUT_DIR"] = str(self._output_root(session_path))

        try:
            process = await asyncio.create_subprocess_exec(
                self.python_executable,
                str(script_path),
                *map(str, args),
                cwd=str(self.script_dir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self.timeout_seconds
                )
            except TimeoutError:
                process.kill()
                stdout, stderr = await process.communicate()
                elapsed = time.perf_counter() - started
                stderr_text = stderr.decode("utf-8", errors="replace").strip()
                timeout_message = f"Timed out after {self.timeout_seconds:g} seconds"
                stderr_text = "\n".join(filter(None, (stderr_text, timeout_message)))
                result = ToolResult(
                    ok=False,
                    data={
                        "error": "timeout",
                        "timeout_seconds": self.timeout_seconds,
                        "stdout": stdout.decode("utf-8", errors="replace").strip(),
                    },
                    cost=call_cost,
                    elapsed=elapsed,
                    stderr=stderr_text,
                )
                self._record(tool_name, result)
                return result

            elapsed = time.perf_counter() - started
            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            ok = process.returncode == 0
            data = self._parse_stdout(stdout_text)
            if not ok:
                data = {
                    **data,
                    "error": "process_failed",
                    "returncode": process.returncode,
                }
            result = ToolResult(ok, data, call_cost, elapsed, stderr_text)
            self._record(tool_name, result)
            return result
        except Exception as exc:
            elapsed = time.perf_counter() - started
            stderr_text = str(exc)
            result = ToolResult(
                ok=False,
                data={"error": "process_start_failed", "detail": str(exc)},
                cost=call_cost,
                elapsed=elapsed,
                stderr=stderr_text,
            )
            self._record(tool_name, result)
            return result

    def _record(self, tool_name: str, result: ToolResult) -> None:
        self.ledger.record_tool_call(
            tool_name=tool_name,
            elapsed_seconds=result.elapsed,
            success=result.ok,
            cost=result.cost,
            stderr=result.stderr,
        )

    @staticmethod
    def _parse_stdout(stdout: str) -> dict:
        if not stdout:
            return {}
        try:
            parsed = json.loads(stdout)
            return parsed if isinstance(parsed, dict) else {"result": parsed}
        except json.JSONDecodeError:
            pass

        decoder = json.JSONDecoder()
        for index, character in enumerate(stdout):
            if character not in "[{":
                continue
            try:
                candidate, consumed = decoder.raw_decode(stdout[index:])
            except json.JSONDecodeError:
                continue
            if stdout[index + consumed :].strip():
                continue
            if isinstance(candidate, dict):
                return candidate
            return {"result": candidate, "stdout": stdout}
        return {"stdout": stdout}

    async def analyze_garment(
        self,
        image: str,
        custom_prompt: str | None = None,
        *,
        session_dir: str | Path,
    ) -> ToolResult:
        args = [image, "--json"]
        if custom_prompt is not None:
            args.extend(["--prompt", custom_prompt])
        return await self._run(
            "analyze_garment", "garment_analyzer.py", args, session_dir=session_dir
        )

    async def list_models(self, *, session_dir: str | Path) -> ToolResult:
        return await self._run(
            "list_models", "model_manager.py", ["list"], session_dir=session_dir
        )

    async def recommend_models(
        self, desc: str, *, session_dir: str | Path
    ) -> ToolResult:
        return await self._run(
            "recommend_models",
            "model_manager.py",
            ["recommend", desc],
            session_dir=session_dir,
        )

    async def validate_model_image(
        self, path: str, *, session_dir: str | Path
    ) -> ToolResult:
        return await self._run(
            "validate_model_image",
            "model_manager.py",
            ["validate", path],
            session_dir=session_dir,
        )

    async def preprocess_garment(
        self, path: str, *, session_dir: str | Path
    ) -> ToolResult:
        session_path = Path(session_dir).resolve()
        source = Path(path)
        suffix = source.suffix if source.suffix else ".jpg"
        output = session_path / f"{source.stem}_processed{suffix}"
        return await self._run(
            "preprocess_garment",
            "preprocess.py",
            [path, "--output", str(output)],
            session_dir=session_path,
        )

    async def upload_to_oss(
        self, path: str, *, session_dir: str | Path
    ) -> ToolResult:
        return await self._run(
            "upload_to_oss", "oss_uploader.py", [path], session_dir=session_dir
        )

    async def run_tryon(
        self,
        garment: str,
        model: str,
        garment_part: str,
        synthesis_method: str = "prompt",
        *,
        session_dir: str | Path,
    ) -> ToolResult:
        args = [
            "--garment",
            garment,
            "--model",
            model,
            "--garment-part",
            garment_part,
            "--synthesis-method",
            synthesis_method,
            "--output-dir",
            str(Path(session_dir).resolve()),
        ]
        return await self._run(
            "run_tryon", "tryon_runner.py", args, session_dir=session_dir
        )

    async def generate_variants(
        self,
        desc: str,
        image_backend: str = "douban",
        variants: int = 1,
        angle_preset: str | None = None,
        garment_img: str | None = None,
        model_img: str | None = None,
        *,
        session_dir: str | Path,
    ) -> ToolResult:
        if not 1 <= variants <= 9:
            raise ValueError("variants must be between 1 and 9")
        if angle_preset and (not garment_img or not model_img):
            raise ValueError("angle_preset requires garment_img and model_img")
        args = [
            "--desc",
            desc,
            "--image-backend",
            image_backend,
            "--variants",
            str(variants),
            "--output-dir",
            str(Path(session_dir).resolve()),
        ]
        if angle_preset:
            args.extend(["--angle-preset", angle_preset])
        if garment_img:
            args.extend(["--garment-img", garment_img])
        if model_img:
            args.extend(["--model-img", model_img])
        return await self._run(
            "generate_variants",
            "image_gen_tryon.py",
            args,
            session_dir=session_dir,
            cost=self._cost("generate_variants", variants),
        )

    async def partial_tryon(
        self,
        model: str,
        new_garment: str,
        replace: Literal["upper", "lower"],
        get_bbox: bool = False,
        *,
        session_dir: str | Path,
    ) -> ToolResult:
        if replace not in {"upper", "lower"}:
            raise ValueError("replace must be 'upper' or 'lower'")
        args = [
            "--model",
            model,
            "--output-dir",
            str(Path(session_dir).resolve()),
        ]
        if get_bbox:
            args.extend(["--get-bbox", replace])
        else:
            args.extend(["--new-garment", new_garment, "--replace", replace])
        return await self._run(
            "partial_tryon", "partial_tryon.py", args, session_dir=session_dir
        )

    async def generate_video(
        self,
        images: list[str],
        prompt: str,
        ratio: str = "9:16",
        *,
        session_dir: str | Path,
    ) -> ToolResult:
        if not images:
            raise ValueError("images must contain at least one image")
        if len(images) > 4:
            raise ValueError("video_gen.py supports at most four reference images")
        session_path = Path(session_dir).resolve()
        args = ["--image", images[0]] if len(images) == 1 else ["--images", *images]
        args.extend(
            [
                "--prompt",
                prompt,
                "--ratio",
                ratio,
                "--output",
                str(session_path / "video.mp4"),
            ]
        )
        return await self._run(
            "generate_video", "video_gen.py", args, session_dir=session_path
        )

    async def new_session(self) -> ToolResult:
        return await self._session("new_session", "--new-session")

    async def get_session(self) -> ToolResult:
        return await self._session("get_session", "--get-session")

    async def _session(self, tool_name: str, flag: str) -> ToolResult:
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        result = await self._run(
            tool_name,
            "output_manager.py",
            [flag],
            session_dir=self.sessions_root,
        )
        stdout = result.data.get("stdout")
        if result.ok and isinstance(stdout, str) and stdout:
            session_dir = stdout.splitlines()[-1].strip()
            return ToolResult(
                ok=True,
                data={"session_dir": session_dir},
                cost=result.cost,
                elapsed=result.elapsed,
                stderr=result.stderr,
            )
        return result


_default_tools: TryonTools | None = None


def _defaults() -> TryonTools:
    global _default_tools
    if _default_tools is None:
        _default_tools = TryonTools()
    return _default_tools


async def analyze_garment(image, custom_prompt=None, *, session_dir):
    return await _defaults().analyze_garment(image, custom_prompt, session_dir=session_dir)


async def list_models(*, session_dir):
    return await _defaults().list_models(session_dir=session_dir)


async def recommend_models(desc, *, session_dir):
    return await _defaults().recommend_models(desc, session_dir=session_dir)


async def validate_model_image(path, *, session_dir):
    return await _defaults().validate_model_image(path, session_dir=session_dir)


async def preprocess_garment(path, *, session_dir):
    return await _defaults().preprocess_garment(path, session_dir=session_dir)


async def upload_to_oss(path, *, session_dir):
    return await _defaults().upload_to_oss(path, session_dir=session_dir)


async def run_tryon(
    garment, model, garment_part, synthesis_method="prompt", *, session_dir
):
    return await _defaults().run_tryon(
        garment,
        model,
        garment_part,
        synthesis_method,
        session_dir=session_dir,
    )


async def generate_variants(
    desc,
    image_backend="douban",
    variants=1,
    angle_preset=None,
    garment_img=None,
    model_img=None,
    *,
    session_dir,
):
    return await _defaults().generate_variants(
        desc,
        image_backend,
        variants,
        angle_preset,
        garment_img,
        model_img,
        session_dir=session_dir,
    )


async def partial_tryon(
    model, new_garment, replace, get_bbox=False, *, session_dir
):
    return await _defaults().partial_tryon(
        model, new_garment, replace, get_bbox, session_dir=session_dir
    )


async def generate_video(images, prompt, ratio="9:16", *, session_dir):
    return await _defaults().generate_video(
        images, prompt, ratio, session_dir=session_dir
    )


async def new_session():
    return await _defaults().new_session()


async def get_session():
    return await _defaults().get_session()


__all__ = [
    "ToolResult",
    "TryonTools",
    "analyze_garment",
    "generate_variants",
    "generate_video",
    "get_session",
    "list_models",
    "new_session",
    "partial_tryon",
    "preprocess_garment",
    "recommend_models",
    "run_tryon",
    "upload_to_oss",
    "validate_model_image",
]
