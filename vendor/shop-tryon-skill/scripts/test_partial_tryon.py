"""
test_partial_tryon.py — 局部试衣完整测试场景

覆盖场景：
  Case 1：保留原下装，只换上衣
  Case 2：保留原上装，只换下装
  Case 3：套装全换（上下同时换）
  Case 4：纯生图兜底（不调试衣 API，验证降级路径）

用法：
  python test_partial_tryon.py            # 跑全部 Case
  python test_partial_tryon.py --case 1   # 只跑 Case 1
  python test_partial_tryon.py --dry-run  # 只打印参数，不实际调 API
"""

import os, sys, argparse, json, time
from pathlib import Path

# ── 加载 .env ──────────────────────────────────────────
try:
    from dotenv import load_dotenv
    _here = Path(__file__).resolve().parent
    for _p in [_here, _here.parent, _here.parent.parent]:
        if (_p / ".env").exists():
            load_dotenv(_p / ".env", override=False)
            break
    else:
        load_dotenv(override=False)
except ImportError:
    pass

# ── 公开测试图（无需自备）──────────────────────────────
# 模特图：穿着完整 JK 制服的模特（有上装+下装）
MODEL_WITH_FULL_OUTFIT = (
    "https://replicate.delivery/pbxt/"
    "KgwTlhCMvDagRrcVzZJbuozNJ8esPqiNAIJS3eMgHrYuHmW4/"
    "KakaoTalk_Photo_2024-04-04-21-44-45.png"
)
# 新上衣：要换上的上衣（白色毛衣）
NEW_TOP_GARMENT = (
    "https://replicate.delivery/pbxt/"
    "KgwTlZyFx5aUU3gc5gMiKuD5nNPTgliMlLUWx160G4z99YjO/"
    "sweater.webp"
)
# 新下装：要换上的裙子（可替换为你自己的图）
NEW_BOTTOM_GARMENT = NEW_TOP_GARMENT   # 临时复用，实际测试请替换为裙子图

OUTPUT_DIR = "./partial_tryon_output"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ── 测试报告 ───────────────────────────────────────────
results = []

def report(case_id, name, status, output=None, error=None, duration=None):
    icon = "✅" if status == "ok" else "❌"
    dur  = f"{duration:.1f}s" if duration else "—"
    print(f"\n{icon} Case {case_id}: {name}  [{dur}]")
    if output:
        print(f"   输出: {output}")
    if error:
        print(f"   错误: {error}")
    results.append({"case": case_id, "name": name, "status": status,
                    "output": output, "error": str(error) if error else None})


# ── 单个 Case 执行器 ───────────────────────────────────
def run_case(case_id: int, dry_run: bool = False):

    if case_id == 1:
        print(f"\n{'═'*55}")
        print(f"  Case 1：保留原下装，只换上衣")
        print(f"  模特：穿完整套装的真实模特图")
        print(f"  操作：分割出原下装 → 换上白色毛衣")
        print(f"{'═'*55}")
        print(f"  person_image : {MODEL_WITH_FULL_OUTFIT[:70]}")
        print(f"  new top      : {NEW_TOP_GARMENT[:70]}")
        print(f"  keep_bottom  : True  （保留原下装）")

        if dry_run:
            report(1, "保留原下装，换上衣", "dry-run")
            return

        t0 = time.time()
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from tryon_runner import run_pipeline
            paths = run_pipeline(
                garment_path = NEW_TOP_GARMENT,
                model_path   = MODEL_WITH_FULL_OUTFIT,
                category     = "top",
                keep_bottom  = True,          # ← 核心：保留原下装
                output_dir   = OUTPUT_DIR,
                skip_preprocess = True,       # URL 无需去背景
            )
            report(1, "保留原下装，换上衣", "ok",
                   output=paths[0] if paths else None,
                   duration=time.time() - t0)
        except SystemExit as e:
            report(1, "保留原下装，换上衣", "fail",
                   error=f"脚本中断 (exit {e.code})", duration=time.time() - t0)
        except Exception as e:
            report(1, "保留原下装，换上衣", "fail",
                   error=e, duration=time.time() - t0)

    elif case_id == 2:
        print(f"\n{'═'*55}")
        print(f"  Case 2：保留原上装，只换下装")
        print(f"  模特：穿完整套装的真实模特图")
        print(f"  操作：分割出原上装 → 换上新下装")
        print(f"{'═'*55}")
        print(f"  person_image : {MODEL_WITH_FULL_OUTFIT[:70]}")
        print(f"  new bottom   : {NEW_BOTTOM_GARMENT[:70]}")
        print(f"  keep_top     : True  （保留原上装）")

        if dry_run:
            report(2, "保留原上装，换下装", "dry-run")
            return

        t0 = time.time()
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from tryon_runner import run_pipeline
            paths = run_pipeline(
                garment_path = NEW_BOTTOM_GARMENT,
                model_path   = MODEL_WITH_FULL_OUTFIT,
                category     = "bottom",
                keep_top     = True,          # ← 核心：保留原上装
                output_dir   = OUTPUT_DIR,
                skip_preprocess = True,
            )
            report(2, "保留原上装，换下装", "ok",
                   output=paths[0] if paths else None,
                   duration=time.time() - t0)
        except SystemExit as e:
            report(2, "保留原上装，换下装", "fail",
                   error=f"脚本中断 (exit {e.code})", duration=time.time() - t0)
        except Exception as e:
            report(2, "保留原上装，换下装", "fail",
                   error=e, duration=time.time() - t0)

    elif case_id == 3:
        print(f"\n{'═'*55}")
        print(f"  Case 3：套装全换（上下同时换）")
        print(f"  模特：无服装的素模特图（AI 生成）")
        print(f"  操作：同时传上衣图 + 下装图 → 全套试穿")
        print(f"{'═'*55}")
        print(f"  top_garment  : {NEW_TOP_GARMENT[:70]}")
        print(f"  bottom_garment: （与上衣同一图，实测请替换为独立裙子图）")
        print(f"  model        : AI 自动生成")

        if dry_run:
            report(3, "套装全换（AI模特）", "dry-run")
            return

        t0 = time.time()
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from tryon_runner import aliyun_tryon, generate_model_image

            # Step 1: 生成素模特
            print("\n  Step 1: 生成素模特图...")
            model_url = generate_model_image(style="neutral_white")
            print(f"  模特图: {model_url[:70]}")

            # Step 2: 套装全换（同时传上下装）
            print("\n  Step 2: 套装试穿（top + bottom）...")
            result_url = aliyun_tryon(
                person_image_url   = model_url,
                top_garment_url    = NEW_TOP_GARMENT,
                bottom_garment_url = NEW_BOTTOM_GARMENT,
                model              = "aitryon-plus",
            )

            # 保存结果
            import urllib.request
            out_path = os.path.join(OUTPUT_DIR, "case3_full_outfit.jpg")
            urllib.request.urlretrieve(result_url, out_path)
            print(f"  💾 已保存: {out_path}")

            report(3, "套装全换（AI模特）", "ok",
                   output=out_path, duration=time.time() - t0)
        except SystemExit as e:
            report(3, "套装全换（AI模特）", "fail",
                   error=f"脚本中断 (exit {e.code})", duration=time.time() - t0)
        except Exception as e:
            report(3, "套装全换（AI模特）", "fail",
                   error=e, duration=time.time() - t0)

    elif case_id == 4:
        print(f"\n{'═'*55}")
        print(f"  Case 4：纯生图兜底（不调试衣 API）")
        print(f"  场景：试衣 API 不可用时的降级路径")
        print(f"  操作：服装参考图 + 描述 → 生图模型 → 3个场景变体")
        print(f"{'═'*55}")
        print(f"  garment_ref  : {NEW_TOP_GARMENT[:70]}")
        print(f"  desc         : white sweater casual style")
        print(f"  variants     : 3")
        print(f"  provider     : auto（优先 doubao）")

        if dry_run:
            report(4, "纯生图兜底（3变体）", "dry-run")
            return

        t0 = time.time()
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from image_gen_tryon import run as gen_run
            saved = gen_run(
                garment_path = NEW_TOP_GARMENT,
                garment_desc = "white sweater casual style",
                category     = "top",
                num_variants = 3,
                output_dir   = OUTPUT_DIR,
            )
            report(4, "纯生图兜底（3变体）", "ok" if saved else "fail",
                   output=f"{len(saved)} 张图片保存至 {OUTPUT_DIR}",
                   duration=time.time() - t0)
        except SystemExit as e:
            report(4, "纯生图兜底（3变体）", "fail",
                   error=f"脚本中断 (exit {e.code})", duration=time.time() - t0)
        except Exception as e:
            report(4, "纯生图兜底（3变体）", "fail",
                   error=e, duration=time.time() - t0)


# ── 汇总报告 ───────────────────────────────────────────
def print_summary():
    print(f"\n{'═'*55}")
    print(f"  测试汇总")
    print(f"{'═'*55}")
    ok   = [r for r in results if r["status"] == "ok"]
    fail = [r for r in results if r["status"] == "fail"]
    dry  = [r for r in results if r["status"] == "dry-run"]

    for r in results:
        icon = {"ok": "✅", "fail": "❌", "dry-run": "🔍"}.get(r["status"], "?")
        print(f"  {icon} Case {r['case']}: {r['name']}")
        if r["error"]:
            print(f"      └─ {r['error']}")

    print(f"\n  结果: {len(ok)} 通过 / {len(fail)} 失败 / {len(dry)} 预演")

    if fail:
        print(f"\n  ❌ 失败项目排查建议：")
        for r in fail:
            print(f"     Case {r['case']}: {r['error']}")
            if "ALIYUN_API_KEY" in str(r["error"]):
                print(f"       → 在 .env 填写 ALIYUN_API_KEY")
            elif "OSS" in str(r["error"]):
                print(f"       → 在 .env 填写 OSS_* 配置")
            elif "生图" in str(r["error"]) or "provider" in str(r["error"]).lower():
                print(f"       → 在 .env 填写 ARK_API_KEY")

    # 保存详细 JSON 报告
    report_path = os.path.join(OUTPUT_DIR, "test_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n  📄 详细报告: {report_path}")


# ── CLI ────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="局部试衣测试")
    parser.add_argument("--case",    type=int, help="只跑指定 Case（1-4）")
    parser.add_argument("--dry-run", action="store_true", help="只打印参数，不调 API")
    parser.add_argument("--output-dir", default="./partial_tryon_output")
    args = parser.parse_args()

    OUTPUT_DIR = args.output_dir
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("\n🧪 局部试衣测试开始")
    print(f"   输出目录: {OUTPUT_DIR}")
    print(f"   模式: {'Dry Run（预演）' if args.dry_run else '实际执行'}\n")

    cases = [args.case] if args.case else [1, 2, 3, 4]
    for c in cases:
        run_case(c, dry_run=args.dry_run)

    print_summary()
