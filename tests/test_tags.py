import json

import pytest

from novelai_krita import api
from novelai_krita.tags import TagContext, completion_text, is_japanese, tag_at_cursor


def at_end(text):
    return tag_at_cursor(text, len(text))


@pytest.mark.parametrize("text, query", [
    ("silver hai", "silver hai"),
    ("1girl, silver hai", "silver hai"),
    ("1girl,silver hai", "silver hai"),
    ("1girl, {{silver hai", "silver hai"),
    ("1girl, [silver hai", "silver hai"),
    ("1girl, 1.2::silver hai", "silver hai"),
    ("1girl, -0.5::{silver hai", "silver hai"),
    ("1girl\nsilver hai", "silver hai"),
    ("girl | silver hai", "silver hai"),
    ("Hu_Tao", "hu tao"),
    ("1gi", "1gi"),
    ("rating:gen", "rating:gen"),
    ("silver   hai", "silver hai"),
])
def test_query_strips_prompt_syntax(text, query):
    context = at_end(text)
    assert context is not None and context.query == query and context.lang == "en"


@pytest.mark.parametrize("text", [
    "", "1girl, ", "1girl, s", "{silver hair}", "1.2::silver hair::", "1girl,",
    "1.2::",
])
def test_no_query(text):
    assert at_end(text) is None


def test_replacement_range_covers_whole_tag_under_cursor():
    text = "1girl, {silver ha}, smile"
    cursor = text.index("ha") + 2
    context = tag_at_cursor(text, cursor)
    assert text[context.start:context.end] == "silver ha"
    assert context.query == "silver ha"

    text = "1girl, silver hxx  , smile"
    context = tag_at_cursor(text, text.index("hxx") + 1)  # Cursor mid-word.
    assert text[context.start:context.end] == "silver hxx"
    assert context.query == "silver h"

    text = "1.2::silver ha::, smile"
    context = tag_at_cursor(text, text.index("ha") + 2)
    assert text[context.start:context.end] == "silver ha"


def test_japanese_queries():
    assert is_japanese("銀髪") and is_japanese("ぎん") and not is_japanese("silver")
    context = at_end("1girl, 銀")  # A single kanji is enough.
    assert context.query == "銀" and context.lang == "jp"


def test_completion_text():
    text = "1girl, silver hai"
    context = at_end(text)
    assert completion_text(text, context, "silver hair") == "silver hair, "
    text = "silver hai, smile"
    context = tag_at_cursor(text, 10)
    assert completion_text(text, context, "silver hair") == "silver hair"
    new = text[:context.start] + "silver hair" + text[context.end:]
    assert new == "silver hair, smile"


def test_out_of_range_cursor():
    assert tag_at_cursor("abc", 10) is None
    assert isinstance(tag_at_cursor("abc", 3), TagContext)


def test_suggest_url():
    url = api.suggest_tags_url("nai-diffusion-4-5-full", "silver hai")
    assert url.endswith("suggest-tags?model=nai-diffusion-4-5-full&prompt=silver+hai")
    assert "lang=jp" in api.suggest_tags_url("m", "銀", "jp")


def test_parse_english_suggestions():
    body = json.dumps({"tags": [
        {"tag": "silver hair", "count": 10000, "confidence": 0.0},
        {"tag": "silver crow", "count": 4500, "confidence": 0.67},
    ]}).encode()
    results = api.parse_tag_suggestions(body)
    assert [(r.tag, r.label, r.count, r.exact) for r in results] == [
        ("silver hair", "silver hair", 10000, True),
        ("silver crow", "silver crow", 4500, False),
    ]
    assert api.parse_tag_suggestions(b'{"tags": null}') == []


def test_parse_japanese_suggestions():
    body = json.dumps([{"jp_tag": "銀髪", "en_tag": "grey hair", "power": 10000}]).encode()
    (result,) = api.parse_tag_suggestions(body)
    assert result.tag == "grey hair" and result.label == "銀髪 → grey hair"


def test_format_count():
    assert api.format_count(10000) == "10k+"
    assert api.format_count(4500) == "4.5k"
    assert api.format_count(940) == "940"
