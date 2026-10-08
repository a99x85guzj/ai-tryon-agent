"""
jimeng_client.py — 即梦 AI 图像 / 视频生成客户端

支持：
  - 即梦 4.0 图像生成  (req_key=jimeng_t2i_v40)
  - 即梦视频 3.0 Pro   (req_key=jimeng_ti2v_v30_pro)

认证：火山引擎 HMAC-SHA256 签名
  Region  = cn-north-1
  Service = cv
  API     = https://visual.volcengineapi.com

环境变量：
  JIMENG_ACCESS_KEY   # 火山引擎 AccessKeyId
  JIMENG_SECRET_KEY   # 火山引擎 SecretAccessKey

用法：
  from jimeng_client import JimengClient
  client = JimengClient()                     # 自动从环境变量读取
  urls   = client.generate_image("穿着蕾丝裙的少女，纯白背景")
  url    = client.generate_video("模特缓缓转身", image_url="https://...")
"""

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


# ── 常量 ─────────────────────────────────────────────────
_HOST       = "visual.volcengineapi.com"
_BASE_URL   = f"https://{_HOST}"
_REGION     = "cn-north-1"
_SERVICE    = "cv"
_ALGORITHM  = "HMAC-SHA256"
_VERSION    = "2022-08-31"

# 即梦 req_key 常量
REQ_KEY_IMAGE = "jimeng_t2i_v40"
REQ_KEY_VIDEO = "jimeng_ti2v_v30_pro"

# 任务轮询状态
STATUS_DONE      = "done"
STATUS_IN_QUEUE  = "in_queue"
STATUS_GENERATING = "generating"
STATUS_NOT_FOUND = "not_found"
STATUS_EXPIRED   = "expired"


# ── Volcengine HMAC-SHA256 签名 ────────────────────────────

def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _build_canonical_query(query_params: dict) -> str:
    """构建规范化 Query String（按 key 字母序排序，URL 编码）"""
    import urllib.parse
    if not query_params:
        return ""
    return "&".join(
        f"{urllib.parse.quote(str(k), safe='')}={urllib.parse.quote(str(v), safe='')}"
        for k, v in sorted(query_params.items())
    )


def _sign_request(
    method: str,
    path: str,
    query_params: dict,
    headers: dict,
    body: bytes,
    access_key: str,
    secret_key: str,
) -> dict:
    """
    执行 Volcengine HMAC-SHA256 签名，返回包含 Authorization 的完整 Header 字典。

    参数：
      method       : HTTP 方法，大写，如 "POST"
      path         : 请求路径，如 "/"
      query_params : Query 参数字典，如 {"Action": "CVSync2AsyncSubmitTask", "Version": "2022-08-31"}
      headers      : 已有 Header 字典（不含 Authorization），必须含 Host 和 X-Date
      body         : 请求体字节串
      access_key   : 火山引擎 AccessKeyId
      secret_key   : 火山引擎 SecretAccessKey
    """
    # 1. 当前时间
    now = datetime.now(timezone.utc)
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = x_date[:8]  # YYYYMMDD

    # 2. 补全必要 Headers
    body_hash = _sha256_hex(body)
    headers = dict(headers)
    headers["Host"]              = _HOST
    headers["X-Date"]            = x_date
    headers["X-Content-Sha256"]  = body_hash
    headers["Content-Type"]      = "application/json"

    # 3. 规范化 Header（按 key 小写字母序）
    signed_header_names = sorted(k.lower() for k in headers)
    canonical_headers = "".join(
        f"{k.lower()}:{headers[next(h for h in headers if h.lower() == k.lower())].strip()}\n"
        for k in signed_header_names
    )
    signed_headers_str = ";".join(signed_header_names)

    # 4. 构建 CanonicalRequest
    canonical_query = _build_canonical_query(query_params)
    canonical_request = "\n".join([
        method.upper(),
        path,
        canonical_query,
        canonical_headers,
        signed_headers_str,
        body_hash,
    ])

    # 5. 构建 StringToSign
    credential_scope = f"{short_date}/{_REGION}/{_SERVICE}/request"
    string_to_sign = "\n".join([
        _ALGORITHM,
        x_date,
        credential_scope,
        _sha256_hex(canonical_request.encode("utf-8")),
    ])

    # 6. 推导签名密钥
    k_date    = _hmac_sha256(secret_key.encode("utf-8"), short_date)
    k_region  = _hmac_sha256(k_date,   _REGION)
    k_service = _hmac_sha256(k_region, _SERVICE)
    k_signing = _hmac_sha256(k_service, "request")

    # 7. 计算签名
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    # 8. 组装 Authorization
    headers["Authorization"] = (
        f"{_ALGORITHM} Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers_str}, Signature={signature}"
    )
    return headers


def _do_request(
    query_params: dict,
    body_dict: dict,
    access_key: str,
    secret_key: str,
    timeout: int = 30,
) -> dict:
    """
    发送一次签名 POST 请求到即梦 API，返回响应 dict。
    body_dict 会序列化为 JSON bytes。
    """
    body = json.dumps(body_dict, ensure_ascii=False).encode("utf-8")
    import urllib.parse
    query_string = urllib.parse.urlencode(sorted(query_params.items()))
    url = f"{_BASE_URL}/?{query_string}"

    signed_headers = _sign_request(
        method="POST",
        path="/",
        query_params=query_params,
        headers={},
        body=body,
        access_key=access_key,
        secret_key=secret_key,
    )

    req = urllib.request.Request(
        url,
        data=body,
        headers=signed_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {err_body}") from e


# ── 任务提交 / 轮询 ────────────────────────────────────────

def _submit_task(req_key: str, payload: dict, access_key: str, secret_key: str) -> str:
    """提交异步任务，返回 task_id"""
    query = {"Action": "CVSync2AsyncSubmitTask", "Version": _VERSION}
    body = {"req_key": req_key, **payload}
    result = _do_request(query, body, access_key, secret_key)
    if result.get("code") != 10000:
        raise RuntimeError(
            f"[code={result.get('code')}] {result.get('message', str(result))} "
            f"(request_id={result.get('request_id', '-')})"
        )
    task_id = result.get("data", {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"未获取到 task_id，响应: {result}")
    return task_id


def _poll_task(
    req_key: str,
    task_id: str,
    access_key: str,
    secret_key: str,
    timeout: int = 300,
    poll_interval: int = 4,
    return_url: bool = True,
) -> dict:
    """
    轮询任务结果，直到 status=done 或超时。
    返回 data 字段的 dict（含 image_urls / video_url 等）。
    """
    query = {"Action": "CVSync2AsyncGetResult", "Version": _VERSION}
    req_json_obj = {"return_url": return_url}
    body = {
        "req_key": req_key,
        "task_id": task_id,
        "req_json": json.dumps(req_json_obj),
    }

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_interval)
        try:
            result = _do_request(query, body, access_key, secret_key, timeout=15)
        except RuntimeError as e:
            print(f"  ⚠️  轮询出错: {e}，继续重试...")
            continue

        code   = result.get("code")
        status = (result.get("data") or {}).get("status", "")

        if code != 10000:
            raise RuntimeError(
                f"任务失败 [code={code}] {result.get('message', '')} "
                f"(request_id={result.get('request_id', '-')})"
            )

        print(f"  ⏳ 状态: {status}...")

        if status == STATUS_DONE:
            return result.get("data", {})
        if status in (STATUS_NOT_FOUND, STATUS_EXPIRED):
            raise RuntimeError(f"任务无效: status={status}, task_id={task_id}")

    raise TimeoutError(f"轮询超时（{timeout}s），task_id={task_id}")


# ── 公开客户端类 ────────────────────────────────────────────

class JimengClient:
    """
    即梦 API 高层封装。

    使用示例：
      client = JimengClient()
      image_urls = client.generate_image(
          prompt="穿着米白色蕾丝裙的少女，纯白背景，全身像",
          image_urls=["https://xxx/model.jpg"],  # 可选：参考图
          width=1024, height=1792,
      )
      video_url = client.generate_video(
          prompt="模特缓缓转身展示裙子",
          image_url="https://xxx/tryon.jpg",
          aspect_ratio="9:16",
          frames=121,
      )
    """

    def __init__(self, access_key: str = None, secret_key: str = None):
        self.access_key = access_key or os.getenv("JIMENG_ACCESS_KEY", "")
        self.secret_key = secret_key or os.getenv("JIMENG_SECRET_KEY", "")

    @property
    def is_configured(self) -> bool:
        return bool(self.access_key and self.secret_key)

    def _check(self):
        if not self.is_configured:
            raise RuntimeError(
                "JIMENG_ACCESS_KEY / JIMENG_SECRET_KEY 未配置，"
                "请在 scripts/.env 中填写火山引擎的 AccessKeyId 和 SecretAccessKey"
            )

    # ── 图像生成 ────────────────────────────────────────────

    def generate_image(
        self,
        prompt: str,
        image_urls: list = None,
        width: int = None,
        height: int = None,
        size: int = None,
        scale: float = 0.5,
        force_single: bool = True,
        timeout: int = 180,
    ) -> list:
        """
        即梦 4.0 图像生成（异步任务模式）。

        参数：
          prompt       : 生图提示词（支持中文）
          image_urls   : 参考图 URL 列表（图生图，可选，最多10张）
          width/height : 输出尺寸（需同时指定，否则用 size）
          size         : 输出面积（默认 2048*2048）
          scale        : 文本影响权重 [0,1]，越大越贴近 prompt，默认 0.5
          force_single : 强制只输出1张，默认 True（减少延迟）
          timeout      : 最大等待秒数

        返回：图片 URL 列表（有效期24h）
        """
        self._check()

        payload = {
            "prompt": prompt,
            "scale": scale,
            "force_single": force_single,
        }
        if image_urls:
            payload["image_urls"] = image_urls
        if width and height:
            payload["width"]  = width
            payload["height"] = height
        elif size:
            payload["size"] = size
        else:
            # 默认竖版人像尺寸 1024×1792（接近 9:16）
            payload["width"]  = 1024
            payload["height"] = 1792

        print(f"  🎨 [即梦] 图像生成 | prompt: {prompt[:80]}...")
        print(f"         参考图数量: {len(image_urls) if image_urls else 0}")

        task_id = _submit_task(REQ_KEY_IMAGE, payload, self.access_key, self.secret_key)
        print(f"  ⏳ 任务提交成功: {task_id}")

        data = _poll_task(
            REQ_KEY_IMAGE, task_id,
            self.access_key, self.secret_key,
            timeout=timeout,
        )
        urls = data.get("image_urls") or []
        if not urls:
            raise RuntimeError(f"即梦图像任务完成但未返回图片 URL，data={data}")
        print(f"   [即梦] 图像生成完成，共 {len(urls)} 张")
        return urls

    # ── 视频生成 ────────────────────────────────────────────

    def generate_video(
        self,
        prompt: str,
        image_url: str = None,
        frames: int = 121,
        aspect_ratio: str = "9:16",
        seed: int = -1,
        timeout: int = 360,
    ) -> str:
        """
        即梦视频 3.0 Pro 图生视频 / 文生视频。

        参数：
          prompt       : 视频描述提示词
          image_url    : 首帧参考图 URL（图生视频；None 则为文生视频）
          frames       : 总帧数 121=5s, 241=10s（默认5s）
          aspect_ratio : 画面比例，可选 "16:9"/"9:16"/"1:1"/"4:3"/"3:4"/"21:9"
          seed         : 随机种子，-1=随机
          timeout      : 最大等待秒数

        返回：视频 URL（有效期1h）
        """
        self._check()

        payload = {
            "prompt": prompt,
            "frames": frames,
            "aspect_ratio": aspect_ratio,
            "seed": seed,
        }
        if image_url:
            payload["image_urls"] = [image_url]

        print(f"  🎬 [即梦] 视频生成 | {frames}帧 {aspect_ratio} | prompt: {prompt[:80]}...")

        task_id = _submit_task(REQ_KEY_VIDEO, payload, self.access_key, self.secret_key)
        print(f"  ⏳ 任务提交成功: {task_id}")

        data = _poll_task(
            REQ_KEY_VIDEO, task_id,
            self.access_key, self.secret_key,
            timeout=timeout,
            poll_interval=6,
        )
        video_url = data.get("video_url")
        if not video_url:
            raise RuntimeError(f"即梦视频任务完成但未返回 video_url，data={data}")
        print(f"   [即梦] 视频生成完成: {video_url[:80]}...")
        return video_url


# ── 模块级便捷函数（自动从环境变量初始化）──────────────────

def _default_client() -> JimengClient:
    return JimengClient()


def is_jimeng_configured() -> bool:
    """检查 JIMENG_ACCESS_KEY / JIMENG_SECRET_KEY 是否已配置"""
    return bool(
        os.getenv("JIMENG_ACCESS_KEY", "").strip()
        and os.getenv("JIMENG_SECRET_KEY", "").strip()
    )


def jimeng_generate_image(
    prompt: str,
    image_urls: list = None,
    width: int = 1024,
    height: int = 1792,
    force_single: bool = True,
    timeout: int = 180,
) -> list:
    """模块级便捷函数：即梦图像生成"""
    return _default_client().generate_image(
        prompt=prompt,
        image_urls=image_urls,
        width=width,
        height=height,
        force_single=force_single,
        timeout=timeout,
    )


def jimeng_generate_video(
    prompt: str,
    image_url: str = None,
    frames: int = 121,
    aspect_ratio: str = "9:16",
    timeout: int = 360,
) -> str:
    """模块级便捷函数：即梦视频生成"""
    return _default_client().generate_video(
        prompt=prompt,
        image_url=image_url,
        frames=frames,
        aspect_ratio=aspect_ratio,
        timeout=timeout,
    )
