# 试穿 API 接入详情

## 阿里云百炼 AI试衣（主方案）

### 正确的 API 参数（已验证）

```
POST https://dashscope.aliyuncs.com/api/v1/services/aigc/image2image/image-synthesis/
Header: X-DashScope-Async: enable
Header: Authorization: Bearer {ALIYUN_API_KEY}
Header: Content-Type: application/json
```

| model 参数 | 说明 |
|-----------|------|
| `aitryon` | 基础版，速度快 |
| `aitryon-plus` | Plus 版，质量更高 |
| `aitryon-refiner` | 精修版，在试穿结果上叠加细化 |
| `aitryon-parsing` | 图片分割，提取服装/局部区域 |

### 完整接入代码

```python
import os, json, time, urllib.request

ALIYUN_API_KEY = os.getenv("ALIYUN_API_KEY", "")
_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/image2image/image-synthesis/"
_TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"


def _submit(payload: dict) -> str:
    """提交异步任务，返回 task_id"""
    req = urllib.request.Request(
        _ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {ALIYUN_API_KEY}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.loads(r.read())
    if result.get("code"):
        raise RuntimeError(f"提交失败: {result}")
    return result["output"]["task_id"]


def _poll(task_id: str, timeout: int = 120) -> dict:
    """轮询任务结果，返回 output 字段"""
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
            raise RuntimeError(f"任务失败: {s['output'].get('message', s)}")
    raise TimeoutError(f"超时 {timeout}s，task_id={task_id}")


def aliyun_tryon(
    person_image_url: str,
    top_garment_url: str = None,
    bottom_garment_url: str = None,
    model: str = "aitryon-plus",
    resolution: int = -1,
    restore_face: bool = True,
) -> str:
    """
    全身/上衣/下装试穿，返回结果图片 URL

    试穿模式说明：
    - 只传 top_garment_url   → 只换上衣，下装随机生成
    - 只传 bottom_garment_url → 只换下装，上衣随机生成
    - 两个都传               → 上下装全换（套装）
    """
    inp = {"person_image_url": person_image_url}
    if top_garment_url:
        inp["top_garment_url"] = top_garment_url
    if bottom_garment_url:
        inp["bottom_garment_url"] = bottom_garment_url

    task_id = _submit({
        "model": model,
        "input": inp,
        "parameters": {"resolution": resolution, "restore_face": restore_face},
    })
    print(f"  ⏳ 试衣任务提交: {task_id}")
    output = _poll(task_id)
    return output["image_url"]


def aliyun_refine(
    person_image_url: str,
    coarse_image_url: str,
    top_garment_url: str = None,
    bottom_garment_url: str = None,
    gender: str = "woman",
) -> str:
    """
    在试穿结果上叠加精修（可选，适合商品主图级别）
    coarse_image_url: 上一步 aliyun_tryon 的输出 URL
    """
    inp = {
        "person_image_url": person_image_url,
        "coarse_image_url": coarse_image_url,
    }
    if top_garment_url:
        inp["top_garment_url"] = top_garment_url
    if bottom_garment_url:
        inp["bottom_garment_url"] = bottom_garment_url

    task_id = _submit({
        "model": "aitryon-refiner",
        "input": inp,
        "parameters": {"gender": gender},
    })
    print(f"  ⏳ 精修任务提交: {task_id}")
    return _poll(task_id)["image_url"]


def aliyun_parse_garment(
    person_image_url: str,
    parse_type: str = "upper",
) -> str:
    """
    局部试穿：先分割出服装区域，返回分割后的服装图 URL
    parse_type: "upper"=上装 / "lower"=下装 / "overall"=全身
    用途：保留模特原有某件衣服，只换另一件时使用
    """
    task_id = _submit({
        "model": "aitryon-parsing",
        "input": {"person_image_url": person_image_url},
        "parameters": {"parse_type": parse_type},
    })
    print(f"  ⏳ 分割任务提交: {task_id}")
    return _poll(task_id)["image_url"]
```

### 局部试穿（只换上衣，保留原下装）

```python
# 场景：模特已有下装，只想换上衣
# Step 1: 分割出模特原下装
original_bottom_url = aliyun_parse_garment(person_image_url, parse_type="lower")

# Step 2: 试穿新上衣 + 保留原下装
result = aliyun_tryon(
    person_image_url=person_image_url,
    top_garment_url=new_top_url,
    bottom_garment_url=original_bottom_url,
)
```

### 调用示例

```python
# 只换上衣
result = aliyun_tryon(
    person_image_url="https://xxx/model.jpg",
    top_garment_url="https://xxx/shirt.jpg",
)

# 套装全换（JK制服）
result = aliyun_tryon(
    person_image_url="https://xxx/model.jpg",
    top_garment_url="https://xxx/jk_blazer.jpg",
    bottom_garment_url="https://xxx/jk_skirt.jpg",
)

# Plus + 精修（最高质量）
tryon_url = aliyun_tryon(
    person_image_url="https://xxx/model.jpg",
    top_garment_url="https://xxx/coat.jpg",
    model="aitryon-plus",
)
final_url = aliyun_refine(
    person_image_url="https://xxx/model.jpg",
    coarse_image_url=tryon_url,
    top_garment_url="https://xxx/coat.jpg",
)
```

---

## 多提示词变体生成（Prompt Variants）

用户描述一个试穿需求，AI 生成最多 5 个风格变体提示词，
每个提示词生成一张效果图，用户选择喜欢的，后续合并视频时使用。

### 流程

```
用户描述 → Claude 生成 1~5 个 Prompt 变体
              ↓
        并发/串行提交试衣任务（或生图任务）
              ↓
        展示所有结果，让用户选择
              ↓
        用户选中的图片 → 后续合并视频
```

### Prompt 变体生成规则

```python
# ⚠️ 以下是发给 Claude 的 system 提示，用于指导 Claude 生成变体，
# 不是生图 prompt，不要直接用于生图或试衣 API
VARIANT_SYSTEM_PROMPT = """
你是一个专业的 AI 试穿效果图提示词专家。
用户描述一个试穿需求，你生成 {n} 个不同风格的场景变体。
每个变体只修改：背景场景、光线氛围、模特姿势、季节感，
不修改服装本身的描述。

输出格式（JSON数组），内容需根据用户请求动态生成，不要照搬示例：
[
  {
    "id": <整数>,
    "label": "<简短风格描述，如：日系校园>",
    "model_prompt": "<英文模特图生成 prompt>",
    "scene_note": "<给用户看的场景说明（中文）>"
  }
]
只返回 JSON，不要其他内容。
"""

def generate_prompt_variants(
    garment_description: str,
    n: int = 3,
    style_hint: str = "",
) -> list:
    """调用 Claude 生成 n 个试穿场景变体"""
    import urllib.request, json, os

    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("ALIYUN_API_KEY")
    if os.getenv("ANTHROPIC_API_KEY"):
        return _variants_claude(garment_description, n, style_hint)
    # 无 Claude Key 时使用内置默认变体（见 image_gen_tryon.py）
    return []


def _variants_claude(desc: str, n: int, style_hint: str) -> list:
    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 800,
        "system": VARIANT_SYSTEM_PROMPT.format(n=n),
        "messages": [{"role": "user", "content": f"服装：{desc}\n风格偏好：{style_hint or '不限'}"}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": os.getenv("ANTHROPIC_API_KEY"),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = json.loads(r.read())["content"][0]["text"]
    return json.loads(raw)
```

### 变体展示与选择

```python
def show_variants_and_select(variants: list, result_urls: list) -> list:
    """
    展示变体结果让用户选择
    返回用户选中的图片 URL 列表
    """
    print("\n🎨 生成了以下试穿效果，请选择满意的：\n")
    for i, (v, url) in enumerate(zip(variants, result_urls)):
        print(f"  [{i+1}] {v['label']} — {v['scene_note']}")
        print(f"       图片: {url}\n")

    choices = input("输入选择（如 1,3 或 all）：").strip()
    if choices.lower() == "all":
        return result_urls
    indices = [int(x.strip()) - 1 for x in choices.split(",")]
    return [result_urls[i] for i in indices]
```

---

## 备用方案：IDM-VTON via Replicate

> ⚠️ 模型 License CC BY-NC-SA 4.0（原作者 KAIST），严格合规商业场景请用阿里云。
> Replicate 平台收费 ≠ 获得商业授权。个人测试/非商业项目可用。

```python
import os, replicate

def idmvton_tryon(
    human_img_url: str,
    garm_img_url: str,
    garment_des: str = "clothing item",
) -> str:
    os.environ["REPLICATE_API_TOKEN"] = os.getenv("REPLICATE_API_TOKEN", "")
    output = replicate.run(
        "cuuupid/idm-vton:0513734a452173b8173e907e3a59d19a36266e55b48528559432bd21c7d7e985",
        input={
            "garm_img":    garm_img_url,
            "human_img":   human_img_url,
            "garment_des": garment_des,
        }
    )
    return output.url  # 或 output.read() 保存本地
```

---

## 备用方案：Fashn.ai（海外用户）

```python
import os, json, time, urllib.request

def fashn_tryon(
    model_image_url: str,
    garment_image_url: str,
    category: str,   # "tops" | "bottoms" | "one-pieces"
    num_samples: int = 1,
) -> list:
    FASHN_API_KEY = os.getenv("FASHN_API_KEY", "")
    if not FASHN_API_KEY or FASHN_API_KEY == "fa-":
        raise RuntimeError("FASHN_API_KEY 未配置")

    payload = json.dumps({
        "model_image": model_image_url,
        "garment_image": garment_image_url,
        "category": category,
        "mode": "quality",
        "garment_photo_type": "flat-lay",
        "num_samples": num_samples,
    }).encode()
    req = urllib.request.Request(
        "https://api.fashn.ai/v1/run",
        data=payload,
        headers={"Authorization": f"Bearer {FASHN_API_KEY}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        pid = json.loads(r.read())["id"]

    for _ in range(40):
        time.sleep(3)
        with urllib.request.urlopen(
            urllib.request.Request(f"https://api.fashn.ai/v1/status/{pid}",
                                   headers={"Authorization": f"Bearer {FASHN_API_KEY}"}),
            timeout=10,
        ) as r:
            res = json.loads(r.read())
        if res["status"] == "completed":
            return res["output"]
        if res["status"] == "failed":
            raise RuntimeError(f"Fashn.ai 失败: {res.get('error')}")
    raise TimeoutError("Fashn.ai 超时")
```
