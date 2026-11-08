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


def test_selection_mode():
    assert api.selection_mode("nai-diffusion-4-5-full", 1.0) == "infill"
    assert api.selection_mode("nai-diffusion-4-5-full", 0.7) == "infill"  # Not img2img.
    assert api.selection_mode("nai-diffusion-5-full", 0.5) == "infill"
    assert api.selection_mode("nai-diffusion-3", 1.0) == "infill"
    assert api.selection_mode("nai-diffusion-3", 0.7) == "img2img"  # V3: no inpaint strength.
    assert api.selection_mode("nai-diffusion-5-curated", 0.7) == "infill"


def test_infill_payload_strength_like_the_site():
    p = api.GenerationParams(prompt="cat", image="A", mask="B", strength=0.7, seed=5)
    params = api.build_payload(p)["parameters"]
    assert params["img2img"] == {"strength": 0.7, "color_correct": True}
    assert params["extra_noise_seed"] == 5 and params["noise"] == 0.0
    full = api.build_payload(api.GenerationParams(prompt="cat", image="A", mask="B", strength=1.0))
    assert "img2img" not in full["parameters"]  # Full strength: no nested object.
    v3 = api.build_payload(api.GenerationParams(prompt="c", image="A", mask="B",
                                                model="nai-diffusion-3", strength=1.0))
    assert "img2img" not in v3["parameters"]


def test_params_version():
    assert api.build_payload(api.GenerationParams(prompt="c", model="nai-diffusion-5-full"))["parameters"]["params_version"] == 4
    assert api.build_payload(api.GenerationParams(prompt="c", model="nai-diffusion-5-curated"))["parameters"]["params_version"] == 4
    assert api.build_payload(api.GenerationParams(prompt="c"))["parameters"]["params_version"] == 3


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
    assert payload["input"] == "1girl, night sky, location, very aesthetic, masterpiece, no text"
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
    assert params["negative_prompt"] == "nsfw, dog"
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
    assert params["extra_noise_seed"] == 7


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


def _contains(region, sel):
    x, y, w, h = region
    sx, sy, sw, sh = sel
    return x <= sx and y <= sy and x + w >= sx + sw and y + h >= sy + sh


def test_plan_region_uses_whole_small_canvas_like_the_site():
    # The user's case: 832x1216 canvas, face selection.
    sel = (200, 150, 440, 390)
    region = api.plan_region(832, 1216, *sel, padding_percent=20)
    assert region == (0, 0, 832, 1216)
    assert api.fit_resolution(*region[2:], upscale=False) == (832, 1216)  # Sent 1:1.


def test_plan_region_widens_at_native_resolution_on_big_canvas():
    sel = (1500, 1200, 300, 300)
    x, y, w, h = api.plan_region(4000, 3000, *sel, padding_percent=20)
    assert _contains((x, y, w, h), sel)
    assert w % 64 == 0 and h % 64 == 0
    assert w * h <= api.FREE_MAX_PIXELS and w * h > 0.9 * api.FREE_MAX_PIXELS
    assert api.fit_resolution(w, h, upscale=False) == (w, h)  # No resampling.
    # Centred on the selection.
    assert abs((x + w / 2) - 1650) <= 1 and abs((y + h / 2) - 1350) <= 1


def test_plan_region_near_edges_stays_inside_canvas():
    for sel in [(0, 0, 200, 200), (3800, 2800, 200, 200), (0, 2900, 4000, 100)]:
        x, y, w, h = api.plan_region(4000, 3000, *sel, padding_percent=50)
        assert x >= 0 and y >= 0 and x + w <= 4000 and y + h <= 3000
        assert _contains((x, y, w, h), sel)


def test_plan_region_large_selection_keeps_minimum_context():
    sel = (500, 500, 1800, 1500)  # Bigger than the budget: scaled down later.
    x, y, w, h = api.plan_region(4000, 3000, *sel, padding_percent=10)
    assert (w, h) == (1800 + 2 * 180, 1500 + 2 * 180)
    assert _contains((x, y, w, h), sel)


def test_plan_region_respects_larger_budget():
    sel = (1500, 1200, 300, 300)
    _, _, w, h = api.plan_region(4000, 3000, *sel, padding_percent=20, max_pixels=1536 * 1536)
    assert w * h > api.FREE_MAX_PIXELS and w * h <= 1536 * 1536
