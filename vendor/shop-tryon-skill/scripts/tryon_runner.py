"""
tryon_runner.py — AI 虚拟试穿完整流程

用法：
  # 两图齐全
  python tryon_runner.py --garment shirt.jpg --model model.jpg --category top

  # 只有服装图，自动生成模特
  python tryon_runner.py --garment jk.jpg --category full

  # 无图，全 AI 生成（描述服装）
  python tryon_runner.py --desc "黑色JK制服外套" --category top

  # 生成多个变体让用户选择
  python tryon_runner.py --garment coat.jpg --variants 3

  # 局部试穿（保留原下装，只换上衣）
  python tryon_runner.py --model model.jpg --garment new_top.jpg --keep-bottom
"""

import os, sys, json, time, argparse, urllib.request, urllib.error, tempfile, base64
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent  # ai-tryon/

# ── 加载 .env ──────────────────────────────────────────
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from output_manager import load_env
load_env(__file__)

# ── 配置 ───────────────────────────────────────────────
ALIYUN_API_KEY      = os.getenv("ALIYUN_API_KEY", "")
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
ARK_API_KEY         = os.getenv("ARK_API_KEY", "")         # 火山方舟（豆包 Seedream）
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL     = os.getenv("OPENAI_BASE_URL",  "https://api.openai.com/v1")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")
FASHN_API_KEY       = os.getenv("FASHN_API_KEY", "")

sys.path.insert(0, str(Path(__file__).parent))
from output_manager import save_url, print_summary, get_output_dir
from model_manager import (recommend_model, get_model_image,
    validate_user_model_image, show_model_requirements,
    validate_garment_image, show_garment_requirements, check_outfit_mode)
try:
    from jimeng_client import JimengClient, is_jimeng_configured
except Exception:
    def is_jimeng_configured(): return False
    JimengClient = None

try:
    from image_gen_tryon import (
        generate_image_auto as _gen_image_auto,
        generate_image_multi_auto as _gen_image_multi_auto,
    )
    _HAS_IMAGE_GEN = True
except Exception:
    _HAS_IMAGE_GEN = False
    _gen_image_auto = None
    _gen_image_multi_auto = None

try:
    from oss_uploader import ensure_url as ensure_public_url
except Exception:
    def ensure_public_url(path: str) -> str:
        """OSS 不可用时的降级：本地路径转 base64"""
        if path.startswith("http") or path.startswith("data:"):
            return path
        import base64 as _b64
        ext = path.rsplit(".", 1)[-1].lower()
        mime = "image/png" if ext == "png" else "image/jpeg"
        with open(path, "rb") as _f:
            return f"data:{mime};base64,{_b64.b64encode(_f.read()).decode()}"

_TRYON_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/image2image/image-synthesis/"
_TASK_POLL_URL  = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"

# ── 图片 URL 格式校验 ─────────────────────────────────────
# API 支持：JPG/JPEG/PNG/BMP（豆包 Seedream 和阿里云试衣均适用）
# 明确不支持：AVIF、HEIC/HEIF、TIFF、GIF、SVG 等现代/特殊格式
_UNSUPPORTED_IMG_EXTS = {
    ".avif": "AVIF",
    ".heic": "HEIC",
    ".heif": "HEIF",
    ".tiff": "TIFF",
    ".tif":  "TIFF",
    ".gif":  "GIF",
    ".svg":  "SVG",
    ".webp": "WebP",  # 部分 API 不支持，统一拒绝
}


def _validate_image_url_format(url: str) -> tuple:
    """
    检查图片 URL 的文件格式是否被生图/试衣 API 支持。
    返回 (is_valid: bool, message: str)

    只做扩展名检测（快速，无网络请求）。
    支持 .jpg.avif 这类带双扩展名的 URL（取最后一段扩展名）。
    """
    from urllib.parse import urlparse
    path_lower = urlparse(url).path.lower().split("?")[0]  # 去查询参数
    # 取最后一个扩展名（处理 .jpg.avif → .avif）
    ext = ""
    for part in reversed(path_lower.split(".")):
        if part:
            ext = "." + part
            break
    fmt_name = _UNSUPPORTED_IMG_EXTS.get(ext)
    if fmt_name:
        return False, (
            f"图片格式 {fmt_name}（{ext}）不被 API 支持。\n"
            "支持的格式：JPG / JPEG / PNG / BMP。\n"
            "请重新提供 JPG 或 PNG 格式的图片 URL，或下载后转换格式再上传。"
        )
    return True, "格式检查通过"


# ── 兜底：统一错误处理 ────────────────────────────────────

class TryOnError(Exception):
    """试穿流程中断，直接告知用户原因"""
    pass


def fail_fast(step: str, error: Exception, suggestion: str = ""):
    """
    遇到无法继续的错误时，打印清晰的失败报告并退出
    不再尝试其他方案，让用户知道发生了什么
    """
    print(f"\n{'─'*55}")
    print(f"❌  流程中断于：{step}")
    print(f"    错误信息：{error}")
    if suggestion:
        print(f"    建议处理：{suggestion}")
    print(f"{'─'*55}")

    # 已完成步骤的提示
    print("\n📋 已完成的步骤不受影响，中断前的输出文件仍然有效。")
    print("    如需重新运行，解决上述问题后重试即可。\n")
    sys.exit(1)


def check_key(key_name: str, value: str, placeholder: str = ""):
    """检查 API Key 是否已配置，未配置则立即报错"""
    if not value or value == placeholder:
        fail_fast(
            step=f"配置检查：{key_name}",
            error=ValueError(f"{key_name} 未设置或仍是占位值"),
            suggestion=f"在 scripts/.env 中填入真实的 {key_name}，参考 .env.example"
        )


# ── 工具函数 ────────────────────────────────────────────

def download_image(url: str) -> str:
    """下载远程图片到临时文件，返回本地路径"""
    suffix = ".png" if url.lower().endswith(".png") else ".jpg"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    urllib.request.urlretrieve(url, tmp.name)
    return tmp.name


def to_public_url_or_b64(path: str) -> tuple:
    """
    返回 (url, None) 或 (None, base64_data_uri)
    阿里云 API 优先接受公网 URL；本地文件转 base64
    """
    if path.startswith("http"):
        return path, None
    ext = path.rsplit(".", 1)[-1].lower()
    mime = "image/png" if ext == "png" else "image/jpeg"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return None, f"data:{mime};base64,{b64}"


def save_image(url: str, output_path: str):
    urllib.request.urlretrieve(url, output_path)
    print(f"  💾 已保存: {output_path}")


# ── 阿里云 API 核心 ─────────────────────────────────────

def _aliyun_submit(payload: dict) -> str:
    check_key("ALIYUN_API_KEY", ALIYUN_API_KEY)
    req = urllib.request.Request(
        _TRYON_ENDPOINT,
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
        fail_fast(
            step="阿里云 API 提交任务",
            error=RuntimeError(f"HTTP {e.code}: {body}"),
            suggestion=_aliyun_error_hint(e.code, body),
        )
    except urllib.error.URLError as e:
        fail_fast(
            step="阿里云 API 网络连接",
            error=e,
            suggestion="检查网络连接，阿里云 API 需要能访问 dashscope.aliyuncs.com"
        )
    if result.get("code"):
        fail_fast(
            step="阿里云 API 提交任务",
            error=RuntimeError(result.get("message", str(result))),
            suggestion=_aliyun_error_hint(None, str(result)),
        )
    return result["output"]["task_id"]


def _aliyun_error_hint(http_code, body: str) -> str:
    """根据错误内容返回具体建议"""
    b = body.lower()
    if "model not exist" in b or "invalidparameter" in b:
        return "model 参数有误，正确值为：aitryon / aitryon-plus / aitryon-refiner / aitryon-parsing"
    if "authentication" in b or "unauthorized" in b or http_code == 401:
        return "ALIYUN_API_KEY 无效或已过期，请在阿里云百炼控制台重新生成"
    if "quota" in b or "insufficient" in b:
        return "免费额度已用完，请在阿里云控制台充值后继续"
    if "region" in b or "not supported" in b:
        return "地域选择错误，AI试衣仅支持「中国内地（北京）」地域的 API Key"
    if "image" in b and ("format" in b or "url" in b):
        return "图片格式或 URL 有问题，请确保图片为 JPG/PNG，URL 可公网访问"
    if http_code == 400:
        return "请求参数有误，请检查 top_garment_url / bottom_garment_url 至少传一个"
    if http_code == 429:
        return "请求过于频繁，稍等几秒后重试"
    return "请查看上方错误信息，或访问 https://bailian.aliyun.com 控制台查看详情"





def _aliyun_poll(task_id: str, timeout: int = 120) -> str:
    url = _TASK_POLL_URL.format(task_id=task_id)
    deadline = time.time() + timeout
    retry = 0
    while time.time() < deadline:
        time.sleep(3)
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={"Authorization": f"Bearer {ALIYUN_API_KEY}"}),
                timeout=10,
            ) as r:
                s = json.loads(r.read())
            retry = 0
        except Exception as e:
            retry += 1
            if retry >= 5:
                fail_fast(
                    step="轮询任务结果",
                    error=e,
                    suggestion="网络不稳定导致多次轮询失败，请检查网络后重试"
                )
            print(f"   轮询出错 ({retry}/5): {e}，继续重试...")
            continue
        status = s["output"]["task_status"]
        if status == "SUCCEEDED":
            return s["output"]["image_url"]
        if status == "FAILED":
            msg = s["output"].get("message", str(s["output"]))
            fail_fast(
                step="阿里云试衣任务执行",
                error=RuntimeError(msg),
                suggestion=_aliyun_error_hint(None, msg),
            )
        print(f"  ⏳ 状态: {status}...")
    fail_fast(
        step="等待任务完成",
        error=TimeoutError(f"超过 {timeout}s 仍未完成，task_id={task_id}"),
        suggestion="网络较慢或服务繁忙，可稍后重试；或在阿里云控制台查看该任务状态"
    )


def aliyun_tryon(
    person_image_url: str,
    top_garment_url: str = None,
    bottom_garment_url: str = None,
    model: str = "aitryon-plus",
) -> str:
    inp = {"person_image_url": person_image_url}
    if top_garment_url:    inp["top_garment_url"]    = top_garment_url
    if bottom_garment_url: inp["bottom_garment_url"] = bottom_garment_url
    task_id = _aliyun_submit({
        "model": model,
        "input": inp,
        "parameters": {"resolution": -1, "restore_face": True},
    })
    print(f"  ⏳ 试衣任务: {task_id}")
    return _aliyun_poll(task_id)


def aliyun_parse_garment(person_image_url: str, parse_type: str = "upper") -> str:
    """局部试穿：分割出服装区域"""
    task_id = _aliyun_submit({
        "model": "aitryon-parsing",
        "input": {"person_image_url": person_image_url},
        "parameters": {"parse_type": parse_type},
    })
    print(f"  ⏳ 分割任务: {task_id}")
    return _aliyun_poll(task_id)


# ── 服装图预处理（去背景）──────────────────────────────

def preprocess_garment(path: str, skip: bool = False) -> str:
    """尝试去背景；rembg 不可用时直接跳过（阿里云 API 能处理一般背景）"""
    if skip or path.startswith("http"):
        return path
    try:
        from rembg import remove
        from PIL import Image
        import io
        print("  🔧 rembg 去背景...")
        with open(path, "rb") as f:
            result = remove(f.read())
        img = Image.open(io.BytesIO(result)).convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        out_path = path.rsplit(".", 1)[0] + "_whitebg.jpg"
        bg.convert("RGB").save(out_path, "JPEG", quality=95)
        print(f"  去背景完成: {out_path}")
        return out_path
    except ImportError:
        print("   rembg 未安装，跳过去背景（如有背景可能影响效果）")
        return path
    except Exception as e:
        print(f"   去背景失败: {e}，跳过")
        return path


# ── AI 生成模特 ─────────────────────────────────────────

MODEL_PROMPTS = {
    "asian_studio":   "Young Asian female, average figure, standing upright, arms slightly away from body, neutral expression, facing camera, full body head to toe, pure white studio background, soft even lighting, photorealistic, high resolution, no clothing",
    "western_studio": "Young Caucasian female model, average figure, relaxed natural pose, full body visible, light gray seamless background, natural soft light, photorealistic, fashion photography, no clothing",
    "male_studio":    "Young Asian male model, average build, standing straight, arms relaxed at sides, neutral expression, facing camera, full body head to toe, pure white background, studio lighting, photorealistic, no clothing",
    "neutral_white":  "Female model, average figure, standing straight, arms relaxed at sides, pure white background, even studio lighting, full body, photorealistic, no clothing, neutral expression, facing forward",
}

STYLE_AUTO_MAP = {
    "jk": "asian_studio", "制服": "asian_studio", "学院": "asian_studio",
    "lolita": "asian_studio", "和风": "asian_studio", "旗袍": "asian_studio",
    "男": "male_studio", "male": "male_studio",
    "欧美": "western_studio", "街头": "western_studio",
}


def generate_model_image(style: str = "neutral_white", provider: str = "auto", output_dir: str = None) -> str:
    prompt = MODEL_PROMPTS.get(style, MODEL_PROMPTS["neutral_white"])
    print(f"  🎨 生成模特图 | 风格: {style} | prompt: {prompt[:60]}...")

    if provider == "auto":
        if ARK_API_KEY:         provider = "ark"
        elif OPENAI_API_KEY:  provider = "dalle3"
        else: raise RuntimeError("未找到可用的生图 API Key（ARK/OPENAI）")

    _base_dir = output_dir or get_output_dir()

    if provider == "ark":
        try:
            from openai import OpenAI as _OAI
        except ImportError:
            fail_fast("生图依赖", ImportError("openai 未安装"), "pip install openai")
        client = _OAI(base_url=os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"), api_key=ARK_API_KEY)
        resp = client.images.generate(
            model=os.getenv("ARK_IMAGE_MODEL", "doubao-seedream-5-0-260128"),
            prompt=prompt,
            size="2:3",
            response_format="url",
            extra_body={"watermark": False},
        )
        url = resp.data[0].url
        rec = save_url(url, stage="model", label="ark", base_dir=_base_dir)
        return rec["path"]

    elif provider == "dalle3":
        import openai
        client = openai.OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
        resp = client.images.generate(model="dall-e-3", prompt=prompt,
                                      size="1024x1024", quality="hd", n=1)
        url = resp.data[0].url
        rec = save_url(url, stage="model", label="dalle3", base_dir=_base_dir)
        return rec["path"]

    fail_fast(
        step="生成模特图",
        error=ValueError(f"未知 provider: {provider}"),
        suggestion="image_provider 可选值: ark / dalle3 / auto"
    )


# ── AI 生成服装图 ───────────────────────────────────────

def generate_garment_image(description: str, garment_type: str = "top", provider: str = "auto") -> str:
    prompt = (
        f"{description}, flat lay on pure white background, "
        "studio lighting, top-down angle, full garment visible, "
        "no wrinkles, sharp details, e-commerce product photo, no text, no model"
    )
    print(f"  🎨 生成服装图 | {description[:30]} | prompt: {prompt[:60]}...")
    # 复用模特图生成逻辑（同样的生图 API）
    return generate_model_image.__wrapped__(prompt, provider) if hasattr(generate_model_image, "__wrapped__") \
        else _generate_image(prompt, provider)


def _generate_image(prompt: str, provider: str = "auto") -> str:
    # 即梦优先（provider=auto 时自动判断）
    if provider in ("auto", "jimeng") and is_jimeng_configured():
        try:
            client = JimengClient()
            urls = client.generate_image(prompt=prompt, width=1024, height=1536)
            rec = save_url(urls[0], stage="garment", label="jimeng_gen", base_dir=get_output_dir())
            return rec["path"]
        except Exception as e:
            print(f"   [即梦] 生图失败: {e}，回退到豆包...")
            if provider == "jimeng":
                fail_fast("生成图片", e, "检查 JIMENG_ACCESS_KEY / JIMENG_SECRET_KEY 配置")

    if provider == "auto":
        if ARK_API_KEY:       provider = "ark"
        elif OPENAI_API_KEY:  provider = "dalle3"
        else:
            fail_fast(
                step="生图 API 初始化",
                error=RuntimeError("未找到可用的生图 API Key"),
                suggestion="请在 .env 中填写 JIMENG_ACCESS_KEY / ARK_API_KEY 或 OPENAI_API_KEY"
            )

    if provider == "ark":
        try:
            from openai import OpenAI as _OAI
        except ImportError:
            fail_fast("生图依赖", ImportError("openai 未安装"), "pip install openai")
        client = _OAI(base_url=os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"), api_key=ARK_API_KEY)
        resp = client.images.generate(
            model=os.getenv("ARK_IMAGE_MODEL", "doubao-seedream-5-0-260128"),
            prompt=prompt,
            size="2:3",
            response_format="url",
            extra_body={"watermark": False},
        )
        url = resp.data[0].url
        rec = save_url(url, stage="garment", label="ark_gen", base_dir=get_output_dir())
        return rec["path"]

    fail_fast(
        step="生成图片",
        error=ValueError(f"未知 provider: {provider}"),
        suggestion="image_provider 可选值: jimeng / ark / dalle3 / auto"
    )


# ── 多变体生成 ──────────────────────────────────────────

VARIANT_SYSTEM = """你是 AI 试穿效果图专家。根据服装描述，生成 {n} 个不同风格的试穿场景变体。
每个变体只修改：背景场景、光线氛围、模特姿势、季节感，不修改服装本身。
返回 JSON 数组，每项包含：id, label（简短英文风格名）, scene_note（中文说明）, model_prompt（英文模特 prompt）
只返回 JSON，不要其他内容。"""


def generate_variants(garment_desc: str, n: int = 3) -> list:
    """调用 Claude 生成 n 个试穿场景变体"""
    if not ANTHROPIC_API_KEY:
        # 无 Claude 时用默认变体
        return [
            {"id": i+1, "label": s, "scene_note": c, "model_prompt": MODEL_PROMPTS["neutral_white"]}
            for i, (s, c) in enumerate([
                ("studio_white", "纯白棚拍"),
                ("asian_school", "日系学院风"),
                ("urban_casual", "城市街头"),
            ])[:n]
        ]
    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 800,
        "system": VARIANT_SYSTEM.format(n=n),
        "messages": [{"role": "user", "content": f"服装：{garment_desc}"}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = json.loads(r.read())["content"][0]["text"].strip()
    raw = raw.lstrip("```json").rstrip("```").strip()
    return json.loads(raw)


# ── 服装部位专用 Prompt ──────────────────────────────────
# garment_part: top / bottom / full / one_piece
_GARMENT_PART_PROMPTS = {
    "top": (
        "穿着这件上装（衬衫/外套/T恤/卫衣等）在上半身，服装自然垂合，"
        "下半身搭配中性色修身长裤，上装细节（领口/袖口/版型）清晰可见，"
        "全身可见从头到脚。"
        " | Wearing this TOP garment on upper body with natural drape and fit, "
        "paired with neutral pants on lower body, full body head to toe."
    ),
    "bottom": (
        "穿着这件下装（裤子/半裙/短裙/阔腿裤等）在下半身，服装自然垂合，"
        "上半身搭配简约白色T恤，下装细节（裤腿/裙摆/腰线）清晰可见，"
        "全身可见从头到脚。"
        " | Wearing this BOTTOM garment on lower body with natural drape and fit, "
        "paired with plain white t-shirt on top, full body head to toe."
    ),
    "full": (
        "穿着完整上下装套装：上装在上半身，下装在下半身，"
        "上下搭配自然协调，整体轮廓清晰，全身可见从头到脚。"
        " | Wearing COMPLETE OUTFIT: top garment on upper body, "
        "bottom garment on lower body, perfectly coordinated, full body head to toe."
    ),
    "one_piece": (
        "穿着这件单件连体服装（连衣裙/连体裤/长裙/连体短裙等），"
        "服装从肩部延伸至膝盖或脚踝，整件服装轮廓和细节（裙摆/腰线/领口）完整清晰，"
        "全身可见从头到脚。"
        " | Wearing this ONE-PIECE garment (dress/jumpsuit/romper/maxi-skirt), "
        "flowing from shoulders to knees/ankles, "
        "full silhouette and garment details visible, full body head to toe."
    ),
}


def _build_tryon_prompt(garment_desc: str = "", garment_part: str = "top") -> str:
    """根据服装部位生成精准的试穿 prompt"""
    part_text = _GARMENT_PART_PROMPTS.get(garment_part, _GARMENT_PART_PROMPTS["top"])
    desc_note = f"服装描述：{garment_desc}。" if garment_desc else ""
    return (
        "使用参考图1（服装）和参考图2（模特），创建一张超现实的全身时尚照片。"
        f"{desc_note}"
        f"试穿部位：{part_text}"
        "关键要求：服装必须自然地垂在模特身上，贴合其姿势并产生逼真的褶皱；"
        "极其精确地保留服装的原始面料质感、颜色和任何标志；"
        "通过完美匹配环境光、色温和阴影方向，将服装融入模特照片中。"
        "摄影风格：干净的电商产品画册（Lookbook）风格，"
        "使用佳能EOS R5相机和50mm f/1.8镜头拍摄，呈现自然、专业的外观。"
        "背景：纯白色或浅灰色摄影棚，均匀柔和的打光，绝对不要黑色或深色背景。"
        "full body head to toe, 9:16 vertical portrait, white studio background, "
        "bright and clean lighting, no dark or black background."
    )


# 兜底默认 Prompt（向后兼容，新代码应使用 _build_tryon_prompt）
TRYON_PROMPT_ENHANCED = _build_tryon_prompt(garment_part="top")


def _ask_synthesis_method() -> str:
    """
    交互式询问用户选择试穿合成方式。
    返回 'prompt'（提示词图生图，默认）或 'qwen'（阿里云试衣API，备选）。
    """
    print()
    print("┌──────────────────────────────────────────────────────────────┐")
    print("│  🧥 请选择试穿合成方式                                              │")
    print("├──────────────────────────────────────────────────────────────┤")
    print("│  1. 提示词图生图（推荐，默认）                                         │")
    print("│     增强约束 Prompt + 豆包/即梦，风格多样，无需额外试衣 API            │")
    print("│     需要：ARK_API_KEY 或 JIMENG_ACCESS_KEY                        │")
    print("│                                                                │")
    print("│  2. 阿里云试衣 API（备选，精准贴合）                                    │")
    print("│     专业试穿引擎，精准处理褶皱/贴合/光影                              │")
    print("│     需要：.env 中配置 ALIYUN_API_KEY                              │")
    print("└──────────────────────────────────────────────────────────────┘")
    if not sys.stdin.isatty():
        print("  （非交互模式，自动选择提示词图生图）")
        return "prompt"
    while True:
        try:
            choice = input("  请输入选项 [1/2，默认 1]: ").strip()
        except EOFError:
            print("  （无法读取输入，自动选择提示词图生图）")
            return "prompt"
        if choice in ("", "1"):
            return "prompt"
        elif choice == "2":
            return "qwen"
        else:
            print("   无效输入，请输入 1 或 2")


# ── 主流程 ──────────────────────────────────────────────

def run_pipeline(
    garment_path: str = None,
    tryon_mode: str = "single",
    bottom_garment_path: str = None,
    garment_desc: str = None,
    model_path: str = None,
    category: str = None,            # 兼容旧参数，新代码用 garment_part
    garment_part: str = "top",       # top=上装 / bottom=下装 / full=上下装 / one_piece=单件连体
    model_style: str = None,
    keep_bottom: bool = False,       # 局部试穿：保留原下装
    keep_top: bool = False,          # 局部试穿：保留原上装
    num_variants: int = 1,           # 生成变体数量
    output_dir: str = None,
    skip_preprocess: bool = False,
    tryon_model: str = "aitryon-plus",
    image_provider: str = "auto",
    synthesis_method: str = "prompt",  # prompt=提示词图生图（默认）/ qwen=阿里云试衣API / auto=交互询问
) -> list:

    # category 参数向后兼容
    if category is not None and garment_part == "top":
        garment_part = category

    output_dir = get_output_dir(output_dir)
    print("\n🚀 AI 虚拟试穿开始\n")
    os.makedirs(output_dir, exist_ok=True)

    # Step 1: 服装图 + 模式校验
    print("👗 Step 1: 服装图准备")

    # 有纯文字描述时跳过图片模式校验（desc 会自动生成服装图）
    if not garment_desc:
        ok_m, msg_m = check_outfit_mode(garment_path, bottom_garment_path, tryon_mode)
        if not ok_m:
            fail_fast("试穿模式校验", ValueError(msg_m),
                      "模式：single=单件装（上装或下装任一）/ outfit=上下装（需同时提供两张图）")

    def _prep_garment(path: str, label: str) -> str:
        if path.startswith("http"):
            ok_fmt, msg_fmt = _validate_image_url_format(path)
            if not ok_fmt:
                fail_fast(
                    f"{label}图格式不支持",
                    ValueError(msg_fmt),
                    "请提供 JPG/PNG/BMP 格式图片的 URL，或下载后转换格式再重新传入"
                )
            return path
        ok2, msg2 = validate_garment_image(path)
        if not ok2:
            fail_fast(f"{label}图校验", ValueError(msg2), show_garment_requirements())
        print(f"  {msg2}")
        return ensure_public_url(preprocess_garment(path, skip=skip_preprocess))

    final_garment = final_bottom = None
    if garment_path:
        final_garment = _prep_garment(garment_path, "上装/服装")
    elif garment_desc:
        print(f"  根据描述生成服装图: {garment_desc}")
        final_garment = _generate_image(
            f"{garment_desc}, flat lay on pure white background, studio lighting, "
            "top-down angle, full garment visible, no wrinkles, e-commerce style, no model",
            provider=image_provider,
        )
        print(f"  服装图生成: {final_garment[:60]}")
    else:
        fail_fast("服装图准备", ValueError("缺少服装图"),
                  "请通过 --garment 提供服装图路径/URL，或通过 --desc 描述服装内容")

    if bottom_garment_path:
        print("  处理下装图...")
        final_bottom = _prep_garment(bottom_garment_path, "下装")

    # Step 2: 模特图
    print("\n🧍 Step 2: 模特图准备")
    if model_path:
        # 解析相对路径 → 相对于项目根目录（ai-tryon/）
        if not model_path.startswith("http"):
            _p = Path(model_path)
            if not _p.is_absolute():
                model_path = str(_PROJECT_ROOT / model_path)
        # 用户提供模特图：先校验格式和尺寸
        if not model_path.startswith("http") and Path(model_path).exists():
            ok, msg = validate_user_model_image(model_path)
            if not ok:
                fail_fast("用户模特图校验", ValueError(msg),
                          "请参考要求：\n" + show_model_requirements())
            print(f"  {msg}")
        final_model = ensure_public_url(model_path) if not model_path.startswith("http") else model_path
        print(f"  使用提供的模特图: {str(final_model)[:60]}")
    else:
        # 从内置模特库智能推荐
        garment_hint = garment_desc or (Path(garment_path or "").stem if garment_path else "")
        recommended  = recommend_model(garment_desc=garment_hint)
        model_img    = get_model_image(recommended)
        print(f"  推荐模特：{recommended['name']} ({recommended['style']}, {recommended['height']}, {recommended['bodyType']})")
        final_model = model_img if model_img.startswith("http") else ensure_public_url(model_img)
        print(f"  模特图就绪: {final_model[:60]}")

    # Step 3: 组装服装参数
    top_url = bottom_url = None

    if tryon_mode == "outfit" and final_bottom:
        top_url = final_garment
        bottom_url = final_bottom
        print("  👔 上下装模式：上装+下装同时试穿")
    elif keep_bottom:
        # 保留模特原下装：分割出原下装 → 只换上衣
        print("\n✂️  Step 3: 局部试穿 — 保留原下装，只换上衣")
        person_url = ensure_public_url(final_model)
        bottom_url = aliyun_parse_garment(person_url, parse_type="lower")
        top_url    = final_garment
        print(f"  原下装分割完成，将保留并合并新上衣")

    elif keep_top:
        # 保留模特原上装：分割出原上装 → 只换下装
        print("\n✂️  Step 3: 局部试穿 — 保留原上装，只换下装")
        person_url = ensure_public_url(final_model)
        top_url    = aliyun_parse_garment(person_url, parse_type="upper")
        bottom_url = final_garment
        print(f"  原上装分割完成，将保留并合并新下装")

    else:
        # 普通全换模式
        gp = garment_part  # top / bottom / full / one_piece
        if gp in ("top", "one_piece"):
            top_url    = final_garment
        elif gp == "bottom":
            bottom_url = final_garment
        elif gp == "full":
            # full 模式需要两张图，garment_path 给上装
            # 用户可通过 --bottom-garment 额外传下装图（见 CLI）
            top_url    = final_garment
            bottom_url = final_bottom

    # ── 选择合成方式 ────────────────────────────────────────
    method = synthesis_method
    if method == "auto":
        if final_garment and final_model:
            method = _ask_synthesis_method()
        else:
            method = "prompt"  # 默认走提示词图生图

    _METHOD_LABELS = {
        "qwen":   "阿里云试衣 API（aitryon / Qwen）",
        "prompt": "提示词图生图（增强约束 Prompt + 豆包/即梦）",
    }
    method_label = _METHOD_LABELS.get(method, method)
    print(f"\n合成方式：{method_label}")

    # Step 4: 合成效果图
    all_paths = []

    if method == "prompt" and _HAS_IMAGE_GEN:
        # ── 提示词图生图路径（默认路径）──────────────────────────
        tryon_prompt = _build_tryon_prompt(
            garment_desc=garment_desc or "",
            garment_part=garment_part,
        )
        _PART_LABELS = {
            "top": "上装", "bottom": "下装",
            "full": "上下装", "one_piece": "单件连体",
        }
        part_label = _PART_LABELS.get(garment_part, garment_part)
        print(f"\n🎨 Step 4: 提示词图生图（增强约束 Prompt，服装部位：{part_label}）")
        ref_imgs = [img for img in [final_model, final_garment] if img]
        if garment_part == "full" and final_bottom:
            ref_imgs = [img for img in [final_model, final_garment, final_bottom] if img]
        print(f"   使用参考图: {len(ref_imgs)} 张")
        print(f"   Prompt: {tryon_prompt[:100]}...")
        if num_variants > 1:
            # 先单独生成第1张变体
            print(f"  [1/{num_variants}] 生成变体 1...")
            all_paths = _gen_image_auto(
                prompt=tryon_prompt,
                reference_images=ref_imgs,
                output_dir=output_dir,
            )
            # 只换上装时：用第1张结果替换模特参考，锁定下装样式
            if garment_part == "top" and all_paths:
                locked_refs = [all_paths[0], final_garment]
                print(f"  🔒 下装锁定：以变体 1 的下装为基准生成后续变体")
            else:
                locked_refs = ref_imgs
            # 生成剩余变体
            for i in range(1, num_variants):
                print(f"  [{i+1}/{num_variants}] 生成变体 {i+1}...")
                paths = _gen_image_auto(
                    prompt=tryon_prompt,
                    reference_images=locked_refs,
                    output_dir=output_dir,
                )
                all_paths.extend(paths)
        else:
            all_paths = _gen_image_auto(
                prompt=tryon_prompt,
                reference_images=ref_imgs,
                output_dir=output_dir,
            )
    else:
        # ── 阿里云试衣 API 路径 ───────────────────────────────
        if num_variants > 1:
            print(f"\n🎨 Step 4: 生成 {num_variants} 个试穿变体（阿里云 Qwen aitryon）...")
            variants = generate_variants(garment_desc or Path(garment_path or "").stem, n=num_variants)
            for v in variants:
                print(f"\n  变体 [{v['id']}] {v['label']} — {v['scene_note']}")
                variant_model = generate_model_image(v["model_prompt"], image_provider, output_dir=output_dir) \
                    if v.get("model_prompt") else final_model
                url = aliyun_tryon(
                    person_image_url=variant_model,
                    top_garment_url=top_url,
                    bottom_garment_url=bottom_url,
                    model=tryon_model,
                )
                rec = save_url(url, stage="tryon", label=v["label"], base_dir=output_dir)
                all_paths.append(rec["path"])
                print(f"  变体效果图: {url[:60]}")
        else:
            print(f"\n👗 Step 4: 试穿合成（阿里云 Qwen aitryon / model={tryon_model}）")
            url = aliyun_tryon(
                person_image_url=final_model,
                top_garment_url=top_url,
                bottom_garment_url=bottom_url,
                model=tryon_model,
            )
            rec = save_url(url, stage="tryon", label="result_1", base_dir=output_dir)
            all_paths.append(rec["path"])

    # Step 5: 汇总
    print(f"\n💾 Step 5: 结果汇总")
    all_records = [
        {"stage": "tryon", "label": f"result_{i+1}", "path": p, "url": None}
        for i, p in enumerate(all_paths)
    ]
    print_summary(all_records, title=f"试穿效果图（{method_label}）")
    from output_manager import get_session_dir
    session_dir = get_session_dir(output_dir)
    print(f"\n📁 输出目录: {session_dir}")
    print(f"   （后续多角度/变体/视频请传 --output-dir '{output_dir}'）")
    return all_paths


# ── CLI ────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI 虚拟试穿")
    parser.add_argument("--garment",          help="服装图路径或 URL")
    parser.add_argument("--desc",             help="服装文字描述（无服装图时使用）")
    parser.add_argument("--model",            help="模特图路径或 URL（可选，不提供则自动生成）")
    parser.add_argument("--tryon-mode",      default="single", help="single=单件装 / outfit=上下装")
    parser.add_argument("--bottom-garment",  default=None,     help="下装图（outfit模式必须）")
    parser.add_argument("--garment-part",    default="top",
                        help="服装部位：top=上装 / bottom=下装 / full=上下装 / one_piece=单件连体（裙子等）")
    parser.add_argument("--category",        default=None,    help="同 --garment-part，向后兼容")
    parser.add_argument("--model-style",      help="asian_studio / western_studio / male_studio / neutral_white")
    parser.add_argument("--keep-bottom",      action="store_true", help="局部试穿：保留模特原下装")
    parser.add_argument("--keep-top",         action="store_true", help="局部试穿：保留模特原上装")
    parser.add_argument("--variants",         type=int, default=1, help="生成变体数量（1-5）")
    parser.add_argument("--tryon-model",      default="aitryon-plus", help="aitryon / aitryon-plus")
    parser.add_argument("--image-provider",     default="auto", help="ark（豆包Seedream）/ dalle3 / auto")
    parser.add_argument("--synthesis-method",   default="prompt",
                        help="试穿合成方式：prompt=提示词图生图（默认）/ qwen=阿里云试衣API / auto=交互询问")
    parser.add_argument("--output-dir",         default=None)
    parser.add_argument("--skip-preprocess",    action="store_true", help="跳过去背景处理")
    args = parser.parse_args()

    run_pipeline(
        garment_path         = args.garment,
        bottom_garment_path  = args.bottom_garment,
        tryon_mode           = args.tryon_mode,
        garment_desc         = args.desc,
        model_path           = args.model,
        garment_part         = args.garment_part,
        category             = args.category,
        model_style          = args.model_style,
        keep_bottom          = args.keep_bottom,
        keep_top             = args.keep_top,
        num_variants         = min(args.variants, 5),
        tryon_model          = args.tryon_model,
        image_provider       = args.image_provider,
        synthesis_method     = args.synthesis_method,
        output_dir           = args.output_dir,
        skip_preprocess      = args.skip_preprocess,
    )
