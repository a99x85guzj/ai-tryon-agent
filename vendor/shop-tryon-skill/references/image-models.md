# 生图模型接入（用于 AI 生成模特）

## 模型选择策略

| 模型 | 国内直连 | 质量 | 费用 | 适合场景 |
|------|---------|------|------|---------|
| 豆包（火山引擎） | ✅ | ⭐⭐⭐⭐ | ¥0.14/张 | 日系/亚洲模特 |
| DALL·E 3 | 需代理 | ⭐⭐⭐⭐⭐ | ~$0.04/张 | 欧美模特 |
| SD（Stability AI） | 需代理 | ⭐⭐⭐⭐ | $0.003/step | 可精细控制 |

---

## 模特 Prompt 模板库

### 按服装风格自动选择

```python
MODEL_PROMPTS = {
    # 日系/学院风 → 亚洲模特
    "asian_studio": (
        "Young Asian female, {build} figure, standing upright, "
        "arms slightly away from body, neutral expression, facing camera, "
        "full body head to toe, pure white studio background, "
        "soft even lighting, photorealistic, high resolution, no clothing"
    ),

    # 欧美/街头风 → 西方模特
    "western_studio": (
        "Young Caucasian female model, {build} figure, "
        "relaxed natural pose, full body visible, "
        "light gray seamless background, natural soft light, "
        "photorealistic, fashion photography, no clothing"
    ),

    # 男装通用
    "male_studio": (
        "Young {ethnicity} male model, {build} build, standing straight, "
        "arms relaxed at sides, neutral expression, facing camera, "
        "full body head to toe, pure white background, "
        "studio lighting, photorealistic, no clothing"
    ),

    # 最稳定（试穿效果最好）
    "neutral_white": (
        "Female model, {build} figure, standing straight, "
        "arms relaxed at sides, pure white background, "
        "even studio lighting, full body, photorealistic, "
        "no clothing, neutral expression, facing forward"
    ),
}

BUILD_MAP = {"纤细": "slim", "标准": "average", "丰满": "plus-size"}
```

### 服装风格 → 模特风格自动映射

```python
def auto_select_style(garment_keywords: str) -> str:
    kw = garment_keywords.lower()
    if any(w in kw for w in ["jk", "制服", "学院", "和风", "lolita"]):
        return "asian_studio"
    if any(w in kw for w in ["男", "boy", "male", "衬衫(男)", "西装(男)"]):
        return "male_studio"
    if any(w in kw for w in ["欧美", "街头", "潮牌", "oversize"]):
        return "western_studio"
    return "neutral_white"  # 默认：最稳定
```

---

## 豆包（火山引擎）接入

```python
import requests

DOUBAO_API_KEY = "your-key"

def doubao_generate(prompt: str, width=768, height=1024) -> str:
    """返回图片 URL"""
    resp = requests.post(
        "https://visual.volcengineapi.com",
        headers={
            "Authorization": f"Bearer {DOUBAO_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "req_key": "high_aes_general_v21_L",
            "prompt": prompt,
            "width": width,
            "height": height,
            "use_sr": True,
            "return_url": True,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"]["image_urls"][0]
```

**注册地址**：https://console.volcengine.com → 开通豆包大模型

---

## DALL·E 3 接入

```python
import os, openai

# 支持代理/中转地址（OpenAI 兼容接口）
# OPENAI_BASE_URL 留空则直连官方，填代理地址则走代理
_base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None

client = openai.OpenAI(
    api_key=os.getenv("OPENAI_API_KEY", ""),
    base_url=_base_url,   # None = 默认官方地址
)

def dalle3_generate(prompt: str) -> str:
    resp = client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size="1024x1024",
        quality="hd",
        n=1,
    )
    return resp.data[0].url
```

---

## 图片下载工具（Kolors 等需要本地文件时）

```python
import urllib.request, tempfile, os

def download_image(url: str, suffix=".jpg") -> str:
    """下载图片到临时文件，返回本地路径"""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    urllib.request.urlretrieve(url, tmp.name)
    return tmp.name
```

---

## 服装图生成 Prompt 模板

用于「无服装图，只有文字描述」的情况。

```python
GARMENT_PROMPTS = {
    # 上衣类（衬衫/外套/卫衣等）
    "top": (
        "{color} {style} {garment_type}, "
        "flat lay on pure white background, "
        "studio lighting, top-down angle, "
        "full garment visible, no wrinkles, "
        "sharp details, e-commerce product photo, "
        "no text, no model, no watermarks"
    ),

    # 下装类（裤子/裙子）
    "bottom": (
        "{color} {garment_type}, "
        "flat lay on pure white background, "
        "neatly arranged, full length visible, "
        "studio lighting, sharp details, "
        "e-commerce style, no model, no text"
    ),

    # 套装（需要分开生成上衣图和下装图）
    "set_top": (
        "{color} {style} blazer or jacket from a set, "
        "flat lay on pure white background, "
        "studio lighting, top-down angle, "
        "full garment only, sharp details, no model"
    ),
    "set_bottom": (
        "{color} {style} skirt or pants from a matching set, "
        "flat lay on pure white background, "
        "studio lighting, full length, "
        "sharp details, no model"
    ),
}

# 使用示例
def build_garment_prompt(description: str, garment_type: str = "top") -> str:
    """
    description: 用户描述，如「黑色JK制服西装外套」
    garment_type: top / bottom / set_top / set_bottom
    """
    template = GARMENT_PROMPTS[garment_type]
    # 直接把用户描述嵌入，让模型理解
    return f"{description}, " + template.replace("{color} {style} {garment_type}, ", "")
```

### 套装处理说明
套装（如 JK 制服）需要**分别生成**上衣图和下装图，然后同时传给阿里云试衣 API：
```python
top_img    = generate_image(build_garment_prompt("黑色JK制服西装外套", "set_top"))
bottom_img = generate_image(build_garment_prompt("黑色JK制服百褶裙", "set_bottom"))

result = aliyun_tryon(
    model_image_url=model_url,
    top_garment_url=top_img,
    bottom_garment_url=bottom_img,
)
```
