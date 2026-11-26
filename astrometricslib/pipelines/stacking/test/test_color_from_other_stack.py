"""Tests for the color picture of a monochrome stack, colored by another stack.

Each test builds small synthetic pictures with Gaussian stars and a plate
solution (WCS). The alignment must recover a move that differs in scale,
turn and position, and a small error in the plate solution. A star's color,
measured on a wide red star of the color stack, must be painted only inside
the narrow monochrome star. The alignment must also work when the
monochrome stars are twice as wide as expected, the plate solutions disagree
by more than the pairing radius, and the monochrome frame lies off the center
of a wide color frame over a bright nebula (the M 42 and M 45 failure). The
extended color must lose a tinted sky, under a nebula or galaxy too, and
the red skirts StarNet leaves around stars, and keep the object's own color.
Where little sky lies near, the frame-wide tint is taken away. The step
must be skipped, with a reason, when there is no color stack or the color
stack covers too little of the frame. A saturated star must not take the
red color of its wing when the color stack's red stars are wider than its
green ones: it takes its catalog color, or white. StarNet, SIMBAD and the
Gaia archive are not used.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS

from astrometricslib.pipelines.shared.stack_preview_path import (
    color_preview_path_for,
    processed_fits_path_for,
)
from astrometricslib.pipelines.stacking.post_processing import color_from_other_stack
from astrometricslib.pipelines.stacking.post_processing.color_from_other_stack import (
    ColorStackChoice,
    choose_color_stack,
    compose_color_picture,
    extended_color,
    make_color_preview_for_target,
    neutralize_sky,
    write_color_preview,
)
from astrometricslib.pipelines.stacking.post_processing.other_stack_alignment import (
    align_with_stars,
    find_bright_stars,
)
from astrometricslib.pipelines.stacking.post_processing.star_color_measurement import (
    SOURCE_NAMES,
    chroma_per_lightness,
    measure_star_colors,
)

CENTER_RA = 300.0
CENTER_DEC = 22.0


def make_wcs(
    shape: tuple[int, int],
    arcsec_per_pixel: float,
    rotation_degrees: float,
    shift: tuple[float, float] = (0.0, 0.0),
) -> WCS:
    """Make a tangent-plane plate solution centered on the test field.

    Parameters
    ----------
    shape : `tuple` [`int`, `int`]
        Rows and columns of the picture.
    arcsec_per_pixel : `float`
        The pixel size on the sky.
    rotation_degrees : `float`
        The turn of the picture on the sky.
    shift : `tuple` [`float`, `float`], optional
        A shift of the reference pixel (column, row), to move the field.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        The plate solution.
    """
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [CENTER_RA, CENTER_DEC]
    wcs.wcs.crpix = [shape[1] / 2 + shift[0], shape[0] / 2 + shift[1]]
    scale = arcsec_per_pixel / 3600.0
    angle = np.radians(rotation_degrees)
    wcs.wcs.cd = scale * np.array([[-np.cos(angle), np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    return wcs


def sky_stars(count: int, seed: int, radius_degrees: float = 0.12) -> np.ndarray:
    """Place stars at random on the sky around the test field.

    Parameters
    ----------
    count : `int`
        How many stars.
    seed : `int`
        The random seed.
    radius_degrees : `float`, optional
        Half the width of the square they are placed in.

    Returns
    -------
    stars : `numpy.ndarray`
        Shape ``(count, 3)``: right ascension, declination, brightness.
    """
    generator = np.random.default_rng(seed)
    ra = CENTER_RA + generator.uniform(-radius_degrees, radius_degrees, count) / np.cos(
        np.radians(CENTER_DEC)
    )
    dec = CENTER_DEC + generator.uniform(-radius_degrees, radius_degrees, count)
    brightness = generator.uniform(0.2, 1.0, count)
    return np.column_stack([ra, dec, brightness])


def render(
    shape: tuple[int, int], wcs: WCS, stars: np.ndarray, sigma: float, noise: float, seed: int
) -> np.ndarray:
    """Draw Gaussian stars on a flat, noisy sky.

    Parameters
    ----------
    shape : `tuple` [`int`, `int`]
        Rows and columns.
    wcs : `astropy.wcs.WCS`
        Where the stars fall.
    stars : `numpy.ndarray`
        Right ascension, declination and peak brightness of each star.
    sigma : `float`
        The star's Gaussian width in pixels.
    noise : `float`
        The sky noise.
    seed : `int`
        The random seed.

    Returns
    -------
    picture : `numpy.ndarray`
        The picture, 32-bit floats.
    """
    columns, rows = wcs.world_to_pixel_values(stars[:, 0], stars[:, 1])
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    picture = np.full(shape, 0.1, dtype=np.float64)
    for row, column, peak in zip(rows, columns, stars[:, 2], strict=True):
        if -10 < row < shape[0] + 10 and -10 < column < shape[1] + 10:
            box = (
                slice(max(0, int(row) - 15), int(row) + 16),
                slice(max(0, int(column) - 15), int(column) + 16),
            )
            picture[box] += peak * np.exp(-((yy[box] - row) ** 2 + (xx[box] - column) ** 2) / (2 * sigma**2))
    picture += noise * np.random.default_rng(seed).standard_normal(shape)
    return picture.astype(np.float32)


def test_alignment_recovers_scale_turn_and_a_plate_solution_error() -> None:
    """The fitted move puts the color stars on the monochrome stars closely."""
    stars = sky_stars(400, seed=1)
    reference_shape = (500, 500)
    reference_wcs = make_wcs(reference_shape, 1.5, 0.0)
    reference = render(reference_shape, reference_wcs, stars, 1.5, 0.003, seed=2)
    moving_shape = (420, 460)
    true_moving_wcs = make_wcs(moving_shape, 2.0, 12.0, shift=(10.0, -6.0))
    moving = render(moving_shape, true_moving_wcs, stars, 2.0, 0.003, seed=3)
    # The moving picture's plate solution is off by 1.5 pixels.
    stated_moving_wcs = make_wcs(moving_shape, 2.0, 12.0, shift=(11.5, -6.0))

    reference_stars = find_bright_stars(reference, 3000, 3.5)
    fit = align_with_stars(reference_stars, reference_wcs, moving, stated_moving_wcs, 4.7)

    assert fit is not None
    assert fit.residual_pixels < 0.1
    assert fit.transform.scale == pytest.approx(2.0 / 1.5, rel=1e-3)
    columns, rows = true_moving_wcs.world_to_pixel_values(stars[:50, 0], stars[:50, 1])
    expected_columns, expected_rows = reference_wcs.world_to_pixel_values(stars[:50, 0], stars[:50, 1])
    inside = (rows > 20) & (rows < 400) & (columns > 20) & (columns < 440)
    mapped_rows, mapped_columns = fit.transform.map_points(rows[inside], columns[inside])
    assert (
        np.max(np.hypot(mapped_rows - expected_rows[inside], mapped_columns - expected_columns[inside])) < 0.1
    )


def test_alignment_survives_wide_stars_a_large_plate_solution_error_and_an_off_center_overlap() -> None:
    """Wide stars, a 10-pixel plate error and an off-center field still align.

    This is the M 42 and M 45 case: the monochrome stars are about 7 pixels
    wide (the finder's starting width is 3.5), a bright nebula fills the
    field, the color frame is 1.42 times coarser and much wider, with the
    monochrome field on its left side, and the two plate solutions disagree
    by about 10 pixels, more than the first pairing radius.
    """
    stars = sky_stars(1500, seed=11, radius_degrees=0.3)
    reference_shape = (500, 500)
    reference_wcs = make_wcs(reference_shape, 1.9, 0.0)
    reference = render(reference_shape, reference_wcs, stars, 3.0, 0.003, seed=12)
    yy, xx = np.mgrid[0 : reference_shape[0], 0 : reference_shape[1]]
    reference += (0.5 * np.exp(-((yy - 250) ** 2 + (xx - 220) ** 2) / (2 * 60.0**2))).astype(np.float32)
    moving_shape = (500, 900)
    # The monochrome field sits about 250 columns left of the color center.
    true_moving_wcs = make_wcs(moving_shape, 1.9 * 1.42, 6.0, shift=(-250.0, 0.0))
    moving = render(moving_shape, true_moving_wcs, stars, 2.0, 0.003, seed=13)
    stated_moving_wcs = make_wcs(moving_shape, 1.9 * 1.42, 6.0, shift=(-243.0, -5.0))

    reference_stars = find_bright_stars(reference, 3000, 3.5)
    assert len(reference_stars) > 100
    fit = align_with_stars(reference_stars, reference_wcs, moving, stated_moving_wcs, 4.7)

    assert fit is not None
    assert fit.plate_solution_offset_pixels > 8.0
    assert fit.residual_pixels < 0.3
    assert fit.model == "polynomial"
    columns, rows = true_moving_wcs.world_to_pixel_values(stars[:, 0], stars[:, 1])
    expected_columns, expected_rows = reference_wcs.world_to_pixel_values(stars[:, 0], stars[:, 1])
    inside = (expected_rows > 40) & (expected_rows < 460) & (expected_columns > 40) & (expected_columns < 460)
    mapped_rows, mapped_columns = fit.transform.map_points(rows[inside], columns[inside])
    distance = np.hypot(mapped_rows - expected_rows[inside], mapped_columns - expected_columns[inside])
    assert np.median(distance) < 0.3


def test_extended_color_drops_a_tinted_sky_and_red_star_skirts_but_keeps_the_nebula() -> None:
    """A red sky tint and red skirts around stars go; a teal nebula stays teal.

    This is the Nikon 300 mm case: its starless picture has a sky slightly
    redder than green, and StarNet leaves the faint red outer skirt of each
    star, because the lens's red stars are wider than its green ones. The
    tint lies on the nebula's light too, so it is taken away there as well.
    """
    size = 300
    yy, xx = np.mgrid[0:size, 0:size]
    generator = np.random.default_rng(21)
    nebula = np.exp(-((yy - 150) ** 2 + (xx - 150) ** 2) / (2 * 30.0**2))
    mono_starless = (0.1 + 0.4 * nebula + 0.002 * generator.standard_normal((size, size))).astype(np.float32)
    clean = np.stack([0.1 + 0.15 * nebula, 0.1 + 0.3 * nebula, 0.1 + 0.3 * nebula])
    labels = np.zeros((size, size), dtype=np.int32)
    skirts = np.zeros((size, size))
    star_positions = [(30 + 60 * i, 30 + 60 * j) for i in range(5) for j in range(5) if (i, j) != (2, 2)]
    for label, (row, column) in enumerate(star_positions, start=1):
        distance_squared = (yy - row) ** 2 + (xx - column) ** 2
        labels[distance_squared <= 4] = label
        skirts += 0.05 * np.exp(-distance_squared / (2 * 5.0**2))
    noise = 0.002 * generator.standard_normal((3, size, size))
    tinted = clean + noise
    tinted[0] += 0.01 + skirts
    covered = np.ones((size, size), dtype=bool)

    a_value, b_value, statistics = extended_color(
        mono_starless, tinted.astype(np.float32), covered, labels, halo_radius_px=12.0
    )
    reference_a, reference_b, _ = extended_color(mono_starless, (clean + noise).astype(np.float32), covered)
    old_a, _, _ = extended_color(mono_starless, tinted.astype(np.float32), covered)

    assert statistics["sky_tint_removed_rgb"][0] > 0.005
    assert statistics["sky_tint_largest_removed"] > 0.005
    # Beside a star the red skirt was strongly red before; it is gray now.
    row, column = star_positions[0]
    assert old_a[row, column + 5] > 3.0
    assert abs(a_value[row, column + 5]) < 1.0
    # The nebula has the color it has with no sky tint at all, and stays teal.
    assert a_value[150, 150] == pytest.approx(reference_a[150, 150], abs=1.0)
    assert b_value[150, 150] == pytest.approx(reference_b[150, 150], abs=1.0)
    assert a_value[150, 150] < -5.0
    # The sky far from stars and nebula is as gray as with no tint.
    assert abs(a_value[150, 2]) <= abs(reference_a[150, 2]) + 0.5


def test_a_galaxy_filling_the_frame_loses_the_sky_tint_but_keeps_its_own_color() -> None:
    """A large red galaxy under a red sky tint keeps only its own red.

    This is the M 31 case: the galaxy covers most of the frame and the sky
    is a strip at one edge. The sky sample must not hold the galaxy's faint
    disk, or the galaxy's own red would be taken away as a tint. The sky tint
    itself lies on the galaxy's light too, so it is taken away there.
    """
    size = 300
    yy, xx = np.mgrid[0:size, 0:size]
    generator = np.random.default_rng(31)
    # An elongated galaxy over most of the frame; the right edge is sky.
    galaxy = np.exp(-(((yy - 150) / 120.0) ** 2 + ((xx - 110) / 90.0) ** 2))
    mono_starless = (0.1 + 0.5 * galaxy + 0.002 * generator.standard_normal((size, size))).astype(np.float32)
    clean = np.stack([0.1 + 0.30 * galaxy, 0.1 + 0.22 * galaxy, 0.1 + 0.18 * galaxy])
    noise = 0.002 * generator.standard_normal((3, size, size))
    tint = np.array([0.006, -0.003, -0.003])[:, None, None]
    covered = np.ones((size, size), dtype=bool)
    labels = np.zeros((size, size), dtype=np.int32)
    labels[5:8, 5:8] = 1

    a_value, b_value, statistics = extended_color(
        mono_starless, (clean + noise + tint).astype(np.float32), covered, labels, 6.0
    )
    reference_a, reference_b, _ = extended_color(mono_starless, (clean + noise).astype(np.float32), covered)

    removed = statistics["sky_tint_removed_rgb"]
    assert removed[0] == pytest.approx(0.006, abs=0.002)
    # Where the galaxy is bright, the color is the galaxy's own, still red.
    for row, column in ((150, 110), (150, 160), (100, 110)):
        assert a_value[row, column] == pytest.approx(reference_a[row, column], abs=0.5)
        assert b_value[row, column] == pytest.approx(reference_b[row, column], abs=0.5)
        assert a_value[row, column] > 3.0
    # The sky at the right edge is as gray as with no tint at all.
    sky = (slice(100, 200), slice(285, 295))
    assert np.abs(a_value[sky]).mean() <= np.abs(reference_a[sky]).mean() + 0.3
    assert np.abs(b_value[sky]).mean() <= np.abs(reference_b[sky]).mean() + 0.3


def test_the_sky_tint_follows_the_nearby_sky_and_falls_back_to_the_frame_wide_tint() -> None:
    """The tint taken is the nearby sky's; far from any sky, the frame's.

    The sky is a band at the left edge. Its top third is red and the rest
    is gray, so the frame-wide tint is gray. Next to the red sky the red goes;
    next to the gray sky nothing is taken. Deep inside the object, level with
    the red sky but where the smoothing holds almost no sky, the frame-wide
    (gray) tint is used, so the object keeps its own red. Without the
    fallback the map's red there would take that red away.
    """
    rows, columns = 1500, 1200
    picture = np.full((3, rows, columns), 0.1, dtype=np.float32)
    sky = np.zeros((rows, columns), dtype=bool)
    sky[:, :300] = True
    picture[0, :500, :300] += 0.008
    # An object with a red color of its own fills the rest of the frame.
    picture[:, :, 300:] += 0.2
    picture[0, :, 300:] += 0.004

    neutral, offsets, largest = neutralize_sky(picture, sky)

    def red_minus_green(row: int, column: int) -> float:
        """Give red minus green at one pixel of the result.

        Returns
        -------
        difference : `float`
            Red minus green, in picture units.
        """
        return float(neutral[0, row, column] - neutral[1, row, column])

    assert max(abs(value) for value in offsets) < 0.002
    assert largest > 0.004
    assert abs(red_minus_green(50, 100)) < 0.001
    assert abs(red_minus_green(1400, 100)) < 0.001
    assert red_minus_green(100, 750) == pytest.approx(0.004, abs=0.0005)


def make_star_field(seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[tuple[int, int]]]:
    """Make a monochrome picture with narrow stars and a wide-star color stack.

    The stars sit on a 6 by 6 grid. A third of them are red in the color
    stack (red twice green and blue); the rest are white. One saturated star
    in the color stack sets the top of its range.

    Parameters
    ----------
    seed : `int`
        The random seed.

    Returns
    -------
    luminance : `numpy.ndarray`
        The monochrome picture, narrow stars (width 1.2 pixels).
    starless : `numpy.ndarray`
        The same picture without stars.
    linear : `numpy.ndarray`
        The linear color stack on the same grid, wide stars (width 3.5
        pixels), shape (3, rows, columns).
    red_stars : `list` [`tuple` [`int`, `int`]]
        The positions of the red stars.
    """
    size = 600
    yy, xx = np.mgrid[0:size, 0:size]
    generator = np.random.default_rng(seed)
    luminance = np.full((size, size), 0.1, dtype=np.float64)
    linear = np.full((3, size, size), 0.05, dtype=np.float64)
    red_stars = []
    for index, (row, column) in enumerate((60 + 90 * i, 60 + 90 * j) for i in range(6) for j in range(6)):
        luminance += 0.6 * np.exp(-((yy - row) ** 2 + (xx - column) ** 2) / (2 * 1.2**2))
        wide = np.exp(-((yy - row) ** 2 + (xx - column) ** 2) / (2 * 3.5**2))
        red = 2.0 if index % 3 == 0 else 1.0
        if index % 3 == 0:
            red_stars.append((row, column))
        for channel, weight in enumerate((red, 1.0, 1.0)):
            linear[channel] += 0.2 * weight * wide
    # One saturated star, flat at the top of the range, sets each channel's
    # top as on a real stack; it is not in the monochrome picture.
    linear[:, 10:16, 300:306] = 1.0
    starless = np.full((size, size), 0.1, dtype=np.float32)
    luminance += 0.002 * generator.standard_normal((size, size))
    linear += 0.0005 * generator.standard_normal((3, size, size))
    return luminance.astype(np.float32), starless, linear.astype(np.float32), red_stars


def test_star_color_is_painted_only_inside_the_narrow_monochrome_star() -> None:
    """A red star is red inside its monochrome footprint, gray outside it."""
    luminance, starless, linear, red_stars = make_star_field(seed=4)
    covered = np.ones(luminance.shape, dtype=bool)
    wcs, gaia = grid_gaia_colors(luminance.shape)
    footprints, colors = measure_star_colors(luminance, starless, linear, covered, wcs, gaia)

    assert colors.record["white_balance"]["method"] == "gaia_sun_like_stars"
    assert colors.counts["measured"] >= 30
    measured = SOURCE_NAMES.index("measured")
    row, column = red_stars[1]
    label = footprints.labels[row, column]
    assert colors.sources[label] == measured
    assert colors.chroma_a[label] > 0.1  # red: positive Lab a

    zero = np.zeros(luminance.shape, dtype=np.float32)
    picture = compose_color_picture(luminance, footprints, colors, zero, zero)
    red, green, blue = picture[row, column]
    assert red > green + 0.05 and red > blue + 0.05
    # Six pixels out the wide red star still holds light, but the monochrome
    # star (width 1.2 pixels) has none: the picture must be gray there.
    outside = picture[row, column + 6]
    assert footprints.labels[row, column + 6] == 0
    assert np.ptp(outside) < 0.01
    # A white star stays white.
    white_row, white_column = 60, 150
    assert np.ptp(picture[white_row, white_column]) < 0.05


def grid_gaia_colors(shape: tuple[int, int]) -> tuple[WCS, Table]:
    """Give a plate solution and Gaia colors for the 6 by 6 star grid.

    A third of the stars (every third one) are red (``BP - RP`` 1.6); the
    rest are like the Sun (0.82). All have G = 12.

    Parameters
    ----------
    shape : `tuple` [`int`, `int`]
        Rows and columns of the picture.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        The plate solution.
    gaia : `astropy.table.Table`
        The Gaia stars, with ``ra``, ``dec``, ``g`` and ``bp_rp``.
    """
    wcs = make_wcs(shape, 2.0, 0.0)
    positions = [(60 + 90 * i, 60 + 90 * j) for i in range(6) for j in range(6)]
    rows = np.array([row for row, _ in positions], dtype=float)
    columns = np.array([column for _, column in positions], dtype=float)
    ra, dec = wcs.pixel_to_world_values(columns, rows)
    colors = np.array([1.6 if index % 3 == 0 else 0.82 for index in range(len(positions))])
    return wcs, Table({"ra": ra, "dec": dec, "g": np.full(len(positions), 12.0), "bp_rp": colors})


def test_without_gaia_colors_the_stars_are_left_gray_and_uncalibrated() -> None:
    """No white point gives gray stars; no median star is taken as white."""
    luminance, starless, linear, _ = make_star_field(seed=4)
    covered = np.ones(luminance.shape, dtype=bool)
    _, colors = measure_star_colors(luminance, starless, linear, covered, None, None, " (no network)")

    assert colors.record["white_balance"]["method"] == "uncalibrated"
    assert "red_gain" not in colors.record["white_balance"]
    assert colors.counts["measured"] == 0
    assert colors.counts["neutral"] >= 30
    assert not np.any(colors.chroma_a) and not np.any(colors.chroma_b)
    assert any("uncalibrated" in note for note in colors.notes)


def make_red_wide_field() -> tuple[np.ndarray, np.ndarray, np.ndarray, WCS, Table, list[tuple[int, int]]]:
    """Make a field with red-wide color stars and two saturated stars.

    The 36 grid stars are white or red (a third of them) and have Gaia
    colors. Their red light is spread 1.4 times wider than their green and
    blue light, as with the Nikkor at 300 mm. Two hot, bright stars saturate
    the color stack; the red wing around their flat core makes them look red.

    Returns
    -------
    luminance : `numpy.ndarray`
        The monochrome picture.
    starless : `numpy.ndarray`
        The same picture without stars.
    linear : `numpy.ndarray`
        The linear color stack, shape (3, rows, columns).
    wcs : `astropy.wcs.WCS`
        The plate solution shared by both.
    gaia : `astropy.table.Table`
        The Gaia colors of the grid stars (none for the saturated stars).
    saturated : `list` [`tuple` [`int`, `int`]]
        The positions of the two saturated stars.
    """
    size = 600
    yy, xx = np.mgrid[0:size, 0:size]
    generator = np.random.default_rng(8)
    luminance = np.full((size, size), 0.1)
    linear = np.full((3, size, size), 0.05)
    wcs = make_wcs((size, size), 2.0, 0.0)
    rows, columns, colors = [], [], []
    for index, (row, column) in enumerate((60 + 90 * i, 60 + 90 * j) for i in range(6) for j in range(6)):
        distance_squared = (yy - row) ** 2 + (xx - column) ** 2
        luminance += 0.6 * np.exp(-distance_squared / (2 * 1.2**2))
        red = 2.0 if index % 3 == 0 else 1.0
        for channel, (weight, width) in enumerate(((red, 4.2), (1.0, 3.0), (1.0, 3.0))):
            linear[channel] += 0.2 * weight * (3.0 / width) ** 2 * np.exp(-distance_squared / (2 * width**2))
        rows.append(row)
        columns.append(column)
        colors.append(1.6 if index % 3 == 0 else 0.82)
    saturated = [(105, 105), (285, 375)]
    for row, column in saturated:
        distance_squared = (yy - row) ** 2 + (xx - column) ** 2
        luminance += 0.85 * np.exp(-distance_squared / (2 * 2.5**2))
        for channel, width in enumerate((4.2, 3.0, 3.0)):
            star = 40.0 * (3.0 / width) ** 2 * np.exp(-distance_squared / (2 * width**2))
            linear[channel] = np.minimum(linear[channel] + star, 1.0)
    luminance += 0.002 * generator.standard_normal((size, size))
    linear += 0.0005 * generator.standard_normal((3, size, size))
    ra, dec = wcs.pixel_to_world_values(np.array(columns, dtype=float), np.array(rows, dtype=float))
    gaia = Table({"ra": ra, "dec": dec, "g": np.full(len(colors), 12.0), "bp_rp": np.array(colors)})
    starless = np.full((size, size), 0.1, dtype=np.float32)
    return (
        np.clip(luminance, 0.0, 1.0).astype(np.float32),
        starless,
        linear.astype(np.float32),
        wcs,
        gaia,
        saturated,
    )


def test_a_saturated_star_with_a_red_wide_wing_takes_its_catalog_color_or_white() -> None:
    """A hot saturated star is blue by catalog, or white; never red."""
    luminance, starless, linear, wcs, gaia, saturated = make_red_wide_field()
    covered = np.ones(luminance.shape, dtype=bool)
    (hot_row, hot_column), (plain_row, plain_column) = saturated
    hot_ra, hot_dec = wcs.pixel_to_world_values(float(hot_column), float(hot_row))
    asked = []

    def bright_star_catalog() -> tuple[Table, str]:
        """Give a catalog with the first saturated star, a blue-white B star.

        Returns
        -------
        catalog : `astropy.table.Table`
            One star with ``B - V`` = -0.1.
        note : `str`
            An empty string.
        """
        asked.append(True)
        return Table({"ra": [hot_ra], "dec": [hot_dec], "v": [3.0], "b_minus_v": [-0.1]}), ""

    footprints, colors = measure_star_colors(
        luminance, starless, linear, covered, wcs, gaia, "", bright_star_catalog
    )

    record = colors.record["saturated_stars"]
    assert record["red_to_green_width_ratio"] > 1.2
    assert record["wing_trusted"] is False
    assert asked
    hot = footprints.labels[hot_row, hot_column]
    plain = footprints.labels[plain_row, plain_column]
    assert colors.sources[hot] == SOURCE_NAMES.index("catalog")
    assert colors.chroma_b[hot] < -0.05  # blue
    assert colors.chroma_a[hot] < 0.05  # not red
    assert colors.sources[plain] == SOURCE_NAMES.index("neutral")
    assert abs(colors.chroma_a[plain]) < 1e-6 and abs(colors.chroma_b[plain]) < 1e-6
    assert record["saturated_catalog"] >= 1 and record["saturated_neutral"] >= 1
    assert record["saturated_wing"] == 0
    zero = np.zeros(luminance.shape, dtype=np.float32)
    picture = compose_color_picture(luminance, footprints, colors, zero, zero)
    for row, column in saturated:
        red, green, blue = picture[row, column + 2]
        assert red <= green + 0.01 and red <= blue + 0.01


def test_chroma_of_a_white_star_is_zero() -> None:
    """Equal red, green and blue give no color."""
    a_value, b_value = chroma_per_lightness(np.array([1.0]), np.array([1.0]))
    assert abs(a_value[0]) < 1e-3 and abs(b_value[0]) < 1e-3


def test_no_color_stack_is_a_skip_with_a_reason(tmp_path: Path) -> None:
    """A target with only a monochrome stack gets no color picture."""
    mono = tmp_path / "M_27_L.fits"
    fits.PrimaryHDU(np.zeros((10, 10), dtype=np.float32)).writeto(mono)
    stacking = SimpleNamespace(
        stacks_by_configuration={
            "ZWO@405mm": SimpleNamespace(
                stacked_image=str(mono), focal_length_mm=405.0, camera="ZWO", frames_stacked=10
            )
        }
    )
    choice, reason = choose_color_stack(stacking, str(mono))
    assert choice is None
    assert "no color stack" in reason


def test_nearest_focal_length_is_chosen(tmp_path: Path) -> None:
    """The color stack at the monochrome focal length is chosen."""
    mono = tmp_path / "mono.fits"
    fits.PrimaryHDU(np.zeros((10, 10), dtype=np.float32)).writeto(mono)
    entries = {
        "ZWO@405mm": SimpleNamespace(
            stacked_image=str(mono), focal_length_mm=405.0, camera="ZWO", frames_stacked=9
        )
    }
    for focal_length in (300, 405):
        path = tmp_path / f"nikon_{focal_length}.fits"
        fits.PrimaryHDU(np.zeros((3, 10, 10), dtype=np.float32)).writeto(path)
        fits.PrimaryHDU(np.zeros((3, 10, 10), dtype=np.float32)).writeto(processed_fits_path_for(str(path)))
        entries[f"Nikon@{focal_length}mm"] = SimpleNamespace(
            stacked_image=str(path), focal_length_mm=float(focal_length), camera="Nikon", frames_stacked=5
        )
    choice, reason = choose_color_stack(SimpleNamespace(stacks_by_configuration=entries), str(mono))
    assert reason == ""
    assert choice is not None and choice.configuration_key == "Nikon@405mm"


def test_poor_coverage_is_a_skip_and_writes_nothing(tmp_path: Path) -> None:
    """A color stack covering part of the frame gives no picture."""
    stars = sky_stars(500, seed=5, radius_degrees=0.15)
    mono_shape = (400, 400)
    mono_wcs = make_wcs(mono_shape, 1.5, 0.0)
    mono = tmp_path / "mono.fits"
    fits.PrimaryHDU(np.zeros(mono_shape, dtype=np.float32), header=mono_wcs.to_header()).writeto(mono)
    picture = np.clip(render(mono_shape, mono_wcs, stars, 1.5, 0.003, seed=6), 0, 1)
    fits.PrimaryHDU(picture, header=mono_wcs.to_header()).writeto(processed_fits_path_for(str(mono)))
    # The color frame is the same size on the sky but moved by half a frame.
    color_shape = (400, 400)
    color_wcs = make_wcs(color_shape, 1.5, 0.0, shift=(200.0, 0.0))
    plane = np.clip(render(color_shape, color_wcs, stars, 2.0, 0.003, seed=7), 0, 1)
    color = tmp_path / "color.fits"
    header = color_wcs.to_header()
    fits.PrimaryHDU(np.stack([plane, plane, plane]), header=header).writeto(color)
    fits.PrimaryHDU(np.stack([plane, plane, plane]), header=header).writeto(
        processed_fits_path_for(str(color))
    )
    choice = ColorStackChoice(str(color), processed_fits_path_for(str(color)), "Nikon@405mm", "Nikon", 405.0)

    outcome = write_color_preview(str(mono), choice, "siril-not-used")

    assert not outcome.made and not outcome.failed
    assert "covers" in outcome.reason
    assert outcome.record["coverage_fraction"] < 0.95
    assert not Path(color_preview_path_for(str(mono))).exists()


def test_switch_off_removes_an_old_picture_and_skips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the setting off, nothing is made and an old picture goes."""
    mono = tmp_path / "mono.fits"
    fits.PrimaryHDU(np.zeros((10, 10), dtype=np.float32)).writeto(mono)
    old = Path(color_preview_path_for(str(mono)))
    old.write_bytes(b"old")
    monkeypatch.setattr(
        color_from_other_stack,
        "get_configuration",
        lambda: SimpleNamespace(get_preview_color_from_other_stack_enabled=lambda: False),
    )
    outcome = make_color_preview_for_target(SimpleNamespace(stacking=None), str(mono))
    assert not outcome.made and not outcome.failed
    assert not old.exists()


def test_the_target_points_at_a_new_picture_and_drops_a_removed_one(tmp_path: Path) -> None:
    """The shown stack's color picture is recorded; a removed one, cleared."""
    mono = str(tmp_path / "mono.fits")
    picture = color_preview_path_for(mono)
    Path(picture).write_bytes(b"jpeg")
    stacking = SimpleNamespace(stacked_image=mono, color_preview_image="")
    target = SimpleNamespace(stacking=stacking)
    made = color_from_other_stack.ColorPreviewOutcome(True, path=picture)
    color_from_other_stack._record_on_target(target, mono, made)
    assert stacking.color_preview_image == picture
    Path(picture).unlink()
    skipped = color_from_other_stack.ColorPreviewOutcome(False, "no color stack")
    color_from_other_stack._record_on_target(target, mono, skipped)
    assert stacking.color_preview_image == ""
