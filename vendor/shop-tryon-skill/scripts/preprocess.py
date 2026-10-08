"""
preprocess.py
服装图预处理：去背景 / 白底化
"""
import os, sys, io, urllib.request, tempfile
from pathlib import Path

# ─── 加载 .env ────────────────────────────────────
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from output_manager import load_env
load_env(__file__)

REMOVEBG_API_KEY = os.getenv("REMOVEBG_API_KEY", "")

def remove_bg_rembg(input_path: str, output_path: str):
    """使用 rembg 去背景并白底化（本地，免费）"""
    try:
        from rembg import remove, new_session
        from PIL import Image
    except ImportError:
        print("请先安装依赖: pip install rembg pillow")
        sys.exit(1)

    # 显式使用 u2net（约 176MB，去背景 ~10s）。rembg 新版本默认模型是
    # bria-rmbg（约 1GB，去背景 ~29s），会让预检接口超时。
    session = new_session("u2net")
    with open(input_path, "rb") as f:
        result = remove(f.read(), session=session)

    img = Image.open(io.BytesIO(result)).convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.paste(img, mask=img.split()[3])
    bg.convert("RGB").save(output_path, "JPEG", quality=95)
    print(f" 去背景完成: {output_path}")
    return output_path


def remove_bg_api(input_path: str, output_path: str, api_key: str):
    """使用 remove.bg API（50次/月免费）"""
    with open(input_path, "rb") as f:
        img_data = f.read()

    resp = urllib.request.urlopen(
        urllib.request.Request(
            "https://api.remove.bg/v1.0/removebg",
            data=urllib.parse.urlencode({
                "size": "auto",
            }).encode() + b"&image_file_b64=" +
            __import__("base64").b64encode(img_data),
            headers={
                "X-Api-Key": api_key,
                "Content-Type": "application/x-www-form-urlencoded",
            }
        )
    )
    with open(output_path, "wb") as f:
        f.write(resp.read())
    print(f" remove.bg 去背景完成: {output_path}")
    return output_path


def download_image(url: str) -> str:
    """下载远程图片到临时文件，返回本地路径"""
    suffix = ".jpg" if "jpg" in url.lower() else ".png"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    urllib.request.urlretrieve(url, tmp.name)
    print(f" 图片已下载: {tmp.name}")
    return tmp.name


def is_white_background(image_path: str, threshold: float = 0.85) -> bool:
    """检测图片是否已经是白底（跳过预处理的判断依据）"""
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return False  # 无法判断，保守处理

    img = Image.open(image_path).convert("RGB")
    arr = __import__("numpy").array(img)

    # 检查四个角落像素是否接近白色
    corners = [arr[0,0], arr[0,-1], arr[-1,0], arr[-1,-1]]
    white_corners = sum(1 for c in corners if all(v > 230 for v in c))

    # 检查整体白色像素占比
    white_pixels = __import__("numpy").all(arr > 230, axis=2).sum()
    total_pixels = arr.shape[0] * arr.shape[1]
    white_ratio = white_pixels / total_pixels

    return white_corners >= 3 and white_ratio > threshold


def preprocess_garment(
    input_path: str,
    output_path: str = None,
    method: str = "rembg",
    api_key: str = None,
    skip_if_white: bool = True,
) -> str:
    """
    主入口：智能预处理服装图

    method: "rembg" | "removebg_api"
    返回处理后的图片路径
    """
    if output_path is None:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_processed.jpg"

    # 如果已经是白底，跳过
    if skip_if_white and is_white_background(input_path):
        print(" 已是白底图，跳过预处理")
        return input_path

    print(f"🔧 开始去背景处理: {input_path}")

    if method == "rembg":
        return remove_bg_rembg(input_path, output_path)
    elif method == "removebg_api":
        if not api_key:
            raise ValueError("使用 remove.bg API 需要提供 api_key")
        return remove_bg_api(input_path, output_path, api_key)
    else:
        raise ValueError(f"未知方法: {method}，可选: rembg | removebg_api")


# ─── CLI 入口 ─────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="服装图去背景 / 白底化")
    parser.add_argument("image", help="图片路径或 URL")
    parser.add_argument("-o", "--output", default=None,
                        help="输出路径（默认: 原文件名_processed.jpg）")
    parser.add_argument("--overwrite", action="store_true",
                        help="直接覆盖原文件（本地图片时有效）")
    parser.add_argument("--method", default="rembg", choices=["rembg", "removebg_api"],
                        help="去背景方法（默认: rembg）")
    parser.add_argument("--api-key", default=None,
                        help="remove.bg API Key（method=removebg_api 时需要）")
    parser.add_argument("--force", action="store_true",
                        help="强制处理，即使检测到已是白底也执行去背景")
    parser.add_argument("--upload", action="store_true",
                        help="处理后上传 OSS 并打印公网 URL")
    args = parser.parse_args()

    inp = args.image
    is_url = inp.startswith("http")

    # 如果是 URL，先下载
    if is_url:
        inp = download_image(inp)

    # 确定输出路径
    out = args.output
    if args.overwrite and not is_url:
        out = inp  # 覆盖原文件
    elif out is None:
        base, ext = os.path.splitext(inp)
        out = f"{base}_processed.jpg"

    result = preprocess_garment(
        inp, out,
        method=args.method,
        api_key=args.api_key or REMOVEBG_API_KEY or None,
        skip_if_white=not args.force,
    )
    print(f"输出文件: {result}")

    # 处理后上传 OSS
    if args.upload:
        try:
            from oss_uploader import ensure_url
            url = ensure_url(result)
            print(f"公网 URL: {url}")
        except Exception as e:
            print(f" 上传失败: {e}")
