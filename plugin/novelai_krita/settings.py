"""Persistent plugin settings, stored in Krita's own settings file (kritarc)."""

from krita import Krita

from . import api

GROUP = "NovelAIKrita"

DEFAULTS = {
    "token": "",
    "model": api.DEFAULT_MODEL,
    "prompt": "",
    "negative_prompt": api.DEFAULT_NEGATIVE_PROMPT,
    "strength": "100",
    "steps": "28",
    "scale": "5.0",
    "cfg_rescale": "0.0",
    "sampler": "k_euler_ancestral",
    "noise_schedule": "karras",
    "seed": "0",
    "random_seed": "true",
    "n_samples": "1",
    "quality_tags": "true",
    "max_pixels": str(api.FREE_MAX_PIXELS),
    "context_padding": "20",
    "show_advanced": "false",
    "save_dir": "",
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
