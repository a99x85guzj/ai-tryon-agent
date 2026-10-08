"""
garment_analyzer.py — 服装图视觉理解

功能：
- 接收服装图片（本地路径或 URL），调用 Qwen 视觉大模型进行分析
- 返回结构化的服装描述：类型、颜色、风格、面料、适合场景等
- 需要 OSS 配置（本地图片需上传为公网 URL）

用法：
  python garment_analyzer.py /path/to/garment.jpg
  python garment_analyzer.py https://example.com/garment.jpg
  python garment_analyzer.py /path/to/garment.jpg --json

依赖：
  pip install openai
"""

import os, sys, json
from pathlib import Path

# ── 加载 .env ──────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))
from output_manager import load_env, get_output_dir
load_env(__file__)

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY") or os.getenv("ALIYUN_API_KEY", "")
DASHSCOPE_BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)
DASHSCOPE_MODEL = os.getenv("DASHSCOPE_MODEL", "qwen-vl-max")

# ── 服装分析提示词 ──────────────────────────────────────

GARMENT_ANALYSIS_PROMPT = """\
你是一位专业的服装分析师。请仔细观察这张服装图片，输出以下信息：

1. **服装类型**：具体款式名称（如：圆领短袖T恤、A字半裙、连帽卫衣、西装外套等）
2. **服装分类**：上装 / 下装 / 全身（连衣裙/套装算全身）
3. **颜色与图案**：主色调、配色、是否有印花/条纹/纯色等
4. **面料质感**：看起来像什么材质（棉、涤纶、丝绸、牛仔、针织等）
5. **风格标签**：2~4 个风格关键词（如：休闲、正式、运动、甜美、街头、复古等）
6. **适合性别**：男装 / 女装 / 中性
7. **适合季节**：春夏 / 秋冬 / 四季
8. **适合场景**：日常通勤、约会、运动、正式场合等
9. **一句话描述**：用一句自然语言描述这件服装，适合直接作为 AI 生图的 prompt 输入

请用 JSON 格式输出，字段如下：
```json
{
  "type": "圆领短袖T恤",
  "category": "top",
  "color": "白色，胸前有蓝色几何印花",
  "fabric": "纯棉",
  "style": ["休闲", "街头", "日系"],
  "gender": "female",
  "season": "春夏",
  "occasion": "日常、逛街",
  "description": "白色纯棉短袖T恤，胸前饰有蓝色几何印花，版型宽松，日系休闲风格"
}
```

只输出 JSON，不要其他文字。"""


def _ensure_image_url(path_or_url: str) -> str:
    """
    确保图片是公网可访问的 URL。
    - HTTP(s) URL → 直接返回
    - 本地路径 → 上传 OSS 获取公网 URL
    """
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url

    from oss_uploader import ensure_url
    url = ensure_url(path_or_url)
    if url.startswith("data:"):
        # base64 data URI 也可以被 Qwen VL 接受
        return url
    return url


def analyze(image_path_or_url: str, prompt: str = None) -> dict:
    """
    分析服装图片，返回结构化描述。

    参数：
        image_path_or_url: 本地图片路径或 URL
        prompt: 自定义分析提示词（可选，默认用内置提示词）

    返回：
        dict — 包含 type/category/color/fabric/style/gender/season/occasion/description
        分析失败时返回 {"error": "...", "raw": "原始回复"}
    """
    if not DASHSCOPE_API_KEY:
        return {"error": "未配置 DASHSCOPE_API_KEY 或 ALIYUN_API_KEY，无法进行视觉分析"}

    image_url = _ensure_image_url(image_path_or_url)
    analysis_prompt = prompt or GARMENT_ANALYSIS_PROMPT

    try:
        from openai import OpenAI
    except ImportError:
        return {"error": "openai 库未安装，请运行: pip install openai"}

    client = OpenAI(api_key=DASHSCOPE_API_KEY, base_url=DASHSCOPE_BASE_URL)

    print(f"   正在分析服装图片（模型: {DASHSCOPE_MODEL}）...")

    try:
        completion = client.chat.completions.create(
            model=DASHSCOPE_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url},
                        },
                        {"type": "text", "text": analysis_prompt},
                    ],
                },
            ],
        )
    except Exception as e:
        return {"error": f"API 调用失败: {e}"}

    raw = completion.choices[0].message.content.strip()

    # 尝试解析 JSON（模型可能包裹 ```json ... ```）
    json_str = raw
    if "```json" in json_str:
        json_str = json_str.split("```json", 1)[1]
    if "```" in json_str:
        json_str = json_str.split("```", 1)[0]
    json_str = json_str.strip()

    try:
        result = json.loads(json_str)
        print("   服装分析完成")
        return result
    except json.JSONDecodeError:
        print("   JSON 解析失败，返回原始文本")
        return {"error": "JSON 解析失败", "raw": raw}


def format_analysis(result: dict) -> str:
    """将分析结果格式化为用户友好的文本展示"""
    if "error" in result:
        return f" 分析失败: {result['error']}\n{result.get('raw', '')}"

    lines = [
        "📋 **服装分析结果**\n",
        f"👗 类型：{result.get('type', '未知')}",
        f"📂 分类：{result.get('category', '未知')}（{'上装' if result.get('category') == 'top' else '下装' if result.get('category') == 'bottom' else '全身'}）",
        f"🎨 颜色：{result.get('color', '未知')}",
        f"🧵 面料：{result.get('fabric', '未知')}",
        f"🏷️ 风格：{', '.join(result.get('style', []))}",
        f"👤 适合：{result.get('gender', '未知')}",
        f"🌤️ 季节：{result.get('season', '未知')}",
        f"📍 场景：{result.get('occasion', '未知')}",
        f"\n 一句话描述：{result.get('description', '')}",
    ]
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="服装图视觉理解")
    parser.add_argument("image", help="服装图片路径或 URL")
    parser.add_argument("--json", action="store_true", dest="output_json",
                        help="输出 JSON 格式")
    parser.add_argument("--prompt", default=None,
                        help="自定义分析提示词")
    args = parser.parse_args()

    result = analyze(args.image, prompt=args.prompt)

    if args.output_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_analysis(result))
