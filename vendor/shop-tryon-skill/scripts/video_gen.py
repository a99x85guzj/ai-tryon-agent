"""
video_gen.py — 豆包 Seedance 生视频

支持模式：
  1. 图生视频-首帧     (试穿图 → 模特动起来，核心展示)
  2. 图生视频-首尾帧   (控制开始和结束帧)
  3. 有声视频          (加环境音效)
  4. 参考图视频        (多张试穿图合成，lite 模型)
  5. base64 输入       (本地图片)

用法：
  # 单张试穿图 → 展示视频
  python video_gen.py --image tryon_result.jpg --prompt "模特缓缓转身展示服装"

  # 多张试穿图 → 合成视频
  python video_gen.py \\
    --images img1.jpg img2.jpg img3.jpg \\
    --prompt "[图1]白底主图，[图2]校园场景，[图3]街头场景，服装展示"

  # 首尾帧控制
  python video_gen.py \\
    --first-frame model_front.jpg \\
    --last-frame model_side.jpg \\
    --prompt "模特缓缓转身展示服装细节"
"""

import os, sys, json, time, argparse, base64, urllib.request, urllib.error
from pathlib import Path

# ── 加载 .env ──────────────────────────────────────────
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from output_manager import load_env
load_env(__file__)

# ── 输出管理 ──────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from output_manager import save_video, print_summary, get_output_dir
from jimeng_client import JimengClient, is_jimeng_configured

ARK_API_KEY   = os.getenv("ARK_API_KEY", "")
ARK_BASE_URL  = os.getenv("ARK_BASE_URL",          "https://ark.cn-beijing.volces.com/api/v3")
MODEL_PRO     = os.getenv("ARK_VIDEO_MODEL_PRO",  "doubao-seedance-1-5-pro-251215")
MODEL_LITE    = os.getenv("ARK_VIDEO_MODEL_LITE", "doubao-seedance-1-0-lite-i2v-250428")


def _check_key():
    if not ARK_API_KEY:
        print("❌  ARK_API_KEY 未配置，请在 .env 中填写")
        sys.exit(1)


def _post(path: str, payload: dict) -> dict:
    """发送 POST 请求"""
    req = urllib.request.Request(
        f"{ARK_BASE_URL}{path}",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ARK_API_KEY}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"❌  HTTP {e.code}: {body}")
        sys.exit(1)


def _get(path: str) -> dict:
    """发送 GET 请求"""
    req = urllib.request.Request(
        f"{ARK_BASE_URL}{path}",
        headers={"Authorization": f"Bearer {ARK_API_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"❌  轮询出错 HTTP {e.code}: {body}")
        sys.exit(1)


def _to_image_content(url_or_path: str, role: str = None) -> dict:
    """
    构建 image_url content 块
    url_or_path: 公网 URL 或本地路径（自动转 base64）
    role: None / "first_frame" / "last_frame" / "reference_image"
    """
    if url_or_path.startswith("http"):
        img_url = url_or_path
    else:
        # 本地文件转 base64
        ext = Path(url_or_path).suffix.lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
        with open(url_or_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        img_url = f"data:{mime};base64,{b64}"

    item = {
        "type": "image_url",
        "image_url": {"url": img_url},
    }
    if role:
        item["role"] = role
    return item


def _poll_task(task_id: str, timeout: int = 300) -> str:
    """
    轮询任务结果，返回视频 URL
    TODO: 查询接口路径待验证（根据官方文档确认）
    """
    path = f"/contents/generations/tasks/{task_id}"
    print(f"  ⏳ 任务提交成功: {task_id}")
    print(f"  ⏳ 等待生成（视频约需 1-3 分钟）...")

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        result = _get(path)

        status = result.get("status", "")
        print(f"  ⏳ 状态: {status}...")

        if status in ("succeeded", "success"):
            # 从 content 列表中提取 video_url
            # 真实响应：{"content": [{"type": "video", "video_url": "https://..."}]}
            video_url = None
            for item in result.get("content", []):
                if isinstance(item, dict) and item.get("video_url"):
                    video_url = item["video_url"]
                    break
            # 兜底：直接在顶层找
            if not video_url:
                video_url = result.get("video_url") or result.get("output", {}).get("video_url")
            if video_url:
                print(f"  ✅ 视频生成完成: {video_url[:60]}...")
                return video_url
            else:
                print(f"  ⚠️  任务成功但未找到 video_url，完整响应:")
                print(json.dumps(result, ensure_ascii=False, indent=2)[:800])
                sys.exit(1)

        elif status in ("failed", "cancelled"):
            print(f"  ❌  任务失败: {result.get('error', result)}")
            sys.exit(1)

    print(f"  ❌  超时 {timeout}s，task_id={task_id}")
    print(f"  💡 可手动查询: GET {ARK_BASE_URL}/contents/generations/tasks/{task_id}")
    sys.exit(1)


def _download_video(url: str, output_path: str):
    """下载视频到本地"""
    urllib.request.urlretrieve(url, output_path)
    print(f"  💾 视频已保存: {output_path}")


# ──────────────────────────────────────────────────────
# 4 种生视频函数
# ──────────────────────────────────────────────────────

def image_to_video(
    prompt: str,
    image_url: str,
    duration: int = 5,
    ratio: str = "9:16",
    generate_audio: bool = False,
    model: str = MODEL_PRO,
    output_path: str = None,
) -> str:
    """
    模式1&3：图生视频（首帧）/ 有声视频
    image_url: 试穿效果图的 URL 或本地路径
    """
    _check_key()
    payload = {
        "model": model,
        "content": [
            {"type": "text", "text": prompt},
            _to_image_content(image_url),
        ],
        "duration": duration,
        "ratio": ratio,
        "watermark": False,
    }
    if generate_audio:
        payload["generate_audio"] = True

    print(f"\n🎬 图生视频 | prompt: {prompt[:60]}...")
    result = _post("/contents/generations/tasks", payload)
    task_id = result.get("id") or result.get("task_id")
    if not task_id:
        print(f"  ❌  未获取到 task_id，响应: {result}")
        sys.exit(1)

    video_url = _poll_task(task_id)
    rec = save_video(video_url, stage="video", label="i2v")
    print_summary([rec], title="展示视频")
    return rec["path"]


def first_last_frame_video(
    prompt: str,
    first_frame_url: str,
    last_frame_url: str,
    duration: int = 5,
    ratio: str = "9:16",
    generate_audio: bool = False,
    output_path: str = None,
) -> str:
    """模式2：首尾帧控制视频"""
    _check_key()
    payload = {
        "model": MODEL_PRO,
        "content": [
            {"type": "text", "text": prompt},
            _to_image_content(first_frame_url, role="first_frame"),
            _to_image_content(last_frame_url, role="last_frame"),
        ],
        "duration": duration,
        "ratio": ratio,
        "watermark": False,
    }
    if generate_audio:
        payload["generate_audio"] = True

    print(f"\n🎬 首尾帧视频 | prompt: {prompt[:60]}...")
    result = _post("/contents/generations/tasks", payload)
    task_id = result.get("id") or result.get("task_id")
    if not task_id:
        print(f"  ❌  未获取到 task_id，响应: {result}")
        sys.exit(1)

    video_url = _poll_task(task_id)
    rec = save_video(video_url, stage="video", label="firstlast")
    print_summary([rec], title="首尾帧视频")
    return rec["path"]


def multi_ref_video(
    prompt: str,
    reference_images: list,
    duration: int = 5,
    ratio: str = "16:9",
    output_path: str = None,
) -> str:
    """
    模式4：多参考图视频（必须用 MODEL_LITE，PRO 不支持 reference_image 任务类型）
    适合：用户选了多张试穿图，合成一段完整展示视频
    reference_images: 图片 URL 或本地路径列表
    prompt 中用 [图1][图2][图3] 引用对应图片
    ⚠️ 注意：reference_image 任务类型仅 MODEL_LITE 支持，MODEL_PRO 会报 400 错误
    """
    _check_key()
    content = [{"type": "text", "text": prompt}]
    for img in reference_images:
        content.append(_to_image_content(img, role="reference_image"))

    payload = {
        "model": MODEL_LITE,  # reference_image 任务类型只有 lite 模型支持，PRO 不支持
        "content": content,
        "duration": duration,
        "ratio": ratio,
        "watermark": False,
    }

    print(f"\n🎬 多参考图视频 | {len(reference_images)}张参考图 | prompt: {prompt[:60]}...")
    result = _post("/contents/generations/tasks", payload)
    task_id = result.get("id") or result.get("task_id")
    if not task_id:
        print(f"  ❌  未获取到 task_id，响应: {result}")
        sys.exit(1)

    video_url = _poll_task(task_id)
    rec = save_video(video_url, stage="video", label="multiref")
    print_summary([rec], title="多参考图视频")
    return rec["path"]


# ──────────────────────────────────────────────────────
# 试穿场景专用封装
# ──────────────────────────────────────────────────────

def tryon_to_video(
    tryon_images: list,
    garment_desc: str = "服装",
    duration: int = 5,
    output_dir: str = None,
) -> str:
    """
    试穿图 → 展示视频（对话层调用的主入口）

    tryon_images: 试穿效果图路径或URL列表
    - 1张图  → 图生视频（模特转身展示）
    - 多张图 → 多参考图视频（场景切换展示）
    """
    output_dir = get_output_dir(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    # ── 全身锁定 + 物理真实性约束 ──
    FULL_BODY_LOCK = (
        "full body always visible from head to toe throughout entire video, "
        "model centered in frame with 10% margin top and bottom, "
        "camera stays wide and fixed, absolutely no zoom in, no close-up, no cropping, "
        "camera only pans slowly or stays static, "
        "9:16 vertical format maintained throughout, "
        "smooth natural human motion only, physically realistic movement, "
        "head and body rotate naturally in clockwise direction only, "
        "never reversed or counterclockwise head rotation, "
        "no unnatural body distortion, no impossible physics, "
        "clean white or light studio background, no black or dark background, "
        "clothing details clearly visible at all times, consistent outfit throughout. "
        "全身始终完整可见从头到脚，相机保持广角固定，禁止推近和特写，"
        "仅允许轻微平移或静止，9:16竖版比例全程保持，"
        "人物动作必须符合真实物理规律，头部和身体只允许自然方向转动，"
        "禁止逆时针旋转头部等违反物理的动作，"
        "背景保持明亮干净，禁止黑色或深色背景，服装细节清晰可见。"
    )

    if len(tryon_images) == 1:
        prompt = (
            FULL_BODY_LOCK +
            f"model wearing {garment_desc}, "
            "slowly turns clockwise to show front and side, smooth natural catwalk movement, "
            "professional fashion showcase, static or very slight camera pan only, "
            "bright clean studio background, physically natural human motion"
        )
        return video_from_image_auto(
            prompt=prompt,
            image_url=tryon_images[0],
            duration=duration,
            ratio="9:16",
        )
    else:
        # 多张图合成：每张图对应一个场景，全程保持全身可见
        img_refs = "".join([f"[图{i+1}]" for i in range(len(tryon_images))])
        prompt = (
            FULL_BODY_LOCK +
            f"{img_refs} show the same {garment_desc} outfit in different scenes. "
            "Create a smooth fashion showcase video transitioning between scenes. "
            "Each scene shows full body from head to toe. "
            "No zoom in, camera stays wide at all times. "
            "Bright clean background, physically natural motion only."
        )
        # 多图合成：即梦只支持单首帧，使用第一张图；豆包支持多参考图
        if is_jimeng_configured():
            return video_from_image_auto(
                prompt=prompt,
                image_url=tryon_images[0],
                duration=min(duration * len(tryon_images), 10),
                ratio="9:16",
            )
        else:
            return multi_ref_video(
                prompt=prompt,
                reference_images=tryon_images,
                duration=min(duration * len(tryon_images), 10),
                ratio="9:16",
            )


# ──────────────────────────────────────────────────────
# 即梦优先视频路由（统一入口）
# ──────────────────────────────────────────────────────

def video_from_image_auto(
    prompt: str,
    image_url: str,
    duration: int = 5,
    ratio: str = "9:16",
    output_path: str = None,
    generate_audio: bool = False,
) -> str:
    """
    自动选择视频生成后端（即梦优先，否则豆包 Seedance）。

    参数：
      prompt       : 视频描述
      image_url    : 首帧图片 URL 或本地路径
      duration     : 时长（秒），即梦支持5s/10s
      ratio        : 画面比例（即梦: "9:16"/"16:9"/"1:1" 等）
      generate_audio: 是否生成音效（仅豆包支持）

    返回：本地视频路径
    """
    from output_manager import get_output_dir

    if is_jimeng_configured():
        try:
            client = JimengClient()
            # 即梦仅支持公网 URL（本地文件需 OSS）
            resolved_url = image_url
            if not image_url.startswith("http"):
                try:
                    from oss_uploader import ensure_url
                    resolved_url = ensure_url(image_url)
                except Exception as e:
                    raise RuntimeError(f"本地图片需先上传 OSS 才能用即梦视频: {e}")

            # 时长换算：即梦用帧数，5s=121帧, 10s=241帧
            frames = 241 if duration >= 10 else 121

            video_url = client.generate_video(
                prompt=prompt,
                image_url=resolved_url,
                frames=frames,
                aspect_ratio=ratio,
            )
            rec = save_video(video_url, stage="video", label="jimeng_i2v")
            print_summary([rec], title="即梦展示视频")
            return rec["path"]
        except Exception as e:
            print(f"  ⚠️  [即梦] 视频生成失败: {e}")
            print(f"  ↩️  回退到豆包 Seedance...")

    # 豆包 Seedance 降级
    return image_to_video(
        prompt=prompt,
        image_url=image_url,
        duration=duration,
        ratio=ratio,
        generate_audio=generate_audio,
    )


# ──────────────────────────────────────────────────────
# 图片列举工具（Agent 在生视频前调用让用户选图）
# ──────────────────────────────────────────────────────

def list_session_images(output_dir: str = None) -> list:
    """
    列出当前 session 所有已生成的试穿图（step3_tryon + step4_variants）。
    返回按文件名排序的绝对路径列表。
    Agent 调用 --list-images 后把列表展示给用户，请用户最多选 4 张。
    """
    from output_manager import get_session_dir, STAGE_DIRS
    session_dir = get_session_dir(output_dir)
    scan_dirs = [
        os.path.join(session_dir, STAGE_DIRS["tryon"]),    # step3_tryon
        os.path.join(session_dir, STAGE_DIRS["variants"]),  # step4_variants
    ]
    img_exts = {".jpg", ".jpeg", ".png", ".webp"}
    images = []
    for d in scan_dirs:
        if os.path.exists(d):
            for fname in sorted(os.listdir(d)):
                if Path(fname).suffix.lower() in img_exts:
                    images.append(os.path.join(d, fname))
    return images


# ──────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────

_MAX_MULTI_IMAGES = 4   # MODEL_LITE 最多支持 4 张参考图

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI 生视频（即梦优先 / 豆包 Seedance 降级）")
    parser.add_argument("--list-images",  action="store_true",
                        help="列出当前 session 的试穿图供用户选择（生视频前调用）")
    parser.add_argument("--image",       help="单张首帧图片 URL 或路径")
    parser.add_argument("--images",      nargs="+", help=f"多张参考图（最多 {_MAX_MULTI_IMAGES} 张）")
    parser.add_argument("--first-frame", help="首帧图片（配合 --last-frame 控制首尾）")
    parser.add_argument("--last-frame",  help="尾帧图片")
    parser.add_argument("--prompt",      help="视频描述 prompt")
    parser.add_argument("--duration",    type=int, default=5, help="视频时长（秒）")
    parser.add_argument("--ratio",       default="9:16",     help="画面比例：9:16（默认，保证全身可见）/ 16:9 / 1:1")
    parser.add_argument("--audio",       action="store_true", help="生成音效（仅豆包支持）")
    parser.add_argument("--output",      default=None, help="输出路径，默认读 TRYON_OUTPUT_DIR")
    args = parser.parse_args()

    # ── --list-images：列出可用的试穿图，供 Agent/用户选择 ──────────────
    if args.list_images:
        imgs = list_session_images(args.output)
        if not imgs:
            print("  当前 session 尚无试穿图，请先生成试穿效果图。")
            sys.exit(0)
        print(f"\n📸 当前 session 共 {len(imgs)} 张试穿图（多图视频最多选 {_MAX_MULTI_IMAGES} 张）：\n")
        for i, p in enumerate(imgs, 1):
            print(f"  [{i}] {p}")
        print()
        sys.exit(0)

    # ── 其余模式需要 --prompt ───────────────────────────────────────────
    if not args.prompt:
        parser.error("请提供 --prompt 视频描述（或先用 --list-images 查看可用图片）")

    if args.first_frame and args.last_frame:
        first_last_frame_video(
            prompt=args.prompt,
            first_frame_url=args.first_frame,
            last_frame_url=args.last_frame,
            duration=args.duration,
            ratio=args.ratio,
            generate_audio=args.audio,
            output_path=args.output,
        )
    elif args.images:
        if len(args.images) > _MAX_MULTI_IMAGES:
            print(f"⚠️  多图视频最多支持 {_MAX_MULTI_IMAGES} 张参考图（当前传入 {len(args.images)} 张），已自动保留前 {_MAX_MULTI_IMAGES} 张")
            args.images = args.images[:_MAX_MULTI_IMAGES]
        multi_ref_video(
            prompt=args.prompt,
            reference_images=args.images,
            duration=args.duration,
            ratio=args.ratio,
            output_path=args.output,
        )
    elif args.image:
        image_to_video(
            prompt=args.prompt,
            image_url=args.image,
            duration=args.duration,
            ratio=args.ratio,
            generate_audio=args.audio,
            output_path=args.output,
        )
    else:
        print("❌  请提供 --image / --images / --first-frame+--last-frame")
        sys.exit(1)
