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
    assert api.supports_inpainting("nai-diffusion-5-full")
    assert not api.supports_inpainting("nai-diffusion-5-curated")
    assert api.inpainting_model("nai-diffusion-5-full") == "nai-diffusion-5-full-inpainting"


def test_model_helpers():
    assert api.model_family("nai-diffusion-4-5-full") == "4-5"
    assert api.model_family("nai-diffusion-4-full") == "4"
    assert api.model_family("nai-diffusion-3") == "3"
    assert api.model_family("nai-diffusion-furry-3") == "3"
    assert api.model_family("nai-diffusion-5-full") == "new"
    assert api.is_v4("nai-diffusion-5-full") and not api.is_v4("nai-diffusion-3")
    assert api.inpainting_model("nai-diffusion-4-5-full") == "nai-diffusion-4-5-full-inpainting"
    assert api.inpainting_model("nai-diffusion-4-curated-preview") == "nai-diffusion-4-curated-inpainting"
    assert api.inpainting_model("nai-diffusion-3-inpainting") == "nai-diffusion-3-inpainting"


def test_text_to_image_payload_v45():
    p = api.GenerationParams(prompt="1girl, night sky", negative_prompt="lowres", seed=42)
    payload = api.build_payload(p)
    assert payload["action"] == "generate"
    assert payload["model"] == "nai-diffusion-4-5-full"
    params = payload["parameters"]
    assert payload["input"].startswith("1girl, night sky, very aesthetic")
    assert params["v4_prompt"]["caption"]["base_caption"] == payload["input"]
    assert params["v4_negative_prompt"]["caption"]["base_caption"] == "lowres"
    assert params["seed"] == 42 and params["width"] == 832 and params["height"] == 1216
    assert "image" not in params and "mask" not in params
    assert "sm" not in params
    json.dumps(payload)  # Must be serializable.


def test_quality_tags_can_be_disabled():
    payload = api.build_payload(api.GenerationParams(prompt="cat", quality_tags=False))
    assert payload["input"] == "cat"


def test_v3_payload_has_no_v4_fields():
    payload = api.build_payload(api.GenerationParams(prompt="cat", model="nai-diffusion-3"))
    params = payload["parameters"]
    assert "v4_prompt" not in params
    assert params["sm"] is False
    assert payload["input"].endswith("absurdres")


def test_newer_model_gets_v4_payload_without_guessed_tags():
    payload = api.build_payload(api.GenerationParams(prompt="cat", model="nai-diffusion-5-full"))
    assert payload["input"] == "cat"
    assert "v4_prompt" in payload["parameters"] and "sm" not in payload["parameters"]


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


def test_parse_anlas():
    body = json.dumps({"trainingStepsLeft": {"fixedTrainingStepsLeft": 10000,
                                             "purchasedTrainingSteps": 500}}).encode()
    assert api.parse_anlas(body) == 10500
    assert api.parse_anlas(b"{}") is None
    assert api.parse_anlas(b"not json") is None
