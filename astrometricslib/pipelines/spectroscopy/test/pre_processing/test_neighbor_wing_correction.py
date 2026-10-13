"""Purpose: Unit tests for the pipeline side of the neighbour-wing stage.

Description: Builds synthetic images of two streaks side by side, with a blur
the code does not know beforehand (lopsided, with heavy wings). The blur is
measured on a separate isolated star and stored the way a camera's profile is
stored, then used to correct a crowded pair taken at a slightly different
focus. The tests check the geometry helpers (any dispersion direction), that a
faint star's spectrum is pulled toward the truth, that a bright star and a
lone star are left alone, that a distant star is not called a neighbour, that a
bad fit is skipped with a reason, and that the pipeline's switch turns the
whole stage on and off.
"""

import json

import numpy as np
import pytest

from astrometricslib.pipelines.spectroscopy.pre_processing.neighbor_trail_deblending import (
    measure_empirical_blur,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.neighbor_wing_correction import (
    STATUS_APPLIED,
    STATUS_NOT_NEEDED,
    StarTrace,
    StoredCrossTrailBlur,
    box_half_widths_px,
    correct_neighbor_wings,
    cross_trail_blur_record,
    load_cross_trail_blur,
    star_trace_from_result,
    working_frame_for,
)

ROWS = 420
COLUMNS = 240
SKY_LEVEL = 5.0
BOX_RADIUS_PX = 5
BAND_EDGES = np.arange(0.0, 401.0, 50.0)
BRIGHT_COLUMN = 140.0
FAINT_COLUMN = 123.0
FINE_STEP_PX = 0.05


def true_light(row: int, scale: float, centre_px: float) -> np.ndarray:
    """Give the fraction of a star's light in each column of one row.

    The blur is a Gaussian core plus a lopsided, heavy wing, stretched by
    `scale` (a focus change).

    Returns
    -------
    light : `numpy.ndarray`
        The fraction of the light in each column.
    """
    fine = np.arange(-90.0, 90.0 + FINE_STEP_PX, FINE_STEP_PX)
    x = fine / scale
    core = np.exp(-0.5 * (x / (1.3 + 0.3 * row / ROWS)) ** 2)
    core /= core.sum()
    wing = (1.0 + (x / np.where(x < 0, 9.0, 12.0)) ** 2) ** -1.6 * np.where(x < 0, 0.8, 1.0)
    wing /= wing.sum()
    density = 0.95 * core + 0.05 * wing
    cumulative = np.cumsum(density) / density.sum()
    cross = np.arange(COLUMNS, dtype=float)
    return np.interp(cross + 0.5 - centre_px, fine, cumulative, left=0.0, right=1.0) - np.interp(
        cross - 0.5 - centre_px, fine, cumulative, left=0.0, right=1.0
    )


def make_image(stars: list[tuple[float, np.ndarray]], scale: float, seed: int = 0) -> np.ndarray:
    """Build an image of vertical streaks with a sky level and noise.

    Returns
    -------
    image : `numpy.ndarray`
        The image, rows by columns.
    """
    image = np.full((ROWS, COLUMNS), SKY_LEVEL)
    for row in range(ROWS):
        for centre, spectrum in stars:
            image[row] += spectrum[row] * true_light(row, scale, centre)
    return image + np.random.default_rng(seed).normal(0.0, 1.0, image.shape)


def stored_blur(scale: float = 1.0) -> StoredCrossTrailBlur:
    """Measure a blur on an isolated star and store it as a camera's profile.

    Returns
    -------
    stored : `StoredCrossTrailBlur`
        The stored blur.
    """
    image = make_image([(110.0, np.full(ROWS, 6000.0))], scale, seed=1)
    blur = measure_empirical_blur(image, True, np.full(ROWS, 110.0), BAND_EDGES, 0.0)
    return StoredCrossTrailBlur(blur, BAND_EDGES, "Test camera", "synthetic isolated star", None)


def make_trace(image: np.ndarray, column: float) -> StarTrace:
    """Read a star's box flux off the image the way the extractor would.

    Returns
    -------
    trace : `StarTrace`
        The star's trace, sampled once per row.
    """
    rows = np.arange(400, dtype=float)
    low, high = round(column) - BOX_RADIUS_PX, round(column) + BOX_RADIUS_PX
    flux = image[:400, low : high + 1].sum(axis=1) - SKY_LEVEL * (2 * BOX_RADIUS_PX + 1)
    return StarTrace(
        x_px=np.full(400, float(round(column))),
        y_px=rows,
        distance_px=rows,
        box_flux=flux,
        box_half_width_px=np.full(400, BOX_RADIUS_PX + 0.5),
        zero_order_x_px=float(round(column)),
        zero_order_y_px=0.0,
    )


def make_pair(scale: float = 1.1) -> tuple[np.ndarray, list[StarTrace], np.ndarray]:
    """Build a bright and a faint streak, and their traces.

    Returns
    -------
    image, traces, faint_only_flux : `tuple`
        The image, the traces (bright star first), and what the faint star's
        box would hold with no bright star at all.
    """
    bright = np.full(ROWS, 8000.0)
    faint = np.linspace(3000.0, 100.0, ROWS)
    image = make_image([(BRIGHT_COLUMN, bright), (FAINT_COLUMN, faint)], scale, seed=2)
    faint_alone = make_image([(FAINT_COLUMN, faint)], scale, seed=3)
    return (
        image,
        [make_trace(image, BRIGHT_COLUMN), make_trace(image, FAINT_COLUMN)],
        make_trace(faint_alone, FAINT_COLUMN).box_flux,
    )


VERTICAL = (0.0, 1.0)


@pytest.mark.parametrize(
    ("vector", "is_transposed", "is_flipped"),
    [
        ((0.0, 1.0), False, False),
        ((0.0, -1.0), False, True),
        ((1.0, 0.0), True, False),
        ((-1.0, 0.0), True, True),
    ],
)
def test_working_frame_lays_every_dispersion_direction_down_the_rows(
    vector: tuple[float, float], is_transposed: bool, is_flipped: bool
) -> None:
    """However the spectrum runs, it runs down the working frame's rows."""
    plane = np.arange(30.0).reshape(5, 6)
    frame = working_frame_for(vector, plane.shape)
    assert frame.is_transposed is is_transposed
    assert frame.is_flipped is is_flipped
    # One step along the dispersion direction is one row further down.
    x0, y0 = 2.0, 2.0
    along_start, cross_start = frame.coordinates(np.array([x0]), np.array([y0]))
    along_next, cross_next = frame.coordinates(np.array([x0 + vector[0]]), np.array([y0 + vector[1]]))
    assert along_next[0] - along_start[0] == pytest.approx(1.0)
    assert cross_next[0] == pytest.approx(cross_start[0])
    # The laid-out image holds the same value at the same point.
    image = frame.image(plane)
    along, cross = frame.coordinates(np.array([3.0]), np.array([1.0]))
    assert image[int(along[0]), int(cross[0])] == plane[1, 3]


def test_box_half_widths_follow_the_traced_extraction_rule() -> None:
    """A traced box reaches 2.5 x the smoothed width, plus half a pixel."""
    half_widths = box_half_widths_px([1.9, 1.9, 0.0, 1.9], extraction_radius=8, sample_count=4)
    assert half_widths.tolist() == pytest.approx([5.25, 5.25, 8.5, 5.25])


def test_box_half_widths_are_at_least_one_pixel_and_ignore_a_single_noisy_width() -> None:
    """A tiny width gives a one-pixel box; one wild width moves nothing."""
    assert box_half_widths_px([0.1] * 5, extraction_radius=8, sample_count=5).tolist() == [1.5] * 5
    widths = [1.8] * 9 + [6.0] + [1.8] * 9
    assert box_half_widths_px(widths, extraction_radius=8, sample_count=19) == pytest.approx([5.0] * 19)


def test_an_untraced_extraction_uses_the_configured_radius() -> None:
    """Without trail widths every box reaches the configured radius."""
    assert box_half_widths_px(None, extraction_radius=6, sample_count=4).tolist() == [6.5] * 4


def test_the_asi533_stored_blur_loads_and_an_unknown_camera_has_none() -> None:
    """The camera's stored blur loads by name; another camera has none."""
    stored = load_cross_trail_blur("ZWO ASI 533MM Pro")
    assert stored is not None
    assert stored.blur.profiles.shape[0] == len(stored.band_edges_px) - 1 == 8
    assert np.allclose(stored.blur.profiles.sum(axis=1), 1.0, atol=1e-3)
    assert load_cross_trail_blur("A camera that does not exist") is None


def test_a_blur_record_survives_being_written_as_json() -> None:
    """The stored form has the expected shapes and can be written as JSON."""
    stored = stored_blur()
    record = json.loads(
        json.dumps(cross_trail_blur_record(stored.blur, BAND_EDGES, "Test camera", "synthetic", 20.5))
    )
    assert np.array(record["profiles"]).shape == stored.blur.profiles.shape
    assert record["band_edges_px"] == BAND_EDGES.tolist()
    assert record["focuser_temperature_c"] == pytest.approx(20.5)


def test_a_result_without_geometry_gives_no_trace() -> None:
    """A result that recorded no geometry cannot be corrected."""
    assert star_trace_from_result({"intensities": [1.0, 2.0]}) is None


def test_a_lone_star_needs_no_correction() -> None:
    """With no other streak in the image the star is left alone."""
    image, traces, _ = make_pair()
    outcomes = correct_neighbor_wings(image, traces[:1], stored_blur(), VERTICAL)
    assert outcomes[0].status == STATUS_NOT_NEEDED
    assert outcomes[0].corrected_flux is None


def test_the_faint_star_is_pulled_toward_its_true_spectrum() -> None:
    """The corrected faint star is closer to what it would hold alone."""
    image, traces, faint_alone = make_pair(scale=1.1)
    outcomes = correct_neighbor_wings(image, traces, stored_blur(), VERTICAL)
    faint = outcomes[1]
    assert faint.status == STATUS_APPLIED
    assert faint.fit is not None
    assert faint.fit.is_reliable
    last = slice(300, 400)
    error_before = np.mean(np.abs(traces[1].box_flux[last] - faint_alone[last]) / faint_alone[last])
    error_after = np.mean(np.abs(faint.corrected_flux[last] - faint_alone[last]) / faint_alone[last])
    assert error_before > 0.03
    assert error_after < error_before / 2
    assert faint.wing_fraction[-1] > faint.wing_fraction[0]


def test_the_bright_star_is_hardly_changed() -> None:
    """The faint star adds almost nothing to the bright star's box."""
    image, traces, _ = make_pair()
    bright = correct_neighbor_wings(image, traces, stored_blur(), VERTICAL)[0]
    assert bright.status == STATUS_APPLIED
    assert np.max(bright.wing_fraction) < 0.02
    assert np.allclose(bright.corrected_flux, traces[0].box_flux, rtol=0.03)


def test_without_a_stored_blur_the_star_is_skipped_and_says_why() -> None:
    """With a neighbour but no stored blur, a star is left alone."""
    image, traces, _ = make_pair()
    outcomes = correct_neighbor_wings(image, traces, None, VERTICAL)
    assert all(outcome.status.startswith("skipped: no blur profile") for outcome in outcomes)
    assert all(outcome.corrected_flux is None for outcome in outcomes)


def test_a_distant_star_is_not_a_neighbour() -> None:
    """A streak far across the image does not trigger any correction."""
    faint = np.linspace(3000.0, 250.0, ROWS)
    bright = np.full(ROWS, 8000.0)
    image = make_image([(BRIGHT_COLUMN, bright), (BRIGHT_COLUMN - 100.0, faint)], 1.1, seed=4)
    traces = [make_trace(image, BRIGHT_COLUMN), make_trace(image, BRIGHT_COLUMN - 100.0)]
    outcomes = correct_neighbor_wings(image, traces, stored_blur(), VERTICAL)
    assert [outcome.status for outcome in outcomes] == [STATUS_NOT_NEEDED, STATUS_NOT_NEEDED]


def test_a_blur_that_does_not_match_the_image_is_skipped_not_applied() -> None:
    """Far too blurry a crowded image for the stored blur is not trusted."""
    image, traces, _ = make_pair(scale=3.5)
    outcomes = correct_neighbor_wings(image, traces, stored_blur(1.0), VERTICAL)
    assert outcomes[1].status.startswith("skipped: the blur did not fit well enough")
    assert outcomes[1].corrected_flux is None


class _ArrayImage:
    """A minimal stand-in for an image; the pipeline only reads `.data`."""

    def __init__(self, data: np.ndarray) -> None:
        """Hold the array the correction will read."""
        self.data = data


def _result_from_trace(trace: StarTrace) -> dict[str, object]:
    """Build the extraction result the pipeline would have made for a star.

    Returns
    -------
    result : `dict`
        A result carrying the intensities and the geometry the extraction used.
    """
    return {
        "wavelengths": np.linspace(380.0, 800.0, trace.distance_px.size).tolist(),
        "intensities": trace.box_flux.tolist(),
        "target_pos": (trace.zero_order_x_px, trace.zero_order_y_px),
        "detected_angle": 0.0,
        "extraction_radius": BOX_RADIUS_PX,
        "trail_centerline_px": None,
        "trail_width_px": None,
        "distances_from_zero_order_px": trace.distance_px.tolist(),
        "base_position_px": (trace.zero_order_x_px, trace.zero_order_y_px),
        "dispersion_vector": VERTICAL,
    }


def _run_pipeline(
    is_switch_on: bool, blur: StoredCrossTrailBlur | None, count: int = 2
) -> tuple[list[dict[str, object]], list[StarTrace]]:
    """Run the pipeline's star loop over synthetic results.

    Returns
    -------
    results, traces : `tuple`
        The pipeline's results, and the traces they were made from.
    """
    from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
    from astrometricslib.utilities import CameraConfig, SpectroscopyConfig

    camera = CameraConfig(
        name="Test camera",
        pixel_size_um=3.76,
        sensor_width_px=COLUMNS,
        sensor_height_px=ROWS,
        grating_distance_mm=16.5,
        sensor_min_wavelength=300.0,
        sensor_max_wavelength=1000.0,
    )
    config = SpectroscopyConfig(camera=camera, grating_distance_mm=16.5, subtract_neighbor_wings=is_switch_on)
    pipeline = SpectroscopyPipeline(config)
    pipeline.cross_trail_blur = blur
    image, traces, _ = make_pair()
    traces = traces[:count]
    by_position = {
        (trace.zero_order_x_px, trace.zero_order_y_px): _result_from_trace(trace) for trace in traces
    }
    pipeline._process_single_star = lambda _image, pos, **_options: dict(by_position[pos[0], pos[1]])  # type: ignore[method-assign]
    stars = [(trace.zero_order_x_px, trace.zero_order_y_px) for trace in traces]
    return pipeline._process_target_stars(
        _ArrayImage(image), stars, limit=10, auto_detect_angle=False
    ), traces


def test_the_switch_off_leaves_every_result_as_extracted() -> None:
    """With the switch off no result has a record; the light is untouched."""
    results, traces = _run_pipeline(False, stored_blur())
    assert all("neighbor_wing_status" not in result for result in results)
    for result, trace in zip(results, traces, strict=True):
        assert result["intensities"] == trace.box_flux.tolist()


def test_the_switch_on_corrects_the_faint_star_and_records_what_it_did() -> None:
    """With the switch on the faint star's light drops and is recorded."""
    results, traces = _run_pipeline(True, stored_blur())
    faint = results[1]
    assert faint["neighbor_wing_status"] == STATUS_APPLIED
    assert len(faint["neighbor_wing_fraction"]) == len(faint["intensities"])
    assert np.sum(faint["intensities"]) < np.sum(traces[1].box_flux)
    assert results[0]["neighbor_wing_status"] == STATUS_APPLIED


def test_the_switch_on_without_a_stored_blur_only_records_why_nothing_was_done() -> None:
    """With no stored blur the light is untouched and the result says so."""
    results, traces = _run_pipeline(True, None)
    for result, trace in zip(results, traces, strict=True):
        assert result["neighbor_wing_status"].startswith("skipped: no blur profile")
        assert result["intensities"] == trace.box_flux.tolist()
        assert "neighbor_wing_fraction" not in result


def test_a_lone_star_gets_no_record_even_with_the_switch_on() -> None:
    """A star with no neighbour in the image has nothing recorded."""
    results, _ = _run_pipeline(True, stored_blur(), count=1)
    assert "neighbor_wing_status" not in results[0]
