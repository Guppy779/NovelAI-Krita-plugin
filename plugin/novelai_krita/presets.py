"""Quality tags and Undesired Content presets, added the way the NovelAI site does.

The server adds neither; the site adds both to the request text, hidden from
the prompt boxes. Tag text is copied exactly from NovelAI's docs:
  https://docs.novelai.net/en/image/qualitytags
  https://docs.novelai.net/en/image/undesiredcontent
How they are combined (preset first, then the user's own undesired content,
and "nsfw" in front for non-curated models) matches requests captured from
the site. Plain Python, no Qt.
"""

from typing import List, Optional, Tuple

# ---- Models ----------------------------------------------------------------


def model_key(model: str) -> Optional[str]:
    """The preset family of a model API name, or None if unknown."""
    model = model.replace("-inpainting", "")
    if "diffusion-5" in model:
        return "5-curated" if "curated" in model else "5-full"
    if "diffusion-4-5" in model:
        return "4-5-curated" if "curated" in model else "4-5-full"
    if "diffusion-4" in model:
        return "4-curated" if "curated" in model else "4-full"
    if "furry-3" in model:
        return "furry-3"
    if "diffusion-3" in model:
        return "3"
    return None


def is_curated(model: str) -> bool:
    return "curated" in (model_key(model) or "")


# ---- Quality tags ----------------------------------------------------------

STANDARD, LIGHT, OFF = "standard", "light", "off"

_V5_QUALITY = {
    STANDARD: ", very aesthetic, masterpiece, no text",
    LIGHT: ", very aesthetic, amazing quality, no text",
}
QUALITY_TAGS = {
    "5-full": _V5_QUALITY,
    "5-curated": _V5_QUALITY,
    "4-5-full": {STANDARD: ", location, very aesthetic, masterpiece, no text"},
    "4-5-curated": {STANDARD: ", location, masterpiece, no text, -0.8::feet::, rating:general"},
    "4-full": {STANDARD: ", no text, best quality, very aesthetic, absurdres"},
    "4-curated": {STANDARD: ", rating:general, amazing quality, very aesthetic, absurdres"},
    "3": {STANDARD: ", best quality, amazing quality, very aesthetic, absurdres"},
    "furry-3": {STANDARD: ", {best quality}, {amazing quality}"},
}


def quality_options(model: str) -> List[Tuple[str, str]]:
    """(value, label) choices for the Quality tags box."""
    tags = QUALITY_TAGS.get(model_key(model))
    if tags is None:
        return [(OFF, "Off")]
    if LIGHT in tags:
        return [(STANDARD, "Standard"), (LIGHT, "Light"), (OFF, "Off")]
    return [(STANDARD, "On"), (OFF, "Off")]


def apply_quality(model: str, prompt: str, level: str) -> str:
    """The prompt with the model's quality tags added at the end."""
    tags = QUALITY_TAGS.get(model_key(model), {})
    if level == OFF or not tags:
        return prompt
    return prompt + tags.get(level, tags[STANDARD])


# ---- Undesired Content presets ----------------------------------------------

NONE = "None"

_HEAVY_45 = (
    "lowres, artistic error, film grain, scan artifacts, worst quality, bad quality, "
    "jpeg artifacts, very displeasing, chromatic aberration, dithering, halftone, "
    "screentone, multiple views, logo, too many watermarks, negative space, blank page")
_FURRY_FOCUS = (
    "{worst quality}, distracting watermark, unfinished, bad quality, {widescreen}, "
    "upscale, {sequence}, {{grandfathered content}}, blurred foreground, chromatic "
    "aberration, sketch, everyone, [sketch background], simple, [flat colors], "
    "ych (character), outline, multiple scenes, [[horror (theme)]], comic")
_HUMAN_FOCUS_45 = _HEAVY_45 + ", @_@, mismatched pupils, glowing eyes, bad anatomy"
_V5 = [
    ("Heavy", _HEAVY_45),
    ("Light", "lowres, bad hands, bad anatomy, artistic error, sepia, white haze, worst "
              "quality, very displeasing, jpeg artifacts, 0::ai-generated::"),
    ("Furry Focus", _FURRY_FOCUS),
    ("Human Focus", _HUMAN_FOCUS_45),
]
_HEAVY_V3 = (
    "lowres, {bad}, error, fewer, extra, missing, worst quality, jpeg artifacts, bad "
    "quality, watermark, unfinished, displeasing, chromatic aberration, signature, extra "
    "digits, artistic error, username, scan, [abstract],")

# In the site's order: a preset's position is the "ucPreset" number it sends.
UC_PRESETS = {
    "5-full": _V5,
    "5-curated": _V5,
    "4-5-full": [
        ("Heavy", _HEAVY_45),
        ("Light", "lowres, artistic error, scan artifacts, worst quality, bad quality, "
                  "jpeg artifacts, multiple views, very displeasing, too many watermarks, "
                  "negative space, blank page"),
        ("Furry Focus", _FURRY_FOCUS),
        ("Human Focus", _HUMAN_FOCUS_45),
    ],
    "4-5-curated": [
        ("Heavy", "blurry, lowres, upscaled, artistic error, film grain, scan artifacts, "
                  "worst quality, bad quality, jpeg artifacts, very displeasing, chromatic "
                  "aberration, halftone, multiple views, logo, too many watermarks, negative "
                  "space, blank page"),
        ("Light", "blurry, lowres, upscaled, artistic error, scan artifacts, jpeg artifacts, "
                  "logo, too many watermarks, negative space, blank page"),
        ("Human Focus", "blurry, lowres, upscaled, artistic error, film grain, scan artifacts, "
                        "bad anatomy, bad hands, worst quality, bad quality, jpeg artifacts, "
                        "very displeasing, chromatic aberration, halftone, multiple views, logo, "
                        "too many watermarks, @_@, mismatched pupils, glowing eyes, negative "
                        "space, blank page"),
    ],
    "4-full": [
        ("Heavy", "blurry, lowres, error, film grain, scan artifacts, worst quality, bad "
                  "quality, jpeg artifacts, very displeasing, chromatic aberration, multiple "
                  "views, logo, too many watermarks"),
        ("Light", "blurry, lowres, error, worst quality, bad quality, jpeg artifacts, very "
                  "displeasing"),
    ],
    "4-curated": [
        ("Heavy", "blurry, lowres, error, film grain, scan artifacts, worst quality, bad "
                  "quality, jpeg artifacts, very displeasing, chromatic aberration, logo, "
                  "dated, signature, multiple views, gigantic breasts"),
        ("Light", "blurry, lowres, error, worst quality, bad quality, jpeg artifacts, very "
                  "displeasing, logo, dated, signature"),
    ],
    "3": [
        ("Heavy", _HEAVY_V3),
        ("Light", "lowres, jpeg artifacts, worst quality, watermark, blurry, very displeasing,"),
        ("Human Focus", _HEAVY_V3 + " bad anatomy, bad hands, @_@, mismatched pupils, "
                                    "heart-shaped pupils, glowing eyes,"),
    ],
    "furry-3": [
        ("Heavy", "{{worst quality}}, [displeasing], {unusual pupils}, guide lines, "
                  "{{unfinished}}, {bad}, url, artist name, {{tall image}}, mosaic, {sketch "
                  "page}, comic panel, impact (font), [dated], {logo}, ych, {what}, {where is "
                  "your god now}, {distorted text}, repeated text, {floating head}, {1994}, "
                  "{widescreen}, absolutely everyone, sequence, {compression artifacts}, hard "
                  "translated, {cropped}, {commissioner name}, unknown text, high contrast,"),
        ("Light", "{worst quality}, guide lines, unfinished, bad, url, tall image, widescreen, "
                  "compression artifacts, unknown text,"),
    ],
}
DEFAULT_UC_PRESET = "Heavy"

# The site's numbers for its presets, sent alongside the text they add.
_QUALITY_HINTS = {STANDARD: 1, LIGHT: 3, OFF: 0}
_UC_HINTS = {NONE: 0, "Heavy": 2, "Light": 3, "Human Focus": 4, "Furry Focus": 5}


def quality_hint(level: str) -> int:
    return _QUALITY_HINTS.get(level, 0)


def uc_hint(preset: str) -> int:
    return _UC_HINTS.get(preset, 0)


def uc_options(model: str) -> List[str]:
    """Preset names for the model, in the site's order, then "None"."""
    return [name for name, _ in UC_PRESETS.get(model_key(model), [])] + [NONE]


def join_tags(first: str, second: str) -> str:
    """Join two comma-separated tag lists, either of which may be empty."""
    first, second = first.strip(), second.strip()
    if not first or not second:
        return first or second
    # Some presets already end in a comma (as on the site); don't double it.
    return first + (" " if first.endswith(",") else ", ") + second


def apply_uc(model: str, prompt: str, undesired: str, preset: str) -> Tuple[str, int]:
    """The undesired content to send, and the site's "ucPreset" number.

    Preset first, then the user's own undesired content. Like the site, for
    non-curated models "nsfw" goes in front unless the prompt asks for it.
    """
    presets = UC_PRESETS.get(model_key(model), [])
    names = [name for name, _ in presets]
    text = dict(presets).get(preset, "")
    index = names.index(preset) if preset in names else len(names)
    negative = join_tags(text, undesired)
    if (model_key(model) is not None and not is_curated(model)
            and "nsfw" not in prompt.lower() and "nsfw" not in negative.lower()):
        negative = join_tags("nsfw,", negative) if negative else "nsfw"
    return negative, index
