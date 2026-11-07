"""Quality tags and Undesired Content presets must match the NovelAI site."""

import pytest

from novelai_krita import presets

ALL_MODELS = [
    "nai-diffusion-5-full", "nai-diffusion-5-curated", "nai-diffusion-4-5-full",
    "nai-diffusion-4-5-curated", "nai-diffusion-4-full", "nai-diffusion-4-curated-preview",
    "nai-diffusion-3", "nai-diffusion-furry-3",
]


def test_model_keys():
    assert [presets.model_key(m) for m in ALL_MODELS] == [
        "5-full", "5-curated", "4-5-full", "4-5-curated", "4-full", "4-curated", "3", "furry-3"]
    assert presets.model_key("nai-diffusion-4-5-full-inpainting") == "4-5-full"
    assert presets.model_key("nai-diffusion-5-full-medium") == "5-full"
    assert presets.model_key("something-else") is None


# Exact strings from docs.novelai.net/en/image/qualitytags.
@pytest.mark.parametrize("model, expected", [
    ("nai-diffusion-5-full", "x, very aesthetic, masterpiece, no text"),
    ("nai-diffusion-5-curated", "x, very aesthetic, masterpiece, no text"),
    ("nai-diffusion-4-5-full", "x, location, very aesthetic, masterpiece, no text"),
    ("nai-diffusion-4-5-curated", "x, location, masterpiece, no text, -0.8::feet::, rating:general"),
    ("nai-diffusion-4-full", "x, no text, best quality, very aesthetic, absurdres"),
    ("nai-diffusion-4-curated-preview", "x, rating:general, amazing quality, very aesthetic, absurdres"),
    ("nai-diffusion-3", "x, best quality, amazing quality, very aesthetic, absurdres"),
    ("nai-diffusion-furry-3", "x, {best quality}, {amazing quality}"),
])
def test_quality_tags(model, expected):
    assert presets.apply_quality(model, "x", presets.STANDARD) == expected
    assert presets.apply_quality(model, "x", presets.OFF) == "x"


def test_light_quality_is_v5_only():
    assert presets.apply_quality("nai-diffusion-5-curated", "x", presets.LIGHT) == \
        "x, very aesthetic, amazing quality, no text"
    assert [v for v, _ in presets.quality_options("nai-diffusion-5-full")] == ["standard", "light", "off"]
    assert [v for v, _ in presets.quality_options("nai-diffusion-4-5-full")] == ["standard", "off"]
    # Light asked of a model without it falls back to its tags.
    assert presets.apply_quality("nai-diffusion-4-full", "x", presets.LIGHT).endswith("absurdres")
    assert presets.quality_options("unknown") == [("off", "Off")]


def test_uc_options_follow_the_site():
    assert presets.uc_options("nai-diffusion-5-full") == [
        "Heavy", "Light", "Furry Focus", "Human Focus", "None"]
    assert presets.uc_options("nai-diffusion-4-5-curated") == ["Heavy", "Light", "Human Focus", "None"]
    assert presets.uc_options("nai-diffusion-4-full") == ["Heavy", "Light", "None"]
    assert presets.uc_options("nai-diffusion-3") == ["Heavy", "Light", "Human Focus", "None"]
    assert presets.uc_options("nai-diffusion-furry-3") == ["Heavy", "Light", "None"]
    assert presets.uc_options("unknown") == ["None"]


# Undesired content exactly as captured from requests the NovelAI site sent
# (NekoAI-API's examples/payloads): preset + user text, "nsfw" in front.
def test_matches_site_v45_human_focus_with_user_text():
    negative, index = presets.apply_uc("nai-diffusion-4-5-full", "1girl, cute", "1234", "Human Focus")
    assert negative == (
        "nsfw, lowres, artistic error, film grain, scan artifacts, worst quality, bad quality, "
        "jpeg artifacts, very displeasing, chromatic aberration, dithering, halftone, "
        "screentone, multiple views, logo, too many watermarks, negative space, blank page, "
        "@_@, mismatched pupils, glowing eyes, bad anatomy, 1234")
    assert index == 3  # The site sent ucPreset 3.


def test_matches_site_v45_heavy_no_user_text():
    negative, index = presets.apply_uc("nai-diffusion-4-5-full", "1girl, cute", "", "Heavy")
    assert negative == (
        "nsfw, lowres, artistic error, film grain, scan artifacts, worst quality, bad quality, "
        "jpeg artifacts, very displeasing, chromatic aberration, dithering, halftone, "
        "screentone, multiple views, logo, too many watermarks, negative space, blank page")
    assert index == 0


def test_matches_site_v3_heavy_trailing_comma():
    negative, _ = presets.apply_uc("nai-diffusion-3", "1 girl", "bad fingers, bad hands,", "Heavy")
    assert negative == (
        "nsfw, lowres, {bad}, error, fewer, extra, missing, worst quality, jpeg artifacts, bad "
        "quality, watermark, unfinished, displeasing, chromatic aberration, signature, extra "
        "digits, artistic error, username, scan, [abstract], bad fingers, bad hands,")


def test_matches_site_curated_has_no_nsfw():
    negative, _ = presets.apply_uc("nai-diffusion-4-5-curated", "1 girl", "bad fingers, bad hands,", "Heavy")
    assert negative == (
        "blurry, lowres, upscaled, artistic error, film grain, scan artifacts, worst quality, "
        "bad quality, jpeg artifacts, very displeasing, chromatic aberration, halftone, "
        "multiple views, logo, too many watermarks, negative space, blank page, bad fingers, "
        "bad hands,")


def test_nsfw_prompt_is_not_blocked():
    negative, _ = presets.apply_uc("nai-diffusion-4-5-full", "nsfw, 1girl", "", "Light")
    assert not negative.startswith("nsfw")
    negative, _ = presets.apply_uc("nai-diffusion-4-5-full", "1girl", "NSFW", "None")
    assert negative == "NSFW"  # Already there: not added twice.


def test_none_and_unknown_presets():
    assert presets.apply_uc("nai-diffusion-4-full", "x", "", "None") == ("nsfw", 2)
    assert presets.apply_uc("nai-diffusion-4-curated-preview", "x", "", "None") == ("", 2)
    # A preset the model doesn't have counts as None.
    assert presets.apply_uc("nai-diffusion-4-full", "x", "dog", "Furry Focus") == ("nsfw, dog", 2)
    # Unknown models get the user's text only.
    assert presets.apply_uc("custom-model", "x", "dog", "Heavy") == ("dog", 0)


def test_v5_light_preset_and_v3_human_focus():
    negative, _ = presets.apply_uc("nai-diffusion-5-full", "x", "", "Light")
    assert negative.endswith("jpeg artifacts, 0::ai-generated::")
    negative, _ = presets.apply_uc("nai-diffusion-3", "x", "", "Human Focus")
    assert "[abstract], bad anatomy, bad hands, @_@" in negative
    assert negative.endswith("heart-shaped pupils, glowing eyes,")
