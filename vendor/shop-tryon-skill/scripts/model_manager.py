"""
model_manager.py — 内置模特管理

功能：
- 列出所有内置模特
- 根据用户需求智能推荐模特
- 解析模特图片路径（本地优先，本地不存在则用远程 URL）
- 校验用户自定义模特图片是否符合要求
"""

import os, json
from pathlib import Path

# 模特数据文件和图片目录
_HERE        = Path(__file__).resolve().parent.parent  # ai-tryon/
_MODELS_JSON = _HERE / "assets" / "models.json"
_MODELS_DIR  = _HERE / "assets" / "models"


def load_models() -> list:
    """加载内置模特列表"""
    with open(_MODELS_JSON, encoding="utf-8") as f:
        return json.load(f)


def get_model_image(model: dict) -> str:
    """
    返回模特图片路径：本地文件优先，不存在则返回 None
    """
    local = _HERE / model["local"]
    if local.exists():
        return str(local)
    return model.get("url")  # models.json 可能没有 url 字段


def list_models(gender: str = None, age_group: str = None) -> list:
    """
    列出模特，支持过滤

    gender:    "female" / "male" / None（全部）
    age_group: "adult" / "child" / None（全部）
    """
    models = load_models()
    if gender:
        models = [m for m in models if m["gender"] == gender]
    if age_group:
        models = [m for m in models if m["ageGroup"] == age_group]
    return models


def recommend_model(
    garment_desc: str = "",
    gender: str = None,
    style: str = None,
    age_group: str = "adult",
) -> dict:
    """
    根据服装描述和用户偏好推荐最合适的模特

    优先级：
    1. 匹配 age_group
    2. 匹配 gender
    3. 匹配 style 风格
    4. 默认返回 default=true 的模特
    """
    models = load_models()

    # 从服装描述推断性别（没有明确指定时）
    if gender is None:
        desc_lower = garment_desc.lower()
        male_keywords   = ["男", "boy", "male", "先生", "他", "男装", "男款"]
        child_keywords  = ["童装", "儿童", "小孩", "宝宝", "child", "kid"]
        if any(k in desc_lower for k in child_keywords):
            age_group = "child"
        if any(k in desc_lower for k in male_keywords):
            gender = "male"
        else:
            gender = "female"

    # 过滤
    candidates = [m for m in models if m["ageGroup"] == age_group]
    if gender:
        gender_matched = [m for m in candidates if m["gender"] == gender]
        if gender_matched:
            candidates = gender_matched

    if not candidates:
        candidates = models  # 兜底

    # 风格匹配（先匹配 style 字段，再匹配 desc）
    if style:
        style_matched = [m for m in candidates if style in m["style"]]
        if style_matched:
            return style_matched[0]

    # desc 关键词匹配（根据服装描述智能推荐）
    if garment_desc:
        desc_lower = garment_desc.lower()
        desc_matched = [m for m in candidates
                        if any(kw in desc_lower for kw in m.get("desc", "").split("、")
                               if len(kw) >= 2)]
        if desc_matched:
            return desc_matched[0]

    # 返回 default 或第一个
    default = next((m for m in candidates if m.get("default")), None)
    return default or candidates[0]


def format_model_list(models: list) -> str:
    """格式化模特列表，用于对话展示"""
    lines = []
    female_adult  = [m for m in models if m["gender"] == "female" and m["ageGroup"] == "adult"]
    male_adult    = [m for m in models if m["gender"] == "male"   and m["ageGroup"] == "adult"]
    children      = [m for m in models if m["ageGroup"] == "child"]

    def _fmt(m: dict, icon: str) -> str:
        desc = m.get("desc", "")
        return (f"   {icon} {m['name']}  {m['style']}  {m['height']}  "
                f"{m['bodyType']}\n      {desc}")

    if female_adult:
        lines.append("👩 女装模特：")
        for m in female_adult:
            lines.append(_fmt(m, "👩"))

    if male_adult:
        lines.append("👨 男装模特：")
        for m in male_adult:
            lines.append(_fmt(m, "👨"))

    if children:
        lines.append("👧👦 童装模特：")
        for m in children:
            icon = "👧" if m["gender"] == "female" else "👦"
            lines.append(_fmt(m, icon))

    return "\n".join(lines)


# ── 用户自定义模特图校验 ──────────────────────────────────

USER_MODEL_RULES = """
用户上传模特图的要求：

【文件要求】
- 大小：5KB ~ 5MB
- 分辨率：图片边长在 150px ~ 4096px 之间
- 格式：JPG、JPEG、PNG、BMP、HEIC

【照片要求】
- 人群：支持不同性别、肤色、年龄（6岁以上）
- 姿势：全身正面照，光照良好
- 手部：展示完整，避免手臂交叉遮挡
- 背景：建议纯色背景，效果更佳
"""


def validate_user_model_image(file_path: str) -> tuple:
    """
    校验用户提供的模特图是否符合要求
    返回 (is_valid: bool, message: str)
    """
    path = Path(file_path)

    # 格式检查
    allowed_exts = {".jpg", ".jpeg", ".png", ".bmp", ".heic"}
    if path.suffix.lower() not in allowed_exts:
        return False, f"不支持的图片格式 {path.suffix}，请使用 JPG/PNG/BMP/HEIC"

    # 大小检查
    size_bytes = path.stat().st_size
    if size_bytes < 5 * 1024:
        return False, f"图片太小（{size_bytes//1024}KB），最小需要 5KB"
    if size_bytes > 5 * 1024 * 1024:
        return False, f"图片太大（{size_bytes//1024//1024}MB），最大 5MB"

    # 分辨率检查
    try:
        from PIL import Image
        with Image.open(file_path) as img:
            w, h = img.size
            min_side = min(w, h)
            max_side = max(w, h)
            if min_side < 150:
                return False, f"分辨率太低（{w}×{h}），最短边需要 ≥ 150px"
            if max_side > 4096:
                return False, f"分辨率太高（{w}×{h}），最长边需要 ≤ 4096px"
    except ImportError:
        pass  # 没装 Pillow 则跳过分辨率检查
    except Exception as e:
        return False, f"无法读取图片：{e}"

    return True, " 图片符合要求"


def show_model_requirements() -> str:
    """返回用户模特图要求说明"""
    return USER_MODEL_RULES.strip()


# ── CLI 工具 ──────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        models = load_models()
        print(format_model_list(models))
    elif len(sys.argv) > 1 and sys.argv[1] == "recommend":
        desc = sys.argv[2] if len(sys.argv) > 2 else ""
        m = recommend_model(garment_desc=desc)
        print(f"推荐模特：{m['name']} ({m['style']}, {m['height']}, {m['bodyType']})")
        print(f"图片路径：{get_model_image(m)}")
    elif len(sys.argv) > 1 and sys.argv[1] == "validate":
        ok, msg = validate_user_model_image(sys.argv[2])
        print(msg)
    else:
        print("用法：")
        print("  python model_manager.py list               # 列出所有模特")
        print("  python model_manager.py recommend '女装'   # 推荐模特")
        print("  python model_manager.py validate img.jpg   # 校验用户图片")


# ── 服装图校验 ──────────────────────────────────────────

GARMENT_IMAGE_RULES = """
服装图要求：

【文件要求】
- 大小：5KB ~ 5MB
- 分辨率：图片边长在 150px ~ 4096px 之间
- 格式：JPG、JPEG、PNG、BMP
  ⚠️ 不支持 AVIF、WebP、HEIC、TIFF、GIF、SVG 等格式
  ⚠️ 京东 / 淘宝商品图常以 .avif 结尾，请下载后转换为 JPG 再使用

【照片要求】
- 服装照片尽量清晰平整，无遮挡
- 必须是单件服装（不能多件叠放）
- 必须是正面展示（平铺图或挂拍图）
- 不能是折叠、遮挡、侧面等情况
"""

# 试穿模式定义
TRYON_MODES = {
    "single":   "单件装模式（只试穿一件上衣或下装）",
    "outfit":   "上下装模式（上装 + 下装同时试穿，需分别提供两张图）",
}


def validate_garment_image(file_path: str) -> tuple:
    """
    校验服装图是否符合要求
    返回 (is_valid: bool, message: str)
    """
    path = Path(file_path)

    # 格式检查
    allowed_exts = {".jpg", ".jpeg", ".png", ".bmp"}
    if path.suffix.lower() not in allowed_exts:
        return False, f"不支持的图片格式 {path.suffix}，请使用 JPG/PNG/BMP"

    # 大小检查
    size_bytes = path.stat().st_size
    if size_bytes < 5 * 1024:
        return False, f"图片太小（{size_bytes // 1024}KB），最小需要 5KB"
    if size_bytes > 5 * 1024 * 1024:
        return False, f"图片太大（{size_bytes // 1024 // 1024}MB），最大 5MB"

    # 分辨率检查
    try:
        from PIL import Image
        with Image.open(file_path) as img:
            w, h = img.size
            if min(w, h) < 150:
                return False, f"分辨率太低（{w}×{h}），最短边需要 ≥ 150px"
            if max(w, h) > 4096:
                return False, f"分辨率太高（{w}×{h}），最长边需要 ≤ 4096px"
    except ImportError:
        pass
    except Exception as e:
        return False, f"无法读取图片：{e}"

    return True, " 服装图符合要求"


def show_garment_requirements() -> str:
    return GARMENT_IMAGE_RULES.strip()


def check_outfit_mode(
    top_path: str = None,
    bottom_path: str = None,
    mode: str = "single",
) -> tuple:
    """
    检查上下装模式的输入完整性
    返回 (is_valid: bool, message: str)

    mode:
      "single" → 单件装，top 或 bottom 提供一个即可
      "outfit" → 上下装，top 和 bottom 必须都提供
    """
    if mode == "outfit":
        if not top_path and not bottom_path:
            return False, (
                "上下装模式需要同时提供上装图和下装图。\n"
                "· 如果只有上装图，请改用【单件装模式】\n"
                "· 如果只有下装图，请改用【单件装模式】"
            )
        if not top_path:
            return False, (
                "上下装模式缺少上装图。\n"
                "请提供上装图，或改用【单件装模式】只试穿下装。"
            )
        if not bottom_path:
            return False, (
                "上下装模式缺少下装图。\n"
                "请提供下装图，或改用【单件装模式】只试穿上装。"
            )
    elif mode == "single":
        if not top_path and not bottom_path:
            return False, "单件装模式需要提供服装图（上装或下装均可）。"

    return True, " 服装输入完整"
