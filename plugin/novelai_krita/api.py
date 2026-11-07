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

from . import presets

IMAGE_API = "https://image.novelai.net"
GENERATE_URL = IMAGE_API + "/ai/generate-image"
GENERATE_STREAM_URL = IMAGE_API + "/ai/generate-image-stream"
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

# Inpainting models whose name isn't "<model>-inpainting". V5 Curated has no
# inpainting model of its own; the NovelAI site uses V4.5 Curated's.
INPAINTING_MODELS = {
    "nai-diffusion-4-curated-preview": "nai-diffusion-4-curated-inpainting",
    "nai-diffusion-5-curated": "nai-diffusion-4-5-curated-inpainting",
}

SAMPLERS = [
    ("k_euler_ancestral", "Euler Ancestral"),
    ("k_euler", "Euler"),
    ("k_dpmpp_2s_ancestral", "DPM++ 2S Ancestral"),
    ("k_dpmpp_2m_sde", "DPM++ 2M SDE"),
    ("k_dpmpp_2m", "DPM++ 2M"),
    ("k_dpmpp_sde", "DPM++ SDE"),
]
NOISE_SCHEDULES = ["karras", "exponential", "polyexponential", "native"]

# Opus subscribers generate without spending Anlas while staying within all
# of these: an area of at most 1024 x 1024 pixels, 28 steps or fewer, and one
# image per request.
FREE_MAX_PIXELS = 1024 * 1024
FREE_MAX_STEPS = 28
FREE_MAX_IMAGES = 1
MAX_SEED = 2**32 - 1


def anlas_warnings(width: int, height: int, steps: int, n_samples: int,
                   model: str = "", account: "Optional[Account]" = None) -> List[str]:
    """Reasons a generation would cost Anlas (empty when it's free).

    Without ``account`` (not loaded yet) an Opus plan is assumed.
    """
    if account is not None and account.tier is not None and not account.is_opus:
        return ["free generations need an Opus plan (yours: %s)" % account.plan_name]
    reasons = []
    if (account is not None and "diffusion-5" in model
            and (account.v5_unavailable or account.v5_percent == 0)):
        reasons.append("your V5 Opus allowance is used up")
    if width * height > FREE_MAX_PIXELS:
        reasons.append("%d x %d is larger than 1024 x 1024" % (width, height))
    if steps > FREE_MAX_STEPS:
        reasons.append("%d steps (free up to %d)" % (steps, FREE_MAX_STEPS))
    if n_samples > FREE_MAX_IMAGES:
        reasons.append("%d images per request (free: %d)" % (n_samples, FREE_MAX_IMAGES))
    return reasons


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
    return INPAINTING_MODELS.get(model, model + "-inpainting")


def selection_mode(model: str, strength: float) -> str:
    """How a selection is generated: "infill" (inpainting) or "img2img".

    Inpainting is preferred because NovelAI then knows which part may change
    and matches it to the surroundings. V4+ inpainting accepts a strength;
    V3 inpainting only regenerates fully, so V3 uses image to image below 100%.
    """
    if strength >= 1.0 or is_v4(model):
        return "infill"
    return "img2img"


def params_version(model: str) -> int:
    """V5 models use parameter format 4, everything older format 3."""
    return 4 if "diffusion-5" in model else 3


def random_seed() -> int:
    return random.randint(0, MAX_SEED)


def fit_resolution(width: int, height: int, max_pixels: int = FREE_MAX_PIXELS,
                   multiple: int = SIZE_MULTIPLE, upscale: bool = True) -> Tuple[int, int]:
    """Generation size for an area of (width, height) canvas pixels.

    Keeps the aspect ratio as closely as possible, uses multiples of
    ``multiple`` and keeps each side within [multiple, MAX_SIDE] and the
    area within ``max_pixels``.

    With ``upscale`` (text to image), small areas are scaled up to fill the
    budget. Without it (an image is sent), areas that already fit are kept
    at their own size, only rounded to the nearest multiple: scaling a
    source image up blurs it, and NovelAI then generates blurry detail.
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    scale = math.sqrt(max_pixels / float(width * height))
    if not upscale and scale >= 1.0:
        scale = 1.0

    def snap(value: float) -> int:
        if scale == 1.0:
            value = int(round(value / multiple)) * multiple
        else:
            value = int(value // multiple) * multiple
        return max(multiple, min(MAX_SIDE, value))

    w, h = snap(width * scale), snap(height * scale)
    while w * h > max_pixels and (w > multiple or h > multiple):
        if w >= h:
            w -= multiple
        else:
            h -= multiple
    return w, h


def plan_region(canvas_w: int, canvas_h: int, sel_x: int, sel_y: int, sel_w: int,
                sel_h: int, padding_percent: int, max_pixels: int = FREE_MAX_PIXELS,
                multiple: int = SIZE_MULTIPLE) -> Tuple[int, int, int, int]:
    """Choose the canvas area (x, y, w, h) to send for a selection.

    Like the NovelAI site, the image is sent at its own resolution where
    possible: rather than scaling a small area up (which blurs the result),
    the area around the selection is widened at 1:1 until it reaches the
    generation size limit or the canvas edge. ``padding_percent`` is the
    minimum context around the selection. Only when that minimum is already
    larger than the limit does the area get scaled down.
    """
    pad = int(max(sel_w, sel_h) * padding_percent / 100)
    min_w = min(canvas_w, sel_w + 2 * pad)
    min_h = min(canvas_h, sel_h + 2 * pad)
    w, h = min_w, min_h
    if w * h < max_pixels:
        scale = math.sqrt(max_pixels / float(w * h))
        w = min(canvas_w, MAX_SIDE, max(min_w, int(w * scale)))
        h = min(canvas_h, MAX_SIDE, max(min_h, max_pixels // w))
        w = min(canvas_w, MAX_SIDE, max(min_w, max_pixels // h))  # Re-spend leftover budget.
        w = _snap_side(w, min_w, canvas_w, multiple)
        h = _snap_side(h, min_h, canvas_h, multiple)

    # Centre on the selection, then keep the area inside the canvas.
    x = int(round(sel_x + sel_w / 2.0 - w / 2.0))
    y = int(round(sel_y + sel_h / 2.0 - h / 2.0))
    x = max(0, min(canvas_w - w, x))
    y = max(0, min(canvas_h - h, y))
    return x, y, w, h


def _snap_side(value: int, minimum: int, limit: int, multiple: int) -> int:
    """Round a side down to a multiple (so it is sent 1:1) if that still
    covers ``minimum``; otherwise up, if the canvas allows; else unchanged."""
    down = (value // multiple) * multiple
    if down >= minimum and down > 0:
        return down
    up = -(-minimum // multiple) * multiple
    return up if up <= limit else value


@dataclass
class GenerationParams:
    prompt: str
    negative_prompt: str = ""  # The user's own undesired content.
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
    quality: str = presets.STANDARD  # Quality tags: standard, light or off.
    uc_preset: str = presets.DEFAULT_UC_PRESET  # Undesired Content preset name.
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
    # Quality tags and the Undesired Content preset are added here, as the
    # NovelAI site does (see presets.py); the server adds neither.
    prompt = presets.apply_quality(p.model, p.prompt.strip(), p.quality)
    negative, uc_index = presets.apply_uc(p.model, p.prompt, p.negative_prompt, p.uc_preset)
    action = p.action
    model = inpainting_model(p.model) if action == "infill" else p.model

    params = {
        "params_version": params_version(p.model),
        "width": int(p.width),
        "height": int(p.height),
        "scale": float(p.scale),
        "sampler": p.sampler,
        "steps": int(p.steps),
        "n_samples": int(p.n_samples),
        "seed": int(p.seed),
        "qualityToggle": p.quality != presets.OFF,
        "ucPreset": uc_index,
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
        params.update({
            "image": p.image,
            "mask": p.mask,
            "noise": float(p.noise),
            "extra_noise_seed": int(p.seed),
        })
        if is_v4(p.model):
            # As the NovelAI site sends it: inpaint strength below 100% goes in
            # the nested "img2img" object (the API spec: "used by inpaint");
            # at 100% the object is left out.
            params["inpaintImg2ImgStrength"] = float(p.strength)
            if p.strength < 1.0:
                params["img2img"] = {"strength": float(p.strength), "color_correct": True}

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


# ---- Account (GET /user/subscription) ---------------------------------------

TIER_NAMES = {0: "Paper", 1: "Tablet", 2: "Scroll", 3: "Opus"}
OPUS_TIER = 3


def _int(value) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass
class Account:
    """What /user/subscription says about the plan, Anlas and V5 allowance."""

    tier: Optional[int] = None
    active: bool = False
    plan_anlas: Optional[int] = None  # Refilled monthly with the subscription.
    purchased_anlas: Optional[int] = None
    # Opus's V5 allowance ("usage"): percent left (0-100+), percent bought on
    # top (0-400), seconds until the next +1%, and whether V5 is unavailable.
    v5_percent: Optional[int] = None
    v5_purchased_percent: Optional[int] = None
    v5_refill_seconds: Optional[int] = None
    v5_unavailable: bool = False

    @property
    def anlas(self) -> Optional[int]:
        if self.plan_anlas is None and self.purchased_anlas is None:
            return None
        return (self.plan_anlas or 0) + (self.purchased_anlas or 0)

    @property
    def is_opus(self) -> bool:
        return self.active and self.tier == OPUS_TIER

    @property
    def plan_name(self) -> str:
        if not self.active:
            return "No subscription"
        return TIER_NAMES.get(self.tier, "Tier %s" % self.tier)


def parse_account(body: bytes) -> Account:
    data = json.loads(body.decode("utf-8"))
    steps = data.get("trainingStepsLeft") or {}
    usage = data.get("usage") or {}
    return Account(
        tier=_int(data.get("tier")),
        active=data.get("active") is True,
        plan_anlas=_int(steps.get("fixedTrainingStepsLeft")),
        purchased_anlas=_int(steps.get("purchasedTrainingSteps")),
        v5_percent=_int(usage.get("percent")),
        v5_purchased_percent=_int(usage.get("purchasedPercent")),
        v5_refill_seconds=_int(usage.get("timeUntilNextPercent")),
        v5_unavailable=usage.get("isNegative") is True,
    )


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return "%dh %dm" % (seconds // 3600, seconds % 3600 // 60)
    if seconds >= 60:
        return "%dm" % (seconds // 60)
    return "%ds" % seconds


def v5_allowance_text(account: Account) -> str:
    """e.g. "V5 99%", or "" when the account has no V5 allowance info."""
    if account.v5_unavailable:
        return "V5 used up"
    if account.v5_percent is None:
        return ""
    text = "V5 %d%%" % account.v5_percent
    if account.v5_purchased_percent:
        text += " +%d%%" % account.v5_purchased_percent
    return text


def account_summary(account: Account) -> str:
    """One line for the panel, e.g. "Opus · 15,441 Anlas · V5 99%"."""
    parts = [account.plan_name]
    if account.anlas is not None:
        parts.append("{:,} Anlas".format(account.anlas))
    if account.is_opus:
        v5 = v5_allowance_text(account)
        if v5:
            parts.append(v5)
    return " · ".join(parts)


def account_details(account: Account) -> str:
    """Multi-line tooltip text."""
    lines = ["Plan: %s" % account.plan_name]
    if account.anlas is not None:
        lines.append("Anlas: {:,} ({:,} from the plan, {:,} purchased)".format(
            account.anlas, account.plan_anlas or 0, account.purchased_anlas or 0))
    if account.is_opus:
        if account.v5_unavailable:
            lines.append("V5 Opus allowance: used up")
        elif account.v5_percent is not None:
            line = "V5 Opus allowance: %d%% left" % account.v5_percent
            if account.v5_purchased_percent:
                line += " (+%d%% purchased)" % account.v5_purchased_percent
            lines.append(line)
            if account.v5_refill_seconds:
                lines.append("Next +1%% in %s" % format_duration(account.v5_refill_seconds))
    return "\n".join(lines)


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


# ---- Streaming generation (POST /ai/generate-image-stream) -----------------
#
# With parameters.stream = "sse" the server sends Server-Sent Events:
#
#   event: intermediate
#   data: {"event_type": "intermediate", "samp_ix": 0, "step_ix": 3,
#          "gen_id": ..., "sigma": 6.7, "image": "<base64 JPEG, half size>"}
#
#   event: final
#   data: {"event_type": "final", "samp_ix": 0, "gen_id": ..., "image": "<base64 PNG>"}
#
# plus "error" ({"code", "message"}) and "retry" events. V3 models don't stream.


def supports_streaming(model: str) -> bool:
    return is_v4(model)


@dataclass
class StreamEvent:
    kind: str  # "intermediate", "final", "error" or "retry"
    sample: int = 0  # Index of the image in the batch.
    step: int = 0
    image: bytes = b""
    message: str = ""


class SSEParser:
    """Incremental Server-Sent Events parser: feed bytes, get StreamEvents."""

    def __init__(self):
        self._buffer = b""

    def feed(self, chunk: bytes) -> List[StreamEvent]:
        self._buffer += chunk.replace(b"\r\n", b"\n")
        events = []
        while b"\n\n" in self._buffer:
            block, self._buffer = self._buffer.split(b"\n\n", 1)
            event = self._parse_block(block.decode("utf-8", "replace"))
            if event is not None:
                events.append(event)
        return events

    def finish(self) -> List[StreamEvent]:
        """Parse whatever is left when the stream ends without a blank line."""
        rest, self._buffer = self._buffer, b""
        event = self._parse_block(rest.decode("utf-8", "replace")) if rest.strip() else None
        return [event] if event is not None else []

    @staticmethod
    def _parse_block(block: str) -> Optional[StreamEvent]:
        name, data = "", []
        for line in block.split("\n"):
            if line.startswith(":"):
                continue  # Comment / keep-alive.
            field_name, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field_name == "event":
                name = value
            elif field_name == "data":
                data.append(value)
        if not data:
            return None
        try:
            obj = json.loads("\n".join(data))
        except ValueError:
            return None
        kind = obj.get("event_type") or name
        image = obj.get("image")
        return StreamEvent(
            kind=kind,
            sample=int(obj.get("samp_ix") or 0),
            step=int(obj.get("step_ix") or 0),
            image=base64.b64decode(image) if isinstance(image, str) else b"",
            message=str(obj.get("message") or obj.get("code") or ""),
        )
