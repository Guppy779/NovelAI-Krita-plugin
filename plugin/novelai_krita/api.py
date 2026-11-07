"""NovelAI image API: request building and response parsing.

Plain Python on purpose (no Qt, no Krita), so it can be unit tested and
reused. Endpoint reference: https://image.novelai.net/docs/index.html
"""

import base64
import io
import json
import math
import random
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

IMAGE_API = "https://image.novelai.net"
GENERATE_URL = IMAGE_API + "/ai/generate-image"
SUGGEST_TAGS_URL = IMAGE_API + "/ai/generate-image/suggest-tags"
# api.novelai.net rejects third-party clients here ("update to the image
# URL"); the image host serves the same endpoint.
SUBSCRIPTION_URL = IMAGE_API + "/user/subscription"

# The API spec does not enumerate model names. These are the published API
# names; the model box in the docker is editable so new ones can be typed in.
MODELS = [
    ("nai-diffusion-5-full", "NAI Diffusion V5 Full"),
    ("nai-diffusion-5-curated", "NAI Diffusion V5 Curated"),
    ("nai-diffusion-4-5-full", "NAI Diffusion V4.5 Full"),
    ("nai-diffusion-4-5-curated", "NAI Diffusion V4.5 Curated"),
    ("nai-diffusion-4-full", "NAI Diffusion V4 Full"),
    ("nai-diffusion-4-curated-preview", "NAI Diffusion V4 Curated"),
    ("nai-diffusion-3", "NAI Diffusion Anime V3"),
    ("nai-diffusion-furry-3", "NAI Diffusion Furry V3"),
]
DEFAULT_MODEL = "nai-diffusion-4-5-full"

# Models the server has no "-inpainting" variant for (checked against
# /ai/generate-image/suggest-tags, which answers "Model not found").
NO_INPAINTING = {"nai-diffusion-5-curated"}

SAMPLERS = [
    ("k_euler_ancestral", "Euler Ancestral"),
    ("k_euler", "Euler"),
    ("k_dpmpp_2s_ancestral", "DPM++ 2S Ancestral"),
    ("k_dpmpp_2m_sde", "DPM++ 2M SDE"),
    ("k_dpmpp_2m", "DPM++ 2M"),
    ("k_dpmpp_sde", "DPM++ SDE"),
]
NOISE_SCHEDULES = ["karras", "exponential", "polyexponential", "native"]

# Tags the NovelAI web app appends when "Add Quality Tags" is on. The server
# does not add them itself, so the plugin does it client side.
QUALITY_TAGS = {
    "4-5": ", very aesthetic, masterpiece, no text",
    "4": ", no text, best quality, very aesthetic, absurdres",
    "3": ", best quality, amazing quality, very aesthetic, absurdres",
}

DEFAULT_NEGATIVE_PROMPT = (
    "lowres, artistic error, worst quality, bad quality, jpeg artifacts, "
    "very displeasing, blurry, logo, watermark, signature"
)

# Largest image area that Opus subscribers can generate without spending
# Anlas (with 28 steps or fewer, one image).
FREE_MAX_PIXELS = 1024 * 1024
MAX_SEED = 2**32 - 1
SIZE_MULTIPLE = 64
MAX_SIDE = 2048


def model_family(model: str) -> str:
    """Return "3", "4", "4-5" or "new" for a model API name.

    Unknown names (e.g. nai-diffusion-5-full, which the server recognises
    but which is not in MODELS yet) are treated as newer than V4.5.
    """
    if "diffusion-4-5" in model:
        return "4-5"
    if "diffusion-4" in model:
        return "4"
    if "diffusion-3" in model or "furry-3" in model:
        return "3"
    return "new"


def is_v4(model: str) -> bool:
    """True for models that take the V4-style prompt structure (V4 and later)."""
    return model_family(model) != "3"


def inpainting_model(model: str) -> str:
    """Map a base model to the model NovelAI uses for inpainting ("infill")."""
    if model.endswith("-inpainting"):
        return model
    if model == "nai-diffusion-4-curated-preview":
        return "nai-diffusion-4-curated-inpainting"
    return model + "-inpainting"


def supports_inpainting(model: str) -> bool:
    return model not in NO_INPAINTING


def random_seed() -> int:
    return random.randint(0, MAX_SEED)


def fit_resolution(width: int, height: int, max_pixels: int = FREE_MAX_PIXELS,
                   multiple: int = SIZE_MULTIPLE) -> Tuple[int, int]:
    """Scale (width, height) up or down to fill at most ``max_pixels``.

    Keeps the aspect ratio as closely as possible, rounds both sides down to
    a multiple of ``multiple`` and keeps each side within [multiple, MAX_SIDE].
    Small regions are scaled up so inpainting a small selection still gets a
    full-resolution generation.
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    scale = math.sqrt(max_pixels / float(width * height))

    def snap(value: float) -> int:
        value = int(value // multiple) * multiple
        return max(multiple, min(MAX_SIDE, value))

    w, h = snap(width * scale), snap(height * scale)
    while w * h > max_pixels and (w > multiple or h > multiple):
        if w >= h:
            w -= multiple
        else:
            h -= multiple
    return w, h


@dataclass
class GenerationParams:
    prompt: str
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT
    model: str = DEFAULT_MODEL
    width: int = 832
    height: int = 1216
    steps: int = 28
    scale: float = 5.0  # "Prompt guidance"
    cfg_rescale: float = 0.0
    sampler: str = "k_euler_ancestral"
    noise_schedule: str = "karras"
    seed: int = 0
    n_samples: int = 1
    quality_tags: bool = True
    # Image to image / inpainting. ``image`` and ``mask`` are base64 PNGs of
    # exactly width x height pixels; white in the mask means "regenerate".
    image: Optional[str] = None
    mask: Optional[str] = None
    strength: float = 0.7
    noise: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def action(self) -> str:
        if self.image and self.mask:
            return "infill"
        if self.image:
            return "img2img"
        return "generate"


def build_payload(p: GenerationParams) -> dict:
    """Build the JSON body for POST /ai/generate-image."""
    family = model_family(p.model)
    prompt = p.prompt.strip()
    if p.quality_tags:
        prompt += QUALITY_TAGS.get(family, "")  # Unknown for newer models.
    negative = p.negative_prompt.strip()
    action = p.action
    model = inpainting_model(p.model) if action == "infill" else p.model

    params = {
        "params_version": 3,
        "width": int(p.width),
        "height": int(p.height),
        "scale": float(p.scale),
        "sampler": p.sampler,
        "steps": int(p.steps),
        "n_samples": int(p.n_samples),
        "seed": int(p.seed),
        "qualityToggle": bool(p.quality_tags),
        "dynamic_thresholding": False,
        "controlnet_strength": 1,
        "legacy": False,
        "add_original_image": True,
        "cfg_rescale": float(p.cfg_rescale),
        "noise_schedule": p.noise_schedule,
        "legacy_v3_extend": False,
        "negative_prompt": negative,
    }

    if is_v4(p.model):
        if params["noise_schedule"] == "native":  # V3 only.
            params["noise_schedule"] = "karras"
        params.update({
            "deliberate_euler_ancestral_bug": False,
            "prefer_brownian": True,
            "use_coords": False,
            "characterPrompts": [],
            "v4_prompt": {
                "caption": {"base_caption": prompt, "char_captions": []},
                "use_coords": False,
                "use_order": True,
            },
            "v4_negative_prompt": {
                "caption": {"base_caption": negative, "char_captions": []},
                "legacy_uc": False,
            },
        })
    else:
        params.update({"sm": False, "sm_dyn": False})

    if action == "img2img":
        params.update({
            "image": p.image,
            "strength": float(p.strength),
            "noise": float(p.noise),
            "extra_noise_seed": int(p.seed),
        })
    elif action == "infill":
        params.update({"image": p.image, "mask": p.mask})
        if is_v4(p.model):
            params["inpaintImg2ImgStrength"] = float(p.strength)

    params.update(p.extra)
    return {"input": prompt, "model": model, "action": action, "parameters": params}


def parse_images(body: bytes) -> List[Tuple[bytes, Optional[int]]]:
    """Return [(image_bytes, seed_or_None), ...] from a generate-image response.

    The endpoint answers with a zip of PNGs by default, or JSON with base64
    images when ``Accept: application/json`` is sent. Both are handled.
    """
    if body[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            names = sorted(n for n in archive.namelist() if not n.endswith("/"))
            return [(archive.read(n), None) for n in names]
    data = json.loads(body.decode("utf-8"))
    images = data.get("images", [])
    images = sorted(images, key=lambda item: item.get("index", 0))
    return [(base64.b64decode(item["image"]), item.get("seed")) for item in images]


def error_message(status: int, body: bytes) -> str:
    """Turn an error response into a message suitable for the user."""
    detail = ""
    try:
        detail = json.loads(body.decode("utf-8")).get("message", "")
    except (ValueError, UnicodeDecodeError, AttributeError):
        detail = body[:200].decode("utf-8", "replace").strip() if body else ""
    hints = {
        400: "NovelAI rejected the request",
        401: "Your API token is missing, invalid or expired",
        402: "Not enough Anlas for this generation",
        403: "Your subscription does not allow this request",
        429: "Another generation is still running on your account; wait for it to finish",
        500: "NovelAI had an internal error",
        503: "NovelAI is temporarily unavailable",
        504: "NovelAI timed out",
    }
    message = hints.get(status, "Request failed (HTTP %d)" % status)
    if detail and detail.lower() not in message.lower():
        message += ": " + detail
    return message


def parse_anlas(body: bytes) -> Optional[int]:
    """Extract the Anlas balance from a /user/subscription response."""
    try:
        steps = json.loads(body.decode("utf-8")).get("trainingStepsLeft") or {}
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None
    total = 0
    found = False
    for key in ("fixedTrainingStepsLeft", "purchasedTrainingSteps"):
        if isinstance(steps.get(key), int):
            total += steps[key]
            found = True
    return total if found else None


# ---- Tag suggestions (GET /ai/generate-image/suggest-tags) -----------------

@dataclass
class TagSuggestion:
    tag: str  # Text to insert into the prompt.
    label: str  # Text to show in the list.
    count: int  # Popularity; the server caps it at 10000.
    exact: bool  # Prefix match (False for the server's fuzzy matches).


def suggest_tags_url(model: str, query: str, lang: str = "en") -> str:
    params = {"model": model, "prompt": query}
    if lang != "en":
        params["lang"] = lang
    return SUGGEST_TAGS_URL + "?" + urllib.parse.urlencode(params)


def parse_tag_suggestions(body: bytes) -> List[TagSuggestion]:
    """Parse both response shapes: English ({"tags": [...]}) and Japanese ([...])."""
    data = json.loads(body.decode("utf-8"))
    results = []
    if isinstance(data, dict):
        for item in data.get("tags") or []:
            tag = item.get("tag")
            if tag:
                results.append(TagSuggestion(
                    tag, tag, int(item.get("count") or 0),
                    not item.get("confidence")))
    elif isinstance(data, list):
        for item in data:
            tag = item.get("en_tag")
            if tag:
                label = "%s → %s" % (item.get("jp_tag", ""), tag)
                results.append(TagSuggestion(tag, label, int(item.get("power") or 0), True))
    return results


def format_count(count: int) -> str:
    if count >= 10000:
        return "10k+"
    if count >= 1000:
        return "%.1fk" % (count / 1000.0)
    return str(count)
