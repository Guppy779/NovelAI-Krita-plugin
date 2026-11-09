import pytest

from novelai_krita import weights
from novelai_krita.weights import HIGH, LOW, MID


def parts(line, weight=1.0, numeric=True):
    """[(text, color), ...] of a line's coloured parts."""
    return [(line[s.start:s.end], s.color) for s in weights.spans(line, weight, numeric)[0]]


def end_weight(line, weight=1.0, numeric=True):
    return weights.spans(line, weight, numeric)[1]


@pytest.mark.parametrize("line", ["", "1girl, smile", "tag: value", "a:b, 1.5 stars"])
def test_plain_text_has_no_colour(line):
    assert parts(line) == []
    assert end_weight(line) == 1.0


def test_curly_brackets_raise_the_weight():
    assert parts("smile, {red hair}, hat") == [("{red hair", HIGH)]


def test_square_brackets_lower_the_weight():
    assert parts("smile, [red hair], hat") == [("[red hair", LOW)]


def test_nested_brackets_add_up():
    assert end_weight("{{{tag") == pytest.approx(1.05 ** 3)
    assert end_weight("[[tag") == pytest.approx(1.05 ** -2)


def test_matching_brackets_cancel_out():
    assert parts("{[tag]}") == [("{", HIGH), ("]", HIGH)]
    assert end_weight("{tag}") == pytest.approx(1.0)


def test_number_sets_the_weight_and_is_coloured_with_it():
    assert parts("a, 1.5::red hair::, b") == [("1.5::red hair", HIGH), ("::", MID)]
    assert parts("a, 0.5::red hair::, b") == [("0.5::red hair", LOW), ("::", MID)]


def test_a_new_weight_replaces_the_last_one():
    assert parts("2::a, 0.5::b, ::c") == [("2::a, ", HIGH), ("0.5::b, ", LOW), ("::", MID)]


def test_weight_of_one_only_colours_the_marker():
    assert parts("2::a 1::b") == [("2::a ", HIGH), ("1::", MID)]
    assert end_weight("2::a 1::b") == 1.0


def test_brackets_multiply_a_numeric_weight():
    assert end_weight("{1.2::a") == 1.2  # A number replaces the weight.
    assert end_weight("1.2::{a") == pytest.approx(1.26)


@pytest.mark.parametrize("number", ["-", "-.", "."])
def test_incomplete_number_counts_as_zero(number):
    assert end_weight(number + "::a") == 0.0


def test_negative_weight():
    assert parts("-1::a") == [("-1::a", LOW)]
    assert end_weight("-1::a") == -1.0


def test_weights_carry_over_to_the_next_line():
    _, weight = weights.spans("1.5::first")
    assert parts("second:: third", weight) == [("second", HIGH), ("::", MID)]
    assert end_weight("second:: third", weight) == 1.0


def test_older_models_have_no_numeric_weights():
    assert parts("1.5::a::", numeric=False) == []
    assert parts("{a}", numeric=False) == [("{a", HIGH)]


def test_stray_colons_are_plain_text():
    assert parts("a:b :c") == []


@pytest.mark.parametrize("weight, opacity", [
    (1.5, 0.4), (2.0, 0.6), (9.0, 0.6), (0.5, 0.4), (0.0, 0.6), (-3.0, 0.6),
])
def test_highlight_gets_stronger_away_from_one(weight, opacity):
    span = weights.spans("x", weight)[0][0]
    assert span.opacity == pytest.approx(opacity)


def test_weakest_highlight_is_still_visible():
    span = weights.spans("{x")[0][0]
    assert 0.2 < span.opacity < 0.25


@pytest.mark.parametrize("weight", [1.0, 1.05, 0.5, 0.0, -2.5, 19999.0])
def test_state_round_trip(weight):
    state = weights.to_state(weight)
    assert state >= 0
    assert weights.from_state(state) == pytest.approx(weight, abs=1e-4)


def test_no_state_means_weight_one():
    assert weights.from_state(-1) == 1.0


def test_huge_weight_still_fits_a_state():
    state = weights.to_state(float("9" * 30))
    assert 0 <= state < 2 ** 31
