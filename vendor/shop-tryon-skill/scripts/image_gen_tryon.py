"""
image_gen_tryon.py — 豆包 Seedream 生图试穿

内部函数路由（不是 CLI 参数）：
  · multi_image_to_single  模特图+服装图 → 单张换装（核心试穿，有参考图时首选）
  · image_to_single        单张参考图 → 单张生图
  · multi_image_to_multi   多图参考 → 多场景组图（流式）
  · image_to_multi         单张参考图 → 多场景组图（流式）
  · text_to_single         纯描述 → 单张
  · text_to_multi          纯描述 → 多张（流式）

CLI 用法：
  # 核心试穿（模特图+服装图 → 换装，推荐带双参考图）
  python image_gen_tryon.py \\
    --model-img https://xxx/model.jpg \\
    --garment-img https://xxx/shirt.jpg

  # 服装图 + 变体数量
  python image_gen_tryon.py \\
    --garment-img shirt.jpg \\
    --desc "年轻亚洲女性穿着这件衬衫" \\
    --variants 3

  # 纯文字描述 → 单图
  python image_gen_tryon.py --desc "黑色JK水手服，白色宽领，百褶裙"

  # 自定义 prompt，指定后端
  python image_gen_tryon.py \\
    --garment-img coat.jpg \\
    --prompt "woman wearing this coat in Tokyo" \\
    --image-backend douban
"""

import os, sys, json, argparse, base64, datetime, time
from pathlib import Path

# ── 加载 .env ──────────────────────────────────────────
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from output_manager import load_env
load_env(__file__)

# ── 输出管理 ──────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from output_manager import save_url, save_b64, save_video, print_summary, get_output_dir
from model_manager import recommend_model, get_model_image, format_model_list, load_models
from jimeng_client import JimengClient, is_jimeng_configured

ARK_API_KEY       = os.getenv("ARK_API_KEY", "")
OPENAI_BASE_URL   = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ARK_BASE_URL  = os.getenv("ARK_BASE_URL",    "https://ark.cn-beijing.volces.com/api/v3")
ARK_MODEL     = os.getenv("ARK_IMAGE_MODEL", "doubao-seedream-5-0-260128")

# ── 尺寸 & 比例 ────────────────────────────────────────
# Seedream 统一用 "2K"，通过 prompt 描述控制实际比例
# 试穿/全身人像必须竖版，否则下半身会被截断
_SIZE = "2K"
# 一致性约束前缀（纯生图模式，无参考图时必须加）
_CONSISTENCY = (
    "consistent outfit styling, same shoes and accessories throughout, "
    "same hairstyle, no random changes to appearance, "
    "photorealistic e-commerce fashion photography, "
    "clean white or light gray studio background, bright even studio lighting, "
    "absolutely no black background, no dark background, no shadowy environment, "
)

_ORIENT = {
    "portrait":  "vertical portrait, 9:16 aspect ratio, full body visible head to toe, clean bright background,",
    "tryon":     "vertical full body portrait, 9:16 aspect ratio, complete outfit visible from head to toe, "
                 "clean white studio background, bright even lighting, no dark or black background,",
    "garment":   "square flat lay product photo, 1:1 aspect ratio, pure white background, bright even lighting,",
    "landscape": "horizontal wide shot, 16:9 aspect ratio, bright clean background,",
}

def _with_orient(prompt: str, content_type: str = "tryon") -> str:
    """在 prompt 前插入比例/方向描述 + 一致性约束"""
    hint = _ORIENT.get(content_type, _ORIENT["tryon"])
    return f"{hint} {_CONSISTENCY}{prompt}"


# ── 服装锁定前缀模板（按部位分类）────────────────────────────
# run() 循环中根据 garment_part 选择对应模板，注入每条 prompt 头部
# 作用：强制 Seedream 严格复现图2服装的所有视觉细节，防止模型自由发挥
_GARMENT_LOCK = {
    "top": (
        "Using reference image 1 (model) and reference image 2 (top garment): "
        "dress the model from image 1 in the EXACT top/upper-body garment shown in image 2. "
        "Preserve every detail of image 2 — color, cut, neckline, sleeves, fabric texture, "
        "collar, cuffs, and any logos or prints. "
        "Keep the model's original lower-body clothing (pants/skirt/shoes) unchanged. "
        "Do NOT replace or alter the garment. "
    ),
    "bottom": (
        "Using reference image 1 (model) and reference image 2 (bottom garment): "
        "dress the model from image 1 in the EXACT bottom garment shown in image 2. "
        "Preserve every detail of image 2 — color, cut, length, waistband, fabric texture, "
        "pleats, and any patterns or prints. "
        "Keep the model's original upper-body clothing and accessories unchanged. "
        "Do NOT replace or alter the garment. "
    ),
    "one_piece": (
        "Using reference image 1 (model) and reference image 2 (one-piece garment): "
        "dress the model from image 1 in the EXACT one-piece garment shown in image 2. "
        "Preserve every detail of image 2 — color, cut, neckline, hemline, fabric texture, "
        "and any patterns, prints, or embellishments. "
        "Do NOT replace or alter the garment. "
    ),
    "full": (
        "Using reference image 1 (model) and reference image 2 (full outfit): "
        "dress the model from image 1 in the EXACT complete outfit shown in image 2. "
        "Preserve every detail of both upper and lower garments in image 2 — colors, cuts, "
        "fabric textures, and any logos, patterns, or prints. "
        "Do NOT replace or alter any part of the outfit. "
    ),
}


def _ark_client():
    if not ARK_API_KEY:
        print("  ARK_API_KEY 未配置，请在 .env 中填写")
        sys.exit(1)
    try:
        from openai import OpenAI
        return OpenAI(base_url=ARK_BASE_URL, api_key=ARK_API_KEY)
    except ImportError:
        print("  openai 未安装: pip install openai")
        sys.exit(1)


def _print_api_error(backend: str, e: Exception, step: int = None, scene: str = ""):
    """API 调用失败时打印清晰的错误报告（含费用/认证/频率诊断）"""
    msg = str(e)
    step_info = f"（第 {step} 张）" if step else ""
    scene_info = f" [{scene}]" if scene else ""
    _lo = msg.lower()
    if any(k in _lo for k in ("balance", "quota", "insufficient", "credit", "欠费", "余额")):
        hint = "🔴 账户余额不足或免费额度已用完\n    请登录火山引擎/豆包控制台充值后重试"
    elif any(k in _lo for k in ("auth", "unauthorized", "invalid", "api key", "401")):
        hint = "🔴 API Key 无效或已过期，请在 .env 中更新 ARK_API_KEY"
    elif any(k in _lo for k in ("rate", "429", "too many", "频率")):
        hint = "🟡 请求频率超限，稍等 10~30 秒后重试"
    elif any(k in _lo for k in ("timeout", "connection", "network", "超时")):
        hint = "🟡 网络超时，请检查网络连接后重试"
    else:
        hint = "请查看上方错误信息，并检查 ARK_API_KEY 和账户状态"
    print(f"\n{'─'*57}")
    print(f"❌  {backend} 生图失败{step_info}{scene_info}")
    print(f"    错误：{msg[:300]}")
    print(f"    {hint}")
    print(f"{'─'*57}\n")


_UNSUPPORTED_IMG_EXTS = {
    ".avif": "AVIF", ".heic": "HEIC", ".heif": "HEIF",
    ".tiff": "TIFF", ".tif":  "TIFF", ".gif":  "GIF",
    ".svg":  "SVG",  ".webp": "WebP",
}


def _check_url_format(url: str):
    """
    检查图片 URL 格式是否被豆包 Seedream 支持。
    不支持（如 .avif / .heic / .webp）时抛出 ValueError，给出明确提示。
    """
    from urllib.parse import urlparse
    path_lower = urlparse(url).path.lower().split("?")[0]
    for part in reversed(path_lower.split(".")):
        if part:
            ext = "." + part
            fmt = _UNSUPPORTED_IMG_EXTS.get(ext)
            if fmt:
                raise ValueError(
                    f"图片格式 {fmt}（{ext}）不被豆包 Seedream API 支持。\n"
                    "支持的格式：JPG / JPEG / PNG / BMP。\n"
                    "请提供 JPG 或 PNG 格式的图片 URL，或下载后转换格式再重新上传。"
                )
            break


def _to_input(path_or_url: str) -> str:
    """
    Ark API 只接受公网 URL 或 base64 data URI
    本地文件自动转 base64，URL 直接返回
    """
    if path_or_url.startswith("http") or path_or_url.startswith("data:"):
        if path_or_url.startswith("http"):
            _check_url_format(path_or_url)
        return path_or_url
    import base64
    ext = Path(path_or_url).suffix.lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "bmp": "image/bmp",
            "webp": "image/webp"}.get(ext, "image/jpeg")
    with open(path_or_url, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"


def _save_b64(b64_str: str, output_dir: str, prefix: str = "tryon") -> str:
    """保存 base64 图片到文件"""
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
    fpath = os.path.join(output_dir, f"{prefix}_{ts}.png")
    with open(fpath, "wb") as f:
        f.write(base64.b64decode(b64_str))
    return fpath


# ──────────────────────────────────────────────────────
# 6 种生图函数
# ──────────────────────────────────────────────────────

def text_to_single(prompt: str, size: str = _SIZE, content_type: str = "tryon", output_dir: str = None) -> str:
    """模式5：文生图-单张"""
    client = _ark_client()
    prompt = _with_orient(prompt, content_type)
    print(f"  🎨 文生图-单张 | {prompt[:80]}...")
    resp = client.images.generate(
        model=ARK_MODEL,
        prompt=prompt,
        size=size,
        response_format="url",
        extra_body={"watermark": False},
    )
    url = resp.data[0].url
    print(f"  生成完成: {url[:60]}...")
    rec = save_url(url, stage="variants", label="text2single", base_dir=output_dir)
    return rec["path"]


def text_to_multi(
    prompt: str,
    max_images: int = 3,
    size: str = _SIZE,
    content_type: str = "tryon",
    output_dir: str = None,
) -> list:
    """模式6：文生图-组图（流式）"""
    client = _ark_client()
    prompt = _with_orient(prompt, content_type)
    print(f"  🎨 文生图-组图 | {max_images}张 | {prompt[:80]}...")
    stream = client.images.generate(
        model=ARK_MODEL,
        prompt=prompt,
        size=size,
        response_format="b64_json",
        stream=True,
        extra_body={
            "watermark": False,
            "sequential_image_generation": "auto",
            "sequential_image_generation_options": {"max_images": max_images},
        },
    )
    saved = []
    for event in stream:
        if event is None:
            continue
        if event.type == "image_generation.partial_succeeded" and event.b64_json:
            rec = save_b64(event.b64_json, stage="variants", label=f"text2multi_{len(saved)+1}", base_dir=output_dir)
            saved.append(rec["path"])
            print(f"  图片 {len(saved)}/{max_images}")
        elif event.type == "image_generation.completed" and event.usage:
            print(f"  📊 用量: {event.usage}")
    return saved


def image_to_single(
    prompt: str,
    reference_image: str,
    size: str = _SIZE,
    content_type: str = "tryon",
    output_dir: str = None,
) -> str:
    """模式2：图生图-单张参考图→单张"""
    client = _ark_client()
    prompt = _with_orient(prompt, content_type)
    print(f"  🎨 图生图-单张→单张 | {prompt[:80]}...")
    resp = client.images.generate(
        model=ARK_MODEL,
        prompt=prompt,
        size=size,
        response_format="url",
        extra_body={
            "image": _to_input(reference_image),
            "watermark": False,
        },
    )
    url = resp.data[0].url
    print(f"  生成完成: {url[:60]}...")
    rec = save_url(url, stage="variants", label="img2single", base_dir=output_dir)
    return rec["path"]


def image_to_multi(
    prompt: str,
    reference_image: str,
    max_images: int = 3,
    size: str = _SIZE,
    content_type: str = "tryon",
    output_dir: str = None,
) -> list:
    """模式4：图生图-单张参考图→组图（流式）"""
    client = _ark_client()
    prompt = _with_orient(prompt, content_type)
    print(f"  🎨 图生图-单张→组图 | {max_images}张 | {prompt[:80]}...")
    stream = client.images.generate(
        model=ARK_MODEL,
        prompt=prompt,
        size=size,
        response_format="b64_json",
        stream=True,
        extra_body={
            "image": _to_input(reference_image),
            "watermark": False,
            "sequential_image_generation": "auto",
            "sequential_image_generation_options": {"max_images": max_images},
        },
    )
    saved = []
    for event in stream:
        if event is None:
            continue
        if event.type == "image_generation.partial_succeeded" and event.b64_json:
            rec = save_b64(event.b64_json, stage="variants", label=f"img2multi_{len(saved)+1}", base_dir=output_dir)
            saved.append(rec["path"])
            print(f"  图片 {len(saved)}/{max_images}")
        elif event.type == "image_generation.completed" and event.usage:
            print(f"  📊 用量: {event.usage}")
    return saved


def multi_image_to_single(
    prompt: str,
    reference_images: list,
    size: str = _SIZE,
    content_type: str = "tryon",
    output_dir: str = None,
) -> str:
    """模式1：图生图-多张参考图→单张（核心试穿）"""
    client = _ark_client()
    prompt = _with_orient(prompt, content_type)
    print(f"  🎨 图生图-多图→单张 | {len(reference_images)}张参考 | {prompt[:80]}...")
    resp = client.images.generate(
        model=ARK_MODEL,
        prompt=prompt,
        size=size,
        response_format="url",
        extra_body={
            "image": [_to_input(u) for u in reference_images],   # 传列表
            "watermark": False,
            # 注意：不要传 sequential_image_generation —— 该参数仅 Seedream 5.0
            # 标准/Pro 版支持，doubao-seedream-5-0-flash 会报 InvalidParameter。
            # 单张生成默认即单张，无需显式关闭。
        },
    )
    url = resp.data[0].url
    print(f"  生成完成: {url[:60]}...")
    rec = save_url(url, stage="tryon", label="multi2single", base_dir=output_dir)
    return rec["path"]


def multi_image_to_multi(
    prompt: str,
    reference_images: list,
    max_images: int = 3,
    size: str = _SIZE,
    content_type: str = "tryon",
    output_dir: str = None,
) -> list:
    """模式3：图生图-多张参考图→组图（流式）"""
    client = _ark_client()
    prompt = _with_orient(prompt, content_type)
    print(f"  🎨 图生图-多图→组图 | {len(reference_images)}张参考 → {max_images}张输出...")
    stream = client.images.generate(
        model=ARK_MODEL,
        prompt=prompt,
        size=size,
        response_format="b64_json",
        stream=True,
        extra_body={
            "image": [_to_input(u) for u in reference_images],
            "watermark": False,
            "sequential_image_generation": "auto",
            "sequential_image_generation_options": {"max_images": max_images},
        },
    )
    saved = []
    for event in stream:
        if event is None:
            continue
        if event.type == "image_generation.partial_succeeded" and event.b64_json:
            rec = save_b64(event.b64_json, stage="tryon", label=f"multi2multi_{len(saved)+1}", base_dir=output_dir)
            saved.append(rec["path"])
            print(f"  图片 {len(saved)}/{max_images}")
        elif event.type == "image_generation.completed" and event.usage:
            print(f"  📊 用量: {event.usage}")
    return saved


# ──────────────────────────────────────────────────────
# 生图后端选择（两个均配置时交互询问）
# ──────────────────────────────────────────────────────

def _ask_image_backend() -> str:
    """
    豆包 Seedream 和即梦 AI 均已配置时，询问用户选择生图后端。
    仅在 run() 开头调用一次，整个 run 内所有生图复用该选择。
    返回 'jimeng' 或 'douban'。
    """
    print()
    print("\u250c" + "\u2500" * 57 + "\u2510")
    print("\u2502  \U0001f3a8 \u8c46\u5305 Seedream \u548c\u5373\u68a6 AI \u5747\u5df2\u914d\u7f6e\uff0c\u8bf7\u9009\u62e9\u751f\u56fe\u540e\u7aef                    \u2502")
    print("\u251c" + "\u2500" * 57 + "\u2524")
    print("\u2502  1. \u8c46\u5305 Seedream\uff08\u706b\u5c71\u65b9\u821f ARK\uff09                                  \u2502")
    print("\u2502     \u00b7 \u56fe\u751f\u56fe\uff1a\u7cbe\u786e\u9501\u5b9a\u53c2\u8003\u56fe\u7684\u4eba\u7269\u5f62\u8c61/\u670d\u88c5\u5916\u89c2                      \u2502")
    print("\u2502     \u00b7 \u591a\u53c2\u8003\u56fe\u878d\u5408\u7a33\u5b9a\uff08\u6a21\u7279+\u670d\u88c5\u540c\u65f6\u4f20\u5165\uff09                     \u2502")
    print("\u2502     \u00b7 \u63a8\u8350\uff1a\u6709\u53c2\u8003\u56fe\u7684\u6362\u88c5/\u8bd5\u7a7f\u573a\u666f                              \u2502")
    print("\u2502                                                           \u2502")
    print("\u2502  2. \u5373\u68a6 AI\uff08Jimeng\uff09                                           \u2502")
    print("\u2502     \u00b7 \u6587\u751f\u56fe\u8d28\u91cf\u4f18\u79c0\uff0c\u521b\u610f\u751f\u56fe\u6548\u679c\u597d                              \u2502")
    print("\u2502     \u00b7 \u6ce8\u610f\uff1a\u53c2\u8003\u56fe\u4ec5\u4e3a\u300c\u98ce\u683c\u53c2\u8003\u300d\uff0c\u4e0d\u9501\u5b9a\u4eba\u7269\u5916\u89c2                \u2502")
    print("\u2502     \u00b7 \u63a8\u8350\uff1a\u7eaf\u6587\u5b57\u63cf\u8ff0\u751f\u56fe\u3001\u65e0\u53c2\u8003\u56fe\u7684\u521b\u610f\u573a\u666f                  \u2502")
    print("\u2514" + "\u2500" * 57 + "\u2518")
    # 本函数只在 sys.stdin.isatty() 为 True 时被调用
    while True:
        try:
            choice = input("  请输入选项 [1/2，默认 1]: ").strip()
        except EOFError:
            print("  （无法读取输入，自动选择豆包 Seedream）")
            return "douban"
        if choice in ("", "1"):
            return "douban"
        elif choice == "2":
            return "jimeng"
        else:
            print("  ⚠️  无效输入，请输入 1 或 2")


# ──────────────────────────────────────────────────────
# 生图路由 — 统一入口
# ──────────────────────────────────────────────────────

def generate_image_auto(
    prompt: str,
    reference_images: list = None,
    size: str = _SIZE,
    content_type: str = "tryon",
    output_dir: str = None,
    force_single: bool = True,
    image_backend: str = "auto",  # "auto" / "jimeng" / "douban"
) -> list:
    """
    生图路由（尊重 image_backend 选择）。

    auto 规则：即梦 t2i_v40 的 image_urls 是「风格参考」而非「人物锁定」，
    有参考图时走豆包（锁定外观），无参考图时走即梦（文生图质量好）。
    """
    output_dir = get_output_dir(output_dir)

    _use_jimeng = False
    if image_backend == "jimeng":
        if is_jimeng_configured():
            _use_jimeng = True
            if reference_images:
                print("  ⚠️  [即梦] 已选即梦模式，参考图仅为风格参考（不锁定人物外观）")
        else:
            print("  ⚠️  即梦未配置，自动切换到豆包 Seedream")
    elif image_backend == "douban":
        _use_jimeng = False
    else:  # auto
        if is_jimeng_configured() and not reference_images:
            _use_jimeng = True
        elif is_jimeng_configured() and reference_images:
            print("  ℹ️  [auto] 有参考图时走豆包（精准锁定人物形象）")

    if _use_jimeng:
        try:
            client = JimengClient()
            urls = client.generate_image(
                prompt=prompt,
                image_urls=None,
                width=1024,
                height=1792,
                force_single=force_single,
            )
            paths = []
            for i, url in enumerate(urls):
                label = f"jimeng_{i+1}"
                rec = save_url(url, stage="variants",
                               label=label, base_dir=output_dir)
                paths.append(rec["path"])
            return paths
        except Exception as e:
            print(f"  ⚠️  [即梦] 图像生成失败: {e}")
            print(f"  ↩️  回退到豆包 Seedream...")

    # 豆包 Seedream 路径
    if reference_images and len(reference_images) >= 2:
        path = multi_image_to_single(
            prompt, reference_images, size=size, content_type=content_type, output_dir=output_dir
        )
        return [path]
    elif reference_images and len(reference_images) == 1:
        path = image_to_single(
            prompt, reference_images[0], size=size, content_type=content_type, output_dir=output_dir
        )
        return [path]
    else:
        path = text_to_single(prompt, size=size, content_type=content_type, output_dir=output_dir)
        return [path]


def generate_image_multi_auto(
    prompt: str,
    reference_images: list = None,
    n: int = 3,
    size: str = _SIZE,
    content_type: str = "tryon",
    output_dir: str = None,
    image_backend: str = "auto",  # "auto" / "jimeng" / "douban"
) -> list:
    """
    生成 n 张变体图（尊重 image_backend 选择）。
    """
    output_dir = get_output_dir(output_dir)
    results = []

    _use_jimeng = False
    if image_backend == "jimeng" and is_jimeng_configured():
        _use_jimeng = True
    elif image_backend == "douban":
        _use_jimeng = False
    elif image_backend == "auto" and is_jimeng_configured() and not reference_images:
        _use_jimeng = True

    if _use_jimeng:
        try:
            client = JimengClient()
            for i in range(n):
                print(f"  [{i+1}/{n}] 即梦生成...")
                urls = client.generate_image(
                    prompt=prompt,
                    image_urls=None,
                    width=1024,
                    height=1792,
                    force_single=True,
                )
                for url in urls:
                    rec = save_url(url, stage="variants", label=f"jimeng_multi_{len(results)+1}",
                                   base_dir=output_dir)
                    results.append(rec["path"])
                    print(f"  图片 {len(results)}/{n}")
            return results
        except Exception as e:
            print(f"  ⚠️  [即梦] 多图生成失败: {e}")
            print(f"  ↩️  回退到豆包 Seedream...")

    # 豆包 Seedream 路径
    if reference_images and len(reference_images) >= 2:
        saved = multi_image_to_multi(
            prompt, reference_images, max_images=n, size=size, output_dir=output_dir
        )
    elif reference_images and len(reference_images) == 1:
        saved = image_to_multi(
            prompt, reference_images[0], max_images=n, size=size, output_dir=output_dir
        )
    else:
        saved = text_to_multi(prompt, max_images=n, size=size, output_dir=output_dir)
    results.extend(saved)
    return results


# ──────────────────────────────────────────────────────
# Claude API system prompt（仅在调用 Anthropic API 时使用）
# ⚠️ 此字符串是发给 Claude 的指令，不是图像生成 prompt，不要直接用于生图
# ──────────────────────────────────────────────────────

# 发给 AI 模型的 system 提示，告知它如何生成试穿 prompt 列表
# 其中「输出格式」部分只是告知模型返回 JSON 的格式，非真实试穿 prompt
_PROMPT_GEN_SYSTEM = """你是专业的 AI 试穿效果图提示词专家。
根据用户的服装信息和场景需求，生成适合豆包 Seedream 的图生图 prompt。

规则：
- prompt 用中文或英文都可以，Seedream 支持中文
- 如果是换装场景（模特图+服装图），参考图顺序固定为：图1=模特，图2=服装
  每条 prompt 必须明确写："让图1的模特穿上图2的服装，完整保留图2服装的颜色、款式、面料、
  领口、袖口等所有视觉细节，不得改变或替换服装"
- 服装锁定是核心约束，每个场景 prompt 都必须包含对图2服装细节的明确保留指令
- 如果要生成多个场景，每个场景 prompt 要有差异（不同时间/地点/光线/姿势），但服装约束不变
- 输出 JSON 数组，每项含 prompt 和 scene_note（中文场景说明）
- 只返回 JSON，不要其他内容

输出格式（仅为格式示例，内容需根据用户请求动态生成）：
[
  {"prompt": "<根据服装和场景生成的中英文 prompt>", "scene_note": "<中文场景名称>"}
]"""


def generate_prompts_with_ai(
    garment_desc: str,
    has_model_img: bool,
    has_garment_img: bool,
    n: int = 1,
    style_hint: str = "",
    garment_part: str = "top",
) -> list:
    """调用 AI 接口动态生成 prompt 列表，返回 [{"prompt": ..., "scene_note": ...}, ...]
    当前实现：Anthropic API（需配置 ANTHROPIC_API_KEY）
    """
    import urllib.request

    _PART_LABELS = {"top": "上装", "bottom": "下装", "one_piece": "单件连体（连衣裙/连体裤）", "full": "上下装套装"}
    part_label = _PART_LABELS.get(garment_part, "上装")

    user_msg = f"""
服装描述：{garment_desc}
服装部位：{part_label}
有模特图：{"是" if has_model_img else "否"}
有服装图：{"是" if has_garment_img else "否"}
需要生成：{n} 个场景
风格偏好：{style_hint or "不限"}

请生成 {n} 个试穿效果图 prompt。
注意：服装部位是「{part_label}」，prompt 中必须明确说明只替换该部位，保留其他部位不变。
"""
    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 800,
        "system": _PROMPT_GEN_SYSTEM,
        "messages": [{"role": "user", "content": user_msg}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = json.loads(r.read())["content"][0]["text"].strip()
    raw = raw.lstrip("```json").rstrip("```").strip()
    return json.loads(raw)


# ── 多角度锁定生成 ────────────────────────────────────────
# 同模特同服装，只改拍摄角度
# 必须同时传入模特图和服装图作为参考图，否则每次模特/服装会随机变化

# 多角度预设方案组 —— 用户可选或自定义
ANGLE_PRESETS = {
    "ecommerce": {
        "name": "电商标准拍",
        "desc": "正面+侧面+背面，适合电商主图和详情页",
        "angles": [
            {"label": "front", "scene": "正面站立", "suffix": "front view, facing camera directly, standing straight, full body"},
            {"label": "side",  "scene": "侧面展示", "suffix": "three-quarter side view, slight turn right, natural standing pose, full body"},
            {"label": "back",  "scene": "背面转身", "suffix": "back view, model looking over left shoulder toward camera, full body"},
        ],
    },
    "catwalk": {
        "name": "走秀动态",
        "desc": "行走+转身+定点pose，适合视频素材",
        "angles": [
            {"label": "walking", "scene": "T台行走", "suffix": "confident catwalk stride, full body, fashion runway lighting"},
            {"label": "turn",    "scene": "转身回眸", "suffix": "turning around, looking over shoulder, elegant pose, full body"},
            {"label": "pose",    "scene": "定点造型", "suffix": "fashion editorial pose, hand on hip, slight hip tilt, full body"},
        ],
    },
    "detail": {
        "name": "细节特写",
        "desc": "面料+领口+全身，适合展示工艺细节",
        "angles": [
            {"label": "fabric", "scene": "面料特写", "suffix": "close-up on fabric texture and material detail, upper body"},
            {"label": "collar", "scene": "领口细节", "suffix": "upper body close-up, showing collar, neckline, and stitching details"},
            {"label": "full",   "scene": "全身正面", "suffix": "front view, full body head to toe, showing complete outfit details"},
        ],
    },
    "lifestyle": {
        "name": "生活场景",
        "desc": "自然姿态+坐姿+行走，呈现日常穿搭感",
        "angles": [
            {"label": "casual",  "scene": "自然站立", "suffix": "relaxed natural standing pose, slight smile, full body"},
            {"label": "sitting", "scene": "坐姿展示", "suffix": "seated pose on stool, legs crossed, showing outfit drape, full body visible"},
            {"label": "walking", "scene": "街头漫步", "suffix": "casual walking pose, natural stride, full body"},
        ],
    },
}


def generate_multi_angle(
    model_img: str,
    garment_img: str,
    base_desc: str = "",
    n: int = 3,
    size: str = _SIZE,
    output_dir: str = None,
    preset: str = "ecommerce",
    garment_part: str = "top",
) -> list:
    """
    同模特同服装多角度生图

    核心原理：同时传入模特图 + 服装图作为参考，
    豆包 Seedream 会锁住这两张图的外观，只按 prompt 改变角度/姿势。
    不传参考图的话每次都会随机生成，模特脸和服装款式都会变。

    preset: 预设方案组名称（ecommerce/catwalk/detail/lifestyle），
            也可传逗号分隔的多个方案如 "ecommerce,detail"
    garment_part: 服装部位（top/bottom/one_piece/full）
    n: 从选中方案组中取前 n 个角度

    返回：[{"angle": {...}, "path": "本地路径"}, ...]
    """
    # 合并多个预设（如 "ecommerce,detail"）
    preset_keys = [k.strip() for k in preset.split(",")]
    angles = []
    for key in preset_keys:
        p = ANGLE_PRESETS.get(key)
        if p:
            angles.extend(p["angles"])
        else:
            print(f"  ⚠️ 未知预设 '{key}'，已跳过")
    if not angles:
        angles = ANGLE_PRESETS["ecommerce"]["angles"]
    angles = angles[:n]

    # 服装部位锁定前缀
    lock = _GARMENT_LOCK.get(garment_part, _GARMENT_LOCK["top"])

    results = []
    for i, angle in enumerate(angles):
        print(f"\n  [{i+1}/{len(angles)}] 生成{angle['scene']}...")
        prompt = (
            f"{lock}"
            "Keep exactly the same model face, hairstyle, clothing, shoes, "
            "and accessories as the reference images. "
            "Do not add or remove any clothing items or accessories. "
            f"{base_desc + ', ' if base_desc else ''}"
            f"{angle['suffix']}. "
            "Photorealistic, e-commerce fashion photography, "
            "9:16 vertical portrait, full body head to toe, "
            "consistent styling throughout."
        )
        path = multi_image_to_single(
            prompt=prompt,
            reference_images=[model_img, garment_img],
            size=size,
            content_type="tryon",
            output_dir=output_dir,
        )
        results.append({"angle": angle, "path": path})
        print(f"  {angle['scene']} → {Path(path).name}")
    return results


# 内置默认 prompt（无 AI Key 或 AI 调用失败时的后备）
# ⚠️ tryon_* 场景只描述风格/环境/光线，不写服装锁定指令
#    （服装锁定由 run() 的 _lock_prefix 按 garment_part 自动注入）
# garment_* 用于无模特图时，需包含模特描述 + {desc} 占位
DEFAULT_PROMPTS = {
    "tryon_professional": (
        "Create a hyper-realistic full-body fashion photo. "
        "Key fit details: garment drapes naturally on the model, realistic wrinkles matching pose. "
        "High-fidelity: precisely preserve original fabric texture, color, and any logos. "
        "Seamless blend: match ambient light, color temperature, and shadow direction. "
        "Photography style: clean e-commerce lookbook, Canon EOS R5, 50mm f/1.8 lens. "
        "Full body head to toe, 9:16 vertical portrait, white studio background."
    ),
    "tryon_single":  "纯白摄影棚背景，明亮均匀打光，专业服装产品图，保持模特原有姿势和面部特征，绝对禁止黑色或深色背景",
    "tryon_campus":  "日系校园背景，樱花飘落，清晨温柔阳光，明亮自然的环境光",
    "tryon_street":  "城市街头场景，明亮自然光，时尚街拍风格，光线充足",
    "tryon_indoor":  "温馨咖啡馆室内，暖色调，明亮的生活方式摄影，可见明亮光源",
    "tryon_nature":  "户外自然场景，绿色背景，明亮自然光，清新通透",
    "garment_white": "年轻亚洲女性模特，身材纤细，穿着{desc}，纯白背景，专业摄影棚，明亮均匀打光，正面站姿，绝对禁止黑色或深色背景",
    "garment_campus":"年轻亚洲女性模特，穿着{desc}，日系校园背景，樱花，明亮自然光，青春活力",
    "garment_street":"年轻亚洲女性模特，穿着{desc}，城市街头，明亮自然光，时尚街拍",
}


def get_prompts(
    garment_desc: str,
    has_model_img: bool,
    has_garment_img: bool,
    n: int = 1,
    style_hint: str = "",
    garment_part: str = "top",
) -> list:
    """获取 prompt 列表（有 Claude 则调用，否则用内置默认）"""
    if ANTHROPIC_API_KEY:
        try:
            print("  📝 调用 AI 生成 prompt...")
            prompts = generate_prompts_with_ai(
                garment_desc, has_model_img, has_garment_img, n, style_hint,
                garment_part=garment_part,
            )
            print(f"  生成了 {len(prompts)} 个 prompt")
            return prompts
        except Exception as e:
            print(f"  ⚠️  AI 调用失败: {e}，使用内置 prompt")

    # 内置默认
    if has_model_img and has_garment_img:
        # 模特图+服装图同时存在：首选专业增强约束 Prompt
        keys = ["tryon_professional", "tryon_single", "tryon_campus", "tryon_street", "tryon_indoor", "tryon_nature"]
        return [
            {"prompt": DEFAULT_PROMPTS[keys[i % len(keys)]], "scene_note": keys[i % len(keys)]}
            for i in range(n)
        ]
    else:
        keys = ["garment_white", "garment_campus", "garment_street"]
        return [
            {
                "prompt": DEFAULT_PROMPTS[keys[i % len(keys)]].format(desc=garment_desc or "服装"),
                "scene_note": keys[i % len(keys)]
            }
            for i in range(n)
        ]


# ──────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────

def run(
    model_img: str = None,        # 模特图 URL 或本地路径
    garment_img: str = None,      # 服装图 URL 或本地路径
    garment_desc: str = None,     # 服装文字描述
    custom_prompt: str = None,    # 直接指定 prompt（跳过 Claude 生成）
    variants: int = 1,            # 生成变体数量
    style_hint: str = "",         # 风格偏好
    size: str = "2K",
    output_dir: str = None,
    image_backend: str = "auto",  # "auto"=两个都配置时交互询问 / "jimeng" / "douban"
    output_filename: str = None,  # 输出文件名（可选，生成后重命名第一个结果）
    garment_part: str = "top",    # 服装部位：top/bottom/one_piece/full
) -> list:
    output_dir = get_output_dir(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    desc = garment_desc or (Path(garment_img).stem if garment_img else "服装")
    results = []

    # 有模特图+服装图的试穿模式：即梦无法精准锁定服装外观，强制走豆包
    # 即梦的 image_urls 仅为风格参考，会生成多人/违物理图片
    if model_img and garment_img:
        if image_backend == "jimeng":
            print("  ⚠️  即梦 AI 不支持精准试穿（多参考图时无法锁定服装/人物外观），已自动切换到豆包 Seedream")
        image_backend = "douban"

    # ── 选择生图后端（仅询问一次，整个 run 内所有生图复用）─────────────
    resolved_backend = image_backend
    if image_backend == "auto":
        if is_jimeng_configured() and ARK_API_KEY:
            if sys.stdin.isatty():
                resolved_backend = _ask_image_backend()  # TTY 交互询问
            else:
                # 非 TTY（Agent 子进程 / tmux / VS Code 内嵌终端）→ 默认豆包
                resolved_backend = "douban"
                print("  ℹ️  检测到非交互终端，后端自动选择：豆包 Seedream")
                print("      如需即梦请重新运行并添加 --image-backend jimeng")
        elif is_jimeng_configured():
            resolved_backend = "jimeng"
        else:
            resolved_backend = "douban"

    _BACKEND_LABELS = {"jimeng": "即梦 AI（Jimeng）", "douban": "豆包 Seedream"}
    backend_label = _BACKEND_LABELS.get(resolved_backend, resolved_backend)

    mode_note = "提示词图生图"
    print(f"\n🎨 AI 生图试穿（方式：{mode_note} | 后端：{backend_label}）")
    if model_img and garment_img:
        print(f"    模特图 + 服装图 → 使用增强约束 Prompt 融合")
    elif model_img or garment_img:
        print(f"    单张参考图 → 图生图模式")
    else:
        print(f"    无参考图 → 纯文生图模式")
    print(f"   模特图: {'有' if model_img else '无'}")
    print(f"   服装图: {'有' if garment_img else '无'}")
    print(f"   描述:   {desc[:30]}")
    print(f"   变体数: {variants}\n")

    # 确定 prompt 列表
    if custom_prompt:
        prompt_list = [{"prompt": custom_prompt, "scene_note": "自定义"}]
        if variants > 1:
            prompt_list = [{"prompt": custom_prompt, "scene_note": f"自定义_{i+1}"} for i in range(variants)]
    else:
        prompt_list = get_prompts(
            desc, bool(model_img), bool(garment_img), variants, style_hint,
            garment_part=garment_part,
        )

    # 展示 prompt 给用户确认
    print("📋 将使用以下 prompt：")
    for i, p in enumerate(prompt_list):
        print(f"   [{i+1}] {p['scene_note']}: {p['prompt'][:80]}...")
    print()

    # 组合参考图列表
    ref_images = []
    if model_img:
        ref_images.append(model_img)
    if garment_img:
        ref_images.append(garment_img)

    # 有模特图+服装图时，根据服装部位选择对应的锁定前缀
    # 作用：强制 Seedream 以图2为服装参考，且只替换对应部位
    _lock_prefix = ""
    if model_img and garment_img:
        _lock_prefix = _GARMENT_LOCK.get(garment_part, _GARMENT_LOCK["top"])

    # 根据输入选择调用模式
    for i, p in enumerate(prompt_list):
        prompt = (_lock_prefix + p["prompt"]) if _lock_prefix else p["prompt"]
        scene = p["scene_note"]
        print(f"── 场景 {i+1}/{len(prompt_list)}: {scene} ──")

        # 统一走生图路由（尊重用户选择的后端）
        try:
            saved = generate_image_auto(
                prompt=prompt,
                reference_images=ref_images if ref_images else None,
                size=size,
                content_type="tryon",
                output_dir=output_dir,
                image_backend=resolved_backend,
            )
        except Exception as e:
            _print_api_error(backend_label, e, step=i + 1, scene=scene)
            sys.exit(1)
        results.extend(saved)
        print()

    all_records = [{"stage": "tryon" if ref_images else "variants",
                    "label": f"result_{i+1}", "path": r, "url": None}
                   for i, r in enumerate(results)]
    print_summary(all_records, title=f"试穿效果图（{backend_label}）")
    from output_manager import get_session_dir
    session_dir = get_session_dir(output_dir)
    print(f"\n📁 输出目录: {session_dir}")
    print(f"   （后续多角度/变体/视频请传 --output-dir '{output_dir}'）")

    # 如果指定了输出文件名，将第一个结果重命名/复制
    if output_filename and results:
        import shutil
        src = results[0]
        # 确保目标路径包含目录
        if os.path.sep not in output_filename and '/' not in output_filename:
            dst = os.path.join(output_dir, output_filename)
        else:
            dst = output_filename
        os.makedirs(os.path.dirname(dst) or '.', exist_ok=True)
        shutil.copy2(src, dst)
        print(f"    已复制为指定文件名: {dst}")
        results.insert(0, dst)

    return results


# ──────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI 生图试穿（即梦优先 / 豆包 Seedream 降级）")
    parser.add_argument("--model-img",   help="模特图 URL 或本地路径")
    parser.add_argument("--garment-img", help="服装图 URL 或本地路径")
    parser.add_argument("--desc",        help="服装文字描述")
    parser.add_argument("--prompt",      help="直接指定 prompt（优先级最高）")
    parser.add_argument("--style",       default="", help="风格偏好（如：日系/欧美/街头）")
    parser.add_argument("--variants",    type=int, default=1, help="生成变体数量 1-5")
    parser.add_argument("--size",          default="2K", help="图片尺寸（豆包专用），如 2K / 1:1 / 2:3")
    parser.add_argument("--image-backend", default="auto",
                        help="生图后端：auto=两个均配置时交互询问 / jimeng=强制即梦 / douban=强制豆包")
    parser.add_argument("--output-dir",      default=None, help="输出目录，默认读 TRYON_OUTPUT_DIR 或 pwd/tryon_output")
    parser.add_argument("--output-filename", default=None, help="输出文件名前缀（可选）")
    parser.add_argument("--garment-part",  default="top",
                        choices=["top", "bottom", "one_piece", "full"],
                        help="服装部位：top=上装 / bottom=下装 / one_piece=单件连体 / full=上下装套装")
    parser.add_argument("--angle-preset",  default=None,
                        help="多角度预设方案（需同时传 --model-img 和 --garment-img）："
                             "ecommerce=电商标准拍 / catwalk=走秀动态 / detail=细节特写 / "
                             "lifestyle=生活场景。可逗号组合如 ecommerce,detail")
    args = parser.parse_args()

    if not args.model_img and not args.garment_img and not args.desc and not args.prompt:
        print("  请提供 --model-img / --garment-img / --desc / --prompt 中至少一个")
        sys.exit(1)

    # 多角度预设模式：直接调用 generate_multi_angle，不走 run()
    if args.angle_preset:
        if not args.model_img or not args.garment_img:
            print("  多角度预设需同时提供 --model-img 和 --garment-img")
            sys.exit(1)
        output_dir = get_output_dir(args.output_dir)
        results = generate_multi_angle(
            model_img=args.model_img,
            garment_img=args.garment_img,
            base_desc=args.desc or "",
            n=min(args.variants, 9) if args.variants > 1 else 3,
            size=args.size,
            output_dir=output_dir,
            preset=args.angle_preset,
            garment_part=args.garment_part,
        )
        print(f"\n多角度生成完成，共 {len(results)} 张：")
        for r in results:
            print(f"   {r['angle']['scene']} → {r['path']}")
    else:
        run(
            model_img       = args.model_img,
            garment_img     = args.garment_img,
            garment_desc    = args.desc,
            custom_prompt   = args.prompt,
            variants        = min(args.variants, 5),
            style_hint      = args.style,
            size            = args.size,
            output_dir      = args.output_dir,
            image_backend   = args.image_backend,
            output_filename = args.output_filename,
            garment_part    = args.garment_part,
        )
