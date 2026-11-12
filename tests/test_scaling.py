"""Scaling must give what the NovelAI site's scaler (pica 9.0.1) gives, byte for byte."""

import pytest

from novelai_krita import scaling


def made_up(count, seed):
    """The same bytes on every Python: a plain linear congruential generator."""
    values, state = bytearray(), seed
    for _ in range(count):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        values.append(state >> 16 & 0xFF)
    return values


def picture(kind, seed, width, height):
    data = made_up(width * height * 4, seed)
    if kind == "opaque":
        data[3::4] = b"\xff" * (width * height)
    elif kind == "extremes":
        data = bytearray(255 if value & 1 else 0 for value in data)
        data[3::4] = b"\xff" * (width * height)
    return bytes(data)


# (kind, seed, width, height, new width, new height, pica's result as hex). Made by
# running pica's own code, its JavaScript and its WebAssembly, on picture(...).
PICA = [
    ("opaque", 1, 7, 5, 11, 8, (
        "d76d80ff8eb1beff2efffbff2fffc8ff6e9dd7ff811bf3ff297d7eff00cf01ff408a00ff5f5c5dff5e53a8ff"
        "c08352ffab9a6aff8bca97ff7cecb1ff8189b9ff6b1fa0ff168160ff16a935ff794a38ff947044ff84ad49ff"
        "a28c4affc3691bfff54c11fff87181ffa16f97ff356054ff1cae55ff6f7f79ffda0081ffe97546ffcdff16ff"
        "a15cbfffa04467ffcc1e14ffff195cffad74a3ff00e8a5ff64ff98fff59074ffff0963ffff4996fffda7c1ff"
        "6c99f2ff6354b1ff6f0f58ffa54b57ff7aa089ff39d8a8ff8cff91ffb6d37aff89618fffc326b3ffff25caff"
        "1bff9aff407f9bff500489ff0dac62ff3cbb4cffa5464bff8c9c3cff24ee8aff00adeeff73367dffdd000dff"
        "25f32aff5a845dff891d93ff5b7b5bff5b7d4eff962367ffae442fff9f8d4fff8c9faeff956d51ffa54500ff"
        "46b100ff787936ffbf3188ffc83552ff874563ff663b9affc72542ffff3b1fffff7b61ffc89043ff888814ff")),
    ("alpha", 2, 7, 5, 11, 8, (
        "6700ff5bd08e6dadf9f300cecfff0030c4006b55e21266ffba00d7da8857bfc79be112f7c1ff005600000200"
        "8a89d579b68f81c3cc9d53d093eb6a22cf516230d5425edfab16bbf38e2ebed095ba23c053ce06860595236c"
        "a4ff6176928c96a7841bc0a87a00f327ffff000ebcaf4f808848a1ea7c1abec67747625b18a115a50ea029ff"
        "a4ff1717295c91283c00a848d5086a5fff542c3242ff31182ca3a98b2879bc940e1dd81d2eb34c5b30b959b3"
        "ff0b9e0fc165c02b906dae60906d8281c5465651ff00201722f88c3d1ce7b1736fb0f1718abcff486dfff72b"
        "d3935b6cda83acb0c088dce146bfe58739a68556ff413162ff6c003889bf9c7085a1e8da7d8df47500505f05"
        "bf733884d36b89cee16ac8ffc467e4ac858f9d6d70ac237472922a66878488658d96d66ba2639f51be27233a"
        "b1501877ca5169b8ea45a7f8ff29d0c5cb6f9f7c00ff136615b54a8856607c5e00000000ff110027c0463c6f")),
    ("alpha", 3, 12, 9, 5, 4, (
        "619a527877699d8570949e9373557d7163b54383399f6e5153ba9483568d9a90a991a79496987c7aa2874e5c"
        "478a8061905174b39c8c789980c7848d717f5d8c1f8c954f8a546f6eaaa0886a6a57a869")),
    ("extremes", 4, 9, 6, 8, 7, (
        "ff00e4ffc22a22ff34ffffffffff67ffff4e70ff548dffff00ff25ff000bd7ffdd1e15ff3eba39ffaa35ffff"
        "3f8bffff34e675ff0cff30ffa54b05ffeec3e0ffef359dffdedc00ffc60c4aff00ceb7ff73ff53ff24935eff"
        "ca9013ff7660eeffe10aadffafba5affbd460cff40ffd0ff99a5cfff8b0098ffeb7ea0ff6a6b82fffc9000ff"
        "65cfe1ff721c18ff7dffffffac9affff622300ffff0090ffffff09ffd9eb5affb99cdfff006937ff6bff7aff"
        "ffff73ff584940ff923400ffd2bb01ff0000ffff002cfaff02ffffff6fff76ffff6c70ffff00ffff2ee531ff"
        "001e01ff")),
]


@pytest.mark.parametrize("kind, seed, width, height, to_width, to_height, expected", PICA)
def test_resize_matches_pica(kind, seed, width, height, to_width, to_height, expected):
    data = picture(kind, seed, width, height)
    result = scaling.resize(data, width, height, to_width, to_height)
    assert result.hex() == expected


def test_the_same_size_changes_nothing():
    data = picture("alpha", 5, 6, 4)
    assert scaling.resize(data, 6, 4, 6, 4) == data


def test_an_opaque_picture_stays_opaque():
    data = picture("opaque", 6, 10, 10)
    result = scaling.resize(data, 10, 10, 23, 17)
    assert len(result) == 23 * 17 * 4 and result[3::4] == b"\xff" * (23 * 17)


def test_a_flat_colour_stays_that_colour():
    data = bytes([200, 60, 80, 255]) * (20 * 30)
    for size in ((33, 50), (7, 11), (20, 31)):
        assert scaling.resize(data, 20, 30, *size) == bytes([200, 60, 80, 255]) * (size[0] * size[1])


def test_weights_add_up_to_one_in_fixed_point():
    for size, to_size in ((832, 1024), (1216, 1497), (3000, 1024), (10, 10)):
        filters = scaling.filters(size, to_size)
        assert len(filters) == to_size
        for first, weights in filters:
            assert 0 <= first and first + len(weights) <= size
            assert abs(sum(weights) - 16383) <= len(weights)


def hidden(width, height, text=b"stealth_pngcomp"):
    """A picture with ``text`` in the lowest bit of its alpha, as NovelAI writes it."""
    data = bytearray(made_up(width * height * 4, 9))
    data[3::4] = b"\xff" * (width * height)
    bits = [byte >> shift & 1 for byte in text for shift in range(7, -1, -1)]
    for index, bit in enumerate(bits):  # Down the columns, from the left.
        x, y = divmod(index, height)
        data[(y * width + x) * 4 + 3] = 254 | bit
    return bytes(data)


def test_hidden_data_is_taken_out_of_the_alpha_channel():
    data = hidden(16, 16)
    assert data[3::4].count(254) > 0
    cleaned = scaling.without_hidden_data(data, 16, 16)
    assert cleaned[3::4] == b"\xff" * 256
    assert [cleaned[c::4] for c in range(3)] == [data[c::4] for c in range(3)]


def test_hidden_data_is_found_from_the_first_columns_alone():
    data = hidden(16, 16)
    assert scaling.has_hidden_data(data, 16, 16)
    # The marker is 120 bits: 8 columns of a picture 16 high.
    assert scaling.MARKER_PIXELS == 120
    strip = b"".join(data[row * 64:row * 64 + 8 * 4] for row in range(16))
    assert scaling.has_hidden_data(strip, 8, 16)
    assert not scaling.has_hidden_data(hidden(16, 16, b"something_else!"), 16, 16)
    assert not scaling.has_hidden_data(hidden(4, 4, b"s"), 4, 4)  # Too small for the marker.


def test_whole_alpha_changes_only_what_hiding_data_changes():
    data = bytes([10, 20, 30, 254, 40, 50, 60, 1, 70, 80, 90, 255, 1, 2, 3, 0, 4, 5, 6, 128])
    assert scaling.whole_alpha(data) == bytes(
        [10, 20, 30, 255, 40, 50, 60, 0, 70, 80, 90, 255, 1, 2, 3, 0, 4, 5, 6, 128])


def test_other_pictures_keep_their_alpha():
    data = hidden(16, 16, b"something_else!")
    assert scaling.without_hidden_data(data, 16, 16) == data
    partly = picture("alpha", 11, 16, 16)
    assert scaling.without_hidden_data(partly, 16, 16) == partly
    tiny = picture("alpha", 12, 4, 4)  # Too small to hold the marker.
    assert scaling.without_hidden_data(tiny, 4, 4) == tiny


def test_on_black():
    opaque = picture("opaque", 13, 5, 5)
    assert scaling.on_black(opaque) == opaque
    data = bytes([200, 100, 50, 255, 200, 100, 50, 0, 200, 100, 51, 128, 255, 255, 255, 1])
    assert scaling.on_black(data) == bytes(
        [200, 100, 50, 255, 0, 0, 0, 255, 100, 50, 26, 255, 1, 1, 1, 255])
