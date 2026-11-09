import pytest

from novelai_krita import api, inpaint_math as im


def grid(width, height, *marked):
    data = bytearray(width * height)
    for x, y in marked:
        data[y * width + x] = 255
    return bytes(data)


@pytest.mark.parametrize("size, cells", [
    ((832, 1216), (104, 152)),
    ((833, 1216), (105, 152)),
    ((8, 1), (1, 1)),
])
def test_cells_cover_the_canvas(size, cells):
    assert im.cells_for(*size) == cells


def test_small_focus_area_is_left_alone():
    assert im.limit_corner(10, 10, 50, 40) == (50, 40)


def test_focus_area_is_capped_at_the_sites_limit():
    x, y = im.limit_corner(0, 0, 400, 300)
    assert x * y * 64 <= im.MAX_FOCUS_PIXELS
    assert x * y * 64 > im.MAX_FOCUS_PIXELS * 0.97
    assert x / y == pytest.approx(400 / 300, abs=0.02)  # Keeps its shape.


def test_focus_cap_works_in_every_direction():
    x, y = im.limit_corner(400, 300, 0, 0)
    assert x >= 0 and y >= 0
    assert (400 - x) * (300 - y) * 64 <= im.MAX_FOCUS_PIXELS


def test_inner_rect_leaves_the_context_around_it():
    assert im.inner_rect(10, 20, 100, 80, 96) == (22, 32, 76, 56)
    assert im.inner_rect(10, 20, 100, 80, 32) == (14, 24, 92, 72)


def test_inner_rect_of_an_area_too_small_for_its_context():
    assert im.inner_rect(0, 0, 24, 100, 96) is None
    assert im.inner_rect(0, 0, 25, 25, 96) == (12, 12, 1, 1)


@pytest.mark.parametrize("area", [(768, 768), (512, 512), (1024, 256), (304, 448), (128, 128)])
def test_focus_is_scaled_up_within_the_free_size(area):
    width, height = im.focus_size(*area)
    assert width % api.SIZE_MULTIPLE == 0 and height % api.SIZE_MULTIPLE == 0
    assert width * height <= api.FREE_MAX_PIXELS
    assert width > area[0] and height > area[1]
    assert width * height > api.FREE_MAX_PIXELS * 0.8
    assert width / height == pytest.approx(area[0] / area[1], rel=0.1)


def test_extreme_focus_shape_stays_valid():
    width, height = im.focus_size(16, 9000)
    assert width >= api.SIZE_MULTIPLE and height <= api.MAX_SIDE


def test_focus_context_can_hang_off_every_edge():
    # A 40 x 40 area with 96 px (12 cells) of context, on a 100 x 150 grid.
    assert im.moved_focus(-50, -50, 40, 40, 100, 150, 96) == (-12, -12)
    assert im.moved_focus(500, 500, 40, 40, 100, 150, 96) == (72, 122)
    assert im.moved_focus(10, 20, 40, 40, 100, 150, 96) == (10, 20)


def test_what_is_inpainted_stays_on_the_canvas():
    x, y = im.moved_focus(-50, -50, 40, 40, 100, 150, 96)
    assert im.inner_rect(x, y, 40, 40, 96)[:2] == (0, 0)
    x, y = im.moved_focus(500, 500, 40, 40, 100, 150, 96)
    inner = im.inner_rect(x, y, 40, 40, 96)
    assert inner[0] + inner[2] == 100 and inner[1] + inner[3] == 150


@pytest.mark.parametrize("target", [(-500, -500), (500, 500), (500, 20), (-500, 20), (20, 500)])
@pytest.mark.parametrize("context", [im.CONTEXT_MIN, im.CONTEXT_MAX])
@pytest.mark.parametrize("size", [(4, 4), (10, 10), (24, 24), (25, 60), (100, 150)])
def test_focus_area_never_leaves_the_canvas(target, context, size):
    x, y = im.moved_focus(*target, *size, 100, 150, context)
    _, _, width, height = im.focus_crop(x, y, *size, 100, 150)
    assert width > 0 and height > 0
    assert im.focus_size(width * im.CELL, height * im.CELL)  # Can be generated.
    # More than half of it is still on the canvas.
    assert -size[0] / 2 < x < 100 - size[0] / 2 and -size[1] / 2 < y < 150 - size[1] / 2


def test_small_focus_area_hangs_off_less_than_its_context():
    # 10 cells across with 12 cells of context: only 4 may hang off.
    assert im.moved_focus(-50, -50, 10, 10, 100, 150, 96) == (-4, -4)
    assert im.moved_focus(500, 500, 10, 10, 100, 150, 96) == (94, 144)


def test_less_context_brings_the_focus_area_back():
    x, y = im.moved_focus(-50, 500, 40, 40, 100, 150, 96)
    assert (x, y) == (-12, 122)
    assert im.moved_focus(x, y, 40, 40, 100, 150, 32) == (-4, 114)


def test_focus_crop_on_the_canvas_is_the_area():
    assert im.focus_crop(10, 20, 40, 30, 100, 150) == (10, 20, 40, 30)


def test_focus_crop_past_the_left_and_top_grows_like_the_site():
    assert im.focus_crop(-5, -7, 40, 30, 100, 150) == (0, 0, 45, 37)


def test_focus_crop_past_the_right_and_bottom_is_cut():
    assert im.focus_crop(70, 130, 40, 30, 100, 150) == (70, 130, 30, 20)


def test_focus_crop_never_leaves_the_grid():
    assert im.focus_crop(-12, 0, 100, 150, 100, 150) == (0, 0, 100, 150)


def test_character_position_moves_into_the_focus_area():
    area = (200, 400, 400, 400)
    assert im.focus_position(0.5, 0.5, 800, 1200, area) == pytest.approx((0.5, 0.5))
    assert im.focus_position(0.25, 1 / 3, 800, 1200, area) == pytest.approx((0.0, 0.0))


def test_character_outside_the_focus_area_stays_at_its_edge():
    assert im.focus_position(0.0, 1.0, 800, 1200, (200, 400, 400, 400)) == (0.0, 1.0)


def test_blend_keeps_the_masked_cells_whole():
    width, height = 40, 40
    alpha = im.blend_alpha(grid(width, height, *[(x, y) for x in range(15, 25) for y in range(15, 25)]),
                           width, height)
    assert alpha[20 * width + 20] == 255
    assert alpha[15 * width + 15] >= 250


def test_blend_fades_out_past_the_expanded_mask():
    width, height = 60, 60
    alpha = im.blend_alpha(grid(width, height, *[(x, y) for x in range(20, 40) for y in range(20, 40)]),
                           width, height)
    row = [alpha[30 * width + x] for x in range(40, 60)]
    assert row == sorted(row, reverse=True)  # Only ever fades.
    assert row[0] > 240  # Just outside the mask: still there.
    assert 0 < row[4] < 255  # The edge of the expanded mask: partly there.
    assert row[-1] == 0  # Far away: untouched.


def test_empty_mask_blends_nothing():
    assert set(im.blend_alpha(bytes(100), 10, 10)) == {0}


def test_blend_at_the_edge_of_the_grid():
    alpha = im.blend_alpha(grid(30, 30, (0, 0)), 30, 30)
    assert alpha[0] == 255 and len(alpha) == 900
