"""Unit tests for the per-frame photometry helpers in `frame_photometry`.

Covers aperture flux measurement, star centroid re-location, and
frame-to-frame offset calculation -- the stateless, per-frame steps
extracted from `variability_analyzer`.

Two groups of tests use synthetic frames whose true answer is known
(`astrometricslib.test.synthetic`):

* Aperture placement (review item S2). A star's flux must not depend on
  where inside a pixel the star sits, and each star must be re-centered on
  its own centroid in every frame of a drifting sequence.
* Capture times (review item S5). A frame with a missing or unreadable
  ``DATE-OBS`` must be rejected with a reason. A readable one must come
  back as the right UTC instant. Neither case may use the wall clock.
"""

import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.photometry.pre_processing.detector_noise import DetectorNoise
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import (
    FrameRejection,
    ObservationTimeError,
    StarPosition,
    _calculate_frame_offset,
    _measure_aperture_flux,
    _process_single_frame_worker,
    locate_star_centroid,
    parse_observation_time,
    read_observation_time,
    refine_star_centroid,
)
from astrometricslib.test.synthetic import (
    SyntheticStar,
    drifted_stars,
    make_drifted_sequence,
    make_photometry_fits,
    make_photometry_frame,
)

SATURATION_ADU = 65535.0

# The stars in these tests are 3.5 px FWHM (full width at half maximum, the
# star's apparent size). With the default 4 px aperture, a circle this small
# cuts into the star's light, so a misplaced aperture shows up as a flux
# error of more than 1 percent. That makes the tests sensitive to rounding.
TEST_FWHM_PX = 3.5

# A 3-FWHM aperture radius holds more than 99.99 percent of a Gaussian
# star's light, so its flux can be compared with the injected total.
WIDE_APERTURE_RADIUS_PX = 3.0 * TEST_FWHM_PX

# Frame shape and star layout for the drifting-sequence tests. The stars
# are more than 100 px apart so the alignment step's 81 px search windows
# never overlap a neighbor, and there are more than the 5 stars the
# alignment step needs.
SEQUENCE_SHAPE = (340, 480)
SEQUENCE_STAR_FLUX_ADU = 30000.0
SEQUENCE_DRIFT_PX = (0.3, -0.2)
SEQUENCE_FRAME_COUNT = 10


def _sequence_stars() -> list[SyntheticStar]:
    """Build six stars at mixed sub-pixel positions, well separated.

    Returns
    -------
    stars : `list` [`SyntheticStar`]
        Stars in a 3 by 2 grid, each with a fractional part to its position.
    """
    positions = [
        (110.5, 110.3),
        (230.0, 110.0),
        (350.2, 110.7),
        (110.0, 230.5),
        (230.7, 230.2),
        (350.5, 230.5),
    ]
    return [SyntheticStar(x, y, SEQUENCE_STAR_FLUX_ADU, TEST_FWHM_PX) for x, y in positions]


def _measure_sequence(
    tmp_path: Path, frames: list[np.ndarray], stars: list[SyntheticStar]
) -> tuple[np.ndarray, list[list[StarPosition]]]:
    """Write a sequence to FITS files and measure it with the frame worker.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Folder for the FITS files.
    frames : `list` [`numpy.ndarray`]
        The frames, in time order.
    stars : `list` [`SyntheticStar`]
        The stars in frame 0. They are the reference positions.

    Returns
    -------
    fluxes : `numpy.ndarray`
        Shape ``(n_frames, n_stars)``, ADU per second. The exposure is 1 s.
    positions : `list` [`list` [`StarPosition`]]
        For each frame, where each star's aperture sat.
    """
    reference = [(f"star{i}", star.x, star.y) for i, star in enumerate(stars)]
    alignment = [(star.x, star.y, 0.0) for star in stars]
    fluxes = np.zeros((len(frames), len(stars)))
    positions = []
    for index, frame in enumerate(frames):
        path = tmp_path / f"frame_{index:02d}.fits"
        header = fits.PrimaryHDU(frame)
        header.header["DATE-OBS"] = f"2026-05-24T04:{index:02d}:30.570"
        header.header["EXPTIME"] = 1.0
        header.writeto(path)
        _, result = _process_single_frame_worker((
            str(path),
            reference,
            alignment,
            SATURATION_ADU,
            DetectorNoise(),
        ))
        assert isinstance(result, tuple), f"frame {index} was not measured: {result!r}"
        _, star_fluxes, _, _, _, _, star_positions, _ = result
        fluxes[index] = [star_fluxes[f"star{i}"][0] for i in range(len(stars))]
        positions.append([star_positions[f"star{i}"] for i in range(len(stars))])
    return fluxes, positions


def _measure_single_star(x: float, y: float, fwhm: float, radius: float | None = None) -> float:
    """Measure one noise-free star placed exactly at ``(x, y)``.

    Parameters
    ----------
    x, y : `float`
        True star position and the aperture center, in pixels.
    fwhm : `float`
        Star FWHM in pixels.
    radius : `float`, optional
        Aperture radius. The default aperture (radius 4) when `None`. A
        radius gives a background ring 3 to 8 px outside it.

    Returns
    -------
    flux : `float`
        The measured flux in ADU, for a star of 50000 ADU on a 200 ADU sky.
    """
    frame = make_photometry_frame(
        [SyntheticStar(x, y, 50000.0, fwhm)], shape=(200, 200), sky_adu=200.0, add_noise=False
    )
    if radius is None:
        flux, _ = _measure_aperture_flux(frame, x, y, saturation_threshold_adu=SATURATION_ADU)
    else:
        flux, _ = _measure_aperture_flux(
            frame,
            x,
            y,
            radius=radius,
            annulus_inner=radius + 3.0,
            annulus_outer=radius + 8.0,
            cutout_radius=int(radius + 9.0),
            saturation_threshold_adu=SATURATION_ADU,
        )
    return flux


def test_measure_aperture_flux_pins_sum_method_exact() -> None:
    """Verifies the exact flux value produced by sum_method="exact".

    photutils.aperture supports several ways to decide how much of a
    boundary pixel counts as "inside" a circular aperture ("center",
    "exact", "subpixel"), and they give slightly different answers right
    at the edge of the circle. This test uses a hard-edged square star
    (not a soft, star-like glow) so that edge-pixel handling actually
    matters, and pins the exact number photutils gives us today for
    "exact" (its own default method). If this test ever fails after an
    unrelated change, the most likely cause is someone changed which
    method is used -- that is a real, if small, change to every star's
    measured brightness and should be a deliberate decision, not an
    accident.
    """
    data = np.full((100, 100), 500.0)
    data[46:54, 46:54] = 1000.0  # hard-edged square, not a soft star glow

    flux, is_saturated = _measure_aperture_flux(data, 50, 50, saturation_threshold_adu=65000.0)
    assert flux == pytest.approx(23824.693920034817)
    assert is_saturated is False


def test_measure_aperture_flux_empty_annulus_uses_local_median() -> None:
    """Verifies the local-cutout-median fallback when the annulus is empty.

    A star near the edge of a small picture might not have any
    background ring (annulus) pixels to measure at all. When that
    happens, this function should fall back to the median brightness of
    the whole local cutout around the star.
    """
    data = np.full((9, 9), 500.0)
    data[2:7, 2:7] = 1000.0  # star block big enough that the local median is still background (500)

    flux, is_saturated = _measure_aperture_flux(data, 4, 4, cutout_radius=4, saturation_threshold_adu=65000.0)
    assert flux == pytest.approx(12500.0)
    assert is_saturated is False


def test_measure_aperture_flux_empty_annulus_honors_explicit_fallback() -> None:
    """Verifies an explicit fallback_background overrides the local median.

    This is the background the per-frame worker passes in (a frame-wide
    sampled median), which differs from the local-cutout-median fallback
    that the reference-frame flux measurements use when no
    fallback_background is given.
    """
    data = np.full((9, 9), 500.0)
    data[2:7, 2:7] = 1000.0

    flux, is_saturated = _measure_aperture_flux(
        data, 4, 4, cutout_radius=4, fallback_background=100.0, saturation_threshold_adu=65000.0
    )
    assert flux == pytest.approx(32606.192982974677)
    assert is_saturated is False


def test_locate_star_centroid_finds_shifted_star() -> None:
    """Verifies a star shifted from its expected position is re-located."""
    data = np.full((200, 200), 500.0)
    data[63:68, 73:78] += 4000.0  # true centroid near (75, 65)

    located = locate_star_centroid(data, expected_x=70.0, expected_y=60.0)
    assert located is not None
    located_x, located_y = located
    assert abs(located_x - 75.0) < 1.0
    assert abs(located_y - 65.0) < 1.0


def test_locate_star_centroid_returns_none_outside_frame() -> None:
    """Verifies a search window falling off the frame edge returns None."""
    data = np.full((50, 50), 500.0)
    assert locate_star_centroid(data, expected_x=5.0, expected_y=5.0) is None


def test_locate_star_centroid_returns_none_with_no_signal() -> None:
    """Verifies a flat, background-only window returns None."""
    data = np.full((200, 200), 500.0)
    assert locate_star_centroid(data, expected_x=100.0, expected_y=100.0) is None


def test_calculate_frame_offset_recovers_uniform_shift() -> None:
    """Verifies the median offset matches a known shift across many stars.

    Uses a known uniform frame-to-frame shift applied to every star.
    """
    data = np.full((600, 600), 500.0)
    true_shift_x, true_shift_y = 3.0, -2.0
    # Well-separated (>100px apart) so each star's search window
    # (default half-width 40) never overlaps another's injected signal.
    reference_positions = [
        (100.0, 100.0, 0.0),
        (300.0, 100.0, 0.0),
        (500.0, 100.0, 0.0),
        (100.0, 300.0, 0.0),
        (300.0, 300.0, 0.0),
        (500.0, 500.0, 0.0),
    ]
    for reference_x, reference_y, _ in reference_positions:
        x_center = round(reference_x + true_shift_x)
        y_center = round(reference_y + true_shift_y)
        data[y_center - 2 : y_center + 3, x_center - 2 : x_center + 3] += 4000.0

    delta_x, delta_y = _calculate_frame_offset(data, reference_positions)
    assert abs(delta_x - true_shift_x) < 0.5
    assert abs(delta_y - true_shift_y) < 0.5


def test_calculate_frame_offset_returns_zero_when_too_few_stars_located() -> None:
    """Verifies the offset defaults to (0, 0) when too few stars are located.

    Fewer than 5 reference stars re-located (e.g. all fell on empty
    background) should not produce a spurious shift estimate.
    """
    data = np.full((100, 100), 500.0)
    reference_positions = [(20.0, 20.0, 0.0), (40.0, 40.0, 0.0)]
    assert _calculate_frame_offset(data, reference_positions) == (0.0, 0.0)


# --- S2: the aperture follows the star, not the pixel grid -------------------


def test_flux_is_the_same_at_a_half_pixel_and_a_whole_pixel_position() -> None:
    """Verifies a star at (100.5, 100.5) and one at (100.0, 100.0) match.

    With the default 4 px aperture, rounding the aperture center to a whole
    pixel puts it about 0.7 px off a star at (100.5, 100.5). That loses
    about 1.5 percent of the star's light. With the aperture on the star's
    own position the two fluxes agree to 0.2 percent. The remaining
    difference is how a circle's edge cuts through square pixels.
    """
    half_pixel = _measure_single_star(100.5, 100.5, TEST_FWHM_PX)
    whole_pixel = _measure_single_star(100.0, 100.0, TEST_FWHM_PX)

    assert abs(half_pixel - whole_pixel) / ((half_pixel + whole_pixel) / 2) < 0.002


def test_flux_matches_the_injected_total_at_any_sub_pixel_position() -> None:
    """Verifies a wide aperture recovers the injected flux to 0.2 percent.

    An aperture of 3 FWHM radius holds more than 99.99 percent of a
    Gaussian star, so the measured flux must equal the injected 50000 ADU
    wherever the star sits inside its pixel.
    """
    for x, y in [(100.5, 100.5), (100.0, 100.0), (100.25, 100.75), (100.3, 100.1)]:
        flux = _measure_single_star(x, y, TEST_FWHM_PX, radius=WIDE_APERTURE_RADIUS_PX)
        assert flux == pytest.approx(50000.0, rel=0.002), (x, y)


def test_aperture_keeps_the_fractional_part_of_its_position() -> None:
    """Verifies the aperture center is not rounded to a whole pixel.

    Moving the aperture center by 0.4 px, from the star's true position
    to a nearby one that rounds to the same whole pixel, must change the
    measured flux. If the function rounded its input, both calls would
    give the identical number.
    """
    frame = make_photometry_frame(
        [SyntheticStar(100.4, 100.4, 50000.0, TEST_FWHM_PX)], shape=(200, 200), add_noise=False
    )
    on_star, _ = _measure_aperture_flux(frame, 100.4, 100.4, saturation_threshold_adu=SATURATION_ADU)
    off_star, _ = _measure_aperture_flux(frame, 100.0, 100.0, saturation_threshold_adu=SATURATION_ADU)

    assert on_star != off_star
    assert on_star > off_star


@pytest.mark.parametrize("fwhm", [3.0, 3.5])
def test_refine_star_centroid_recovers_sub_pixel_position(fwhm: float) -> None:
    """Verifies the centroid step finds a star's position to 0.05 px.

    The search starts at the whole pixel (100, 100) and the star is up to
    half a pixel away in each direction. The returned position must be the
    star's true position, and the result must say no fallback was needed.
    """
    for dx in (-0.5, -0.2, 0.0, 0.3, 0.5):
        for dy in (-0.5, 0.0, 0.4):
            star = SyntheticStar(100.0 + dx, 100.0 + dy, 40000.0, fwhm)
            frame = make_photometry_frame([star], shape=(200, 200), add_noise=False)

            position = refine_star_centroid(frame, 100.0, 100.0, saturation_threshold_adu=SATURATION_ADU)

            assert position.is_refined
            assert position.fallback_reason is None
            assert math.hypot(position.x - star.x, position.y - star.y) < 0.05


def test_refine_star_centroid_falls_back_when_it_moves_too_far() -> None:
    """Verifies a centroid more than 1.5 px away is refused with a reason.

    The only star is 3 px from the starting position. A move that large
    means the box found something other than the expected star, so the
    result must keep the starting position and say why.
    """
    star = SyntheticStar(103.0, 100.0, 40000.0, TEST_FWHM_PX)
    frame = make_photometry_frame([star], shape=(200, 200), add_noise=False)

    position = refine_star_centroid(frame, 100.0, 100.0, saturation_threshold_adu=SATURATION_ADU)

    assert not position.is_refined
    assert (position.x, position.y) == (100.0, 100.0)
    assert position.fallback_reason is not None
    assert "moved more than" in position.fallback_reason


def test_refine_star_centroid_falls_back_on_a_saturated_pixel() -> None:
    """Verifies a box holding a saturated pixel is refused with a reason.

    A saturated star has a flat, clipped core, so its centroid is not
    trustworthy. The star is bright enough that its peak pixel reaches the
    saturation level.
    """
    star = SyntheticStar(100.2, 100.1, 1200000.0, TEST_FWHM_PX)
    frame = make_photometry_frame([star], shape=(200, 200), add_noise=False)
    assert frame.max() >= SATURATION_ADU

    position = refine_star_centroid(frame, 100.0, 100.0, saturation_threshold_adu=SATURATION_ADU)

    assert not position.is_refined
    assert (position.x, position.y) == (100.0, 100.0)
    assert position.fallback_reason is not None
    assert "saturated" in position.fallback_reason


def test_refine_star_centroid_falls_back_without_light_or_near_the_edge() -> None:
    """Verifies an empty box and a box off the frame edge both fall back.

    A flat sky has no light above the sky level to take a centroid of. A
    box that would extend past the frame edge cannot be measured fully.
    """
    flat = np.full((200, 200), 500.0)
    star = SyntheticStar(3.0, 3.0, 40000.0, TEST_FWHM_PX)
    edge_frame = make_photometry_frame([star], shape=(200, 200), add_noise=False)

    empty = refine_star_centroid(flat, 100.0, 100.0, saturation_threshold_adu=SATURATION_ADU)
    edge = refine_star_centroid(edge_frame, 3.0, 3.0, saturation_threshold_adu=SATURATION_ADU)

    assert not empty.is_refined
    assert "no light" in (empty.fallback_reason or "")
    assert not edge.is_refined
    assert "outside the frame" in (edge.fallback_reason or "")


def test_drifting_sequence_without_noise_has_constant_flux_and_true_positions(tmp_path: Path) -> None:
    """Verifies ten drifting frames give constant fluxes and true positions.

    The field drifts by (0.3, -0.2) px per frame, 2.7 px in x over the
    sequence. With no noise, each star's flux must stay within 0.2 percent
    of its mean, and each aperture must sit within 0.05 px of the star's
    true position (`drifted_stars`). No star may need the fallback.
    """
    stars = _sequence_stars()
    frames = make_drifted_sequence(
        stars, SEQUENCE_FRAME_COUNT, SEQUENCE_DRIFT_PX, shape=SEQUENCE_SHAPE, add_noise=False
    )

    fluxes, positions = _measure_sequence(tmp_path, frames, stars)

    deviation = np.abs(fluxes - fluxes.mean(axis=0)) / fluxes.mean(axis=0)
    assert deviation.max() < 0.002
    for frame_index, frame_positions in enumerate(positions):
        truth = drifted_stars(stars, frame_index, SEQUENCE_DRIFT_PX, shape=SEQUENCE_SHAPE)
        for star_position, true_star in zip(frame_positions, truth, strict=True):
            assert star_position.is_refined
            assert math.hypot(star_position.x - true_star.x, star_position.y - true_star.y) < 0.05


def test_drifting_sequence_with_noise_shows_photon_noise_and_no_drift_trend(tmp_path: Path) -> None:
    """Verifies noisy drifting frames show photon noise and no drift trend.

    The noise is realistic for a 30000 ADU star: Poisson noise on a sky of
    200 ADU per pixel and 5 ADU of read noise. Two checks are made.

    * For each star, a straight line fitted to flux against frame number
      has a slope within 3 standard errors of zero. A drift-driven loss of
      light would show as a trend.
    * Pooled over the stars, the scatter about those lines is within a
      factor of 1.4 of the noise predicted from the star, the sky inside
      the aperture, the read noise, and the sky estimate from the ring.
    """
    stars = _sequence_stars()
    sky_adu, read_noise_adu = 200.0, 5.0
    frames = make_drifted_sequence(
        stars,
        SEQUENCE_FRAME_COUNT,
        SEQUENCE_DRIFT_PX,
        shape=SEQUENCE_SHAPE,
        sky_adu=sky_adu,
        read_noise_adu=read_noise_adu,
        gain_e_per_adu=1.0,
        seed=11,
    )

    fluxes, positions = _measure_sequence(tmp_path, frames, stars)

    frame_numbers = np.arange(SEQUENCE_FRAME_COUNT)
    residual_squares = []
    for star_index in range(len(stars)):
        coefficients, covariance = np.polyfit(frame_numbers, fluxes[:, star_index], 1, cov=True)
        slope, slope_error = coefficients[0], math.sqrt(covariance[0, 0])
        assert abs(slope) < 3.0 * slope_error, (star_index, slope, slope_error)
        fitted = np.polyval(coefficients, frame_numbers)
        residual_squares.extend((fluxes[:, star_index] - fitted) ** 2)
    pooled_scatter = math.sqrt(sum(residual_squares) / (len(residual_squares) - 2 * len(stars)))

    aperture_area = math.pi * 4.0**2
    ring_area = math.pi * (12.0**2 - 7.0**2)
    star_light = float(fluxes.mean())
    sky_variance = aperture_area * (sky_adu + read_noise_adu**2)
    # The sky level comes from the ring's median, which has about 1.57 times
    # the variance of a mean; that error is multiplied by the aperture area.
    sky_estimate_variance = sky_variance * (math.pi / 2.0) * aperture_area / ring_area
    expected_scatter = math.sqrt(star_light + sky_variance + sky_estimate_variance)
    assert expected_scatter / 1.4 < pooled_scatter < expected_scatter * 1.4
    assert all(position.is_refined for frame_positions in positions for position in frame_positions)


def test_noisy_global_shift_is_not_pulled_toward_zero(tmp_path: Path) -> None:
    """Verifies the frame's global shift stays accurate on a noisy frame.

    Counting every pixel above the sky median as light makes the shift too
    small on a noisy frame, because noise alone lifts half the pixels above
    the median. The shift here is 2.7 px in x and -1.8 px in y. The measured
    shift must be within 0.15 px of that.
    """
    stars = _sequence_stars()
    frames = make_drifted_sequence(
        stars, SEQUENCE_FRAME_COUNT, SEQUENCE_DRIFT_PX, shape=SEQUENCE_SHAPE, seed=3
    )
    path = tmp_path / "last.fits"
    header = fits.PrimaryHDU(frames[-1])
    header.header["DATE-OBS"] = "2026-05-24T04:58:30.570"
    header.writeto(path)
    reference = [(f"star{i}", star.x, star.y) for i, star in enumerate(stars)]
    alignment = [(star.x, star.y, 0.0) for star in stars]

    _, result = _process_single_frame_worker((
        str(path),
        reference,
        alignment,
        SATURATION_ADU,
        DetectorNoise(),
    ))

    assert isinstance(result, tuple)
    _, _, shift_x, shift_y, _, _, _, _ = result
    assert shift_x == pytest.approx(2.7, abs=0.15)
    assert shift_y == pytest.approx(-1.8, abs=0.15)


# --- S5: a frame without a readable DATE-OBS is rejected --------------------


def test_fits_date_with_milliseconds_parses_to_the_right_utc_instant() -> None:
    """Verifies a FITS date and time becomes the matching UTC datetime."""
    parsed = parse_observation_time("2026-05-24T04:58:30.570")

    assert parsed == datetime(2026, 5, 24, 4, 58, 30, 570000)
    assert parsed.tzinfo is None


def test_date_with_trailing_z_parses_to_the_same_instant() -> None:
    """Verifies a trailing ``Z`` (UTC marker) reads as the same instant."""
    assert parse_observation_time("2026-05-24T04:58:30.570Z") == datetime(2026, 5, 24, 4, 58, 30, 570000)


@pytest.mark.parametrize(
    "value",
    ["not a date", "", "   ", "2026-13-45T00:00:00", "2026-05-24", "24/05/2026", 20260524, None],
)
def test_unreadable_date_obs_values_raise_with_a_reason(value: object) -> None:
    """Verifies bad values raise an error that says what is wrong.

    A date with no time of day is also refused, since it would give every
    frame of a night the same midnight timestamp.
    """
    with pytest.raises(ObservationTimeError) as error:
        parse_observation_time(value)

    assert "DATE-OBS" in str(error.value)


def test_read_observation_time_reports_a_missing_card() -> None:
    """Verifies a header with no DATE-OBS card raises, not guesses a time."""
    with pytest.raises(ObservationTimeError, match="missing"):
        read_observation_time(fits.Header())


def _worker_result_for_file(path: Path) -> object:
    """Run the frame worker on one file with a single reference star.

    Parameters
    ----------
    path : `pathlib.Path`
        The FITS file.

    Returns
    -------
    result : `object`
        What the worker returned for the file, without the path.
    """
    reference = [("star0", 100.0, 100.0)]
    _, result = _process_single_frame_worker((str(path), reference, [], SATURATION_ADU, DetectorNoise()))
    return result


def test_worker_rejects_a_frame_with_no_date_obs(tmp_path: Path) -> None:
    """Verifies a frame whose DATE-OBS card was removed is rejected.

    The rejection must carry a reason that names the missing card, and the
    worker must not return a measurement stamped with today's date.
    """
    path = tmp_path / "no_date.fits"
    make_photometry_fits(
        path, [SyntheticStar(100.0, 100.0, 20000.0)], date_obs="2026-05-24T04:58:30", exptime_s=30.0
    )
    with fits.open(path, mode="update", memmap=False) as handle:
        del handle[0].header["DATE-OBS"]

    result = _worker_result_for_file(path)

    assert isinstance(result, FrameRejection)
    assert "DATE-OBS" in result.reason
    assert "missing" in result.reason


def test_worker_rejects_a_frame_with_an_unparseable_date_obs(tmp_path: Path) -> None:
    """Verifies ``DATE-OBS = "not a date"`` rejects the frame with a reason."""
    path = tmp_path / "bad_date.fits"
    make_photometry_fits(path, [SyntheticStar(100.0, 100.0, 20000.0)], date_obs="not a date", exptime_s=30.0)

    result = _worker_result_for_file(path)

    assert isinstance(result, FrameRejection)
    assert "not a date" in result.reason


def test_worker_stamps_a_valid_fits_date_with_that_instant(tmp_path: Path) -> None:
    """Verifies a valid FITS date reaches the measurement as UTC."""
    path = tmp_path / "good_date.fits"
    make_photometry_fits(
        path, [SyntheticStar(100.0, 100.0, 20000.0)], date_obs="2026-05-24T04:58:30.570", exptime_s=30.0
    )

    result = _worker_result_for_file(path)

    assert isinstance(result, tuple)
    assert result[0] == datetime(2026, 5, 24, 4, 58, 30, 570000)
