"""
partial_tryon.py — 局部试穿

场景一：局部换装（替换上装或下装，保留另一部分）
  1. 分割模特图，获取指定部位的服饰图和 bbox
  2. 用新服装替换该部位，其余保持不变

场景二：获取服饰 bbox（用于商品热区/标签）
  1. 分割模特图（或试穿效果图）
  2. 返回指定部位的边界框坐标

用法：
  # 只换上衣，保留裤子
  python partial_tryon.py \\
    --model model.jpg \\
    --new-garment new_top.jpg \\
    --replace upper

  # 只换下装，保留上衣
  python partial_tryon.py \\
    --model model.jpg \\
    --new-garment new_skirt.jpg \\
    --replace lower

  # 获取上衣的 bbox 坐标
  python partial_tryon.py \\
    --model tryon_result.jpg \\
    --get-bbox upper
"""

import os, sys, json, time, argparse, urllib.request, urllib.error
from pathlib import Path

# ── 加载 .env ──────────────────────────────────────────
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from output_manager import load_env, save_url, print_summary, get_output_dir
load_env(__file__)

ALIYUN_API_KEY = os.getenv("ALIYUN_API_KEY", "")
_BASE    = "https://dashscope.aliyuncs.com/api/v1"
_ENDPOINT = f"{_BASE}/services/aigc/image2image/image-synthesis/"
_TASK_URL = f"{_BASE}/tasks/{{task_id}}"

# 部位映射（自然语言 → API 参数）
PART_MAP = {
    # 上装
    "上衣": "upper", "上装": "upper", "衬衫": "upper", "外套": "upper",
    "卫衣": "upper", "T恤": "upper", "jacket": "upper", "top": "upper",
    "shirt": "upper", "coat": "upper", "upper": "upper",
    # 下装
    "裤子": "lower", "下装": "lower", "裙子": "lower", "短裤": "lower",
    "半裙": "lower", "pants": "lower", "skirt": "lower", "lower": "lower",
    # 全身
    "全身": "overall", "整体": "overall", "overall": "overall",
    # 连衣裙（分割为整体）
    "连衣裙": "overall", "dress": "overall",
}


def _check_key():
    if not ALIYUN_API_KEY:
        print("  ALIYUN_API_KEY 未配置，请在 scripts/.env 中填写")
        sys.exit(1)


def _submit(payload: dict) -> str:
    req = urllib.request.Request(
        _ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {ALIYUN_API_KEY}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  HTTP {e.code}: {body}")
        sys.exit(1)
    if result.get("code"):
        print(f"  API 错误: {result}")
        sys.exit(1)
    return result["output"]["task_id"]


def _poll(task_id: str, timeout: int = 120) -> dict:
    url = _TASK_URL.format(task_id=task_id)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"Authorization": f"Bearer {ALIYUN_API_KEY}"}),
            timeout=10,
        ) as r:
            s = json.loads(r.read())
        status = s["output"]["task_status"]
        if status == "SUCCEEDED":
            return s["output"]
        if status == "FAILED":
            print(f"  任务失败: {s['output'].get('message', s['output'])}")
            sys.exit(1)
        print(f"   {status}...")
    print(f"  超时 {timeout}s")
    sys.exit(1)


# ── 核心功能 ────────────────────────────────────────────

def parse_part(text: str) -> str:
    """把自然语言描述转换为 API 的 parse_type 参数"""
    text_lower = text.lower().strip()
    for key, val in PART_MAP.items():
        if key.lower() in text_lower:
            return val
    # 默认上装
    return "upper"


def segment_garment(
    person_image_url: str,
    parse_type: str = "upper",
    output_dir: str = None,
) -> dict:
    """
    分割模特图，提取指定部位的服饰图和 bbox 坐标

    返回：
    {
        "parsing_img_url": "分割后的服饰图 URL",
        "bbox": [x1, y1, x2, y2],   # 在原图中的坐标
        "parse_type": "upper/lower/overall",
    }

    parse_type:
        "upper"   → 上装（衬衫/外套/T恤等）
        "lower"   → 下装（裤子/裙子）
        "overall" → 全身/连衣裙
    """
    _check_key()
    print(f"  ✂️  分割{parse_type}部位...")

    task_id = _submit({
        "model": "aitryon-parsing",
        "input": {"person_image_url": person_image_url},
        "parameters": {"parse_type": [parse_type]},
    })
    print(f"   分割任务: {task_id}")
    output = _poll(task_id)

    # 提取分割结果
    result = output.get("results", [{}])[0] if output.get("results") else output
    parsing_url = result.get("parsing_img_url") or output.get("parsing_img_url")
    bbox = result.get("bbox") or output.get("bbox")

    print(f"   分割完成")
    if bbox:
        print(f"     bbox: {bbox}  (x1={bbox[0]}, y1={bbox[1]}, x2={bbox[2]}, y2={bbox[3]})")

    return {
        "parsing_img_url": parsing_url,
        "bbox": bbox,
        "parse_type": parse_type,
    }


def partial_tryon(
    person_image_url: str,
    new_garment_url: str,
    replace_part: str = "upper",
    model: str = "aitryon-plus",
    output_dir: str = None,
) -> dict:
    """
    局部试穿主流程：
    1. 分割模特图，获取要保留的那部分衣服的 parsing_img
    2. 用新服装替换指定部位，保留另一部分

    replace_part: "upper"（换上装保留下装）/ "lower"（换下装保留上装）
    """
    _check_key()
    print(f"\n 局部试穿 — 替换{replace_part}，保留另一部分")

    # Step 1: 分割出要保留的那部分
    keep_part = "lower" if replace_part == "upper" else "upper"
    print(f"\n  Step 1: 分割原{keep_part}（将被保留）...")
    seg_result = segment_garment(person_image_url, parse_type=keep_part, output_dir=output_dir)
    keep_parsing_url = seg_result["parsing_img_url"]

    if not keep_parsing_url:
        print(f"   未能分割到{keep_part}，将直接试穿新服装")
        keep_parsing_url = None

    # Step 2: 组装试穿参数
    print(f"\n  Step 2: 合成试穿（新{replace_part} + 保留原{keep_part}）...")
    inp = {"person_image_url": person_image_url}

    if replace_part == "upper":
        inp["top_garment_url"] = new_garment_url
        if keep_parsing_url:
            inp["bottom_garment_url"] = keep_parsing_url
    else:  # replace_part == "lower"
        inp["bottom_garment_url"] = new_garment_url
        if keep_parsing_url:
            inp["top_garment_url"] = keep_parsing_url

    task_id = _submit({
        "model": model,
        "input": inp,
        "parameters": {"resolution": -1, "restore_face": True},
    })
    print(f"   试穿任务: {task_id}")
    output = _poll(task_id)
    result_url = output["image_url"]

    rec = save_url(result_url, stage="tryon", label=f"partial_{replace_part}", base_dir=output_dir)
    print_summary([rec], title=f"局部试穿结果（替换{replace_part}）")
    return {**rec, "seg_result": seg_result}


def get_bbox(
    image_url: str,
    parts: list = None,
    output_dir: str = None,
) -> list:
    """
    获取图片中指定服饰的 bbox 坐标
    用于商品热区标注、交互热区等场景

    parts: ["upper", "lower"] 或 ["upper"] 等，None 则返回全部
    返回：[{"parse_type": "upper", "bbox": [x1,y1,x2,y2], "parsing_img_url": "..."}, ...]
    """
    _check_key()
    parse_types = parts or ["upper", "lower"]
    print(f"\n📐 获取 bbox 坐标: {parse_types}")

    task_id = _submit({
        "model": "aitryon-parsing",
        "input": {"person_image_url": image_url},
        "parameters": {"parse_type": parse_types},
    })
    print(f"   分割任务: {task_id}")
    output = _poll(task_id)

    results = []
    raw_results = output.get("results", [output])
    for r in raw_results:
        item = {
            "parse_type":      r.get("parse_type", "unknown"),
            "bbox":            r.get("bbox"),
            "parsing_img_url": r.get("parsing_img_url"),
        }
        results.append(item)
        if item["bbox"]:
            print(f"   {item['parse_type']}: bbox={item['bbox']}")

    return results


# ── CLI ────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="局部试穿 / 获取服饰 bbox")
    parser.add_argument("--model",        required=True, help="模特图 URL 或本地路径")
    parser.add_argument("--new-garment",  help="新服装图 URL 或本地路径（局部试穿用）")
    parser.add_argument("--replace",      default="upper",
                        help="替换部位：upper（上装）/ lower（下装）/ 或中文描述如「裤子」")
    parser.add_argument("--get-bbox",     nargs="*",
                        help="获取 bbox 模式：传入部位如 upper lower，不传则获取全部")
    parser.add_argument("--output-dir",   default="./tryon_output")
    args = parser.parse_args()

    from oss_uploader import ensure_url

    model_url = ensure_url(args.model)

    if args.get_bbox is not None:
        # 获取 bbox 模式
        parts = args.get_bbox if args.get_bbox else None
        results = get_bbox(model_url, parts=parts, output_dir=args.output_dir)
        print("\n📐 bbox 结果：")
        for r in results:
            print(f"  {r['parse_type']}: {r['bbox']}")
    else:
        # 局部试穿模式
        if not args.new_garment:
            print("  局部试穿需要 --new-garment 参数")
            sys.exit(1)
        garment_url = ensure_url(args.new_garment)
        replace_part = parse_part(args.replace)
        partial_tryon(
            person_image_url=model_url,
            new_garment_url=garment_url,
            replace_part=replace_part,
            output_dir=args.output_dir,
        )
