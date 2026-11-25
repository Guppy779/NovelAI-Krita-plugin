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


def free_on_plan(account: "Optional[Account]") -> bool:
    """Whether the plan generates for free within the Opus limits. An account
    that isn't loaded yet, or whose plan can't be read, is taken to be Opus."""
    return account is None or account.tier is None or account.is_opus


def anlas_warnings(width: int, height: int, steps: int, n_samples: int,
                   model: str = "", account: "Optional[Account]" = None,
                   references: int = 0) -> List[str]:
    """Reasons a generation would cost Anlas (empty when it's free).

    Without ``account`` (not loaded yet) an Opus plan is assumed.
    ``references``: how many precise references are sent.
    """
    if not free_on_plan(account):
        return ["free generations need an Opus plan (yours: %s)" % account.plan_name]
    reasons = []
    if (account is not None and is_v5(model)
            and (account.v5_unavailable or account.v5_percent == 0)):
        reasons.append("your V5 Opus allowance is used up")
    if width * height > FREE_MAX_PIXELS:
        reasons.append("%d x %d is larger than 1024 x 1024" % (width, height))
    if steps > FREE_MAX_STEPS:
        reasons.append("%d steps (free up to %d)" % (steps, FREE_MAX_STEPS))
    if n_samples > FREE_MAX_IMAGES:
        reasons.append("%d images per request (free: %d)" % (n_samples, FREE_MAX_IMAGES))
    if references:
        reasons.append("%d precise reference%s (%d Anlas per image)" % (
            references, "" if references == 1 else "s", references * REFERENCE_ANLAS))
    return reasons


SIZE_MULTIPLE = 64
MAX_SIDE = 2048


def is_v4(model: str) -> bool:
    """True for models that take the V4-style prompt structure (V4 and later).

    Names the plugin doesn't know (typed into the model box) are treated as
    newer models, since new NovelAI models keep the V4 structure.
    """
    return presets.model_key(model) not in ("3", "furry-3")


def is_v5(model: str) -> bool:
    return "diffusion-5" in model


def inpainting_model(model: str) -> str:
    """Map a base model to the model NovelAI uses for inpainting ("infill")."""
    if model.endswith("-inpainting"):
        return model
    return INPAINTING_MODELS.get(model, model + "-inpainting")


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


# ---- Character prompts (V4 and newer) ---------------------------------------
#
# Each character gets its own prompt and undesired content, so traits don't
# leak between characters. Positions are the character's centre, 0-1 from
# the left and top. V4 and V4.5 only accept a 5 x 5 grid (columns A-E, rows
# 1-5); V5 accepts any position.

GRID = (0.1, 0.3, 0.5, 0.7, 0.9)
GRID_COLUMNS = "ABCDE"


@dataclass
class CharacterPrompt:
    prompt: str = ""
    negative: str = ""
    x: float = 0.5
    y: float = 0.5
    enabled: bool = True


def max_characters(model: str) -> int:
    """How many characters a model takes (0: no character prompts)."""
    if not is_v4(model):
        return 0
    return 22 if free_positions(model) else 6


def snap_to_grid(value: float) -> float:
    return min(GRID, key=lambda cell: abs(cell - value))


def position_name(x: float, y: float) -> str:
    """The grid cell nearest a position, e.g. "C3" for the centre."""
    return GRID_COLUMNS[GRID.index(snap_to_grid(x))] + str(GRID.index(snap_to_grid(y)) + 1)


def free_positions(model: str) -> bool:
    """True if the model places characters anywhere (V5), not only on the grid."""
    return is_v5(model)


def character_position(model: str, x: float, y: float) -> Tuple[float, float]:
    """The position to send: exact for V5, the nearest grid cell before."""
    if free_positions(model):
        return round(x, 3), round(y, 3)
    return snap_to_grid(x), snap_to_grid(y)


# ---- Precise reference -------------------------------------------------------
# An image for NovelAI to take a character, a style or both from. What's sent
# follows the NovelAI site.
#
# V4.5 and V5 take them, differently:
#                      V4.5                       V5
#   types              the three below            also Outfit, and "Prefer same outfit"
#   Strength           0 to 1                     0 to 5
#   Fidelity           yes                        no
#   when inpainting    sent                       left out
#   the picture        fitted in one of three     cut to one of 97 sizes; stays
#                      sizes, on black            transparent where it is

REFERENCE_TYPES = [
    ("character&style", "Character & Style"),
    ("character", "Character"),
    ("style", "Style"),
]
OUTFIT_REFERENCE = "costume"
OUTFIT_REFERENCE_TYPES = REFERENCE_TYPES + [(OUTFIT_REFERENCE, "Outfit")]
DEFAULT_REFERENCE_TYPE = "character&style"
# What a type is sent as with "Prefer same outfit" ticked, and not (V5).
_OUTFIT_CHOICE = {
    "character&style": ("character&style", "character&style_anyoutfit"),
    "character": ("character_mainoutfit", "character"),
}
# V4.5: a reference is sent at one of these sizes, padded with black to fit.
REFERENCE_SIZES = ((1024, 1536), (1536, 1024), (1472, 1472))
# V5: at one of these, cut to fit. They hold about a megapixel each, with sides
# in multiples of 64: 256 x 4096, ..., 1024 x 1024, ..., 4096 x 256.
_TALL_SIZES = [(1024 * 1024 // height // 64 * 64, height) for height in range(4096, 1023, -64)]
REFERENCE_CUT_SIZES = tuple(
    _TALL_SIZES + [(height, width) for width, height in reversed(_TALL_SIZES[:-1])])
MAX_REFERENCES = 16
REFERENCE_ANLAS = 5  # Per reference and image, on top of the price, on every plan.


def supports_references(model: str) -> bool:
    """True for the models that take precise references: V4.5 and V5."""
    return presets.model_key(model) in ("4-5-full", "4-5-curated", "5-full", "5-curated")


def _v5_references(model: str) -> bool:
    return supports_references(model) and is_v5(model)


def reference_outfits(model: str) -> bool:
    """Whether the model takes outfit references and "Prefer same outfit"."""
    return _v5_references(model)


def reference_fidelity(model: str) -> bool:
    """Whether the model has the Fidelity slider."""
    return supports_references(model) and not is_v5(model)


def reference_strength_max(model: str) -> float:
    """Where the Strength slider ends."""
    return 5.0 if _v5_references(model) else 1.0


def reference_cut(model: str) -> bool:
    """Whether a reference is cut to its size (V5), not padded to it (V4.5)."""
    return _v5_references(model)


def sends_references(model: str, inpainting: bool = False) -> bool:
    """Whether references go with a request. V5 takes none when inpainting."""
    return supports_references(model) and not (inpainting and _v5_references(model))


def reference_types(model: str):
    """The types to choose from: (what's sent, the name shown)."""
    return OUTFIT_REFERENCE_TYPES if reference_outfits(model) else REFERENCE_TYPES


def has_outfit_choice(kind: str) -> bool:
    """Whether "Prefer same outfit" applies to a type."""
    return kind in _OUTFIT_CHOICE


def default_same_outfit(kind: str) -> bool:
    """How "Prefer same outfit" stands after choosing a type, as on the site."""
    return kind == DEFAULT_REFERENCE_TYPE


def reference_caption(model: str, kind: str, same_outfit: bool = True) -> Optional[str]:
    """What a reference's type is sent as; None if the model can't take it."""
    if not reference_outfits(model):
        return None if kind == OUTFIT_REFERENCE else kind
    if kind in _OUTFIT_CHOICE:
        return _OUTFIT_CHOICE[kind][0 if same_outfit else 1]
    return kind


def _round(value: float) -> int:
    return int(math.floor(value + 0.5))  # Halves go up, as the site rounds.


def cut_reference_layout(width: int, height: int):
    """How V5 is sent an image of (width, height) as a reference:
    ((sent width, sent height), (scaled width, scaled height), (left, top)).

    It's scaled to cover the size nearest its shape, and what's sent is cut
    out of the middle, from (left, top). Nothing is added around it.
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    shape = math.log(width / float(height))
    sent = REFERENCE_CUT_SIZES[0]
    for size in REFERENCE_CUT_SIZES:
        if abs(shape - math.log(size[0] / float(size[1]))) < abs(
                shape - math.log(sent[0] / float(sent[1]))):
            sent = size
    scale = max(sent[0] / float(width), sent[1] / float(height))
    scaled = max(_round(width * scale), sent[0]), max(_round(height * scale), sent[1])
    return sent, scaled, ((scaled[0] - sent[0]) // 2, (scaled[1] - sent[1]) // 2)


def reference_layout(width: int, height: int):
    """How V4.5 is sent an image of (width, height) as a reference:
    ((sent width, sent height), (image width, image height), (left, top)).

    It goes at the size nearest its shape, scaled to fit inside and centred.
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    ratio = width / float(height)
    sent = REFERENCE_SIZES[0]
    for size in REFERENCE_SIZES:
        if abs(size[0] / float(size[1]) - ratio) < abs(sent[0] / float(sent[1]) - ratio):
            sent = size
    if ratio > sent[0] / float(sent[1]):
        fitted = sent[0], max(1, _round(sent[0] / ratio))
    else:
        fitted = max(1, _round(sent[1] * ratio)), sent[1]
    return sent, fitted, (_round((sent[0] - fitted[0]) / 2.0), _round((sent[1] - fitted[1]) / 2.0))


@dataclass
class Reference:
    """A precise reference. The numbers are the site's sliders."""

    image: str  # Base64 picture, at one of the model's sizes.
    kind: str = DEFAULT_REFERENCE_TYPE
    strength: float = 1.0
    fidelity: float = 1.0  # V4.5 only.
    same_outfit: bool = True  # "Prefer same outfit" (V5; see has_outfit_choice).


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
    # Character prompts (V4 and newer). Disabled and empty ones aren't sent.
    # ``use_coords``: place them at their positions, not where the AI chooses.
    characters: List[CharacterPrompt] = field(default_factory=list)
    use_coords: bool = False
    # Precise references (V4.5 and V5): the ones to send.
    references: List[Reference] = field(default_factory=list)
    # Image to image / inpainting. ``image`` and ``mask`` are base64 PNGs of
    # exactly width x height pixels; white in the mask means "regenerate".
    # Sent masks follow NovelAI's grid of 8 x 8 pixel cells.
    image: Optional[str] = None
    mask: Optional[str] = None
    strength: float = 0.7
    noise: float = 0.0  # Image to image only (0 to 0.99, as on the site).

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
    # They're the generating model's: V5 Curated inpaints with V4.5 Curated's model.
    action = p.action
    model = inpainting_model(p.model) if action == "infill" else p.model
    prompt = presets.apply_quality(model, p.prompt, p.quality)
    if presets.has_auto_text(p.model):  # The selected model's, as on the site.
        # After the quality tags: the text V5 is to draw, from the quotes.
        prompt = presets.add_auto_text(prompt, [
            (c.prompt,) + character_position(p.model, c.x, c.y)
            for c in p.characters if c.enabled], p.use_coords)
    negative, uc_index = presets.apply_uc(model, p.prompt, p.negative_prompt, p.uc_preset)

    params = {
        "params_version": 4,
        "width": int(p.width),
        "height": int(p.height),
        "scale": float(p.scale),
        "sampler": p.sampler,
        "steps": int(p.steps),
        "n_samples": int(p.n_samples),
        "seed": int(p.seed),
        "qualityToggle": p.quality != presets.OFF,
        "ucPreset": uc_index,
        "tag_hint_qt": presets.quality_hint(p.quality),
        "tag_hint_uc_preset": presets.uc_hint(p.uc_preset),
        "dynamic_thresholding": False,
        "controlnet_strength": 1,
        "legacy": False,
        # For inpainting the result is pasted back here, as the site does.
        "add_original_image": action != "infill",
        "cfg_rescale": float(p.cfg_rescale),
        "noise_schedule": p.noise_schedule,
        "legacy_v3_extend": False,
        "negative_prompt": negative,
    }

    if is_v5(model):  # The site's default for models with transparency.
        params["straight_alpha"] = True

    if is_v4(model):
        # "native" is V3 only, and V5 always uses karras.
        if params["noise_schedule"] == "native" or is_v5(model):
            params["noise_schedule"] = "karras"
        # Characters as the NovelAI site sends them: in "characterPrompts",
        # and as character captions next to the base prompt. Quality tags and
        # the preset stay in the base prompt. Their text goes as typed, since
        # the site doesn't trim it either.
        characters = [c for c in p.characters if c.enabled and c.prompt.strip()]
        char_prompts, char_negatives, character_prompts = [], [], []
        for character in characters:
            x, y = character_position(model, character.x, character.y)
            centers = [{"x": x, "y": y}]
            char_prompts.append({"char_caption": character.prompt, "centers": centers})
            char_negatives.append({"char_caption": character.negative, "centers": centers})
            character_prompts.append({
                "prompt": character.prompt, "uc": character.negative,
                "center": {"x": x, "y": y}, "enabled": True})
        use_coords = bool(p.use_coords and characters)
        params.update({
            "use_coords": use_coords,
            "characterPrompts": character_prompts,
            "v4_prompt": {
                "caption": {"base_caption": prompt, "char_captions": char_prompts},
                "use_coords": use_coords,
                "use_order": True,
            },
            "v4_negative_prompt": {
                "caption": {"base_caption": negative, "char_captions": char_negatives},
                "legacy_uc": False,
            },
        })
    else:
        params.update({"sm": False, "sm_dyn": False})

    # The selected model decides, as on the site: V5 Curated inpaints with
    # V4.5 Curated's model, but as V5 it takes no references then.
    references = []
    if sends_references(p.model, action == "infill"):
        references = [(r, reference_caption(p.model, r.kind, r.same_outfit))
                      for r in p.references if r.image]
        references = [(r, caption) for r, caption in references if caption]
    if references:
        fidelity = reference_fidelity(p.model)
        params.update({
            "director_reference_images": [r.image for r, _ in references],
            "director_reference_descriptions": [
                {"caption": {"base_caption": caption, "char_captions": []}, "legacy_uc": False}
                for _, caption in references],
            "director_reference_information_extracted": [1 for _ in references],
            "director_reference_strength_values": [float(r.strength) for r, _ in references],
            # The site's Fidelity slider is sent the other way round. A model
            # without the slider is sent it as if at 1.
            "director_reference_secondary_strength_values": [
                round(1.0 - float(r.fidelity), 6) if fidelity else 0.0 for r, _ in references],
        })

    if p.sampler == "k_euler_ancestral" and params["noise_schedule"] != "native":
        params.update({"deliberate_euler_ancestral_bug": False, "prefer_brownian": True})

    if action == "img2img":
        params.update({
            "image": p.image,
            "strength": float(p.strength),
            "noise": float(p.noise),
            "extra_noise_seed": max(0, int(p.seed) - 1),  # As the site picks it.
        })
    elif action == "infill":
        params.update({
            "image": p.image,
            "mask": p.mask,
            # The site always sends its image to image values here, which have
            # no control when inpainting: these are their defaults. Inpainting's
            # own strength is the one below.
            "strength": 0.7,
            "noise": 0.0,
            "extra_noise_seed": max(0, int(p.seed) - 1),
        })
        if is_v4(model):
            # As the NovelAI site sends it: inpaint strength below 100% goes in
            # the nested "img2img" object (the API spec: "used by inpaint");
            # at 100% the object is left out.
            params["inpaintImg2ImgStrength"] = float(p.strength)
            if p.strength < 1.0:
                params["img2img"] = {"strength": float(p.strength), "color_correct": True}

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
    try:
        detail = json.loads(body.decode("utf-8")).get("message", "")
    except (ValueError, UnicodeDecodeError, AttributeError):
        detail = body[:200].decode("utf-8", "replace").strip() if body else ""
    detail = str(detail or "")  # The message isn't always text.
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
    """e.g. "V5 85%", or "" when the account has no V5 allowance info."""
    if account.v5_unavailable:
        return "V5 used up"
    if account.v5_percent is None:
        return ""
    text = "V5 %d%%" % account.v5_percent
    if account.v5_purchased_percent:
        text += " +%d%%" % account.v5_purchased_percent
    return text


def account_summary(account: Account) -> str:
    """One line for the panel, e.g. "Opus · 12,500 Anlas · V5 85%"."""
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
            if not isinstance(obj, dict):
                return None
            image = obj.get("image")
            return StreamEvent(
                kind=obj.get("event_type") or name,
                sample=int(obj.get("samp_ix") or 0),
                step=int(obj.get("step_ix") or 0),
                image=base64.b64decode(image) if isinstance(image, str) else b"",
                message=str(obj.get("message") or obj.get("code") or ""),
            )
        except (ValueError, TypeError):  # Bad JSON, numbers or base64: skip the event.
            return None


# ---- Director tools (POST /ai/augment-image) and upscaling (POST /ai/upscale) ----
#
# Sizes, prices and request fields follow the NovelAI site's own script (its
# Director Tools page), not the docs, which give neither.

AUGMENT_URL = IMAGE_API + "/ai/augment-image"
UPSCALE_URL = IMAGE_API + "/ai/upscale"

TOOLS = [
    ("bg-removal", "Remove BG"),
    ("lineart", "Line Art"),
    ("sketch", "Sketch"),
    ("colorize", "Colorize"),
]
COLORIZE = "colorize"
REMOVE_BG = "bg-removal"
# The pictures a tool answers with, in order, when there is more than one.
TOOL_RESULTS = {REMOVE_BG: ["Masked", "Generated", "Blend"]}
MAX_DEFRY = 5

TOOL_MAX_PIXELS = 3145728  # Larger pictures are made smaller before sending...
_TOOL_MAX_SENT = TOOL_MAX_PIXELS - 2000  # ...to at most this.
TOOL_MIN_PIXELS = 1048576  # Smaller pictures are enlarged to about this...
_TOOL_ENLARGE_BELOW = 1011712  # ...when they are below this.
_TOOL_STEPS = 28  # A tool is priced like a generation of this many steps.

UPSCALE_MAX_PIXELS = 1048576
UPSCALE_MODEL = "nai-diffusion-5-curated"
UPSCALE_BLUR = 0
# Anlas for a picture of up to UPSCALE_MAX_PIXELS. The site's price list goes
# on to 4 Anlas at three times that, but the site upscales nothing larger.
UPSCALE_PRICE = 1


def tool_name(tool: str) -> str:
    return dict(TOOLS).get(tool, tool)


def _to_area(width: int, height: int, pixels: int) -> Tuple[int, int]:
    """Scale a size to ``pixels`` of area, rounding each side down, as the site does."""
    ratio = math.sqrt(pixels / float(width * height))
    return int(math.floor(width * ratio)), int(math.floor(height * ratio))


def tool_size(width: int, height: int, max_pixels: int = _TOOL_MAX_SENT) -> Tuple[int, int]:
    """The size a picture of (width, height) is sent to a director tool at.

    The site makes a large picture smaller and enlarges a small one to about
    one megapixel, keeping its shape; sides needn't be multiples of anything.
    ``max_pixels`` lowers the upper limit (the plugin's Max generation size).
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    limit = max(TOOL_MIN_PIXELS, min(max_pixels, _TOOL_MAX_SENT))
    if width * height > limit:
        width, height = _to_area(width, height, limit)
    if width * height < _TOOL_ENLARGE_BELOW:
        width, height = _to_area(width, height, TOOL_MIN_PIXELS)
    return width, height


def tool_price(tool: str, width: int, height: int,
               account: "Optional[Account]" = None) -> Optional[int]:
    """Anlas the site shows for a tool on a picture of (width, height); 0 when
    free, None for a picture without a size.

    A tool costs what a 28-step V3 generation of that size costs, so it is
    free on Opus up to 1024 x 1024 in area. Remove BG is never free and
    costs three times that plus 5. Without ``account`` an Opus plan is assumed.
    The largest picture a tool is sent costs 60, well under the 140 Anlas at
    which the site calls a generation invalid.
    """
    if width <= 0 or height <= 0:
        return None
    if width * height > TOOL_MAX_PIXELS:
        width, height = _to_area(width, height, TOOL_MAX_PIXELS)
    if width * height < TOOL_MIN_PIXELS:
        width, height = _to_area(width, height, TOOL_MIN_PIXELS)
    pixels = width * height
    price = math.ceil(2951823174884865e-21 * pixels
                      + 5753298233447344e-22 * pixels * _TOOL_STEPS)
    price = max(price, 2)
    if tool == REMOVE_BG:
        return 3 * price + 5
    return 0 if free_on_plan(account) and pixels <= FREE_MAX_PIXELS else price


def tool_cost(tool: str, width: int, height: int,
              account: "Optional[Account]" = None) -> str:
    """The price of a tool on a picture and why it isn't free, e.g. "65 Anlas:
    Remove BG is never free"; empty when it's free."""
    price = tool_price(tool, width, height, account)
    if not price:
        return ""
    if tool == REMOVE_BG:
        reason = "%s is never free" % tool_name(tool)
    elif not free_on_plan(account):
        reason = "free tools need an Opus plan (yours: %s)" % account.plan_name
    else:
        reason = "%d x %d is larger than 1024 x 1024" % (width, height)
    return "%d Anlas: %s" % (price, reason)


def same_shape(width: int, height: int, other_width: int, other_height: int) -> bool:
    """Whether two sizes have the same proportions, give or take the rounding
    of a size to whole pixels."""
    if min(width, height, other_width, other_height) <= 0:
        return False
    a, b = width * other_height, height * other_width
    return abs(a - b) <= 0.01 * max(a, b)


def build_tool_payload(tool: str, image: str, width: int, height: int,
                       defry: int = 0, prompt: str = "") -> dict:
    """Request body for a director tool. ``image`` is a base64 PNG already at
    (width, height), which is ``tool_size`` of the picture's own size.
    ``defry`` and ``prompt`` are Colorize's and go with no other tool."""
    payload = {"req_type": tool, "use_new_shared_trial": True}
    if tool == COLORIZE:
        payload["prompt"] = prompt
        payload["defry"] = max(0, min(MAX_DEFRY, int(defry)))
    payload.update(width=int(width), height=int(height), image=image)
    return payload


def can_upscale(width: int, height: int) -> bool:
    return 0 < width * height <= UPSCALE_MAX_PIXELS


def upscale_price(width: int, height: int) -> Optional[int]:
    """Anlas the site shows for upscaling a picture; None when it is too large."""
    return UPSCALE_PRICE if can_upscale(width, height) else None


def build_upscale_payload(image: str) -> dict:
    """Request body for the upscaler. ``image`` is the base64 picture as it is;
    the site doesn't scale it first."""
    return {"image": image, "model": UPSCALE_MODEL, "declared_blur_sigma": UPSCALE_BLUR}
