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


# Exact strings from the site's script (its table of quality presets). NovelAI's
# docs differ for V4.5 ("location") and V4 Curated ("amazing quality").
@pytest.mark.parametrize("model, expected", [
    ("nai-diffusion-5-full", "x, very aesthetic, masterpiece, no text"),
    ("nai-diffusion-5-curated", "x, very aesthetic, masterpiece, no text"),
    ("nai-diffusion-4-5-full", "x, very aesthetic, masterpiece, no text"),
    ("nai-diffusion-4-5-curated",
     "x, very aesthetic, masterpiece, no text, -0.8::feet::, rating:general"),
    ("nai-diffusion-4-full", "x, no text, best quality, very aesthetic, absurdres"),
    ("nai-diffusion-4-curated-preview", "x, rating:general, best quality, very aesthetic, absurdres"),
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


def test_nsfw_only_goes_with_a_preset():
    # The site's script adds it only when a preset other than None is chosen.
    for model in ("nai-diffusion-4-5-full", "nai-diffusion-5-full", "nai-diffusion-3"):
        assert presets.apply_uc(model, "1girl", "bad hands, crowd,", "None")[0] == (
            "bad hands, crowd,")
    assert presets.apply_uc("nai-diffusion-4-5-full", "1girl", "", "None")[0] == ""
    assert presets.apply_uc("unknown", "1girl", "bad hands", "Heavy")[0] == "bad hands"


def test_v3_sends_lowres_for_an_empty_box_without_a_preset():
    for model in ("nai-diffusion-3", "nai-diffusion-furry-3", "nai-diffusion-3-inpainting"):
        assert presets.apply_uc(model, "1girl", "", "None")[0] == "lowres"
        assert presets.apply_uc(model, "1girl", "dog", "None")[0] == "dog"


def test_quality_tags_go_in_front_of_the_text_to_draw():
    v45 = "nai-diffusion-4-5-full"
    tags = "very aesthetic, masterpiece, no text"
    assert presets.apply_quality(v45, "1girl, sign. Text: hello", presets.STANDARD) == (
        "1girl, sign., %s Text: hello" % tags)
    assert presets.apply_quality(v45, "text: hello", presets.STANDARD) == tags + "text: hello"
    # Not "context:" or "text::".
    assert presets.apply_quality(v45, "context: x", presets.STANDARD) == "context: x, " + tags
    assert presets.apply_quality(v45, "1girl, text::2", presets.STANDARD) == (
        "1girl, text::2, " + tags)
    assert presets.apply_quality(v45, "", presets.STANDARD) == tags


def test_quality_tags_go_on_the_first_part_from_v4_and_on_every_part_of_v3():
    tags = "very aesthetic, masterpiece, no text"
    assert presets.apply_quality("nai-diffusion-5-full", "1girl|1boy|cat", presets.STANDARD) == (
        "1girl, %s|1boy|cat" % tags)
    v3 = "best quality, amazing quality, very aesthetic, absurdres"
    assert presets.apply_quality("nai-diffusion-3", "1girl:0.5|1boy", presets.STANDARD) == (
        "1girl, %s:0.5|1boy, %s" % (v3, v3))


def test_split_parts_as_the_site_splits():
    assert presets.split_parts("a|b") == ["a", "b"]
    assert presets.split_parts("a||b|c") == ["a||b|c"]  # After "||": not split.
    assert presets.split_parts("a||b|c||d|e") == ["a||b|c||d", "e"]
    assert presets.split_parts("1|2|3|4|5|6|7") == ["1", "2", "3", "4", "5", "6|7"]
    assert presets.split_parts("") == [""]


def test_the_preset_goes_on_the_first_part_of_the_undesired_content():
    heavy = dict(presets.UC_PRESETS["4-5-curated"])["Heavy"]
    curated = "nai-diffusion-4-5-curated"
    assert presets.apply_uc(curated, "x", "dog|cat", "Heavy")[0] == heavy + ", dog|cat"
    assert presets.apply_uc(curated, "x", "|cat", "Heavy")[0] == heavy + "|cat"
    assert presets.apply_uc(curated, "x", " dog ", "Heavy")[0] == heavy + ",  dog "


def test_v4_presets_end_with_the_blank_page_tags():
    for family in ("4-full", "4-curated"):
        for _, text in presets.UC_PRESETS[family]:
            assert text.endswith(", white blank page, blank page")


def test_nsfw_is_added_whatever_the_undesired_content_says():
    # The site only looks at the prompt.
    negative, _ = presets.apply_uc("nai-diffusion-4-5-full", "1girl", "nsfw", "Heavy")
    assert negative.startswith("nsfw, lowres") and negative.endswith("blank page, nsfw")


def test_none_and_unknown_presets():
    assert presets.apply_uc("nai-diffusion-4-full", "x", "", "None") == ("", 2)
    assert presets.apply_uc("nai-diffusion-4-curated-preview", "x", "", "None") == ("", 2)
    # A preset the model doesn't have counts as None.
    assert presets.apply_uc("nai-diffusion-4-full", "x", "dog", "Furry Focus") == ("dog", 2)
    # Unknown models get the user's text only.
    assert presets.apply_uc("custom-model", "x", "dog", "Heavy") == ("dog", 0)


def test_v5_light_preset_and_v3_human_focus():
    negative, _ = presets.apply_uc("nai-diffusion-5-full", "x", "", "Light")
    assert negative.endswith("jpeg artifacts, 0::ai-generated::")
    negative, _ = presets.apply_uc("nai-diffusion-3", "x", "", "Human Focus")
    assert "[abstract], bad anatomy, bad hands, @_@" in negative
    assert negative.endswith("heart-shaped pupils, glowing eyes")
