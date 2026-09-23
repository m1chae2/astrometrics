"""Purpose: Validate the sign convention of auto-detected dispersion angles.

Description: `detect_dispersion_angle` measures a trace's tilt from image
data, and that angle is later fed back into
`SpectroscopyInstrument.get_dispersion_vector()` (via
`self.config.dispersion_angle_degrees`) to build the extraction line and
the visual overlay rectangle. The horizontal branch used to negate the
measured slope's arctan, which -- unlike the vertical branch, where the
negation is mathematically required by `get_dispersion_vector`'s 90-degree
base angle -- reproduced a dispersion vector tilted in the *opposite*
direction from the star's real streak whenever a horizontal-dispersion
camera had any real rotation. These tests catch that class of regression
directly: an angle that isn't self-consistent with
`get_dispersion_vector()` is a real, silent extraction-corrupting bug, not
just a cosmetic one.

A second, independent consumer of the same `detect_dispersion_angle()`
value -- `extract_with_flare_mask[_traced]`'s per-column tilt tracking,
used for the ZWO ASI533MM Pro flare-masking extraction path -- expects
the *opposite* sign convention from `get_dispersion_vector()` for
horizontal dispersion (the two already agree for vertical). Fixing the
`get_dispersion_vector()` side alone silently broke this second consumer,
so `_extract_via_flare_mask` applies a compensating sign flip for
horizontal orientation before calling into the extractor; the tests below
verify the actual end-to-end extracted signal follows the real trace
rather than checking an intermediate angle value in isolation.
"""

import numpy as np
import pytest

from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.spectroscopy.pipeline import (
    DISPERSION_ANGLE_ROI_HALF_WIDTH_PX,
    DISPERSION_ANGLE_ROI_MINIMUM_HALF_WIDTH_PX,
    SpectroscopyPipeline,
    _drop_spurious_trail_detections,
    _is_inside_dispersion_trail,
    _safe_dispersion_angle_roi_half_width_px,
)
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig


class MockAstrometricsImage(AstrometricsImage):
    """Mock AstrometricsImage that accepts a direct array input."""

    def __init__(self, data: np.ndarray):  # ruff: ignore[missing-return-type-special-method]
        """Initialize MockAstrometricsImage with given data."""
        self._data = data
        self._header = {}
        self._wcs = None

    @property
    def data(self) -> np.ndarray:
        """Image array data."""
        return self._data

    @property
    def header(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Image headers dict."""
        return self._header


def _build_pipeline(orientation: str) -> SpectroscopyPipeline:
    """Build a `SpectroscopyPipeline` with a fixed, known dispersion box.

    Returns
    -------
    pipeline : `SpectroscopyPipeline`
        The constructed pipeline.
    """
    camera = CameraConfig(
        name="TestCam",
        pixel_size_um=3.76,
        sensor_width_px=900,
        sensor_height_px=900,
        sensor_min_wavelength=350.0,
        sensor_max_wavelength=900.0,
    )
    config = SpectroscopyConfig(
        camera=camera,
        grating_distance_mm=16.5,
        dispersion_orientation=orientation,
        dispersion_direction="positive",
        dispersion_start_px=200.0,
    )
    return SpectroscopyPipeline(config=config)


def _build_tilted_trace(  # ruff: ignore[missing-return-type-private-function]
    orientation: str, star_pos: tuple[float, float], offset_px: float, length_px: float, true_slope: float
):
    """Build a synthetic image with a known-tilted trace.

    Returns
    -------
    data : `np.ndarray`
        The synthetic image array.
    """
    rng = np.random.default_rng(0)
    data = 10.0 + rng.normal(0, 0.5, size=(900, 900))
    x_star, y_star = star_pos
    data[int(y_star), int(x_star)] = 500.0

    if orientation == "horizontal":
        for x in range(int(x_star + offset_px), int(x_star + offset_px + length_px)):
            y = round(y_star + true_slope * (x - (x_star + offset_px)))
            if 5 <= y < data.shape[0] - 6:
                data[y - 5 : y + 6, x] = 100.0 + rng.normal(0, 2, size=11)
    else:
        for y in range(int(y_star + offset_px), int(y_star + offset_px + length_px)):
            x = round(x_star + true_slope * (y - (y_star + offset_px)))
            if 5 <= x < data.shape[1] - 6:
                data[y, x - 5 : x + 6] = 100.0 + rng.normal(0, 2, size=11)
    return data


@pytest.mark.parametrize("true_slope", [0.15, -0.15])
def test_horizontal_detected_angle_round_trips_through_dispersion_vector(true_slope: float) -> None:
    """A horizontal trace's detected angle must reproduce its own slope.

    Feeding the detected angle back through `get_dispersion_vector()`
    (exactly as `_resolve_global_dispersion_angle` does) must reproduce
    the same slope sign and magnitude that was actually measured in the
    image, not its mirror image.
    """
    pipeline = _build_pipeline("horizontal")
    star_pos = (400.0, 400.0)
    offset_px = pipeline.instrument.zero_order_offset_px
    length_px = pipeline.instrument.expected_length_px
    data = _build_tilted_trace("horizontal", star_pos, offset_px, length_px, true_slope)
    image = MockAstrometricsImage(data)

    detected_angle = pipeline.detect_dispersion_angle(image, star_pos)

    pipeline.instrument.config.dispersion_angle_degrees = detected_angle
    vec = pipeline.instrument.get_dispersion_vector()
    implied_slope = vec[1] / vec[0]

    assert implied_slope == pytest.approx(true_slope, abs=0.05)


@pytest.mark.parametrize("true_slope", [0.15, -0.15])
def test_vertical_detected_angle_round_trips_through_dispersion_vector(true_slope: float) -> None:
    """A vertical trace's detected angle must reproduce its own slope.

    Guards the vertical branch's negation (which is mathematically
    required by `get_dispersion_vector`'s 90-degree base angle, unlike
    the horizontal branch) against ever being "corrected" away by a
    future change that assumes the same fix applies to both branches.
    """
    pipeline = _build_pipeline("vertical")
    star_pos = (400.0, 400.0)
    offset_px = pipeline.instrument.zero_order_offset_px
    length_px = pipeline.instrument.expected_length_px
    data = _build_tilted_trace("vertical", star_pos, offset_px, length_px, true_slope)
    image = MockAstrometricsImage(data)

    detected_angle = pipeline.detect_dispersion_angle(image, star_pos)

    pipeline.instrument.config.dispersion_angle_degrees = detected_angle
    vec = pipeline.instrument.get_dispersion_vector()
    implied_slope = vec[0] / vec[1]

    assert implied_slope == pytest.approx(true_slope, abs=0.05)


def _build_pipeline_asi533(orientation: str) -> SpectroscopyPipeline:
    """Build an ASI533-named `SpectroscopyPipeline` (flare-mask path).

    Returns
    -------
    pipeline : `SpectroscopyPipeline`
        The constructed pipeline.
    """
    camera = CameraConfig(
        name="ZWO ASI533MM Pro",
        pixel_size_um=3.76,
        sensor_width_px=900,
        sensor_height_px=900,
        sensor_min_wavelength=350.0,
        sensor_max_wavelength=900.0,
    )
    config = SpectroscopyConfig(
        camera=camera,
        grating_distance_mm=16.5,
        dispersion_orientation=orientation,
        dispersion_direction="positive",
        dispersion_start_px=200.0,
        extraction_method="fixed",
        use_flare_mask_extraction=True,
    )
    return SpectroscopyPipeline(config=config)


def _build_tilted_trace_from_anchor(  # ruff: ignore[missing-return-type-private-function]
    orientation: str, star_pos: tuple[float, float], offset_px: float, length_px: float, true_slope: float
):
    """Build a synthetic image whose trace runs straight through the star.

    This is the physically correct model for a real optical tilt, and
    what `extract_with_flare_mask`'s per-column dynamic centering
    assumes when following the trace outward from the anchor.

    Returns
    -------
    data : `np.ndarray`
        The synthetic image array.
    """
    rng = np.random.default_rng(3)
    data = 10.0 + rng.normal(0, 0.5, size=(900, 900))
    x_star, y_star = star_pos
    data[int(y_star), int(x_star)] = 500.0

    if orientation == "horizontal":
        for x in range(int(x_star + offset_px), int(x_star + offset_px + length_px)):
            y = round(y_star + true_slope * (x - x_star))
            if 5 <= y < data.shape[0] - 6:
                data[y - 5 : y + 6, x] = 150.0 + rng.normal(0, 3, size=11)
    else:
        for y in range(int(y_star + offset_px), int(y_star + offset_px + length_px)):
            x = round(x_star + true_slope * (y - y_star))
            if 5 <= x < data.shape[1] - 6:
                data[y, x - 5 : x + 6] = 150.0 + rng.normal(0, 3, size=11)
    return data


@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
@pytest.mark.parametrize("true_slope", [0.03, -0.03, 0.06, -0.06])
def test_flare_mask_extraction_follows_a_real_tilted_trace(orientation: str, true_slope: float) -> None:
    """The ASI533 flare-mask path must extract the real trace, not noise.

    With auto-detection enabled, the extracted intensities for a
    tilted trace must sit well above the background level -- if the
    sign convention feeding `extract_with_flare_mask[_traced]` were
    wrong, the per-column window would walk away from the real trace
    and this would silently return near-background noise instead.
    """
    pipeline = _build_pipeline_asi533(orientation)
    star_pos = (400.0, 400.0)
    offset_px = 200.0
    length_px = 250.0
    data = _build_tilted_trace_from_anchor(orientation, star_pos, offset_px, length_px, true_slope)
    image = MockAstrometricsImage(data)

    result = pipeline._process_single_star(image, star_pos, auto_detect_angle=True)
    intensities = np.array(result["intensities"])

    # Background-only columns sum to ~10 * 11 == 110; a correctly
    # followed trace (peak ~150 over an 11px window) should average
    # well above that.
    assert intensities.mean() > 300.0


def test_roi_half_width_is_unchanged_with_no_close_neighbour() -> None:
    """A neighbour far beyond the ROI leaves the default half-width alone."""
    half_width = _safe_dispersion_angle_roi_half_width_px(
        (100.0, 100.0), [(500.0, 100.0)], "vertical", trail_length_px=600.0
    )

    assert half_width == DISPERSION_ANGLE_ROI_HALF_WIDTH_PX


def test_roi_half_width_shrinks_to_the_midpoint_of_a_close_neighbour() -> None:
    """A close neighbour shrinks the ROI to stop exactly at its midpoint."""
    # 10 px away in x, "vertical" orientation reads the x axis: half-width
    # must stop at 5 px so the ROI's edge never reaches the neighbour. Its
    # y is identical to the star's own, well inside one trail length, so
    # its own trail really could reach into this star's strip.
    half_width = _safe_dispersion_angle_roi_half_width_px(
        (100.0, 100.0), [(110.0, 100.0)], "vertical", trail_length_px=600.0
    )

    assert half_width == pytest.approx(5.0)


def test_roi_half_width_never_shrinks_below_the_floor() -> None:
    """An extremely close neighbour still leaves a usable minimum width."""
    half_width = _safe_dispersion_angle_roi_half_width_px(
        (100.0, 100.0), [(102.0, 100.0)], "vertical", trail_length_px=600.0
    )

    assert half_width == DISPERSION_ANGLE_ROI_MINIMUM_HALF_WIDTH_PX


def test_roi_half_width_reads_the_axis_matching_orientation() -> None:
    """Horizontal dispersion measures neighbour distance along y, not x."""
    # Same neighbour: far in x (would not shrink "vertical"), close in y
    # (must shrink "horizontal"). Its position on the other axis (108 for
    # "vertical"'s y-overlap check, 500 for "horizontal"'s x-overlap
    # check) is well within one trail length of the star's own, so its
    # trail really could reach into this star's strip either way.
    neighbor = [(500.0, 108.0)]

    vertical_half_width = _safe_dispersion_angle_roi_half_width_px(
        (100.0, 100.0), neighbor, "vertical", trail_length_px=600.0
    )
    horizontal_half_width = _safe_dispersion_angle_roi_half_width_px(
        (100.0, 100.0), neighbor, "horizontal", trail_length_px=600.0
    )

    assert vertical_half_width == DISPERSION_ANGLE_ROI_HALF_WIDTH_PX
    assert horizontal_half_width == pytest.approx(4.0)


def test_roi_half_width_ignores_a_neighbour_whose_trail_cannot_reach_here() -> None:
    """A neighbour whose own trail cannot reach this strip must not narrow it.

    Reproduces a real incident: reprocessing a standard star (Vega)
    measured its own dispersion angle at 2.11 degrees in isolation, but
    only 0.92 degrees as part of a real multi-star batch that happened to
    include an unrelated field star -- close to it in projection along the
    perpendicular axis, but far along the dispersion axis. Every star's
    trail is the same length, so that neighbour's own trail could never
    reach anywhere near this star's strip. The previous, perpendicular-
    axis-only distance check treated it as a contamination risk anyway,
    needlessly narrowing the ROI and degrading the standard star's own
    angle measurement (and the spectral classification that depends on
    it) for no real reason.
    """
    close_in_x_far_in_y = [(105.0, 5000.0)]

    half_width = _safe_dispersion_angle_roi_half_width_px(
        (100.0, 100.0), close_in_x_far_in_y, "vertical", trail_length_px=600.0
    )

    assert half_width == DISPERSION_ANGLE_ROI_HALF_WIDTH_PX


def _add_tilted_trace(
    data: np.ndarray,
    orientation: str,
    star_pos: tuple[float, float],
    offset_px: float,
    length_px: float,
    true_slope: float,
    seed: int,
) -> None:
    """Draw one more tilted trace onto an existing synthetic image, in place.

    Returns nothing; `data` is modified directly.
    """
    rng = np.random.default_rng(seed)
    x_star, y_star = star_pos
    if orientation == "horizontal":
        for x in range(int(x_star + offset_px), int(x_star + offset_px + length_px)):
            y = round(y_star + true_slope * (x - (x_star + offset_px)))
            if 5 <= y < data.shape[0] - 6:
                data[y - 5 : y + 6, x] = 100.0 + rng.normal(0, 2, size=11)
    else:
        for y in range(int(y_star + offset_px), int(y_star + offset_px + length_px)):
            x = round(x_star + true_slope * (y - (y_star + offset_px)))
            if 5 <= x < data.shape[1] - 6:
                data[y, x - 5 : x + 6] = 100.0 + rng.normal(0, 2, size=11)


def test_a_close_neighbour_no_longer_biases_the_shared_angle() -> None:
    """Two close, differently-tilted traces must not blend into one angle.

    Reproduces the real Albireo bug: two stars closer together (15 px)
    than the previous fixed 20 px ROI half-width, each with its own
    dispersed trace. Before the ROI was shrunk to respect a close
    neighbour, `detect_dispersion_angle`'s single line fit mixed pixels
    from both traces and returned neither star's true angle. With the fix,
    the angle resolved for the primary star must match its own trace, not
    some value pulled toward its neighbour's very different slope.
    """
    pipeline = _build_pipeline("vertical")
    star_a_pos = (400.0, 400.0)
    star_b_pos = (415.0, 400.0)  # 15 px away: inside the old fixed 20 px half-width.
    offset_px = pipeline.instrument.zero_order_offset_px
    length_px = pipeline.instrument.expected_length_px
    # A small, realistic tilt (matching the few degrees seen on real data):
    # large enough to detect, small enough that it stays inside the
    # narrowed ROI for most of the trace's length, the same way the
    # existing round-trip tests need their trace to stay inside a (wider)
    # ROI for theirs.
    true_slope_a = 0.02
    # Untilted, so B's trace never drifts toward A's strip and this test
    # isolates the ROI-width bug from any incidental trail crossing.
    true_slope_b = 0.0

    rng = np.random.default_rng(0)
    data = 10.0 + rng.normal(0, 0.5, size=(900, 900))
    _add_tilted_trace(data, "vertical", star_a_pos, offset_px, length_px, true_slope_a, seed=1)
    _add_tilted_trace(data, "vertical", star_b_pos, offset_px, length_px, true_slope_b, seed=2)
    image = MockAstrometricsImage(data)

    pipeline.process_image(image, target_stars=[star_a_pos, star_b_pos], auto_detect_angle=True)

    vec = pipeline.instrument.get_dispersion_vector()
    implied_slope = vec[0] / vec[1]

    assert implied_slope == pytest.approx(true_slope_a, abs=0.02)


def _star(x: float, y: float, sharpness: float | None = None) -> StellarObject:
    """Build a minimal `StellarObject` carrying only a position and sharpness.

    Returns
    -------
    star : `StellarObject`
        A stand-in for a detected source, as `_drop_spurious_trail_detections`
        and `_is_inside_dispersion_trail` read it.
    """
    star = StellarObject()
    star.star_data = {"xcentroid": x, "ycentroid": y}
    if sharpness is not None:
        star.star_data["sharpness"] = sharpness
    return star


def test_is_inside_dispersion_trail_true_for_a_point_on_the_trail() -> None:
    """A point along the trail axis, within its length, is inside it."""
    is_inside = _is_inside_dispersion_trail(
        (1479.0, 1894.0),
        (1492.8, 1501.9),
        np.array([0.0, 1.0]),
        offset_px=120.0,
        length_px=630.0,
        perpendicular_tolerance_px=20.0,
    )

    assert is_inside


def test_is_inside_dispersion_trail_false_beyond_the_trail_length() -> None:
    """A point past where the trail ends is not inside it."""
    is_inside = _is_inside_dispersion_trail(
        (1492.8, 3000.0),
        (1492.8, 1501.9),
        np.array([0.0, 1.0]),
        offset_px=120.0,
        length_px=630.0,
        perpendicular_tolerance_px=20.0,
    )

    assert not is_inside


def test_is_inside_dispersion_trail_false_off_to_the_side() -> None:
    """A point far to the side of the trail's line is not inside it."""
    is_inside = _is_inside_dispersion_trail(
        (1600.0, 1800.0),
        (1492.8, 1501.9),
        np.array([0.0, 1.0]),
        offset_px=120.0,
        length_px=630.0,
        perpendicular_tolerance_px=20.0,
    )

    assert not is_inside


def test_drop_spurious_trail_detections_drops_a_dim_point_in_a_bright_stars_trail() -> None:
    """A low-sharpness point sitting in Vega's own trail is dropped.

    Reproduces the real incident: Vega's master stack contains a bright,
    localized spectral feature ~392 px along its own trail that
    DAOStarFinder reported as a separate "star" (sharpness 0.268), which
    then legitimately narrowed the dispersion-angle ROI as if it were a
    real, close neighbour -- degrading Vega's own angle measurement and
    spectral classification for a neighbour that was never really there.
    """
    vega = _star(1492.8, 1501.9, sharpness=0.36)
    trail_artifact = _star(1479.0, 1894.0, sharpness=0.268)

    kept = _drop_spurious_trail_detections(
        [vega, trail_artifact],
        dispersion_vector=np.array([0.0, 1.0]),
        offset_px=120.0,
        length_px=630.0,
        perpendicular_tolerance_px=20.0,
    )

    assert kept == [vega]


def test_drop_spurious_trail_detections_keeps_a_sharp_real_companion() -> None:
    """A real, sharp star inside a brighter star's trail region is kept.

    Reproduces Albireo: its two real components are only ~17 px apart,
    so the fainter one's position legitimately falls inside the primary's
    trail region. Its detection is compact and star-like (sharpness 0.72
    on real data), unlike a spurious point on a trail, so it must survive
    this filter -- geometry inside a trail is not enough by itself to drop
    a candidate.
    """
    primary = _star(400.0, 400.0, sharpness=0.69)
    companion = _star(415.0, 400.0, sharpness=0.72)

    kept = _drop_spurious_trail_detections(
        [primary, companion],
        dispersion_vector=np.array([1.0, 0.0]),
        offset_px=120.0,
        length_px=630.0,
        perpendicular_tolerance_px=20.0,
    )

    assert kept == [primary, companion]


def test_drop_spurious_trail_detections_keeps_a_dim_star_outside_any_trail() -> None:
    """A low-sharpness star far from any brighter star's trail is kept.

    Low sharpness alone must not be enough to drop a candidate -- a real,
    faint field star can legitimately be less sharp than a bright one.
    Only the combination of low sharpness *and* sitting inside a brighter
    star's own trail is treated as likely spurious.
    """
    bright_star = _star(1492.8, 1501.9, sharpness=0.36)
    faint_unrelated_star = _star(80.1, 1994.8, sharpness=0.30)

    kept = _drop_spurious_trail_detections(
        [bright_star, faint_unrelated_star],
        dispersion_vector=np.array([0.0, 1.0]),
        offset_px=120.0,
        length_px=630.0,
        perpendicular_tolerance_px=20.0,
    )

    assert kept == [bright_star, faint_unrelated_star]
