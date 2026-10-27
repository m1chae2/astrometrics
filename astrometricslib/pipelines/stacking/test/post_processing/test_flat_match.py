"""Tests for the flat-to-light match: maps, the search and the rows.

The tests build small synthetic sensors. A flat frame holds vignetting, a few
ring-shaped dust shadows (donuts) and noise. A light frame holds a bias level,
a faint sky with the same donuts moved by a known shift and rotation (the
pattern is evaluated at the moved positions, so no resampling error enters),
stars and noise. They check that the search recovers the known placement, that
a flat with no dust or lights with other dust give no placement, that the bias,
the sky requirement, the target disc, the member list and the time limit work,
and that every missing case is a not-measured row with a reason.
"""

import math
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.quality.measurement import MeasurementStatus
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.shared.quality.record_measurements import (
    check_measurement_against_registry,
)
from astrometricslib.pipelines.stacking import metrics as stacking_metrics
from astrometricslib.pipelines.stacking.post_processing import flat_match
from astrometricslib.pipelines.stacking.post_processing.flat_match import (
    MAP_CELL_PX,
    FlatMatchOutcome,
    SkyRequirement,
    band_pass_dust_map,
    count_dust_shadows,
    find_dust_shadows,
    flat_match_rows,
    match_dust_maps,
    measure_flat_match,
)

SENSOR_PX = 1600
BIAS_ADU = 100.0
CONTEXT = PipelineRunContext(
    "M 99", "stacking", "1.3.0", "job-1", "job-1", datetime(2026, 10, 24, tzinfo=UTC)
)
# The tolerances the register entry states (STK-46).
SHIFT_TOLERANCE_PX = 3.0
ROTATION_TOLERANCE_DEG = 0.3
# A requirement the synthetic lights (800 ADU of sky) pass easily.
EASY_SKY = SkyRequirement(48.0)
# The synthetic donuts sit anywhere on the small sensor, so the tests use a
# small target disc (the default one would cover most of the sensor).
TEST_DISC_CELLS = 6.0


def _dust_donuts(seed: int, count: int = 7) -> list[tuple[float, float, float, float]]:
    """Choose dust shadows (soft discs) for a synthetic sensor.

    Parameters
    ----------
    seed : `int`
        Seed of the random generator.
    count : `int`, optional
        How many shadows.

    Returns
    -------
    donuts : `list` of `tuple`
        Each shadow's centre column, centre row, radius and depth (a fraction
        of the level).
    """
    rng = np.random.RandomState(seed)
    return [
        (
            float(rng.uniform(450, SENSOR_PX - 450)),
            float(rng.uniform(450, SENSOR_PX - 450)),
            float(rng.uniform(70, 160)),
            float(rng.uniform(0.012, 0.025)),
        )
        for _ in range(count)
    ]


def _dust(donuts: list[tuple[float, float, float, float]], x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Evaluate the dust pattern (relative dip) at sensor positions.

    Parameters
    ----------
    donuts : `list` of `tuple`
        The shadows (see `_dust_donuts`).
    x, y : `numpy.ndarray`
        Column and row of each position.

    Returns
    -------
    pattern : `numpy.ndarray`
        The relative change of the level: negative inside a shadow, with an
        edge about 10 pixels wide.
    """
    pattern = np.zeros(x.shape)
    for centre_x, centre_y, radius, depth in donuts:
        distance = np.hypot(x - centre_x, y - centre_y)
        pattern -= depth * 0.5 * (1.0 - np.tanh((distance - radius) / 10.0))
    return pattern


def _grid() -> tuple[np.ndarray, np.ndarray]:
    """Give the column and row of every pixel of the synthetic sensor.

    Returns
    -------
    x, y : `numpy.ndarray`
        Column and row arrays of shape (SENSOR_PX, SENSOR_PX).
    """
    return np.meshgrid(np.arange(SENSOR_PX, dtype=float), np.arange(SENSOR_PX, dtype=float))


def _moved_dust(
    donuts: list[tuple[float, float, float, float]], shift_px: tuple[float, float], rotation_deg: float
) -> np.ndarray:
    """Give the dust pattern as the lights see it after a shift and a rotation.

    A pattern point ``p`` of the flat appears at ``R (p + s - c) + c``, so the
    lights at ``x`` show the flat's pattern at ``R^-1 (x - c) + c - s``.

    Parameters
    ----------
    donuts : `list` of `tuple`
        The flat's donuts.
    shift_px : `tuple` [`float`, `float`]
        The shift (columns, rows).
    rotation_deg : `float`
        The rotation about the sensor centre.

    Returns
    -------
    pattern : `numpy.ndarray`
        The relative dip at each pixel of the lights.
    """
    x, y = _grid()
    centre = (SENSOR_PX - 1) / 2.0
    angle = math.radians(rotation_deg)
    dx, dy = x - centre, y - centre
    # R^-1 applied to (dx, dy).
    source_x = math.cos(angle) * dx + math.sin(angle) * dy + centre - shift_px[0]
    source_y = -math.sin(angle) * dx + math.cos(angle) * dy + centre - shift_px[1]
    return _dust(donuts, source_x, source_y)


def _vignette() -> np.ndarray:
    """Give the smooth darkening toward the corners.

    Returns
    -------
    vignette : `numpy.ndarray`
        1 at the centre, 0.6 at the corners.
    """
    x, y = _grid()
    centre = (SENSOR_PX - 1) / 2.0
    return 1.0 - 0.4 * ((x - centre) ** 2 + (y - centre) ** 2) / (2 * centre**2)


def _write_frame(path: Path, data: np.ndarray, *, color: bool = False) -> str:
    """Write a frame as a FITS file.

    Parameters
    ----------
    path : `pathlib.Path`
        The file.
    data : `numpy.ndarray`
        The pixels.
    color : `bool`, optional
        Whether to mark the frame as a color mosaic (``BAYERPAT``).

    Returns
    -------
    path_text : `str`
        The file's path.
    """
    header = fits.Header()
    if color:
        header["BAYERPAT"] = "RGGB"
    fits.PrimaryHDU(data.astype(np.float32), header=header).writeto(path)
    return str(path)


def _flat_paths(
    folder: Path, donuts: list[tuple[float, float, float, float]], count: int = 4, *, color: bool = False
) -> list[str]:
    """Write synthetic flat frames.

    Parameters
    ----------
    folder : `pathlib.Path`
        Where to write them.
    donuts : `list` of `tuple`
        The dust of the flat (empty for a flat with no dust).
    count : `int`, optional
        How many frames.
    color : `bool`, optional
        Whether to mark them as color mosaics.

    Returns
    -------
    paths : `list` of `str`
        The frames' paths.
    """
    x, y = _grid()
    rng = np.random.RandomState(1)
    level = 30000.0 * _vignette() * (1.0 + _dust(donuts, x, y))
    return [
        _write_frame(
            folder / f"flat_{index:02d}.fits", level * (1 + 0.002 * rng.randn(*level.shape)), color=color
        )
        for index in range(count)
    ]


def _bias_paths(folder: Path, count: int = 3, *, level: float = BIAS_ADU, color: bool = False) -> list[str]:
    """Write synthetic bias frames: a constant level and read noise.

    Parameters
    ----------
    folder : `pathlib.Path`
        Where to write them.
    count : `int`, optional
        How many frames.
    level : `float`, optional
        The bias level in ADU.
    color : `bool`, optional
        Whether to mark them as color mosaics.

    Returns
    -------
    paths : `list` of `str`
        The frames' paths.
    """
    rng = np.random.RandomState(5)
    return [
        _write_frame(
            folder / f"bias_{index:02d}.fits",
            level + 3.0 * rng.randn(SENSOR_PX, SENSOR_PX),
            color=color,
        )
        for index in range(count)
    ]


def _light_paths(
    folder: Path,
    donuts: list[tuple[float, float, float, float]],
    shift_px: tuple[float, float] = (0.0, 0.0),
    rotation_deg: float = 0.0,
    count: int = 12,
    *,
    color: bool = False,
    sky_adu: float = 800.0,
) -> list[str]:
    """Write synthetic raw light frames.

    Parameters
    ----------
    folder : `pathlib.Path`
        Where to write them.
    donuts : `list` of `tuple`
        The dust of the flat the lights are compared with.
    shift_px : `tuple` [`float`, `float`], optional
        The shift of the lights' dust.
    rotation_deg : `float`, optional
        The rotation of the lights' dust.
    count : `int`, optional
        How many frames.
    color : `bool`, optional
        Whether to mark them as color mosaics.
    sky_adu : `float`, optional
        The sky above the bias level, at the sensor centre.

    Returns
    -------
    paths : `list` of `str`
        The frames' paths.
    """
    x, y = _grid()
    pattern = _moved_dust(donuts, shift_px, rotation_deg)
    rng = np.random.RandomState(2)
    star_positions = rng.uniform(60, SENSOR_PX - 60, size=(8, 2))
    paths = []
    for index in range(count):
        frame = BIAS_ADU + sky_adu * _vignette() * (1.0 + pattern)
        for star_x, star_y in star_positions + rng.uniform(-3, 3, size=(8, 2)):
            low_x, low_y = int(star_x) - 20, int(star_y) - 20
            window = (slice(low_y, low_y + 41), slice(low_x, low_x + 41))
            frame[window] += 6000.0 * np.exp(
                -((x[window] - star_x) ** 2 + (y[window] - star_y) ** 2) / (2 * 2.5**2)
            )
        frame += 25.0 * rng.randn(*frame.shape)
        paths.append(_write_frame(folder / f"light_{index:02d}.fits", frame, color=color))
    return paths


def _measure(flats: list[str], lights: list[str], biases: list[str], **overrides: object) -> FlatMatchOutcome:
    """Run the measurement with the test's usual inputs.

    Parameters
    ----------
    flats, lights, biases : `list` of `str`
        The frames.
    **overrides
        Arguments of `measure_flat_match` that replace the usual ones.

    Returns
    -------
    outcome : `FlatMatchOutcome`
        What `measure_flat_match` gave.
    """
    arguments: dict[str, object] = {
        "bias_paths": biases,
        "sky_requirement": EASY_SKY,
        "target_radius_cells": TEST_DISC_CELLS,
    }
    arguments.update(overrides)
    return measure_flat_match(flats, lights, **arguments)


@pytest.fixture(scope="module")
def donuts() -> list[tuple[float, float, float, float]]:
    """Give the dust of the synthetic flat.

    Returns
    -------
    donuts : `list` of `tuple`
        Seven donuts.
    """
    return _dust_donuts(7)


@pytest.fixture(scope="module")
def flats(tmp_path_factory: pytest.TempPathFactory, donuts: list) -> list[str]:
    """Write the synthetic flat frames once.

    Parameters
    ----------
    tmp_path_factory : `pytest.TempPathFactory`
        The pytest factory for folders.
    donuts : `list`
        The flat's dust.

    Returns
    -------
    paths : `list` of `str`
        The flat frames.
    """
    return _flat_paths(tmp_path_factory.mktemp("flats"), donuts)


@pytest.fixture(scope="module")
def biases(tmp_path_factory: pytest.TempPathFactory) -> list[str]:
    """Write the synthetic bias frames once.

    Parameters
    ----------
    tmp_path_factory : `pytest.TempPathFactory`
        The pytest factory for folders.

    Returns
    -------
    paths : `list` of `str`
        The bias frames.
    """
    return _bias_paths(tmp_path_factory.mktemp("biases"))


def test_the_search_recovers_a_known_shift_and_rotation(
    tmp_path: Path, donuts: list, flats: list[str], biases: list[str]
) -> None:
    """Dust moved by a known amount gives that shift and rotation."""
    shift = (36.0, -28.0)
    rotation = 1.2
    outcome = _measure(flats, _light_paths(tmp_path, donuts, shift, rotation), biases)
    assert outcome.shift_x_px == pytest.approx(shift[0], abs=SHIFT_TOLERANCE_PX)
    assert outcome.shift_y_px == pytest.approx(shift[1], abs=SHIFT_TOLERANCE_PX)
    assert outcome.rotation_deg == pytest.approx(rotation, abs=ROTATION_TOLERANCE_DEG)
    assert outcome.best_correlation is not None and outcome.correlation_at_zero is not None
    assert outcome.best_correlation > 0.8
    assert outcome.best_correlation > outcome.correlation_at_zero + 0.02
    assert outcome.search_excess_sigmas is not None
    assert outcome.search_excess_sigmas >= flat_match.PLACEMENT_LIMIT_SIGMAS
    assert outcome.split_half_correlation is not None and outcome.split_half_correlation > 0.8
    assert outcome.flat_shadow_count is not None and outcome.flat_shadow_count >= 3


def test_the_uncertainty_holds_the_method_error_as_well_as_the_split_half_disagreement(
    tmp_path: Path, donuts: list, flats: list[str], biases: list[str]
) -> None:
    """The odd/even difference sees noise only; the method error joins it."""
    outcome = _measure(flats, _light_paths(tmp_path, donuts, (12.0, 8.0), 0.5), biases)
    assert outcome.shift_x_uncertainty_px is not None
    assert outcome.shift_x_uncertainty_px >= flat_match.METHOD_SHIFT_ERROR_PX
    assert outcome.rotation_uncertainty_deg is not None
    assert outcome.rotation_uncertainty_deg >= flat_match.METHOD_ROTATION_ERROR_DEG
    odd = SimpleNamespace(shift_x_px=3.0, shift_y_px=0.0, rotation_deg=0.0, at_edge=False)
    even = SimpleNamespace(shift_x_px=-3.0, shift_y_px=0.0, rotation_deg=0.0, at_edge=False)
    x, y, rotation = flat_match._placement_uncertainty(odd, even)
    assert x == pytest.approx(math.hypot(3.0, flat_match.METHOD_SHIFT_ERROR_PX))
    assert y == pytest.approx(flat_match.METHOD_SHIFT_ERROR_PX)
    assert rotation == pytest.approx(flat_match.METHOD_ROTATION_ERROR_DEG)


def test_the_search_recovers_a_negative_rotation(
    tmp_path: Path, donuts: list, flats: list[str], biases: list[str]
) -> None:
    """A rotation the other way round has the opposite sign."""
    outcome = _measure(flats, _light_paths(tmp_path, donuts, (-24.0, 16.0), -1.4), biases)
    assert outcome.rotation_deg == pytest.approx(-1.4, abs=ROTATION_TOLERANCE_DEG)
    assert outcome.shift_x_px == pytest.approx(-24.0, abs=SHIFT_TOLERANCE_PX)
    assert outcome.shift_y_px == pytest.approx(16.0, abs=SHIFT_TOLERANCE_PX)


def test_lights_with_the_same_dust_in_the_same_place_match_at_zero(
    tmp_path: Path, donuts: list, flats: list[str], biases: list[str]
) -> None:
    """With no displacement the correlations agree and the placement is 0."""
    outcome = _measure(flats, _light_paths(tmp_path, donuts), biases)
    assert outcome.correlation_at_zero is not None and outcome.correlation_at_zero > 0.85
    assert outcome.best_correlation == pytest.approx(outcome.correlation_at_zero, abs=0.05)
    assert outcome.shift_x_px == pytest.approx(0.0, abs=SHIFT_TOLERANCE_PX)
    assert outcome.shift_y_px == pytest.approx(0.0, abs=SHIFT_TOLERANCE_PX)
    assert outcome.rotation_deg == pytest.approx(0.0, abs=ROTATION_TOLERANCE_DEG)


def test_the_control_correlation_leaves_the_shadows_out() -> None:
    """A shared shadow correlates highly; masked, only noise is left."""
    rng = np.random.RandomState(4)
    rows, columns = np.mgrid[0:160, 0:160]
    shadow = -0.02 * (np.hypot(rows - 80, columns - 80) <= 8)
    flat_band = (shadow + 0.001 * ndimage_smooth(rng.randn(160, 160))).astype(np.float32)
    light_band = (shadow + 0.001 * ndimage_smooth(rng.randn(160, 160))).astype(np.float32)
    valid = np.ones((160, 160), dtype=bool)
    shadows = find_dust_shadows(flat_band, valid)
    assert shadows.count == 1
    with_shadow = flat_match._pearson(flat_band, light_band, valid)
    without_shadow = flat_match.correlation_outside_shadows(flat_band, valid, light_band, valid, shadows.mask)
    assert with_shadow is not None and with_shadow > 0.9
    assert without_shadow is not None and abs(without_shadow) < 0.3
    assert flat_match.correlation_outside_shadows(flat_band, valid, light_band, valid, valid) is None


def test_a_matching_flat_gives_the_control_row(
    tmp_path: Path, donuts: list, flats: list[str], biases: list[str]
) -> None:
    """The control is measured next to the zero-offset correlation."""
    outcome = _measure(flats, _light_paths(tmp_path, donuts), biases)
    assert outcome.correlation_at_zero is not None and outcome.correlation_outside_shadows is not None
    assert outcome.correlation_at_zero > 0.85
    assert outcome.correlation_outside_shadows <= outcome.correlation_at_zero


def test_dust_turned_half_a_turn_gives_no_placement(
    tmp_path: Path, donuts: list, flats: list[str], biases: list[str]
) -> None:
    """A half turn is outside the search, so there is no placement."""
    outcome = _measure(flats, _light_paths(tmp_path, donuts, (0.0, 0.0), 180.5), biases)
    assert outcome.shift_x_px is None and outcome.rotation_deg is None
    assert flat_match.STACKING_FLAT_LIGHT_BEST_SHIFT_X_PX in outcome.reasons


def test_a_color_mosaic_is_read_in_two_by_two_groups(tmp_path: Path, donuts: list) -> None:
    """Frames marked with a Bayer pattern are merged 2 x 2 and still match."""
    flat_folder = tmp_path / "flats"
    light_folder = tmp_path / "lights"
    bias_folder = tmp_path / "biases"
    for folder in (flat_folder, light_folder, bias_folder):
        folder.mkdir()
    flats = _flat_paths(flat_folder, donuts, color=True)
    biases = _bias_paths(bias_folder, color=True)
    outcome = _measure(flats, _light_paths(light_folder, donuts, (24.0, 16.0), 0.6, color=True), biases)
    assert outcome.shift_x_px == pytest.approx(24.0, abs=SHIFT_TOLERANCE_PX)
    assert outcome.shift_y_px == pytest.approx(16.0, abs=SHIFT_TOLERANCE_PX)
    assert outcome.rotation_deg == pytest.approx(0.6, abs=ROTATION_TOLERANCE_DEG)


def test_lights_with_other_dust_give_a_low_excess_and_no_placement(
    tmp_path: Path, flats: list[str], biases: list[str]
) -> None:
    """Dust that is not the flat's gives no shift or rotation."""
    outcome = _measure(flats, _light_paths(tmp_path, _dust_donuts(99)), biases)
    assert outcome.best_correlation is not None
    assert outcome.shift_x_px is None and outcome.rotation_deg is None
    assert outcome.displaced_correlation_std is not None
    assert outcome.search_excess_sigmas is not None
    assert outcome.search_excess_sigmas < flat_match.PLACEMENT_LIMIT_SIGMAS
    assert "null spreads" in outcome.reasons[flat_match.STACKING_FLAT_LIGHT_BEST_SHIFT_X_PX]


def test_a_flat_with_no_dust_still_measures_the_match_rows(
    tmp_path: Path, donuts: list, biases: list[str]
) -> None:
    """The shadow count is a diagnostic: with 0 shadows the match runs."""
    flat_folder = tmp_path / "flats"
    light_folder = tmp_path / "lights"
    flat_folder.mkdir()
    light_folder.mkdir()
    outcome = _measure(_flat_paths(flat_folder, []), _light_paths(light_folder, donuts), biases)
    assert outcome.flat_shadow_count == 0
    assert outcome.correlation_at_zero is not None and outcome.best_correlation is not None
    assert outcome.correlation_outside_shadows is not None
    assert outcome.shift_x_px is None
    assert outcome.split_half_correlation is not None


def test_missing_inputs_are_not_measured_with_a_reason(
    tmp_path: Path, donuts: list, flats: list[str], biases: list[str]
) -> None:
    """No flats, too few lights and unreadable files each give reasons."""
    no_flats = _measure([], ["a.fits"], biases)
    assert (
        no_flats.flat_shadow_count is None
        and "flat" in no_flats.reasons[flat_match.STACKING_FLAT_LIGHT_DUST_CORRELATION_AT_ZERO_OFFSET]
    )
    unreadable = _measure([str(tmp_path / "missing.fits")], ["a.fits"], biases)
    assert "could be read" in unreadable.reasons[flat_match.STACKING_FLAT_DUST_SHADOW_COUNT]
    few = _measure(flats, _light_paths(tmp_path, donuts, count=5), biases)
    assert few.flat_shadow_count is not None and few.flat_shadow_count > 0
    assert "light frame" in few.reasons[flat_match.STACKING_FLAT_LIGHT_BEST_CORRELATION]
    assert few.best_correlation is None


def test_without_bias_frames_or_a_sky_requirement_nothing_is_measured(
    tmp_path: Path, donuts: list, flats: list[str], biases: list[str]
) -> None:
    """The bias level and the camera profile's read noise are both needed."""
    lights = _light_paths(tmp_path, donuts)
    no_bias = _measure(flats, lights, [])
    assert no_bias.best_correlation is None
    assert "bias" in no_bias.reasons[flat_match.STACKING_FLAT_LIGHT_BEST_CORRELATION]
    assert no_bias.flat_shadow_count is not None
    unknown = _measure(flats, lights, biases, sky_requirement=SkyRequirement(None, "no read noise on file"))
    assert unknown.best_correlation is None
    assert unknown.reasons[flat_match.STACKING_FLAT_LIGHT_BEST_CORRELATION] == "no read noise on file"
    unreadable = _measure(flats, lights, [str(tmp_path / "missing_bias.fits")])
    assert "bias frames could be read" in unreadable.reasons[flat_match.STACKING_FLAT_LIGHT_BEST_CORRELATION]


def test_a_light_frame_with_too_little_sky_is_left_out_and_counted(
    tmp_path: Path, donuts: list, flats: list[str], biases: list[str]
) -> None:
    """Frames under the sky requirement leave the map and are counted."""
    lights = _light_paths(tmp_path, donuts, count=12)
    strict = _measure(flats, lights, biases, sky_requirement=SkyRequirement(5000.0))
    assert strict.best_correlation is None
    assert strict.lights_left_out_faint == 12
    reason = strict.reasons[flat_match.STACKING_FLAT_LIGHT_BEST_CORRELATION]
    assert "12 had a sky under 5000 ADU" in reason
    faint_folder = tmp_path / "faint"
    faint_folder.mkdir()
    faint = _light_paths(faint_folder, donuts, count=3, sky_adu=20.0)
    mixed = _measure(flats, [*lights[:10], *faint], biases)
    assert mixed.lights_left_out_faint == 3
    assert mixed.light_frame_count == 10
    assert mixed.best_correlation is not None


def test_the_bias_level_is_subtracted_before_the_division(
    tmp_path: Path, donuts: list, biases: list[str]
) -> None:
    """The bias level is taken off the cells before they are divided."""
    light = _light_paths(tmp_path, donuts, count=1)[0]
    with_bias = flat_match._read_cells(light, BIAS_ADU)
    without_bias = flat_match._read_cells(light, 0.0)
    assert with_bias is not None and without_bias is not None
    assert with_bias.cells is not None and without_bias.cells is not None
    assert with_bias.level_adu == pytest.approx(without_bias.level_adu - BIAS_ADU, rel=0.01)
    # The cells in ADU differ by exactly the bias level.
    raw_with = with_bias.cells * with_bias.level_adu + BIAS_ADU
    raw_without = without_bias.cells * without_bias.level_adu
    np.testing.assert_allclose(raw_with, raw_without, rtol=1e-4)
    assert not np.allclose(with_bias.cells, without_bias.cells, atol=1e-3)
    level = flat_match._bias_level(biases, flat_match._FrameReader(None, lambda: 0.0))
    assert level == pytest.approx(BIAS_ADU, abs=0.2)


def test_the_target_disc_is_set_aside_in_every_frame(tmp_path: Path, donuts: list) -> None:
    """The disc around the frame centre is NaN in the light map."""
    lights = _light_paths(tmp_path, donuts, count=10)
    reader = flat_match._FrameReader(None, lambda: 0.0)
    maps = flat_match._build_light_maps(lights, reader, BIAS_ADU, 48.0, 12.0)
    assert maps.full_map is not None
    disc = flat_match._target_disc(maps.full_map.shape, 12.0)
    assert disc.sum() > 400
    assert np.isnan(maps.full_map[disc]).all()
    margin = flat_match.EDGE_MARGIN_CELLS
    outside = maps.full_map[margin:-margin, margin:-margin][~disc[margin:-margin, margin:-margin]]
    assert np.isfinite(outside).mean() > 0.8
    assert flat_match.TARGET_MASK_RADIUS_CELLS == pytest.approx(25.0)


def test_the_time_limit_is_checked_before_each_frame(
    tmp_path: Path, donuts: list, flats: list[str], biases: list[str]
) -> None:
    """A fake clock that jumps 10 s per call stops after two frames."""
    ticks = iter(range(0, 10_000, 10))
    outcome = _measure(
        flats,
        _light_paths(tmp_path, donuts, count=10),
        biases,
        time_limit_seconds=25.0,
        clock=lambda: float(next(ticks)),
    )
    reason = outcome.reasons[flat_match.STACKING_FLAT_DUST_SHADOW_COUNT]
    assert reason == "time limit of 25 s reached after 2 frames"
    assert outcome.best_correlation is None and outcome.flat_shadow_count is None
    assert set(outcome.reasons.values()) == {reason}


def test_no_time_limit_means_no_check(
    tmp_path: Path, donuts: list, flats: list[str], biases: list[str]
) -> None:
    """With no limit the clock is never asked and the match is measured."""

    def clock() -> float:
        """Fail if the reader ever asks the time.

        Raises
        ------
        AssertionError
            Always.
        """
        raise AssertionError("the clock was read")

    reader = flat_match._FrameReader(None, lambda: 0.0)
    reader.check()
    assert reader.frames_read == 1
    outcome = _measure(flats, _light_paths(tmp_path, donuts), biases, time_limit_seconds=None, clock=clock)
    assert outcome.best_correlation is not None


def test_lights_of_another_size_are_not_compared(
    tmp_path: Path, donuts: list, flats: list[str], biases: list[str]
) -> None:
    """Lights binned differently from the flats give no match."""
    small = [
        _write_frame(tmp_path / f"small_{index}.fits", np.full((400, 400), 800.0) + index)
        for index in range(10)
    ]
    outcome = _measure(flats, small, biases)
    assert outcome.best_correlation is None
    assert "size" in outcome.reasons[flat_match.STACKING_FLAT_LIGHT_BEST_CORRELATION]


def test_frames_of_a_different_size_than_the_first_are_skipped(
    tmp_path: Path, donuts: list, flats: list[str], biases: list[str]
) -> None:
    """One odd-sized light frame is skipped and the rest are used."""
    paths = _light_paths(tmp_path, donuts, count=9)
    paths.insert(3, _write_frame(tmp_path / "odd.fits", np.full((400, 400), 800.0)))
    outcome = _measure(flats, paths, biases)
    assert outcome.light_frame_count == 9
    assert outcome.best_correlation is not None


def _frames_for(paths: list[str], first_timestamp: float = 1_792_000_000.0) -> list[SimpleNamespace]:
    """Wrap light paths as frame records, one minute apart.

    Parameters
    ----------
    paths : `list` of `str`
        The light frames.
    first_timestamp : `float`, optional
        The first frame's time.

    Returns
    -------
    frames : `list` of `types.SimpleNamespace`
        Records with ``path``, ``timestamp`` and the camera fields.
    """
    return [
        SimpleNamespace(
            path=path,
            timestamp=first_timestamp + 60.0 * index,
            camera="Cam",
            iso="0",
            offset="10",
            binning=1,
        )
        for index, path in enumerate(paths)
    ]


@pytest.fixture
def easy_sky(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every light frame the same easy sky requirement.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to replace the camera profile lookup.
    """
    monkeypatch.setattr(flat_match, "sky_requirement_for_frame", lambda _frame: EASY_SKY)
    monkeypatch.setattr(flat_match, "TARGET_MASK_RADIUS_CELLS", TEST_DISC_CELLS)


def test_rows_cover_every_metric_and_are_accepted_by_the_registry(
    tmp_path: Path, donuts: list, flats: list[str], biases: list[str], easy_sky: None
) -> None:
    """The record step builds ten rows, each valid for the registry."""
    frames = _frames_for(_light_paths(tmp_path, donuts, (4.0, 4.0), 0.2))
    rows = flat_match_rows(
        "M99_Stacked.fits",
        CONTEXT,
        flats,
        frames,
        is_spectral=False,
        bias_paths=biases,
        configuration=SimpleNamespace(get_flat_match_time_limit_seconds=lambda: 600.0),
    )
    names = {row.metric for row in rows}
    expected = {
        spec.name
        for spec in stacking_metrics.STACKING_METRICS
        if spec.name.startswith(("stacking.flat_light", "stacking.flat_dust", "stacking.light_dust"))
    }
    assert names == expected
    assert len(rows) == 10
    by_name = {row.metric: row for row in rows}
    assert all(
        row.status == MeasurementStatus.MEASURED
        for row in rows
        if row.metric
        in names
        - {
            stacking_metrics.STACKING_FLAT_LIGHT_BEST_SHIFT_X_PX,
            stacking_metrics.STACKING_FLAT_LIGHT_BEST_SHIFT_Y_PX,
            stacking_metrics.STACKING_FLAT_LIGHT_BEST_ROTATION_DEG,
        }
    )
    for row in rows:
        check_measurement_against_registry(row)
    assert by_name[stacking_metrics.STACKING_FLAT_DUST_SHADOW_COUNT].sample_count == 4
    assert by_name[stacking_metrics.STACKING_FLAT_LIGHT_DUST_CORRELATION_AT_ZERO_OFFSET].sample_count == 12
    assert "stacking.flat_light_dust_correlation" not in names


def test_only_frames_that_went_into_the_stack_are_read_in_time_and_path_order(
    tmp_path: Path, donuts: list, flats: list[str], biases: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only member frames are read, sorted by time and then path."""
    paths = _light_paths(tmp_path, donuts, count=6)
    frames = _frames_for(paths)
    frames[2].timestamp = frames[1].timestamp  # a tie: the path decides
    frames.reverse()
    seen: dict[str, list[str]] = {}

    def capture(_flats: object, light_paths: list[str], **_kwargs: object) -> FlatMatchOutcome:
        """Remember the light paths the measurement was given.

        Parameters
        ----------
        _flats : `object`
            Unused.
        light_paths : `list` of `str`
            The light paths, remembered.
        **_kwargs
            Unused.

        Returns
        -------
        outcome : `FlatMatchOutcome`
            An empty outcome.
        """
        seen["lights"] = list(light_paths)
        return FlatMatchOutcome()

    monkeypatch.setattr(flat_match, "measure_flat_match", capture)
    monkeypatch.setattr(flat_match, "sky_requirement_for_frame", lambda _frame: EASY_SKY)
    members = [paths[0], paths[1], paths[2], paths[4], paths[5]]
    flat_match_rows(
        "S.fits", CONTEXT, flats, frames, is_spectral=False, bias_paths=biases, member_paths=members
    )
    assert seen["lights"] == [paths[0], paths[1], paths[2], paths[4], paths[5]]
    flat_match_rows(
        "S.fits",
        CONTEXT,
        flats,
        frames,
        is_spectral=False,
        bias_paths=biases,
        member_paths=None,
        corrupt_paths=[paths[0]],
    )
    assert seen["lights"] == paths[1:]


def test_the_sky_requirement_comes_from_the_camera_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ten times the read noise over the gain; no entry, no requirement."""
    entry = SimpleNamespace(
        read_noise_e=SimpleNamespace(value=3.8), gain_e_per_adu=SimpleNamespace(value=0.783)
    )
    profile = SimpleNamespace(sensor_noise_for=lambda _gain, _offset, _binning: entry)
    monkeypatch.setattr(flat_match, "resolve_camera_profile", lambda _camera: profile)
    frame = SimpleNamespace(camera="Cam", iso="0", offset="10", binning=1)
    requirement = flat_match.sky_requirement_for_frame(frame)
    assert requirement.minimum_adu == pytest.approx(10.0 * 3.8 / 0.783)
    profile_without = SimpleNamespace(sensor_noise_for=lambda _gain, _offset, _binning: None)
    monkeypatch.setattr(flat_match, "resolve_camera_profile", lambda _camera: profile_without)
    missing = flat_match.sky_requirement_for_frame(frame)
    assert missing.minimum_adu is None and "read noise" in missing.reason


def test_a_spectral_stack_and_a_failure_are_not_measured(
    flats: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spectral stack and an unexpected error give not-measured rows."""
    rows = flat_match_rows("S.fits", CONTEXT, flats, [], is_spectral=True)
    assert len(rows) == 10 and all(row.status == MeasurementStatus.NOT_MEASURED for row in rows)
    assert "spectra" in rows[0].reason

    def explode(*_args: object, **_kwargs: object) -> None:
        """Raise like a failing measurement.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("boom")

    monkeypatch.setattr(flat_match, "measure_flat_match", explode)
    monkeypatch.setattr(flat_match, "sky_requirement_for_frame", lambda _frame: EASY_SKY)
    rows = flat_match_rows(
        "S.fits", CONTEXT, flats, [SimpleNamespace(path="x.fits", timestamp=1.0)], is_spectral=False
    )
    assert len(rows) == 10 and all(row.status == MeasurementStatus.NOT_MEASURED for row in rows)
    assert "RuntimeError" in rows[0].reason


def test_the_band_pass_removes_vignetting_and_keeps_donuts() -> None:
    """A vignetting that is not a polynomial goes to under 0.2%."""
    cells = SENSOR_PX // MAP_CELL_PX
    row_grid, column_grid = np.meshgrid(np.arange(cells), np.arange(cells), indexing="ij")
    radius = np.hypot(row_grid - cells / 2, column_grid - cells / 2) / (cells / 2)
    level = (1.0 / (1.0 + (radius / 2.0) ** 2) ** 2).astype(np.float32)
    band, valid = band_pass_dust_map(level)
    assert float(np.abs(band[valid]).max()) < 0.002
    dented = level.copy()
    dented[95:105, 95:105] *= 0.95
    band, valid = band_pass_dust_map(dented)
    assert float(band[100, 100]) < -0.03
    count, depth = count_dust_shadows(band, valid)
    assert count == 1
    assert depth >= flat_match.SHADOW_MINIMUM_DEPTH_FRACTION
    shadows = find_dust_shadows(band, valid)
    assert shadows.count == 1 and shadows.mask[100, 100] and shadows.mask.sum() >= 25


def test_the_band_pass_ignores_cells_that_were_set_aside() -> None:
    """NaN cells do not pull the smoothed level, and are not valid."""
    cells = np.ones((100, 100), dtype=np.float32)
    cells[40:50, 40:50] = np.nan
    band, valid = band_pass_dust_map(cells)
    assert not valid[45, 45]
    assert float(np.abs(band[valid]).max()) < 1e-4


def test_the_match_of_a_map_with_itself_is_one_at_zero() -> None:
    """Two equal maps correlate at 1 with no shift and no rotation."""
    rng = np.random.RandomState(3)
    cells = (1.0 + ndimage_smooth(rng.randn(200, 200))).astype(np.float32)
    band, valid = band_pass_dust_map(cells)
    match = match_dust_maps(band, valid, band, valid)
    assert match is not None
    assert match.correlation_at_zero == pytest.approx(1.0)
    assert match.shift_x_px == pytest.approx(0.0, abs=0.5)
    assert match.rotation_deg == pytest.approx(0.0, abs=0.05)
    assert match.excess_sigmas is not None and match.excess_sigmas > flat_match.PLACEMENT_LIMIT_SIGMAS
    without_null = match_dust_maps(band, valid, band, valid, with_null=False)
    assert without_null is not None
    assert without_null.excess_sigmas is None and without_null.displaced_std is None


def ndimage_smooth(array: np.ndarray) -> np.ndarray:
    """Smooth random noise into blobs a few cells across.

    Parameters
    ----------
    array : `numpy.ndarray`
        White noise.

    Returns
    -------
    smoothed : `numpy.ndarray`
        The noise smoothed with a Gaussian of 3 cells, scaled to 2%.
    """
    from scipy import ndimage

    smoothed = ndimage.gaussian_filter(array, 3.0)
    return 0.02 * smoothed / smoothed.std()


def test_the_light_frames_are_spread_evenly_and_capped() -> None:
    """At most the cap is picked, ends included; a short list is kept."""
    items = list(range(100))
    picked = flat_match._evenly_spaced(items, 24)
    assert len(picked) == 24 and picked[0] == 0 and picked[-1] == 99
    assert flat_match._evenly_spaced(items[:5], 24) == items[:5]


def _record_with_captured_rows(
    monkeypatch: pytest.MonkeyPatch, flat_rows_function: object, diagnostics: dict[str, object]
) -> list[list[str]]:
    """Run `record_stacking_quality` with the heavy steps replaced.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to replace the steps and the store.
    flat_rows_function : `object`
        What the record step calls for the flat-to-light rows.
    diagnostics : `dict`
        What the stacking run reported.

    Returns
    -------
    calls : `list` of `list` of `str`
        The metric names of each call to the store, in order.
    """
    from astrometricslib.pipelines.stacking.post_processing import record_quality
    from astrometricslib.pipelines.stacking.test.conformance_fixture import fixture_summary

    calls: list[list[str]] = []

    def capture(rows: list, *, config: object = None) -> int:
        """Remember the metric names of one call to the store.

        Parameters
        ----------
        rows : `list`
            The rows given to the store.
        config : `object`, optional
            Unused.

        Returns
        -------
        count : `int`
            The number of rows, as the store would report new ones.
        """
        calls.append([row.metric for row in rows])
        return len(rows)

    monkeypatch.setattr(
        record_quality, "measure_noise_safely", lambda *_a, **_k: SimpleNamespace(result=None)
    )
    monkeypatch.setattr(record_quality, "determine_stack_members", lambda *_: None)
    monkeypatch.setattr(record_quality, "compute_stack_science", lambda *_a, **_k: None)
    monkeypatch.setattr(record_quality, "stacking_measurements", lambda *_: [])
    monkeypatch.setattr(
        record_quality,
        "frame_geometry_measurements",
        lambda *_a, **_k: record_quality.FrameGeometryRows(rotation=[], pointing=[]),
    )
    monkeypatch.setattr(record_quality, "record_run_measurements", capture)
    monkeypatch.setattr(record_quality, "flat_match_rows", flat_rows_function)
    record_quality.record_stacking_quality(
        fixture_summary(), CONTEXT, "/stacks/M99_L_Stacked.fits", [], diagnostics, None
    )
    return calls


def test_the_record_step_stores_the_match_rows_in_their_own_call(
    flats: list[str], biases: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The match rows are their own call, given the flat and bias paths."""
    seen: dict[str, object] = {}

    def rows(
        name: str, context: PipelineRunContext, flat_paths: object, frames: object, **keywords: object
    ) -> list:
        """Remember the paths and give one row.

        Parameters
        ----------
        name : `str`
            The stack's file name.
        context : `PipelineRunContext`
            The run's shared facts.
        flat_paths : `object`
            The flat frames' paths, remembered.
        frames : `object`
            Unused.
        **keywords
            The other arguments, remembered.

        Returns
        -------
        rows : `list`
            The shadow-count row.
        """
        seen["flats"] = flat_paths
        seen.update(keywords)
        return flat_match.flat_match_measurements(
            flat_match.FlatMatchOutcome(flat_shadow_count=2, flat_frame_count=4),
            context,
            context.stack_subject(name),
        )[:1]

    calls = _record_with_captured_rows(
        monkeypatch,
        rows,
        {"flat_frame_paths": flats, "bias_frame_paths": biases, "corrupt_frames_skipped": ["bad.fits"]},
    )
    assert seen["flats"] == flats
    assert seen["bias_paths"] == biases
    assert seen["corrupt_paths"] == ["bad.fits"]
    assert seen["member_paths"] is None
    assert calls[-1] == [stacking_metrics.STACKING_FLAT_DUST_SHADOW_COUNT]


def test_a_match_step_that_raises_leaves_the_other_rows_stored_and_stores_no_match_row(
    flats: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing match step stores no match row; the earlier calls stand."""

    def explode(*_args: object, **_kwargs: object) -> list:
        """Raise like a failing step.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("broken")

    calls = _record_with_captured_rows(monkeypatch, explode, {"flat_frame_paths": flats})
    # The stack rows, then the rotation and pointing calls; no match call.
    assert len(calls) == 3
    match_names = {
        spec.name
        for spec in stacking_metrics.STACKING_METRICS
        if spec.name.startswith(("stacking.flat_light", "stacking.flat_dust", "stacking.light_dust"))
    }
    assert match_names
    stored = {name for call in calls for name in call}
    assert not stored & match_names
