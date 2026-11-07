"""Finding the tag under the text cursor in a NovelAI prompt (plain Python).

Prompts are comma-separated tags that may carry emphasis ({tag}, [tag]),
V4 weights (1.2::tag::) or "|" separators. Only the tag being typed is sent
to the suggestion endpoint, without any of that syntax.
"""

import re
from dataclasses import dataclass
from typing import Optional

DELIMITERS = ",\n|"
_CLOSERS = "{}[]()"
# Leading whitespace, emphasis openers and an optional "1.2::" weight.
_PREFIX = re.compile(r"[\s{\[(]*(?:-?\d+(?:\.\d+)?::)?[\s{\[(]*")


@dataclass(frozen=True)
class TagContext:
    start: int  # Text position where the tag starts.
    end: int  # Text position where the tag ends (replacement range end).
    query: str  # Normalised text to look up.
    lang: str  # "en" or "jp".


def is_japanese(text: str) -> bool:
    return any("぀" <= c <= "ヿ" or "一" <= c <= "鿿"
               or "ｦ" <= c <= "ﾟ" for c in text)


def tag_at_cursor(text: str, cursor: int, min_length: int = 2) -> Optional[TagContext]:
    """Return the tag being typed at ``cursor``, or None if there isn't one."""
    if cursor < 0 or cursor > len(text):
        return None
    begin = cursor
    while begin > 0 and text[begin - 1] not in DELIMITERS:
        begin -= 1
    start = _PREFIX.match(text, begin, cursor).end()  # Absolute position.
    fragment = text[start:cursor]
    # Cursor is past the end of a weighted/emphasised tag, e.g. "{tag}|".
    if "::" in fragment or any(c in fragment for c in _CLOSERS):
        return None

    end = cursor
    while (end < len(text) and text[end] not in DELIMITERS
           and text[end] not in _CLOSERS and not text.startswith("::", end)):
        end += 1
    while end > cursor and text[end - 1].isspace():
        end -= 1

    lang = "jp" if is_japanese(fragment) else "en"
    query = " ".join(fragment.replace("_", " ").split()).lower()
    if len(query) < (1 if lang == "jp" else min_length):
        return None
    return TagContext(start, end, query, lang)


def completion_text(text: str, context: TagContext, tag: str) -> str:
    """Text to put in place of ``text[context.start:context.end]``.

    Adds ", " when the tag is the last thing in the prompt, so the next tag
    can be typed straight away.
    """
    if text[context.end:].strip() == "":
        return tag + ", "
    return tag
