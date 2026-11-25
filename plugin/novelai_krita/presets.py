"""Quality tags and Undesired Content presets, added the way the NovelAI site does.

The server adds neither; the site adds both to the request text, hidden from
the prompt boxes. The tag text and the way it is combined with what the user
typed are taken from the site's own script (the tables and functions next to
its "ucPresetId" and "qualityPresetId"), which differs in places from
NovelAI's docs:
  https://docs.novelai.net/en/image/qualitytags
  https://docs.novelai.net/en/image/undesiredcontent
Nothing the user typed is trimmed, as the site doesn't. Plain Python, no Qt.
"""

import re
import unicodedata
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


def has_captions(model: str) -> bool:
    """Whether the model is V4 or newer: a base prompt with character prompts."""
    return (model_key(model) or "3").split("-")[0] in ("4", "5")


# ---- Quality tags ----------------------------------------------------------

STANDARD, LIGHT, OFF = "standard", "light", "off"

_V5_QUALITY = {
    STANDARD: ", very aesthetic, masterpiece, no text",
    LIGHT: ", very aesthetic, amazing quality, no text",
}
QUALITY_TAGS = {
    "5-full": _V5_QUALITY,
    "5-curated": _V5_QUALITY,
    "4-5-full": {STANDARD: ", very aesthetic, masterpiece, no text"},
    "4-5-curated": {
        STANDARD: ", very aesthetic, masterpiece, no text, -0.8::feet::, rating:general"},
    "4-full": {STANDARD: ", no text, best quality, very aesthetic, absurdres"},
    "4-curated": {STANDARD: ", rating:general, best quality, very aesthetic, absurdres"},
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


# What JavaScript counts as white space (its \s and trim()), which isn't quite
# what Python does.
_SPACE = ("\t\n\x0b\x0c\r \xa0\u1680" + "".join(map(chr, range(0x2000, 0x200b)))
          + "\u2028\u2029\u202f\u205f\u3000\ufeff")
# Where the text to draw starts ("Text: ..."): quality tags go in front of it.
_TEXT = re.compile(r"(?:^|[%s,.:\[\]{}\u3001\u3002])text:(?!:)" % _SPACE, re.IGNORECASE)
_WEIGHT = re.compile(r":[0-9.]+\Z")  # At the end of a "|" part, on V3.
_PIPE, _PIPES = "\ue000", "\ue001"  # Stand-ins while splitting.


def split_parts(text: str) -> List[str]:
    """``text`` split at "|" as the site does it: into six parts at most, and
    not at "||" or between a pair of them."""
    kept = _PIPES.join(piece.replace("|", _PIPE) if index % 2 else piece
                       for index, piece in enumerate(text.split("||")))
    parts = kept.split("|")
    parts = parts[:5] + (["|".join(parts[5:])] if len(parts) > 5 else [])
    return [part.replace(_PIPE, "|").replace(_PIPES, "||") for part in parts]


def _each_tagged_part(model: str, prompt: str, change) -> Optional[str]:
    """``prompt`` with ``change`` applied to each piece that carries quality
    tags, or None if ``change`` gives None for one of them.

    V4 and newer: the first "|" part, up to "Text:". V3: every "|" part,
    before a weight (":0.5") at its end.
    """
    if has_captions(model):
        parts = split_parts(prompt)
        pieces = _TEXT.split(parts[0])
        found = _TEXT.search(parts[0])
        pieces[0] = change(pieces[0])
        if pieces[0] is None:
            return None
        parts[0] = (found.group(0) if found else "").join(pieces)
        return "|".join(parts)
    parts = []
    for part in prompt.split("|"):
        weight = _WEIGHT.search(part)
        weight = weight.group(0) if weight else ""
        changed = change(part[:len(part) - len(weight)])
        if changed is None:
            return None
        parts.append(changed + weight)
    return "|".join(parts)


def apply_quality(model: str, prompt: str, level: str) -> str:
    """The prompt with the model's quality tags added, where the site adds them."""
    tags = QUALITY_TAGS.get(model_key(model), {})
    if level == OFF or not tags:
        return prompt
    suffix = tags.get(level, tags[STANDARD])[2:]
    return _each_tagged_part(
        model, prompt, lambda text: text + ", " + suffix if text else suffix)


def remove_quality(model: str, prompt: str, level: str) -> Optional[str]:
    """The opposite of apply_quality, or None if the tags aren't there."""
    tags = QUALITY_TAGS.get(model_key(model), {})
    if level not in tags:
        return None
    suffix = tags[level][2:]

    def without(text):
        if text == suffix:
            return ""
        return text[:-len(suffix) - 2] if text.endswith(", " + suffix) else None

    return _each_tagged_part(model, prompt, without)


# ---- Text to draw (V5) ---------------------------------------------------------
# V5 draws the text that follows "Text:" in the prompt. The site adds that on
# its own: what the prompt and the character prompts have in quotes goes at
# the end of the prompt, after the quality tags, as "teXt: ..." (written so,
# to tell it apart on import). Not if a prompt has "Text:" already.

_AUTO_TEXT = re.compile(r"(?:^|[%s,.:\[\]{}、。])teXt:(?!:)" % _SPACE)
_TRAILING = re.compile(r"[%s,]+\Z" % _SPACE)
# A quote, and what ends it. A ' only starts one after a space, comma or full
# stop (or at the start), and a ' or ’ doesn't end one before a letter or digit,
# so that "it's" and "don’t" aren't taken for quotes.
_QUOTES = {'"': '"', "“": "”", "「": "」", "'": "'", "‘": "’"}
_APOSTROPHES = ("'", "’")
# Japanese and Chinese: text mostly in these is collected in the reverse order.
_CJK = re.compile("[　-〿぀-ゟ゠-ヿ＀-ﾟ"
                  "一-龯㐀-䶿]")


def has_auto_text(model: str) -> bool:
    """Whether the site adds the quoted text for this model to draw."""
    return model_key(model) in ("5-full", "5-curated")


def _letter_or_digit(unit: int) -> bool:
    # One UTF-16 unit, as the site's script sees it: half of a pair is neither.
    return unicodedata.category(chr(unit))[0] in "LN"


def _quoted(text: str) -> List[str]:
    """What ``text`` has in quotes, in order, each trimmed; empty ones left out."""
    # The site's script counts in UTF-16 units; so does this, to agree on
    # what comes before and after a quote mark.
    units = memoryview(text.encode("utf-16-le")).cast("H")
    found, at = [], 0
    while at < len(units):
        mark = chr(units[at])
        end_mark = _QUOTES.get(mark)
        if end_mark is None or (mark == "'" and at > 0 and chr(units[at - 1]) not in _SPACE + ",."):
            at += 1
            continue
        end = at + 1
        while end < len(units) and (chr(units[end]) != end_mark or (
                end_mark in _APOSTROPHES and end + 1 < len(units)
                and _letter_or_digit(units[end + 1]))):
            end += 1
        if end >= len(units):
            at += 1
            continue
        piece = units[at + 1:end].tobytes().decode("utf-16-le").strip(_SPACE)
        if piece:
            found.append(piece)
        at = end + 1
    return found


def _in_rows(characters):
    """Characters sorted by height, split into rows where they're far apart."""
    if len(characters) <= 1:
        return [characters]
    cut, widest = 1, -1.0
    for index in range(1, len(characters)):
        gap = characters[index][2] - characters[index - 1][2]
        if gap > widest:
            cut, widest = index, gap
    if characters[-1][2] - characters[0][2] <= 0.15 and widest <= 0.1:
        return [characters]
    return _in_rows(characters[:cut]) + _in_rows(characters[cut:])


def _texts(prompt: str, characters, use_coords: bool) -> List[str]:
    """The quoted text of the prompt, then of each character: in the order
    they're listed, or with positions given, row by row from the top."""
    characters = [c for c in characters if c[0]]
    if use_coords:
        rows = _in_rows(sorted(characters, key=lambda c: c[2]))
        characters = [c for row in rows for c in sorted(row, key=lambda c: c[1])]
    found = [_quoted(prompt)] + [_quoted(c[0]) for c in characters]
    joined = "".join(piece for pieces in found for piece in pieces)
    cjk = len(_CJK.findall(joined))
    if cjk and cjk / (len(joined.encode("utf-16-le")) // 2) > 0.3:
        for pieces in found:
            pieces.reverse()
    return [piece for pieces in found for piece in pieces]


def add_auto_text(prompt: str, characters=(), use_coords: bool = False) -> str:
    """``prompt`` (quality tags included) with the quoted text added for V5
    to draw, as the site adds it. ``characters``: (prompt, x, y) of each
    character that's switched on; ``use_coords``: whether positions are given."""
    characters = [c for c in characters if c[0]]
    if _TEXT.search(prompt) or any(_TEXT.search(c[0]) for c in characters):
        return prompt
    parts = split_parts(prompt)
    texts = _texts(parts[0], characters, use_coords)
    if not texts:
        return prompt
    added = "teXt: " + "\n\n".join(texts)
    first = _TRAILING.sub("", parts[0])
    parts[0] = first + ", " + added if first else added
    return "|".join(parts)


def remove_auto_text(prompt: str, characters=(), use_coords: bool = False) -> str:
    """The opposite of add_auto_text: where a recorded prompt has the text the
    site would have added, it's taken out, as the site does on import."""
    parts = split_parts(prompt)
    for index, part in enumerate(parts):
        found = _AUTO_TEXT.search(part)
        if found is None:
            continue
        before = part[:found.start()]
        if part[found.end():].strip(_SPACE) == "\n\n".join(
                _texts(before, characters, use_coords)):
            parts[index] = _TRAILING.sub("", before)
    return "|".join(parts)


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
    "digits, artistic error, username, scan, [abstract]")

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
                  "views, logo, too many watermarks, white blank page, blank page"),
        ("Light", "blurry, lowres, error, worst quality, bad quality, jpeg artifacts, very "
                  "displeasing, white blank page, blank page"),
    ],
    "4-curated": [
        ("Heavy", "blurry, lowres, error, film grain, scan artifacts, worst quality, bad "
                  "quality, jpeg artifacts, very displeasing, chromatic aberration, logo, "
                  "dated, signature, multiple views, gigantic breasts, white blank page, "
                  "blank page"),
        ("Light", "blurry, lowres, error, worst quality, bad quality, jpeg artifacts, very "
                  "displeasing, logo, dated, signature, white blank page, blank page"),
    ],
    "3": [
        ("Heavy", _HEAVY_V3),
        ("Light", "lowres, jpeg artifacts, worst quality, watermark, blurry, very displeasing"),
        ("Human Focus", _HEAVY_V3 + ", bad anatomy, bad hands, @_@, mismatched pupils, "
                                    "heart-shaped pupils, glowing eyes"),
    ],
    "furry-3": [
        ("Heavy", "{{worst quality}}, [displeasing], {unusual pupils}, guide lines, "
                  "{{unfinished}}, {bad}, url, artist name, {{tall image}}, mosaic, {sketch "
                  "page}, comic panel, impact (font), [dated], {logo}, ych, {what}, {where is "
                  "your god now}, {distorted text}, repeated text, {floating head}, {1994}, "
                  "{widescreen}, absolutely everyone, sequence, {compression artifacts}, hard "
                  "translated, {cropped}, {commissioner name}, unknown text, high contrast"),
        ("Light", "{worst quality}, guide lines, unfinished, bad, url, tall image, widescreen, "
                  "compression artifacts, unknown text"),
    ],
}
# What the site sends for "None" when the undesired content box is empty.
UC_NONE = {"3": "lowres", "furry-3": "lowres"}
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


def apply_uc(model: str, prompt: str, undesired: str, preset: str) -> Tuple[str, int]:
    """The undesired content to send, and the site's "ucPreset" number.

    Preset first, then ", " and the user's own undesired content (on V4 and
    newer, its first "|" part). Like the site, for non-curated models "nsfw"
    goes in front of a preset unless the prompt has it. With the preset
    "None" nothing is added, except "lowres" to an empty box on V3, and the
    site doesn't look at whether the undesired content has "nsfw" already.
    """
    key = model_key(model)
    presets = UC_PRESETS.get(key, [])
    names = [name for name, _ in presets]
    index = names.index(preset) if preset in names else len(names)
    if key is None:
        return undesired, index
    chosen = preset in names
    text = dict(presets)[preset] if chosen else ""
    if has_captions(model):
        parts = undesired.split("|")
        if chosen:
            parts[0] = text + ", " + parts[0] if parts[0] else text
        negative = "|".join(parts)
    elif undesired:
        negative = text + ", " + undesired if chosen else undesired
    else:
        negative = text if chosen else UC_NONE.get(key, "")
    if chosen and not is_curated(model) and "nsfw" not in prompt.lower():
        negative = "nsfw, " + negative
    return negative, index


def remove_uc(model: str, negative: str) -> Tuple[str, str]:
    """The opposite of apply_uc: (the user's own undesired content, preset).

    "nsfw" is only taken out together with a preset and for a model that
    isn't Curated, so it is never lost: generating adds it back.
    """
    key = model_key(model)
    # Longest first: a longer preset can start with a shorter one (Human
    # Focus is Heavy plus more tags).
    options = sorted(UC_PRESETS.get(key, []), key=lambda item: -len(item[1]))
    for lead in ("",) if is_curated(model) else ("nsfw, ", ""):
        if not negative.startswith(lead):
            continue
        rest = negative[len(lead):]
        for name, text in options:
            if rest == text:
                return "", name
            if rest.startswith(text + ", "):
                return rest[len(text) + 2:], name
            if rest.startswith(text + "|") and has_captions(model):
                return rest[len(text):], name
    if negative and negative == UC_NONE.get(key):
        return "", NONE
    return negative, NONE
