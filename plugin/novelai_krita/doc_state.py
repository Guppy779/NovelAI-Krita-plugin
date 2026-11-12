"""The panel's generation settings for one document (plain Python, no Qt).

Like Krita AI Diffusion, each document keeps its own prompt and settings in
a document annotation, which Krita saves inside the .kra file. This module
turns them into bytes for the annotation and back, ignoring anything it
doesn't recognise, so a damaged or newer annotation can't break the panel.
"""

import json
from typing import Any, Dict

ANNOTATION = "novelai_krita/settings"
DESCRIPTION = "NovelAI plugin: prompt and generation settings"
VERSION = 1

# Setting name -> type. The names match the keys in settings.py.
FIELDS = {
    "prompt": str,
    "negative_prompt": str,
    "model": str,
    "quality_tags": str,
    "uc_preset": str,
    "strength": int,
    "steps": int,
    "scale": float,
    "cfg_rescale": float,
    "sampler": str,
    "noise_schedule": str,
    "seed": str,
    "random_seed": bool,
    "n_samples": int,
    # Character prompts: a list of {"prompt", "negative", "x", "y", "enabled"}
    # (api.CharacterPrompt), and whether they're placed at their positions.
    "characters": list,
    "character_positions": bool,
}
# Kept per document only; new documents start with these empty.
PROMPTS = ("prompt", "negative_prompt", "characters")
# Precise references aren't here on purpose: their pictures would add megabytes
# to every .kra. They stay in the panel instead (see references.py).


def _character(item: Any):
    """A stored character, checked and completed, or None if unusable."""
    if not isinstance(item, dict) or not isinstance(item.get("prompt"), str):
        return None
    character = {"prompt": item["prompt"], "negative": "", "x": 0.5, "y": 0.5, "enabled": True}
    if isinstance(item.get("negative"), str):
        character["negative"] = item["negative"]
    for key in ("x", "y"):
        value = item.get(key)
        if _valid(value, float) and 0.0 <= value <= 1.0:
            character[key] = float(value)
    if isinstance(item.get("enabled"), bool):
        character["enabled"] = item["enabled"]
    return character


def _valid(value: Any, kind: type) -> bool:
    if kind is bool:
        return isinstance(value, bool)
    if isinstance(value, bool):  # True/False are ints in Python.
        return False
    if kind is float:
        return isinstance(value, (int, float))
    return isinstance(value, kind)


def encode(state: Dict[str, Any]) -> bytes:
    data = {"version": VERSION}
    data.update({key: state[key] for key in FIELDS if key in state})
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def decode(data: bytes) -> Dict[str, Any]:
    """The settings stored in ``data``; {} if there are none or they can't be read."""
    try:
        obj = json.loads(bytes(data).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(obj, dict):
        return {}
    state = {}
    for key, kind in FIELDS.items():
        value = obj.get(key)
        if kind is list:
            if isinstance(value, list):
                state[key] = [c for c in map(_character, value) if c is not None]
        elif _valid(value, kind):
            state[key] = float(value) if kind is float else value
    return state
