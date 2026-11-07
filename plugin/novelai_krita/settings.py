"""Persistent plugin settings, stored in Krita's own settings file (kritarc)."""

from krita import Krita

from . import api, presets

GROUP = "NovelAIKrita"

DEFAULTS = {
    "token": "",
    "model": api.DEFAULT_MODEL,
    "prompt": "",
    "negative_prompt": "",
    "strength": "100",
    "steps": "28",
    "scale": "5.0",
    "cfg_rescale": "0.0",
    "sampler": "k_euler_ancestral",
    "noise_schedule": "karras",
    "seed": "0",
    "random_seed": "true",
    "n_samples": "1",
    "quality_tags": presets.STANDARD,
    "uc_preset": presets.DEFAULT_UC_PRESET,
    "max_pixels": str(api.FREE_MAX_PIXELS),
    "context_padding": "20",
    "show_advanced": "false",
    "save_dir": "",
    "autocomplete": "true",
    "inpaint_full_image": "false",
    "live_preview": "true",
    "history_limit": "50",
}


def get(key: str) -> str:
    return Krita.instance().readSetting(GROUP, key, DEFAULTS.get(key, ""))


def get_int(key: str) -> int:
    try:
        return int(float(get(key)))
    except ValueError:
        return int(float(DEFAULTS[key]))


def get_float(key: str) -> float:
    try:
        return float(get(key))
    except ValueError:
        return float(DEFAULTS[key])


def get_bool(key: str) -> bool:
    return get(key).lower() == "true"


def set(key: str, value) -> None:  # noqa: A001 - mirrors get()
    if isinstance(value, bool):
        value = "true" if value else "false"
    Krita.instance().writeSetting(GROUP, key, str(value))


# Before Undesired Content presets existed, the plugin pre-filled this text
# into the box. It's now covered by the presets, so it's cleared on load.
LEGACY_NEGATIVE_PROMPT = (
    "lowres, artistic error, worst quality, bad quality, jpeg artifacts, "
    "very displeasing, blurry, logo, watermark, signature"
)


def negative_prompt() -> str:
    text = get("negative_prompt")
    return "" if text.strip() == LEGACY_NEGATIVE_PROMPT else text


def quality() -> str:
    """Quality tag level; older versions stored true/false."""
    value = get("quality_tags").lower()
    return {"true": presets.STANDARD, "false": presets.OFF}.get(value, value)
