from backend.agent.routing import (
    normalize_intent,
    resolve_mode,
    route_after_executor,
    route_after_garment_analysis,
    route_after_intent,
    route_after_model_selection,
    route_after_prompt_engineer,
    select_image_backend,
)


def test_missing_garment_part_routes_to_user() -> None:
    assert route_after_intent({"parsed_intent": {"mode": "B"}}) == "ask_user"


def test_garment_image_with_aliyun_selects_mode_a() -> None:
    intent = {"garment_part": "top", "has_garment_image": True, "variants": 1}
    assert resolve_mode(intent, aliyun_configured=True) == "A"
    normalized = normalize_intent(intent, aliyun_configured=True)
    assert route_after_intent({"parsed_intent": normalized}) == "garment_analyzer"


def test_garment_image_without_aliyun_falls_back_to_mode_b() -> None:
    intent = {"garment_part": "top", "has_garment_image": True, "variants": 1}
    assert resolve_mode(intent, aliyun_configured=False) == "B"


def test_text_or_multiple_variants_select_mode_b() -> None:
    text_only = {"garment_part": "top", "has_garment_image": False}
    variants = {"garment_part": "top", "has_garment_image": True, "variants": 3}
    scenes = {"garment_part": "top", "scenes": ["studio"]}
    assert resolve_mode(text_only, aliyun_configured=True) == "B"
    assert resolve_mode(variants, aliyun_configured=True) == "B"
    assert resolve_mode(scenes, aliyun_configured=True) == "B"


def test_partial_and_video_take_precedence() -> None:
    assert resolve_mode(
        {"mode": "partial", "garment_part": "top", "variants": 3},
        aliyun_configured=True,
    ) == "partial"
    assert resolve_mode(
        {"mode": "video", "garment_part": "full", "has_garment_image": True},
        aliyun_configured=True,
    ) == "video"


def test_angle_preset_forces_mode_b() -> None:
    assert resolve_mode(
        {"garment_part": "top", "angle_preset": "ecommerce"},
        aliyun_configured=True,
    ) == "B"


def test_reference_images_force_doubao_and_jimeng_is_text_only() -> None:
    assert select_image_backend(
        {"has_garment_image": True}, {}, jimeng_configured=True
    ) == "douban"
    assert select_image_backend(
        {}, {"model": "model.jpg"}, jimeng_configured=True
    ) == "douban"
    assert select_image_backend({}, {}, jimeng_configured=True) == "jimeng"
    assert select_image_backend({}, {}, jimeng_configured=False) == "douban"


def test_error_routes_to_error_handler() -> None:
    assert route_after_intent({"error": "boom"}) == "error_handler"


def test_remaining_graph_routing_branches() -> None:
    assert route_after_intent(
        {"parsed_intent": {"mode": "video", "garment_part": "full"}}
    ) == "prompt_engineer"
    assert route_after_intent(
        {"parsed_intent": {"mode": "B", "garment_part": "top"}}
    ) == "prompt_engineer"
    assert route_after_intent(
        {"parsed_intent": {"mode": "A", "garment_part": "top"}}
    ) == "model_selector"
    assert route_after_garment_analysis(
        {"parsed_intent": {"mode": "A", "garment_part": "top"}}
    ) == "model_selector"
    assert route_after_garment_analysis(
        {"parsed_intent": {"mode": "B", "garment_part": "top"}}
    ) == "prompt_engineer"
    assert route_after_garment_analysis({"error": "bad"}) == "error_handler"
    assert route_after_model_selection({}) == "prompt_engineer"
    assert route_after_model_selection({"error": "bad"}) == "error_handler"
    assert route_after_prompt_engineer({}) == "plan_confirm"
    assert route_after_prompt_engineer({"error": "bad"}) == "error_handler"
    assert route_after_executor({}) == "end"
    assert route_after_executor({"error": "bad"}) == "error_handler"
