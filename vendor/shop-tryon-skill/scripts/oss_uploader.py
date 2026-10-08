"""
oss_uploader.py — 阿里云 OSS 图床

功能：
- 本地图片上传到 OSS，返回公网可访问的 URL
- 支持批量上传
- 上传前自动转换 webp → jpg（阿里云试衣 API 不支持 webp）

用法：
  python oss_uploader.py image.jpg           # 上传单张，打印 URL
  python oss_uploader.py img1.jpg img2.jpg   # 批量上传

依赖：
  pip install oss2
"""

import os, sys, uuid, time
from pathlib import Path

# ── 加载 .env ──────────────────────────────────────────
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from output_manager import load_env
load_env(__file__)

OSS_ACCESS_KEY_ID     = os.getenv("OSS_ACCESS_KEY_ID", "")
OSS_ACCESS_KEY_SECRET = os.getenv("OSS_ACCESS_KEY_SECRET", "")
OSS_BUCKET_NAME       = os.getenv("OSS_BUCKET_NAME", "")
OSS_ENDPOINT          = os.getenv("OSS_ENDPOINT", "oss-cn-hangzhou.aliyuncs.com")
OSS_PREFIX            = os.getenv("OSS_PREFIX", "ai-tryon/")   # OSS 目录前缀
OSS_CDN_DOMAIN        = os.getenv("OSS_CDN_DOMAIN", "")        # 可选：自定义 CDN 域名
OSS_SIGN_EXPIRATION   = int(os.getenv("OSS_SIGN_EXPIRATION", "0"))  # >0 时生成临时签名 URL（秒），0=公共读 URL


def _check_config() -> bool:
    """返回 True=配置完整，False=缺少配置（不报错，让调用方决定处理方式）"""
    missing = [k for k, v in {
        "OSS_ACCESS_KEY_ID":     OSS_ACCESS_KEY_ID,
        "OSS_ACCESS_KEY_SECRET": OSS_ACCESS_KEY_SECRET,
        "OSS_BUCKET_NAME":       OSS_BUCKET_NAME,
    }.items() if not v]
    if missing:
        print(f"    OSS 未配置（{', '.join(missing)}），改用 base64 传输")
        return False
    return True


def _get_bucket():
    try:
        import oss2
    except ImportError:
        print("  oss2 未安装，请运行: pip install oss2")
        sys.exit(1)
    auth = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
    return oss2.Bucket(auth, f"https://{OSS_ENDPOINT}", OSS_BUCKET_NAME)


def sign_url(remote_key: str, expires: int = 3600) -> str:
    """
    生成临时签名 URL（适用于私有 Bucket）

    remote_key: OSS 对象键
    expires:    有效期（秒），默认 1 小时
    """
    if not _check_config():
        raise RuntimeError("OSS 未配置，无法生成签名 URL")
    bucket = _get_bucket()
    return bucket.sign_url('GET', remote_key, expires)


def _build_url(remote_key: str) -> str:
    """
    根据配置构建访问 URL：
    - OSS_SIGN_EXPIRATION > 0  → 临时签名 URL（私有 Bucket）
    - OSS_CDN_DOMAIN 已配置   → CDN URL
    - 否则                    → OSS 公共读 URL
    """
    if OSS_SIGN_EXPIRATION > 0:
        url = sign_url(remote_key, OSS_SIGN_EXPIRATION)
        print(f"  🔑 临时签名 URL（{OSS_SIGN_EXPIRATION}s 有效）")
        return url
    if OSS_CDN_DOMAIN:
        return f"https://{OSS_CDN_DOMAIN.rstrip('/')}/{remote_key}"
    return f"https://{OSS_BUCKET_NAME}.{OSS_ENDPOINT}/{remote_key}"


def check_image_format(src_path: str) -> None:
    """
    检测图片格式，若不是 jpg/jpeg 则打印警告。
    不会自动转换，由用户自行决定是否处理。
    """
    ext = Path(src_path).suffix.lower()
    if ext not in (".jpg", ".jpeg"):
        print(
            f"    图片格式为 {ext}，阿里云试衣 API 对非 JPG 格式支持不稳定。\n"
            f"     如效果异常，请手动将图片另存为 JPG 后重试：\n"
            f"     {src_path}"
        )


def upload(local_path: str, remote_key: str = None) -> str:
    """
    上传单张图片到 OSS，返回公网 URL

    local_path:  本地文件路径
    remote_key:  OSS 对象键（不传则自动生成唯一文件名）
    """
    if not _check_config():
        raise RuntimeError("OSS 未配置，无法直接 upload，请使用 ensure_url")

    check_image_format(local_path)
    src = local_path

    # 生成 OSS key
    if remote_key is None:
        ext = Path(src).suffix.lower() or ".jpg"
        remote_key = f"{OSS_PREFIX}{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"

    bucket = _get_bucket()
    bucket.put_object_from_file(remote_key, src)

    url = _build_url(remote_key)
    print(f"   上传成功: {url}")
    return url


def upload_from_url(remote_url: str) -> str:
    """
    下载远程图片 → 转换格式 → 上传到 OSS
    用于把试穿结果图永久保存到自己的 OSS
    """
    import urllib.request, tempfile
    suffix = ".jpg"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    urllib.request.urlretrieve(remote_url, tmp.name)
    return upload(tmp.name)


def _to_base64(path: str) -> str:
    """本地文件转 base64 data URI（OSS 不可用时的降级）"""
    import base64
    ext = Path(path).suffix.lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "bmp": "image/bmp",
            "webp": "image/webp"}.get(ext, "image/jpeg")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"


def ensure_url(path_or_url: str) -> str:
    """
    核心工具函数：
    - http URL → 直接返回
    - 本地路径 + OSS 已配置 → 上传 OSS → 返回公网 URL
    - 本地路径 + OSS 未配置 → 转 base64 data URI（阿里云试衣 API 支持）
    """
    if path_or_url.startswith("http") or path_or_url.startswith("data:"):
        return path_or_url
    if not _check_config():
        print(f"   base64 传输: {Path(path_or_url).name}")
        return _to_base64(path_or_url)
    print(f"   上传本地图片到 OSS: {path_or_url}")
    return upload(path_or_url)


def batch_upload(paths: list) -> list:
    """批量上传，返回 URL 列表"""
    return [ensure_url(p) for p in paths]


# ── CLI ────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python oss_uploader.py <图片路径> [图片路径2 ...]")
        sys.exit(0)
    for path in sys.argv[1:]:
        url = upload(path)
        print(url)
