import base64
import io
import json
import zipfile

import pytest

from novelai_krita import api


@pytest.mark.parametrize("size", [(1000, 1000), (1920, 1080), (3000, 4000), (300, 200), (100, 5000)])
def test_fit_resolution_respects_limits(size):
    w, h = api.fit_resolution(*size)
    assert w % 64 == 0 and h % 64 == 0
    assert w * h <= api.FREE_MAX_PIXELS
    assert 64 <= w <= api.MAX_SIDE and 64 <= h <= api.MAX_SIDE


def test_fit_resolution_keeps_aspect_and_fills_budget():
    assert api.fit_resolution(1024, 1024) == (1024, 1024)
    w, h = api.fit_resolution(2000, 3000)  # Portrait 2:3 scales down to ~832x1216.
    assert (w, h) == (832, 1216)
    w, h = api.fit_resolution(200, 300)  # Small regions scale up.
    assert (w, h) == (832, 1216)


def test_fit_resolution_rejects_empty():
    with pytest.raises(ValueError):
        api.fit_resolution(0, 10)


def test_model_list():
    names = [name for name, _ in api.MODELS]
    assert names[:2] == ["nai-diffusion-5-full", "nai-diffusion-5-curated"]
    assert api.DEFAULT_MODEL in names
    assert len(set(names)) == len(names)
    assert api.inpainting_model("nai-diffusion-5-curated") == "nai-diffusion-4-5-curated-inpainting"
    assert api.inpainting_model("nai-diffusion-5-full") == "nai-diffusion-5-full-inpainting"


def test_infill_payload_strength_like_the_site():
    p = api.GenerationParams(prompt="cat", image="A", mask="B", strength=0.7, seed=5)
    params = api.build_payload(p)["parameters"]
    assert params["img2img"] == {"strength": 0.7, "color_correct": True}
    assert params["extra_noise_seed"] == 4 and params["noise"] == 0.0
    full = api.build_payload(api.GenerationParams(prompt="cat", image="A", mask="B", strength=1.0))
    assert "img2img" not in full["parameters"]  # Full strength: no nested object.
    v3 = api.build_payload(api.GenerationParams(prompt="c", image="A", mask="B",
                                                model="nai-diffusion-3", strength=1.0))
    assert "img2img" not in v3["parameters"]


def test_params_version():
    assert api.build_payload(api.GenerationParams(prompt="c", model="nai-diffusion-5-full"))["parameters"]["params_version"] == 4
    assert api.build_payload(api.GenerationParams(prompt="c", model="nai-diffusion-5-curated"))["parameters"]["params_version"] == 4
    assert api.build_payload(api.GenerationParams(prompt="c"))["parameters"]["params_version"] == 4
    assert api.build_payload(api.GenerationParams(prompt="c", model="nai-diffusion-3"))["parameters"]["params_version"] == 4


def test_model_helpers():
    for model in ("nai-diffusion-5-full", "nai-diffusion-4-5-curated", "nai-diffusion-4-full",
                  "nai-diffusion-4-curated-preview", "nai-diffusion-4-5-full-inpainting",
                  "some-future-model"):
        assert api.is_v4(model), model
    for model in ("nai-diffusion-3", "nai-diffusion-furry-3", "nai-diffusion-3-inpainting"):
        assert not api.is_v4(model), model
    assert api.inpainting_model("nai-diffusion-4-5-full") == "nai-diffusion-4-5-full-inpainting"
    assert api.inpainting_model("nai-diffusion-4-curated-preview") == "nai-diffusion-4-curated-inpainting"
    assert api.inpainting_model("nai-diffusion-3-inpainting") == "nai-diffusion-3-inpainting"


def test_text_to_image_payload_v45():
    p = api.GenerationParams(prompt="1girl, night sky", negative_prompt="red eyes", seed=42)
    payload = api.build_payload(p)
    assert payload["action"] == "generate"
    assert payload["model"] == "nai-diffusion-4-5-full"
    params = payload["parameters"]
    assert payload["input"] == "1girl, night sky, very aesthetic, masterpiece, no text"
    assert params["v4_prompt"]["caption"]["base_caption"] == payload["input"]
    negative = params["negative_prompt"]
    assert negative.startswith("nsfw, lowres, artistic error") and negative.endswith(", red eyes")
    assert params["v4_negative_prompt"]["caption"]["base_caption"] == negative
    assert params["ucPreset"] == 0 and params["qualityToggle"] is True
    assert params["seed"] == 42 and params["width"] == 832 and params["height"] == 1216
    assert "image" not in params and "mask" not in params
    assert "sm" not in params
    json.dumps(payload)  # Must be serializable.


def test_quality_tags_and_preset_can_be_turned_off():
    payload = api.build_payload(api.GenerationParams(
        prompt="cat", quality="off", uc_preset="None", negative_prompt="dog"))
    assert payload["input"] == "cat"
    params = payload["parameters"]
    assert params["negative_prompt"] == "dog"  # No preset: no "nsfw" either.
    assert params["qualityToggle"] is False and params["ucPreset"] == 4


def test_v3_payload_has_no_v4_fields():
    payload = api.build_payload(api.GenerationParams(prompt="cat", model="nai-diffusion-3"))
    params = payload["parameters"]
    assert "v4_prompt" not in params
    assert params["sm"] is False
    assert payload["input"].endswith("absurdres")


def test_v5_payload():
    payload = api.build_payload(api.GenerationParams(prompt="cat", model="nai-diffusion-5-full"))
    assert payload["input"] == "cat, very aesthetic, masterpiece, no text"
    assert "v4_prompt" in payload["parameters"] and "sm" not in payload["parameters"]
    light = api.build_payload(api.GenerationParams(prompt="cat", model="nai-diffusion-5-full",
                                                   quality="light"))
    assert light["input"] == "cat, very aesthetic, amazing quality, no text"


def test_img2img_payload():
    p = api.GenerationParams(prompt="cat", image="AAAA", strength=0.5, seed=7)
    payload = api.build_payload(p)
    params = payload["parameters"]
    assert payload["action"] == "img2img"
    assert params["image"] == "AAAA"
    assert params["strength"] == 0.5
    assert params["extra_noise_seed"] == 6


def test_v3_gets_the_brownian_settings_too():
    euler_a = api.build_payload(api.GenerationParams(
        prompt="c", model="nai-diffusion-3", noise_schedule="karras"))["parameters"]
    assert euler_a["prefer_brownian"] is True and euler_a["deliberate_euler_ancestral_bug"] is False
    native = api.build_payload(api.GenerationParams(
        prompt="c", model="nai-diffusion-3", noise_schedule="native"))["parameters"]
    assert "prefer_brownian" not in native
    other = api.build_payload(api.GenerationParams(
        prompt="c", model="nai-diffusion-3", sampler="k_euler", noise_schedule="karras"))["parameters"]
    assert "prefer_brownian" not in other


def test_v5_curated_inpainting_follows_its_v4_5_model():
    person = api.CharacterPrompt(prompt="girl", x=0.33, y=0.62)
    inpaint = api.build_payload(api.GenerationParams(
        prompt="c", model="nai-diffusion-5-curated", image="A", mask="B", strength=1.0,
        noise_schedule="exponential", characters=[person], use_coords=True))["parameters"]
    assert inpaint["noise_schedule"] == "exponential"
    assert inpaint["characterPrompts"][0]["center"] == {"x": 0.3, "y": 0.7}  # The 5 x 5 grid.
    plain = api.build_payload(api.GenerationParams(
        prompt="c", model="nai-diffusion-5-curated",
        noise_schedule="exponential", characters=[person], use_coords=True))["parameters"]
    assert plain["noise_schedule"] == "karras"
    assert plain["characterPrompts"][0]["center"] == {"x": 0.33, "y": 0.62}  # Free on V5.


def test_v5_full_inpainting_keeps_free_positions():
    person = api.CharacterPrompt(prompt="girl", x=0.33, y=0.62)
    params = api.build_payload(api.GenerationParams(
        prompt="c", model="nai-diffusion-5-full", image="A", mask="B",
        characters=[person], use_coords=True))["parameters"]
    assert params["characterPrompts"][0]["center"] == {"x": 0.33, "y": 0.62}


def test_v5_always_uses_karras():
    params = api.build_payload(api.GenerationParams(
        prompt="c", model="nai-diffusion-5-full", noise_schedule="exponential"))["parameters"]
    assert params["noise_schedule"] == "karras"
    older = api.build_payload(api.GenerationParams(
        prompt="c", model="nai-diffusion-4-5-full", noise_schedule="exponential"))["parameters"]
    assert older["noise_schedule"] == "exponential"


def test_straight_alpha_follows_the_generating_model():
    def params(**extra):
        return api.build_payload(api.GenerationParams(prompt="c", **extra))["parameters"]

    assert params(model="nai-diffusion-5-full")["straight_alpha"] is True
    assert params(model="nai-diffusion-5-full", image="A", mask="B")["straight_alpha"] is True
    assert "straight_alpha" not in params(model="nai-diffusion-4-5-full")
    # V5 Curated inpaints with V4.5 Curated's model, which has no transparency.
    assert "straight_alpha" not in params(model="nai-diffusion-5-curated", image="A", mask="B")


def test_preset_hints_like_the_site():
    params = api.build_payload(api.GenerationParams(
        prompt="c", model="nai-diffusion-5-full", uc_preset="Heavy"))["parameters"]
    assert (params["tag_hint_qt"], params["tag_hint_uc_preset"]) == (1, 2)
    params = api.build_payload(api.GenerationParams(
        prompt="c", model="nai-diffusion-5-full", quality="light", uc_preset="Human Focus"))["parameters"]
    assert (params["tag_hint_qt"], params["tag_hint_uc_preset"]) == (3, 4)
    params = api.build_payload(api.GenerationParams(
        prompt="c", model="nai-diffusion-5-full", quality="off", uc_preset="None"))["parameters"]
    assert (params["tag_hint_qt"], params["tag_hint_uc_preset"]) == (0, 0)


def test_brownian_settings_only_with_euler_ancestral():
    euler_a = api.build_payload(api.GenerationParams(prompt="c"))["parameters"]
    assert euler_a["prefer_brownian"] is True and euler_a["deliberate_euler_ancestral_bug"] is False
    other = api.build_payload(api.GenerationParams(prompt="c", sampler="k_dpmpp_2m"))["parameters"]
    assert "prefer_brownian" not in other and "deliberate_euler_ancestral_bug" not in other


def test_v5_full_inpaints_with_its_own_model():
    payload = api.build_payload(api.GenerationParams(
        prompt="cat", model="nai-diffusion-5-full", image="A", mask="B", strength=1.0, seed=9))
    assert payload["model"] == "nai-diffusion-5-full-inpainting"
    assert payload["input"].endswith(", very aesthetic, masterpiece, no text")
    params = payload["parameters"]
    assert params["params_version"] == 4 and params["inpaintImg2ImgStrength"] == 1.0
    assert params["strength"] == 0.7 and params["extra_noise_seed"] == 8


def test_v5_curated_inpainting_uses_the_tags_of_the_model_doing_it():
    payload = api.build_payload(api.GenerationParams(
        prompt="cat", model="nai-diffusion-5-curated", image="A", mask="B"))
    assert payload["model"] == "nai-diffusion-4-5-curated-inpainting"
    assert "rating:general" in payload["input"]  # V4.5 Curated's quality tags.


def test_seed_zero_keeps_a_valid_extra_seed():
    params = api.build_payload(api.GenerationParams(prompt="c", image="A", seed=0))["parameters"]
    assert params["extra_noise_seed"] == 0


def test_only_inpainting_asks_for_the_raw_result():
    infill = api.build_payload(api.GenerationParams(prompt="cat", image="A", mask="B"))
    img2img = api.build_payload(api.GenerationParams(prompt="cat", image="A"))
    assert infill["parameters"]["add_original_image"] is False
    assert img2img["parameters"]["add_original_image"] is True


def test_infill_payload_uses_inpainting_model():
    p = api.GenerationParams(prompt="cat", image="AAAA", mask="BBBB", strength=1.0)
    payload = api.build_payload(p)
    assert payload["action"] == "infill"
    assert payload["model"] == "nai-diffusion-4-5-full-inpainting"
    assert payload["parameters"]["mask"] == "BBBB"


def test_native_schedule_replaced_for_v4():
    payload = api.build_payload(api.GenerationParams(prompt="cat", noise_schedule="native"))
    assert payload["parameters"]["noise_schedule"] == "karras"


def test_parse_json_response():
    body = json.dumps({"images": [
        {"image": base64.b64encode(b"second").decode(), "index": 1, "seed": 11},
        {"image": base64.b64encode(b"first").decode(), "index": 0, "seed": 10},
    ]}).encode()
    assert api.parse_images(body) == [(b"first", 10), (b"second", 11)]


def test_parse_zip_response():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("image_1.png", b"two")
        archive.writestr("image_0.png", b"one")
    assert api.parse_images(buffer.getvalue()) == [(b"one", None), (b"two", None)]


def test_error_message():
    body = json.dumps({"statusCode": 402, "message": "Insufficient Anlas"}).encode()
    assert api.error_message(402, body).startswith("Not enough Anlas")
    assert "Insufficient" in api.error_message(402, body)
    assert "HTTP 418" in api.error_message(418, b"")
    assert api.error_message(500, b"<html>oops</html>").endswith("<html>oops</html>")
    assert api.error_message(400, b'{"message": 42}').endswith(": 42")  # Not text.
    assert api.error_message(400, b'{"message": null}') == "NovelAI rejected the request"


# Same shape as a real /user/subscription response (only these fields shown).
SUBSCRIPTION = {
    "tier": 3, "active": True,
    "trainingStepsLeft": {"fixedTrainingStepsLeft": 8000, "purchasedTrainingSteps": 4500},
    "usage": {"percent": 85, "purchasedPercent": 0, "isNegative": False, "timeUntilNextPercent": 6048},
}


def test_parse_account():
    account = api.parse_account(json.dumps(SUBSCRIPTION).encode())
    assert account.is_opus and account.plan_name == "Opus"
    assert account.anlas == 12500
    assert (account.v5_percent, account.v5_refill_seconds, account.v5_unavailable) == (85, 6048, False)
    assert api.account_summary(account) == "Opus · 12,500 Anlas · V5 85%"
    details = api.account_details(account)
    assert "8,000 from the plan, 4,500 purchased" in details and "Next +1% in 1h 40m" in details


def test_parse_account_edge_cases():
    empty = api.parse_account(b"{}")
    assert empty.anlas is None and not empty.is_opus and empty.plan_name == "No subscription"
    assert api.account_summary(empty) == "No subscription"
    scroll = api.parse_account(json.dumps({"tier": 2, "active": True,
                                           "trainingStepsLeft": {"fixedTrainingStepsLeft": 1000}}).encode())
    assert api.account_summary(scroll) == "Scroll · 1,000 Anlas"  # No V5 allowance shown.
    used = dict(SUBSCRIPTION, usage={"percent": 0, "isNegative": True, "purchasedPercent": 50})
    assert api.account_summary(api.parse_account(json.dumps(used).encode())).endswith("V5 used up")
    bought = dict(SUBSCRIPTION, usage={"percent": 40, "purchasedPercent": 100})
    assert api.account_summary(api.parse_account(json.dumps(bought).encode())).endswith("V5 40% +100%")


def test_format_duration():
    assert api.format_duration(6048) == "1h 40m"
    assert api.format_duration(125) == "2m"
    assert api.format_duration(9) == "9s"


def test_anlas_warnings_use_the_account():
    opus = api.parse_account(json.dumps(SUBSCRIPTION).encode())
    assert api.anlas_warnings(1024, 1024, 28, 1, "nai-diffusion-5-full", opus) == []
    used = api.parse_account(json.dumps(dict(SUBSCRIPTION, usage={"percent": 0})).encode())
    assert api.anlas_warnings(1024, 1024, 28, 1, "nai-diffusion-5-full", used) == [
        "your V5 Opus allowance is used up"]
    assert api.anlas_warnings(1024, 1024, 28, 1, "nai-diffusion-4-5-full", used) == []  # Not V5.
    scroll = api.parse_account(json.dumps({"tier": 2, "active": True}).encode())
    assert api.anlas_warnings(512, 512, 10, 1, "nai-diffusion-4-5-full", scroll) == [
        "free generations need an Opus plan (yours: Scroll)"]


def test_anlas_warnings():
    assert api.anlas_warnings(1024, 1024, 28, 1) == []
    assert api.anlas_warnings(832, 1216, 28, 1) == []
    assert api.anlas_warnings(1088, 1024, 28, 1) == ["1088 x 1024 is larger than 1024 x 1024"]
    assert api.anlas_warnings(1024, 1024, 29, 1) == ["29 steps (free up to 28)"]
    assert api.anlas_warnings(1024, 1024, 28, 3) == ["3 images per request (free: 1)"]
    assert len(api.anlas_warnings(1536, 1536, 40, 2)) == 3


def test_fit_resolution_without_upscale_keeps_native_size():
    assert api.fit_resolution(832, 1216, upscale=False) == (832, 1216)
    assert api.fit_resolution(512, 512, upscale=False) == (512, 512)  # Not blown up.
    assert api.fit_resolution(1000, 1000, upscale=False) == (1024, 1024)  # Nearest multiple.
    assert api.fit_resolution(2000, 3000, upscale=False) == (832, 1216)  # Too big: scaled down.
    assert api.fit_resolution(512, 512) == (1024, 1024)  # Text to image still fills the budget.


# ---- Character prompts -------------------------------------------------------

def characters_payload(model="nai-diffusion-4-5-full", use_coords=True, **extra):
    characters = [
        api.CharacterPrompt("girl, red hair", "blue hair", 0.12, 0.85),
        api.CharacterPrompt("boy, glasses", "", 0.7, 0.3),
        api.CharacterPrompt("girl, hat", "", enabled=False),  # Left out.
        api.CharacterPrompt("   ", "lowres"),  # Empty: left out.
    ]
    return api.build_payload(api.GenerationParams(
        prompt="2people, park", model=model, characters=characters,
        use_coords=use_coords, uc_preset="None", **extra))["parameters"]


def test_prompts_are_sent_as_typed():
    # The site trims nothing, so a space or a new line at the end is sent too.
    payload = api.build_payload(api.GenerationParams(
        prompt=" 1girl, \n", negative_prompt=" dog, ", quality="off", uc_preset="None"))
    assert payload["input"] == " 1girl, \n"
    assert payload["parameters"]["negative_prompt"] == " dog, "
    payload = api.build_payload(api.GenerationParams(prompt="1girl, ", negative_prompt="dog, "))
    assert payload["input"] == "1girl, , very aesthetic, masterpiece, no text"
    assert payload["parameters"]["negative_prompt"].endswith("blank page, dog, ")


def test_character_text_is_sent_as_typed():
    # The site doesn't trim it, so neither does the plugin.
    params = api.build_payload(api.GenerationParams(
        prompt="2people", uc_preset="None",
        characters=[api.CharacterPrompt("girl, red hair, ", " blue hair")]))["parameters"]
    assert params["v4_prompt"]["caption"]["char_captions"][0]["char_caption"] == "girl, red hair, "
    assert params["v4_negative_prompt"]["caption"]["char_captions"][0]["char_caption"] == (
        " blue hair")
    assert params["characterPrompts"][0]["prompt"] == "girl, red hair, "
    assert params["characterPrompts"][0]["uc"] == " blue hair"


def test_character_payload_v45_snaps_to_the_grid():
    params = characters_payload()
    prompt, negative = params["v4_prompt"], params["v4_negative_prompt"]
    assert prompt["caption"]["char_captions"] == [
        {"char_caption": "girl, red hair", "centers": [{"x": 0.1, "y": 0.9}]},
        {"char_caption": "boy, glasses", "centers": [{"x": 0.7, "y": 0.3}]}]
    assert negative["caption"]["char_captions"] == [
        {"char_caption": "blue hair", "centers": [{"x": 0.1, "y": 0.9}]},
        {"char_caption": "", "centers": [{"x": 0.7, "y": 0.3}]}]
    assert params["characterPrompts"] == [
        {"prompt": "girl, red hair", "uc": "blue hair", "center": {"x": 0.1, "y": 0.9},
         "enabled": True},
        {"prompt": "boy, glasses", "uc": "", "center": {"x": 0.7, "y": 0.3}, "enabled": True}]
    assert params["use_coords"] is True and prompt["use_coords"] is True
    # Quality tags stay in the base prompt.
    assert prompt["caption"]["base_caption"].startswith("2people, park, very aesthetic")


def test_character_payload_v5_keeps_exact_positions():
    params = characters_payload("nai-diffusion-5-full")
    centers = [c["centers"][0] for c in params["v4_prompt"]["caption"]["char_captions"]]
    assert centers == [{"x": 0.12, "y": 0.85}, {"x": 0.7, "y": 0.3}]


def test_character_payload_ai_choice():
    params = characters_payload(use_coords=False)
    assert params["use_coords"] is False and params["v4_prompt"]["use_coords"] is False
    assert len(params["v4_prompt"]["caption"]["char_captions"]) == 2


def test_no_characters_never_uses_coords():
    params = api.build_payload(api.GenerationParams(prompt="cat", use_coords=True))["parameters"]
    assert params["use_coords"] is False and params["characterPrompts"] == []
    assert params["v4_prompt"]["caption"]["char_captions"] == []


def test_character_payload_inpainting():
    params = characters_payload(image="A", mask="B", strength=1.0)
    assert len(params["v4_prompt"]["caption"]["char_captions"]) == 2


def test_v3_payload_has_no_characters():
    params = characters_payload("nai-diffusion-3")
    assert "v4_prompt" not in params and "characterPrompts" not in params


def test_max_characters():
    assert api.max_characters("nai-diffusion-5-full") == 22
    assert api.max_characters("nai-diffusion-5-curated") == 22
    assert api.max_characters("nai-diffusion-4-5-full") == 6
    assert api.max_characters("nai-diffusion-4-curated-preview") == 6
    assert api.max_characters("nai-diffusion-3") == 0
    assert api.max_characters("nai-diffusion-furry-3") == 0


def test_grid_positions():
    assert api.position_name(0.5, 0.5) == "C3"
    assert api.position_name(0.1, 0.1) == "A1"
    assert api.position_name(0.9, 0.7) == "E4"
    assert api.position_name(0.394, 0.462) == "B3"  # Nearest cell.
    assert api.snap_to_grid(0.0) == 0.1 and api.snap_to_grid(1.0) == 0.9
    assert api.character_position("nai-diffusion-4-full", 0.394, 0.462) == (0.3, 0.5)
    assert api.character_position("nai-diffusion-5-full", 0.3944, 0.462) == (0.394, 0.462)


# ---- Precise reference ----

@pytest.mark.parametrize("model, expected", [
    ("nai-diffusion-4-5-full", True), ("nai-diffusion-4-5-curated", True),
    ("nai-diffusion-4-5-full-inpainting", True),
    ("nai-diffusion-5-full", False), ("nai-diffusion-5-curated", False),
    ("nai-diffusion-4-full", False), ("nai-diffusion-3", False), ("something-new", False),
])
def test_only_v45_takes_references(model, expected):
    assert api.supports_references(model) is expected


@pytest.mark.parametrize("size, sent, fitted, corner", [
    ((1024, 1536), (1024, 1536), (1024, 1536), (0, 0)),  # Already right.
    ((832, 1216), (1024, 1536), (1024, 1497), (0, 20)),  # Scaled up, bars above and below.
    ((3000, 2000), (1536, 1024), (1536, 1024), (0, 0)),  # Scaled down.
    ((1920, 1080), (1536, 1024), (1536, 864), (0, 80)),
    ((500, 500), (1472, 1472), (1472, 1472), (0, 0)),
    ((100, 1000), (1024, 1536), (154, 1536), (435, 0)),  # Very tall: bars at the sides.
    ((900, 1000), (1472, 1472), (1325, 1472), (74, 0)),  # Nearer square than portrait.
])
def test_reference_layout_follows_the_site(size, sent, fitted, corner):
    assert api.reference_layout(*size) == (sent, fitted, corner)


def test_reference_layout_rejects_empty():
    with pytest.raises(ValueError):
        api.reference_layout(0, 10)


def reference_params(**changes):
    values = dict(prompt="1girl", model="nai-diffusion-4-5-full", references=[
        api.Reference("QUJD", "character", 0.8, 0.95),
        api.Reference("R0hJ"),
    ])
    values.update(changes)
    return api.build_payload(api.GenerationParams(**values))["parameters"]


def test_reference_payload_matches_the_site():
    parameters = reference_params()
    assert parameters["director_reference_images"] == ["QUJD", "R0hJ"]  # Not the disabled one.
    assert parameters["director_reference_descriptions"] == [
        {"caption": {"base_caption": "character", "char_captions": []}, "legacy_uc": False},
        {"caption": {"base_caption": "character&style", "char_captions": []}, "legacy_uc": False},
    ]
    assert parameters["director_reference_information_extracted"] == [1, 1]
    assert parameters["director_reference_strength_values"] == [0.8, 1.0]
    # Fidelity goes the other way round: 1 is sent as 0.
    assert parameters["director_reference_secondary_strength_values"] == [0.05, 0.0]


@pytest.mark.parametrize("model", ["nai-diffusion-5-full", "nai-diffusion-5-curated",
                                   "nai-diffusion-4-full", "nai-diffusion-3"])
def test_references_are_left_out_for_other_models(model):
    assert not any("director_reference" in key for key in reference_params(model=model))


def test_no_references_sends_nothing_about_them():
    parameters = reference_params(references=[])
    assert not any("director_reference" in key for key in parameters)
    parameters = reference_params(references=[api.Reference("")])  # No picture.
    assert not any("director_reference" in key for key in parameters)


def test_references_go_with_inpainting():
    parameters = reference_params(image="aW1hZ2U=", mask="bWFzaw==")
    assert parameters["director_reference_images"] == ["QUJD", "R0hJ"]


def test_v5_curated_inpainting_takes_no_references():
    # It inpaints with V4.5 Curated's model, but the site sends none for it.
    parameters = reference_params(model="nai-diffusion-5-curated", image="aW1hZ2U=",
                                  mask="bWFzaw==")
    assert not any("director_reference" in key for key in parameters)


def test_references_always_cost_anlas():
    assert api.anlas_warnings(832, 1216, 28, 1) == []
    assert api.anlas_warnings(832, 1216, 28, 1, references=1) == [
        "1 precise reference (5 Anlas per image)"]
    assert api.anlas_warnings(832, 1216, 28, 1, references=3) == [
        "3 precise references (15 Anlas per image)"]


# Director tools. Expected sizes and prices come from the NovelAI site's own
# arithmetic, run in Node: (picture size, size sent, price on Opus, price on
# another plan, price of Remove BG).
TOOL_CASES = [
    ((1024, 1024), (1024, 1024), 0, 20, 65),
    ((832, 1216), (832, 1216), 0, 20, 65),
    ((1216, 832), (1216, 832), 0, 20, 65),
    ((512, 512), (1024, 1024), 0, 20, 65),  # Small pictures are enlarged.
    ((300, 200), (1254, 836), 0, 20, 65),
    ((640, 360), (1365, 768), 0, 20, 65),
    ((1, 1), (1024, 1024), 0, 20, 65),
    ((100, 5000), (144, 7240), 0, 20, 65),
    ((1000, 1010), (1018, 1029), 0, 20, 65),  # Just under the limit for enlarging.
    ((1005, 1007), (1005, 1007), 0, 20, 65),  # Just over it: sent as it is.
    ((1024, 1025), (1024, 1025), 21, 21, 68),  # Over 1024 x 1024: Opus pays too.
    ((1536, 1536), (1536, 1536), 45, 45, 140),
    ((1773, 1773), (1773, 1773), 60, 60, 185),
    ((1774, 1774), (1773, 1773), 60, 60, 185),  # Large pictures are made smaller.
    ((2048, 2048), (1773, 1773), 60, 60, 185),
    ((4000, 3000), (2047, 1535), 60, 60, 185),
]


@pytest.mark.parametrize("size, sent, opus, other, remove_bg", TOOL_CASES)
def test_tool_size_and_price_follow_the_site(size, sent, opus, other, remove_bg):
    scroll = api.Account(tier=2, active=True)
    assert api.tool_size(*size) == sent
    for tool in ("lineart", "sketch", "colorize"):
        assert api.tool_price(tool, *size) == opus  # No account yet: Opus is assumed.
        assert api.tool_price(tool, *size, api.Account(tier=3, active=True)) == opus
        assert api.tool_price(tool, *size, scroll) == other
        assert api.tool_price(tool, *size, api.Account(tier=3, active=False)) == other
    assert api.tool_price("bg-removal", *size) == remove_bg  # Never free.
    assert api.tool_price("bg-removal", *size, scroll) == remove_bg


def test_tool_size_keeps_within_max_generation_size():
    limit = 1024 * 1024
    assert api.tool_size(1024, 1024, limit) == (1024, 1024)
    assert api.tool_size(832, 1216, limit) == (832, 1216)
    assert api.tool_size(512, 512, limit) == (1024, 1024)
    width, height = api.tool_size(2048, 1536, limit)
    assert width * height <= limit and abs(width / height - 2048 / 1536) < 0.01
    assert api.tool_price("lineart", width, height) == 0  # So Opus stays free.
    # A limit above the site's own, or below what it enlarges to, changes nothing.
    assert api.tool_size(4000, 3000, 4096 * 4096) == (2047, 1535)
    assert api.tool_size(512, 512, 256 * 256) == (1024, 1024)
    with pytest.raises(ValueError):
        api.tool_size(0, 10)
    assert api.tool_price("lineart", 0, 10) is None


def test_tool_payload_matches_the_site():
    assert api.build_tool_payload("lineart", "aW1hZ2U=", 832, 1216) == {
        "req_type": "lineart", "use_new_shared_trial": True,
        "width": 832, "height": 1216, "image": "aW1hZ2U="}
    assert api.build_tool_payload("colorize", "aW1hZ2U=", 1024, 1024, defry=3,
                                  prompt="red hair") == {
        "req_type": "colorize", "use_new_shared_trial": True, "prompt": "red hair",
        "defry": 3, "width": 1024, "height": 1024, "image": "aW1hZ2U="}
    assert api.build_tool_payload("colorize", "A", 64, 64)["prompt"] == ""  # Sent empty.
    assert api.build_tool_payload("colorize", "A", 64, 64, defry=9)["defry"] == 5
    for tool in ("bg-removal", "lineart", "sketch"):  # Colorize's fields stay with it.
        payload = api.build_tool_payload(tool, "A", 64, 64, defry=2, prompt="blue eyes")
        assert "defry" not in payload and "prompt" not in payload


def test_tool_cost_says_why():
    scroll = api.Account(tier=2, active=True)
    assert api.tool_cost("lineart", 1024, 1024) == ""  # Free: nothing to say.
    assert api.tool_cost("bg-removal", 1024, 1024) == "65 Anlas: Remove BG is never free"
    assert api.tool_cost("bg-removal", 1024, 1024, scroll) == "65 Anlas: Remove BG is never free"
    assert api.tool_cost("sketch", 832, 1216, scroll) == (
        "20 Anlas: free tools need an Opus plan (yours: Scroll)")
    assert api.tool_cost("colorize", 1536, 1536) == (
        "45 Anlas: 1536 x 1536 is larger than 1024 x 1024")
    # A plan that couldn't be read is taken to be Opus, as it is for generating.
    unread = api.Account()
    assert api.anlas_warnings(1024, 1024, 28, 1, "nai-diffusion-4-5-full", unread) == []
    assert api.tool_price("lineart", 1024, 1024, unread) == 0
    assert api.tool_cost("lineart", 1024, 1024, unread) == ""


def test_same_shape_allows_for_rounding():
    assert api.same_shape(1024, 1024, 1536, 1536)
    assert api.same_shape(1023, 1023, 1536, 1536)
    assert api.same_shape(1254, 836, 300, 200)  # What the site enlarges 300 x 200 to.
    assert api.same_shape(1664, 2432, 832, 1216)
    assert not api.same_shape(1280, 720, 1024, 1024)
    assert not api.same_shape(832, 1216, 1216, 832)
    assert not api.same_shape(0, 10, 0, 10)


def test_tool_names_and_results():
    assert [tool for tool, _ in api.TOOLS] == ["bg-removal", "lineart", "sketch", "colorize"]
    assert api.tool_name("bg-removal") == "Remove BG"
    assert api.tool_name("something-new") == "something-new"
    assert api.TOOL_RESULTS["bg-removal"] == ["Masked", "Generated", "Blend"]


def test_upscale_follows_the_site():
    assert api.build_upscale_payload("aW1hZ2U=") == {
        "image": "aW1hZ2U=", "model": "nai-diffusion-5-curated", "declared_blur_sigma": 0}
    assert api.can_upscale(1024, 1024) and api.upscale_price(1024, 1024) == 1
    assert api.upscale_price(512, 768) == 1
    assert not api.can_upscale(1024, 1025) and api.upscale_price(1024, 1025) is None
    assert not api.can_upscale(0, 100) and api.upscale_price(0, 100) is None
