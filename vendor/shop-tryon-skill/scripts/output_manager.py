"""
output_manager.py — 统一管理所有生成产出

目录结构：
  tryon_output/
  ├── step1_garment/     服装图（AI生成或预处理后）
  ├── step2_model/       模特图（AI生成）
  ├── step3_tryon/       试穿合成图
  ├── step4_variants/    多场景变体图
  └── step5_video/       展示视频

每次生成后：
  1. 保存到对应子目录，文件名含时间戳
  2. 同时记录 URL（远程）和本地路径
  3. 打印统一格式的产出报告供对话层展示
"""

import os, json, time, urllib.request, datetime
from pathlib import Path


def get_output_dir(fallback: str = None) -> str:
    """
    返回输出根目录，优先级：
      1. 环境变量 TRYON_OUTPUT_DIR（.env 配置）
      2. fallback 参数（脚本传入）
      3. 运行终端 pwd 下的 tryon_output/
    """
    from_env = os.getenv("TRYON_OUTPUT_DIR", "").strip()
    if from_env:
        return from_env
    if fallback and fallback not in ("./tryon_output", "tryon_output"):
        return fallback
    return os.path.join(os.getcwd(), "tryon_output")


def _parse_env_file(env_path: Path):
    """手动解析 .env 文件，不依赖 python-dotenv。
    规则：跳过空行和 # 注释；KEY=VALUE（不覆盖已有环境变量）。"""
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:   # override=False 语义
                os.environ[key] = val


def load_env(script_path: str = __file__):
    """
    按优先级查找并加载 .env 文件（无需 python-dotenv）：
      1. 脚本所在目录（scripts/）— 跟着 Skill 走
      2. 当前运行目录（终端 pwd）
    """
    scripts_env = Path(script_path).resolve().parent / ".env"
    cwd_env     = Path.cwd() / ".env"

    # 优先脚本目录
    if scripts_env.exists():
        _parse_env_file(scripts_env)
        # 同时尝试 dotenv（如果装了更规范，支持多行值等边缘情况）
        try:
            from dotenv import load_dotenv
            load_dotenv(scripts_env, override=False)
        except ImportError:
            pass
        return

    # 其次当前运行目录
    if cwd_env.exists():
        _parse_env_file(cwd_env)
        try:
            from dotenv import load_dotenv
            load_dotenv(cwd_env, override=False)
        except ImportError:
            pass
        return

    # 都没找到
    print(
        "  未找到 .env 配置文件\n"
        f"   请把 .env 放在 Skill 脚本目录（推荐）：\n"
        f"   {scripts_env}\n"
        f"\n"
        f"   快速配置命令：\n"
        f"   cp {scripts_env.parent / '.env.example'} {scripts_env}\n"
        f"   然后编辑填入 ARK_API_KEY 等 Key\n"
        f"   （若已通过系统环境变量配置则忽略此提示）"
    )


# 子目录映射
STAGE_DIRS = {
    "garment":  "step1_garment",
    "model":    "step2_model",
    "tryon":    "step3_tryon",
    "variants": "step4_variants",
    "video":    "step5_video",
}

# ── Session ID（每次进程启动唯一，保证同一次试穿的所有产出在同目录）──
_SESSION_ID = None


def _get_session_id() -> str:
    """
    懒创建唯一 session ID。
    同一进程内复用；跨进程时读取输出目录的 .current_session 文件，
    若文件创建不超过 2 小时则复用同一 session，保证同一轮对话的
    多次脚本调用（tryon_runner / video_gen 等）共享同一任务目录。
    """
    global _SESSION_ID
    if _SESSION_ID is not None:
        return _SESSION_ID

    base = get_output_dir()
    session_file = os.path.join(base, ".current_session")

    # 尝试复用已有 session（2 小时内）
    if os.path.exists(session_file):
        try:
            with open(session_file, encoding="utf-8") as f:
                data = json.loads(f.read())
            sid = data.get("session_id", "")
            created_at = data.get("created_at", 0)
            if sid and (time.time() - created_at) < 86400:  # 24 小时（覆盖完整工作日）
                _SESSION_ID = sid
                return _SESSION_ID
        except Exception:
            pass

    # 新建 session 并持久化
    _SESSION_ID = datetime.datetime.now().strftime("task_%Y%m%d_%H%M%S")
    os.makedirs(base, exist_ok=True)
    try:
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump({"session_id": _SESSION_ID, "created_at": time.time()}, f)
    except Exception:
        pass
    return _SESSION_ID


def get_session_dir(base_dir: str = None) -> str:
    """返回当前 session 的完整目录路径（base/task_YYYYMMDD_HHMMSS/）"""
    base = get_output_dir(base_dir)
    d = os.path.join(base, _get_session_id())
    os.makedirs(d, exist_ok=True)
    return d


def _ts() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def _stage_dir(base: str, stage: str) -> str:
    d = os.path.join(base, _get_session_id(), STAGE_DIRS.get(stage, stage))
    os.makedirs(d, exist_ok=True)
    return d


def save_url(
    url: str,
    stage: str,
    label: str = "",
    base_dir: str = None,
    ext: str = "jpg",
) -> dict:
    """
    下载 URL 图片/视频到本地，返回产出记录

    返回：
    {
        "url":   "https://...",
        "path":  "/abs/path/to/file.jpg",
        "stage": "tryon",
        "label": "场景描述",
    }
    """
    base_dir = get_output_dir(base_dir)
    d = _stage_dir(base_dir, stage)
    suffix = f"_{label}" if label else ""
    fname = f"{stage}{suffix}_{_ts()}.{ext}"
    local_path = os.path.join(d, fname)

    urllib.request.urlretrieve(url, local_path)
    abs_path = str(Path(local_path).resolve())

    record = {"url": url, "path": abs_path, "stage": stage, "label": label}
    _append_log(base_dir, record)
    print(f"   [{stage}] {fname}")
    print(f"     路径: {abs_path}")
    print(f"     URL:  {url[:70]}...")
    return record


def save_b64(
    b64_str: str,
    stage: str,
    label: str = "",
    base_dir: str = None,
) -> dict:
    """保存 base64 图片，返回产出记录"""
    import base64
    base_dir = get_output_dir(base_dir)
    d = _stage_dir(base_dir, stage)
    suffix = f"_{label}" if label else ""
    fname = f"{stage}{suffix}_{_ts()}.png"
    local_path = os.path.join(d, fname)

    with open(local_path, "wb") as f:
        f.write(base64.b64decode(b64_str))
    abs_path = str(Path(local_path).resolve())

    record = {"url": None, "path": abs_path, "stage": stage, "label": label}
    _append_log(base_dir, record)
    print(f"   [{stage}] {fname}")
    print(f"     路径: {abs_path}")
    return record


def save_video(
    url: str,
    stage: str = "video",
    label: str = "",
    base_dir: str = None,
) -> dict:
    """下载视频，返回产出记录。校验文件完整性，过小则删除并报错。"""
    rec = save_url(url, stage, label, base_dir, ext="mp4")
    _MIN_VIDEO_BYTES = 50 * 1024  # 50 KB，低于此视为错误响应
    size = os.path.getsize(rec["path"])
    if size < _MIN_VIDEO_BYTES:
        os.remove(rec["path"])
        raise RuntimeError(
            f"视频文件异常（仅 {size} 字节，疑似 API 返回错误页而非真实视频）。\n"
            f"  URL: {url[:80]}\n"
            f"  建议：检查 API Key 是否有效、账户余额是否充足，稍后重试。"
        )
    return rec


def _append_log(base_dir: str, record: dict):
    """追加写入 session 日志（写入 session 子目录）"""
    session_dir = os.path.join(base_dir, _get_session_id())
    os.makedirs(session_dir, exist_ok=True)
    log_path = os.path.join(session_dir, "session_log.jsonl")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def print_summary(records: list, title: str = "本次生成产出"):
    """在对话里打印产出汇总，供用户查看"""
    if not records:
        return
    print(f"\n{'─'*55}")
    print(f"📦 {title}（共 {len(records)} 个文件）")
    print(f"{'─'*55}")
    for i, r in enumerate(records, 1):
        label = f" [{r['label']}]" if r.get("label") else ""
        print(f"\n  {i}.{label} 阶段：{r['stage']}")
        print(f"     本地路径：{r['path']}")
        if r.get("url"):
            print(f"     远程 URL：{r['url'][:70]}{'...' if len(r.get('url',''))>70 else ''}")
    print(f"\n{'─'*55}\n")


def load_session_log(base_dir: str = None) -> list:
    """读取当前 session 所有产出记录"""
    base_dir = get_output_dir(base_dir)
    session_dir = os.path.join(base_dir, _get_session_id())
    log_path = os.path.join(session_dir, "session_log.jsonl")
    if not os.path.exists(log_path):
        return []
    records = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
    return records


def get_latest(stage: str, base_dir: str = None) -> dict | None:
    """获取某阶段最新的产出记录"""
    records = [r for r in load_session_log(base_dir) if r.get("stage") == stage]
    return records[-1] if records else None


def get_all_by_stage(stage: str, base_dir: str = None) -> list:
    """获取某阶段所有产出"""
    return [r for r in load_session_log(base_dir) if r.get("stage") == stage]


# ──────────────────────────────────────────────────────
# CLI — Agent 在对话开始时调用以锁定 session 目录
# ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse as _argparse
    _p = _argparse.ArgumentParser(
        description="输出目录 & Session 管理工具",
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  # 对话开始时锁定 session（最常用）\n"
            "  OUTPUT_DIR=$(python scripts/output_manager.py --get-session)\n\n"
            "  # 用户主动开始新任务时重置\n"
            "  OUTPUT_DIR=$(python scripts/output_manager.py --new-session)\n"
        ),
    )
    _p.add_argument("--get-session", action="store_true",
                    help="返回当前 session 目录（24h 内复用，超时自动新建）")
    _p.add_argument("--new-session", action="store_true",
                    help="强制新建 session 并返回目录（用户明确说'开始新任务'时调用）")
    _args = _p.parse_args()

    if _args.new_session:
        # 清除旧 session 缓存文件，下次 _get_session_id() 自动新建
        _base = get_output_dir()
        _sf = os.path.join(_base, ".current_session")
        if os.path.exists(_sf):
            os.remove(_sf)
        # 重置全局session ID变量
        globals()['_SESSION_ID'] = None

    if _args.get_session or _args.new_session:
        print(get_session_dir())
    else:
        _p.print_help()
