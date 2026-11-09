import gzip
import json
import struct
import zlib

import pytest

from novelai_krita import api, metadata, presets

V5 = "nai-diffusion-5-full"
HEAVY = dict(presets.UC_PRESETS["5-full"])["Heavy"]
QUALITY = presets.QUALITY_TAGS["5-full"][presets.STANDARD]

# Shaped like the Comment of a V5 image from the NovelAI site (shortened).
COMMENT = {
    "prompt": "1girl, autumn, pumpkin\n" + QUALITY,
    "steps": 28, "height": 1024, "width": 1024, "scale": 5, "cfg_rescale": 0.2,
    "seed": 1234567890, "n_samples": 1, "noise_schedule": "karras",
    "sampler": "k_euler_ancestral", "uc": "nsfw, " + HEAVY + ", watermark",
    "v4_prompt": {"caption": {
        "base_caption": "1girl, autumn, pumpkin\n" + QUALITY,
        "char_captions": [{"char_caption": "girl, sweater, smile, ",
                           "centers": [{"x": 0.4, "y": 0.5}]}]},
        "use_coords": True, "use_order": True},
    "v4_negative_prompt": {"caption": {
        "base_caption": "nsfw, " + HEAVY + ", watermark",
        "char_captions": [{"char_caption": "frown", "centers": [{"x": 0.4, "y": 0.5}]}]},
        "legacy_uc": False},
    "model_name": "NovelAI Diffusion V5", "model_hash": "0ADF9AB7",
}
FIELDS = {"Title": "NovelAI generated image", "Software": "NovelAI",
          "Source": "NovelAI Diffusion V5 0ADF9AB7", "Description": COMMENT["prompt"],
          "Comment": json.dumps(COMMENT)}


def chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(
        ">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def png(*chunks):
    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return (metadata.PNG_SIGNATURE + chunk(b"IHDR", header) + b"".join(chunks)
            + chunk(b"IDAT", zlib.compress(b"\0\0\0\0\0")) + chunk(b"IEND", b""))


def hide(payload, width, height, magic=b"stealth_pngcomp"):
    """Alpha bytes (all 254/255) carrying ``payload`` the way NovelAI hides it."""
    data = magic + struct.pack(">I", len(payload) * 8) + payload
    alpha = bytearray([254] * (width * height))
    for index in range(len(data) * 8):
        bit = (data[index // 8] >> (7 - index % 8)) & 1
        x, y = divmod(index, height)
        alpha[y * width + x] |= bit
    return bytes(alpha)


# ---- Reading the fields -----------------------------------------------------

def test_png_text_reads_all_three_chunk_types():
    data = png(
        chunk(b"tEXt", b"Software\0NovelAI"),
        chunk(b"zTXt", b"Comment\0\0" + zlib.compress(b'{"seed": 5}')),
        chunk(b"iTXt", b"Description\0\0\0\0\0" + "銀髪, night".encode("utf-8")),
        chunk(b"iTXt", b"Title\0\1\0en\0\0" + zlib.compress(b"compressed")),
    )
    assert metadata.png_text(data) == {
        "Software": "NovelAI", "Comment": '{"seed": 5}',
        "Description": "銀髪, night", "Title": "compressed"}


def test_png_text_ignores_other_files_and_broken_chunks():
    assert metadata.png_text(b"RIFF....WEBP") == {}
    assert metadata.png_text(b"") == {}
    broken = png(chunk(b"zTXt", b"Comment\0\0not zlib"), chunk(b"tEXt", b"Software\0NovelAI"))
    assert metadata.png_text(broken) == {"Software": "NovelAI"}
    assert metadata.png_text(png(chunk(b"tEXt", b"Software\0NovelAI"))[:-20]) == {
        "Software": "NovelAI"}  # Cut off at the end.


def test_stealth_text_reads_compressed_and_plain():
    fields = {"Software": "NovelAI", "Comment": json.dumps({"seed": 7})}
    payload = json.dumps(fields).encode("utf-8")
    assert metadata.stealth_text(hide(gzip.compress(payload), 40, 30), 40, 30) == fields
    plain = hide(payload, 40, 30, b"stealth_pnginfo")
    assert metadata.stealth_text(plain, 40, 30) == fields


def test_stealth_text_with_padded_rows():
    payload = gzip.compress(b'{"Software": "NovelAI"}')
    alpha = hide(payload, 40, 30)
    padded = b"".join(alpha[y * 40:(y + 1) * 40] + b"\0\0\0\0" for y in range(30))
    assert metadata.stealth_text(padded, 40, 30, 44) == {"Software": "NovelAI"}


def test_stealth_text_without_hidden_data():
    assert metadata.stealth_text(bytes([255] * 1200), 40, 30) == {}
    assert metadata.stealth_text(bytes([255] * 10), 5, 2) == {}
    wrong = hide(b"\x1f\x8b not gzip", 40, 30)
    assert metadata.stealth_text(wrong, 40, 30) == {}


# ---- Understanding them -----------------------------------------------------

def test_parse_v5_image():
    info = metadata.parse(FIELDS)
    assert info.prompt == COMMENT["prompt"]
    assert info.negative == COMMENT["uc"]
    assert info.characters == [api.CharacterPrompt("girl, sweater, smile, ", "frown", 0.4, 0.5)]
    assert info.has_characters and info.use_coords is True
    assert info.settings == {"model": V5, "steps": 28, "scale": 5.0, "cfg_rescale": 0.2,
                             "sampler": "k_euler_ancestral", "noise_schedule": "karras"}
    assert isinstance(info.settings["scale"], float)
    assert info.seed == 1234567890
    assert info.model == "NovelAI Diffusion V5"


def test_parse_v3_image():
    comment = {"prompt": "1girl", "uc": "lowres", "steps": 28, "seed": 3,
               "sampler": "k_dpmpp_2m", "noise_schedule": "native", "scale": 5}
    info = metadata.parse({"Software": "NovelAI", "Comment": json.dumps(comment),
                           "Source": "Stable Diffusion XL C1E1DE52"})
    assert (info.prompt, info.negative, info.characters) == ("1girl", "lowres", [])
    assert info.use_coords is None and not info.has_characters
    assert info.settings["sampler"] == "k_dpmpp_2m"
    assert info.settings["noise_schedule"] == "native"
    assert info.model == "Stable Diffusion XL C1E1DE52"


def test_parse_skips_values_the_panel_cannot_take():
    comment = {"prompt": "x", "steps": 80, "scale": "5", "cfg_rescale": 3,
               "sampler": "ddim_v3", "noise_schedule": "odd", "seed": -1}
    info = metadata.parse({"Comment": json.dumps(comment)})
    assert info.settings == {} and info.seed is None
    info = metadata.parse({"Comment": json.dumps({"prompt": "x", "steps": True, "seed": True})})
    assert info.settings == {} and info.seed is None


def test_parse_without_parameters():
    assert metadata.parse({}) is None
    assert metadata.parse({"Description": "a cat"}) is None  # Not from NovelAI.
    assert metadata.parse({"Comment": "[1, 2]"}) is None
    info = metadata.parse({"Software": "NovelAI", "Description": "a cat", "Comment": "{bad"})
    assert info.prompt == "a cat" and info.seed is None and info.settings == {}


# ---- Separating what NovelAI added -------------------------------------------

@pytest.mark.parametrize("family", sorted(presets.QUALITY_TAGS))
def test_split_quality_round_trips_every_model(family):
    model = {"5-full": V5, "3": "nai-diffusion-3", "furry-3": "nai-diffusion-furry-3"}.get(
        family, "nai-diffusion-%s" % family)
    for level in presets.QUALITY_TAGS[family]:
        sent = presets.apply_quality(model, "1girl, smile", level)
        assert metadata.split_quality(sent, model) == ("1girl, smile", level)


def test_split_quality_only_takes_tags_at_the_end():
    prompt = "very aesthetic, masterpiece, no text, 1girl"
    assert metadata.split_quality(prompt, V5) == (prompt, presets.OFF)


@pytest.mark.parametrize("family", sorted(presets.UC_PRESETS))
def test_split_uc_round_trips_every_preset(family):
    model = {"5-full": V5, "3": "nai-diffusion-3", "furry-3": "nai-diffusion-furry-3"}.get(
        family, "nai-diffusion-%s" % family)
    for name, _ in presets.UC_PRESETS[family]:
        for own in ("", "watermark, text"):
            sent, _ = presets.apply_uc(model, "1girl", own, name)
            own_back, preset = metadata.split_uc(sent, model)
            assert (own_back, preset) == (own, name)
            assert presets.apply_uc(model, "1girl", own_back, preset)[0] == sent


def test_split_uc_keeps_nsfw_without_a_preset():
    assert metadata.split_uc("nsfw, watermark", V5) == ("nsfw, watermark", presets.NONE)
    assert metadata.split_uc("watermark", V5) == ("watermark", presets.NONE)
    # Text that merely starts like a preset isn't one.
    assert metadata.split_uc(HEAVY + "ish", V5) == (HEAVY + "ish", presets.NONE)


def test_split_only_takes_what_the_model_can_add_back():
    v4_full = "nai-diffusion-4-full"
    human_focus = dict(presets.UC_PRESETS["5-full"])["Human Focus"]
    assert metadata.split_uc("nsfw, " + human_focus, v4_full) == (
        "nsfw, " + human_focus, presets.NONE)
    prompt = "1girl" + presets.QUALITY_TAGS["4-full"][presets.STANDARD]
    assert metadata.split_quality(prompt, V5) == (prompt, presets.OFF)
    # Unknown models have no boxes to move anything into.
    assert metadata.split_quality(prompt, "custom") == (prompt, presets.OFF)
    assert metadata.split_uc(HEAVY, "custom") == (HEAVY, presets.NONE)


def test_split_uc_keeps_nsfw_on_curated_models():
    # Curated models don't add it back, so it has to stay in the text.
    text = "nsfw, " + HEAVY
    assert metadata.split_uc(text, "nai-diffusion-5-curated") == (text, presets.NONE)
    assert metadata.split_uc(HEAVY, "nai-diffusion-5-curated") == ("", "Heavy")


@pytest.mark.parametrize("comment", [
    json.dumps({"sampler": ["k_euler"], "noise_schedule": {"a": 1}}),
    "[" * 200000,
], ids=["wrong types", "absurdly nested"])
def test_parse_survives_malformed_comments(comment):
    fields = {"Software": "NovelAI", "Description": "1girl", "Comment": comment}
    info = metadata.parse(fields)
    assert info is not None and info.settings == {}


# ---- Importing ----------------------------------------------------------------

PANEL = {"prompt": "old prompt", "negative_prompt": "old uc", "quality_tags": "light",
         "uc_preset": "Light", "steps": 20, "scale": 6.0, "cfg_rescale": 0.0,
         "sampler": "k_euler", "noise_schedule": "exponential", "seed": "1",
         "random_seed": True, "model": V5, "character_positions": False,
         "characters": [{"prompt": "boy", "negative": "", "x": 0.1, "y": 0.1, "enabled": False}]}
IMPORTED = {"prompt": "girl, sweater, smile, ", "negative": "frown", "x": 0.4, "y": 0.5,
            "enabled": True}


def test_import_keeps_tags_the_panel_model_lacks():
    prompt = "1girl" + presets.QUALITY_TAGS["4-full"][presets.STANDARD]
    info = metadata.ImageInfo(prompt=prompt)
    state = metadata.import_state(info, PANEL, [metadata.PROMPT])
    assert (state["prompt"], state["quality_tags"]) == (prompt, presets.OFF)


def test_import_everything_clean():
    state = metadata.import_state(metadata.parse(FIELDS), PANEL, metadata.PARTS)
    assert state["prompt"] == "1girl, autumn, pumpkin"
    assert state["quality_tags"] == presets.STANDARD
    assert state["negative_prompt"] == "watermark"
    assert state["characters"] == [IMPORTED] and state["character_positions"] is True
    assert state["uc_preset"] == "Heavy"
    assert (state["steps"], state["scale"], state["cfg_rescale"]) == (28, 5.0, 0.2)
    assert (state["sampler"], state["noise_schedule"]) == ("k_euler_ancestral", "karras")
    assert (state["seed"], state["random_seed"]) == ("1234567890", False)
    assert state["model"] == V5


@pytest.mark.parametrize("source, model", [
    ("NovelAI Diffusion V5 0ADF9AB7", "nai-diffusion-5-full"),
    ("NovelAI Diffusion V5 DB276663", "nai-diffusion-5-curated"),
    ("NovelAI Diffusion V4.5 4BDE2A90", "nai-diffusion-4-5-full"),
    ("NovelAI Diffusion V4 4F49EC75", "nai-diffusion-4-full"),
    ("Stable Diffusion XL C1E1DE52", "nai-diffusion-3"),
    ("Stable Diffusion XL 9CC2F394", "nai-diffusion-furry-3"),
])
def test_parse_finds_the_model(source, model):
    assert metadata.parse(dict(FIELDS, Source=source)).settings["model"] == model


def test_parse_leaves_an_unknown_model_out():
    info = metadata.parse(dict(FIELDS, Source="NovelAI Diffusion V9 12345678"))
    assert "model" not in info.settings


def test_import_settings_changes_the_model_before_moving_tags():
    panel = dict(PANEL, model="nai-diffusion-4-full")
    state = metadata.import_state(metadata.parse(FIELDS), panel, metadata.PARTS)
    assert state["model"] == V5
    assert state["prompt"] == "1girl, autumn, pumpkin"
    assert state["quality_tags"] == presets.STANDARD


def test_import_without_settings_keeps_the_model():
    panel = dict(PANEL, model="nai-diffusion-4-full")
    state = metadata.import_state(metadata.parse(FIELDS), panel, [metadata.PROMPT])
    assert state["model"] == "nai-diffusion-4-full"
    assert (state["prompt"], state["quality_tags"]) == (COMMENT["prompt"], presets.OFF)


def test_import_as_recorded():
    info = metadata.parse(FIELDS)
    state = metadata.import_state(info, PANEL, [metadata.PROMPT, metadata.NEGATIVE], clean=False)
    assert state["prompt"] == info.prompt and state["quality_tags"] == presets.OFF
    assert state["negative_prompt"] == info.negative and state["uc_preset"] == presets.NONE
    # The request is the one NovelAI recorded.
    assert presets.apply_uc(V5, state["prompt"], state["negative_prompt"],
                            state["uc_preset"])[0] == info.negative


def test_import_only_some_parts():
    info = metadata.parse(FIELDS)
    state = metadata.import_state(info, PANEL, [metadata.SEED])
    assert state == dict(PANEL, seed="1234567890", random_seed=False)
    state = metadata.import_state(info, PANEL, [metadata.CHARACTERS])
    assert state == dict(PANEL, characters=[IMPORTED], character_positions=True)
    assert metadata.import_state(info, PANEL, []) == PANEL


def test_import_characters_appended():
    info = metadata.parse(FIELDS)
    state = metadata.import_state(info, PANEL, [metadata.CHARACTERS], append=True)
    assert state["characters"] == PANEL["characters"] + [IMPORTED]
    assert PANEL["characters"] != state["characters"]  # The panel's list isn't changed.
    # AI's choice in the image: appending keeps your positions setting.
    info.use_coords = False
    panel = dict(PANEL, character_positions=True)
    state = metadata.import_state(info, panel, [metadata.CHARACTERS], append=True)
    assert state["character_positions"] is True
    state = metadata.import_state(info, panel, [metadata.CHARACTERS])
    assert state["character_positions"] is False


def test_parse_characters():
    prompt = {"caption": {"base_caption": "2girls", "char_captions": [
        {"char_caption": "girl, red hair", "centers": [{"x": 0.1, "y": 0.9}]},
        {"char_caption": "", "centers": []},  # Empty: left out.
        {"char_caption": "girl, blue hair", "centers": [{"x": 7, "y": "a"}]},
        "not a character"]}, "use_coords": False}
    negative = {"caption": {"base_caption": "", "char_captions": [
        {"char_caption": "smile"}]}}
    info = metadata.parse({"Comment": json.dumps(
        {"v4_prompt": prompt, "v4_negative_prompt": negative})})
    assert info.characters == [api.CharacterPrompt("girl, red hair", "smile", 0.1, 0.9),
                               api.CharacterPrompt("girl, blue hair", "", 0.5, 0.5)]
    assert info.use_coords is False


def test_made_from_image():
    def info(comment):
        return metadata.parse({"Comment": json.dumps(dict({"prompt": "x"}, **comment))})
    assert not info({"request_type": "PromptGenerateRequest"}).made_from_image
    assert info({"request_type": "Img2ImgRequest", "strength": 0.65}).made_from_image
    assert not info({}).made_from_image  # Older images don't say.
    assert not info({"request_type": 3}).made_from_image
