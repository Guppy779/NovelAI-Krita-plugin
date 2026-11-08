"""Reading the generation info NovelAI embeds in its images, for importing it.

NovelAI stores each image's request twice:

- In PNG text chunks. "Comment" holds the parameters as JSON, "Source" the
  model (e.g. "NovelAI Diffusion V5 0ADF9AB7"), "Description" the prompt.
- Hidden in the alpha channel, in the lowest bit of each pixel's alpha, for
  programs that drop text chunks but keep the pixels. The bits run down each
  column, then to the next column, most significant bit first: the magic
  "stealth_pngcomp" (or "stealth_pnginfo", uncompressed), a 32-bit length in
  bits, then the same fields as gzipped JSON.

The prompt NovelAI records is the one it sent: quality tags at the end, the
Undesired Content preset (and "nsfw") at the start of the undesired content.
``import_state`` can move those back into their boxes. Plain Python, no Qt.
"""

import json
import struct
import zlib
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from . import api, presets

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_TEXT = 4 * 1024 * 1024  # Ignore metadata that unpacks to more than this.

# ---- Reading the fields -----------------------------------------------------


def _decompress(data: bytes, wbits: int = zlib.MAX_WBITS) -> bytes:
    decompressor = zlib.decompressobj(wbits)
    out = decompressor.decompress(data, MAX_TEXT)
    if decompressor.unconsumed_tail:
        raise ValueError("metadata too large")
    return out


def _text(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("latin-1")


def png_text(data: bytes) -> Dict[str, str]:
    """The text chunks (tEXt, zTXt, iTXt) of a PNG file, or {} for other files."""
    if not data.startswith(PNG_SIGNATURE):
        return {}
    fields = {}
    pos = len(PNG_SIGNATURE)
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length  # Length, type, data, CRC.
        if kind == b"IEND":
            break
        key, _, rest = body.partition(b"\0")
        try:
            if kind == b"tEXt":
                value = rest
            elif kind == b"zTXt":  # Compression method byte, then zlib data.
                value = _decompress(rest[1:])
            elif kind == b"iTXt":
                # Compressed flag, method, language\0, translated keyword\0, text.
                compressed = rest[:1] == b"\x01"
                _language, _, rest = rest[2:].partition(b"\0")
                _translated, _, value = rest.partition(b"\0")
                if compressed:
                    value = _decompress(value)
            else:
                continue
        except (zlib.error, ValueError):
            continue
        fields[key.decode("latin-1")] = _text(value)
    return fields


STEALTH_MAGIC = {b"stealth_pngcomp": True, b"stealth_pnginfo": False}  # -> compressed
_MAGIC_BITS = 15 * 8
_HEADER_BITS = _MAGIC_BITS + 32


def stealth_text(alpha: bytes, width: int, height: int, stride: int = 0) -> Dict[str, str]:
    """The fields hidden in an image's alpha channel, or {} if there are none.

    ``alpha`` holds one byte per pixel, in rows of ``stride`` bytes (default:
    ``width``).
    """
    stride = stride or width
    total = width * height
    if total < _HEADER_BITS or len(alpha) < stride * (height - 1) + width:
        return {}

    def read(start: int, count: int) -> bytes:
        out = bytearray(count)
        for i in range(count):
            value = 0
            for bit in range(start + 8 * i, start + 8 * i + 8):
                x, y = divmod(bit, height)  # Down each column first.
                value = (value << 1) | (alpha[y * stride + x] & 1)
            out[i] = value
        return bytes(out)

    compressed = STEALTH_MAGIC.get(read(0, 15))
    if compressed is None:
        return {}
    length = int.from_bytes(read(_MAGIC_BITS, 4), "big")
    if length <= 0 or length % 8 or _HEADER_BITS + length > total or length > 8 * MAX_TEXT:
        return {}
    payload = read(_HEADER_BITS, length // 8)
    try:
        if compressed:
            payload = _decompress(payload, 16 + zlib.MAX_WBITS)  # gzip
        data = json.loads(payload.decode("utf-8"))
    except (zlib.error, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): value if isinstance(value, str) else json.dumps(value)
            for key, value in data.items()}


# ---- Understanding them -----------------------------------------------------


@dataclass
class ImageInfo:
    """What a NovelAI image says about how it was made."""

    prompt: str = ""
    negative: str = ""
    characters: List[api.CharacterPrompt] = field(default_factory=list)
    use_coords: Optional[bool] = None  # Characters placed at their positions.
    # Generation settings, by their doc_state names: steps, scale,
    # cfg_rescale, sampler and noise_schedule (only those it has valid values for).
    settings: Dict[str, object] = field(default_factory=dict)
    seed: Optional[int] = None
    model: str = ""  # e.g. "NovelAI Diffusion V5", for showing.
    # "PromptGenerateRequest" for text to image, "Img2ImgRequest" for image
    # to image, ...; "" if the image doesn't say.
    request_type: str = ""

    @property
    def made_from_image(self) -> bool:
        """True if the image was made from another image (image to image,
        inpainting, ...): its prompt and settings alone don't recreate it."""
        return self.request_type not in ("", TEXT_TO_IMAGE)

    @property
    def has_characters(self) -> bool:
        return bool(self.characters)  # Empty ones are left out when parsing.


TEXT_TO_IMAGE = "PromptGenerateRequest"


def _number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if low <= value <= high else None


def _caption(value) -> Tuple[str, list]:
    """(base caption, character captions) of a v4_prompt / v4_negative_prompt."""
    caption = value.get("caption") if isinstance(value, dict) else None
    if not isinstance(caption, dict):
        return "", []
    base = caption.get("base_caption")
    characters = caption.get("char_captions")
    return (base if isinstance(base, str) else "",
            characters if isinstance(characters, list) else [])


def _character_text(item) -> str:
    text = item.get("char_caption") if isinstance(item, dict) else None
    return text if isinstance(text, str) else ""


def _character_center(item) -> Tuple[float, float]:
    centers = item.get("centers") if isinstance(item, dict) else None
    center = centers[0] if isinstance(centers, list) and centers else None
    if not isinstance(center, dict):
        return 0.5, 0.5
    x, y = _number(center.get("x"), 0.0, 1.0), _number(center.get("y"), 0.0, 1.0)
    return (0.5 if x is None else float(x)), (0.5 if y is None else float(y))


def parse(fields: Dict[str, str]) -> Optional[ImageInfo]:
    """The generation info in an image's fields, or None if it has none."""
    try:
        comment = json.loads(fields.get("Comment") or "")
    except ValueError:
        comment = None
    description = fields.get("Description") or ""
    model = fields.get("Source") or ""
    if not isinstance(comment, dict):
        # Without parameters only the prompt can be imported, and only from
        # an image that says it comes from NovelAI.
        if fields.get("Software") == "NovelAI" and description:
            return ImageInfo(prompt=description, model=model)
        return None

    info = ImageInfo(model=comment.get("model_name") if isinstance(
        comment.get("model_name"), str) else model)
    # V4 and newer: the prompt structure; V3: plain "prompt" and "uc".
    prompt, char_prompts = _caption(comment.get("v4_prompt"))
    negative, char_negatives = _caption(comment.get("v4_negative_prompt"))
    if not prompt and isinstance(comment.get("prompt"), str):
        prompt = comment["prompt"]
    if not negative and isinstance(comment.get("uc"), str):
        negative = comment["uc"]
    info.prompt = prompt or description
    info.negative = negative
    for index, item in enumerate(char_prompts):
        other = char_negatives[index] if index < len(char_negatives) else None
        character = api.CharacterPrompt(_character_text(item), _character_text(other))
        character.x, character.y = _character_center(item)
        if character.prompt.strip() or character.negative.strip():
            info.characters.append(character)
    v4_prompt = comment.get("v4_prompt")
    if isinstance(v4_prompt, dict) and isinstance(v4_prompt.get("use_coords"), bool):
        info.use_coords = v4_prompt["use_coords"]

    steps = comment.get("steps")
    if isinstance(steps, int) and not isinstance(steps, bool) and 1 <= steps <= 50:
        info.settings["steps"] = steps
    scale = _number(comment.get("scale"), 0.0, 10.0)
    if scale is not None:
        info.settings["scale"] = float(scale)
    rescale = _number(comment.get("cfg_rescale"), 0.0, 1.0)
    if rescale is not None:
        info.settings["cfg_rescale"] = float(rescale)
    if comment.get("sampler") in dict(api.SAMPLERS):
        info.settings["sampler"] = comment["sampler"]
    if comment.get("noise_schedule") in api.NOISE_SCHEDULES:
        info.settings["noise_schedule"] = comment["noise_schedule"]
    seed = comment.get("seed")
    if isinstance(seed, int) and not isinstance(seed, bool) and 0 <= seed <= api.MAX_SEED:
        info.seed = seed
    if isinstance(comment.get("request_type"), str):
        info.request_type = comment["request_type"]
    return info


# ---- Separating what NovelAI added -------------------------------------------

# Every model's quality tags and presets, longest first: a longer text can
# start with a shorter one (Human Focus is Heavy plus more tags).
_QUALITY = sorted({(text, level) for tags in presets.QUALITY_TAGS.values()
                   for level, text in tags.items()}, key=lambda item: -len(item[0]))
_UC_PRESETS = sorted({(text, name) for options in presets.UC_PRESETS.values()
                      for name, text in options}, key=lambda item: -len(item[0]))


def split_quality(prompt: str) -> Tuple[str, str]:
    """(prompt without quality tags, quality level) of a recorded prompt."""
    for text, level in _QUALITY:
        if prompt.endswith(text):
            return prompt[:-len(text)].rstrip(), level
    return prompt, presets.OFF


def split_uc(negative: str) -> Tuple[str, str]:
    """(the user's own undesired content, preset name) of a recorded one.

    The site writes "nsfw, " (for models that aren't Curated), then the
    preset, then the user's own text. "nsfw" is only removed together with
    a preset, so it is never lost: generating with a model that isn't
    Curated adds it back, as the site does.
    """
    text = negative.strip()
    for lead in ("nsfw, ", ""):
        if not text.startswith(lead):
            continue
        rest = text[len(lead):]
        for preset, name in _UC_PRESETS:
            if rest == preset:
                return "", name
            if rest.startswith(preset):
                tail = rest[len(preset):]
                separator = " " if preset.endswith(",") else ", "
                if tail.startswith(separator):
                    return tail[len(separator):].strip(), name
    return text, presets.NONE


PROMPT, NEGATIVE, CHARACTERS, SETTINGS, SEED = (
    "prompt", "negative", "characters", "settings", "seed")
PARTS = (PROMPT, NEGATIVE, CHARACTERS, SETTINGS, SEED)


def import_state(info: ImageInfo, state: dict, parts, clean: bool = True,
                 append: bool = False) -> dict:
    """The panel state (see doc_state.FIELDS) after importing ``parts`` of ``info``.

    ``clean`` moves NovelAI's quality tags and Undesired Content preset out of
    the text into their boxes; otherwise the text is kept as recorded and the
    boxes are turned off. Either way the request stays the same.
    ``append`` adds the image's characters after the current ones instead of
    replacing them.
    """
    state = dict(state)
    if PROMPT in parts:
        prompt, quality = split_quality(info.prompt) if clean else (info.prompt, presets.OFF)
        state["prompt"], state["quality_tags"] = prompt, quality
    if NEGATIVE in parts:
        negative, preset = split_uc(info.negative) if clean else (info.negative, presets.NONE)
        state["negative_prompt"], state["uc_preset"] = negative, preset
    if CHARACTERS in parts:
        imported = [asdict(character) for character in info.characters]
        state["characters"] = (list(state.get("characters") or []) + imported
                               if append else imported)
        if info.use_coords or (info.use_coords is not None and not append):
            state["character_positions"] = info.use_coords
    if SETTINGS in parts:
        state.update(info.settings)
    if SEED in parts and info.seed is not None:
        state["seed"], state["random_seed"] = str(info.seed), False
    return state
