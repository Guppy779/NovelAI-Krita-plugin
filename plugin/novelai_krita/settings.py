"""Plugin settings, stored in Krita's own settings file (kritarc).

Prompts aren't stored here: each document keeps its own (see doc_state.py).
The generation settings here are the last used ones, for new documents.
"""

from krita import Krita

from . import api, presets

GROUP = "NovelAIKrita"

DEFAULTS = {
    "token": "",
    "model": api.DEFAULT_MODEL,
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
    "character_positions": "false",
    "max_pixels": str(api.FREE_MAX_PIXELS),
    "show_advanced": "false",
    "save_dir": "",
    "autocomplete": "true",
    "inpaint_full_image": "false",
    "live_preview": "true",
    "history_limit": "50",
    "preview_geometry": "",
    # What "Import from image" imports (metadata.PARTS, plus "clean").
    "import_parts": "characters,clean,negative,prompt,seed,settings",
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

