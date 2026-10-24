"""Tests for the stack's noise in empty sky, on a fixed flux scale.

A synthetic stack has a known coordinate solution, known white noise and a few
bright stars. The tests check that the noise is recovered, that the apertures
chosen on the first stack are reused on the next, that a missing solution is
reported and not guessed, and that a reference frame's solution is used only
when it fits the stack.
"""

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib.pipelines.stacking.post_processing import empty_apertures
from astrometricslib.pipelines.stacking.post_processing.difference_noise import (
    discard_noise_sums,
    load_noise_sums,
    noise_sums_path,
    paired_noise_difference,
    paired_noise_ratio,
    save_noise_sums,
)
from astrometricslib.pipelines.stacking.post_processing.empty_apertures import (
    APERTURE_FILE_NAME,
    APERTURE_FILE_VERSION,
    APERTURE_SEPARATION_PX,
    FAINT_SOURCE_CLEARANCE_PX,
    MINIMUM_USABLE_APERTURES,
    OLD_APERTURES_REASON,
    REPLACEMENT_DEPTH_FACTOR,
    ApertureSetMismatchError,
    FaintSourceAnalysis,
    StackDepth,
    analyze_faint_sources,
    faint_source_distance,
    load_apertures,
    load_apertures_with_reason,
    place_empty_apertures,
    reset_apertures,
)
from astrometricslib.pipelines.stacking.post_processing.stack_image import (
    APERTURE_RADIUS_PX,
    MAD_SIGMA_ERROR_FACTOR,
    RING_INNER_PX,
    RING_OUTER_PX,
    signed_aperture_sum,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import (
    NO_WCS_REASON,
    measure_stack_noise,
)

SIZE = 900
PIXEL_SCALE_ARCSEC = 2.0
SKY_LEVEL = 100.0
STAR_POSITIONS = [(150 + 90 * column, 200 + 110 * row) for column in range(8) for row in range(6)]


def _sky_header(scale_arcsec: float = PIXEL_SCALE_ARCSEC) -> fits.Header:
    """Build a header with a plain tangent-plane sky solution.

    Parameters
    ----------
    scale_arcsec : `float`, optional
        The pixel scale.

    Returns
    -------
    header : `astropy.io.fits.Header`
        A header whose solution puts (RA, Dec) = (150, 2) at the image center.
    """
    header = fits.Header()
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["CRPIX1"] = SIZE / 2
    header["CRPIX2"] = SIZE / 2
    header["CRVAL1"] = 150.0
    header["CRVAL2"] = 2.0
    header["CDELT1"] = -scale_arcsec / 3600.0
    header["CDELT2"] = scale_arcsec / 3600.0
    return header


def _sky_image(
    noise_sigma: float,
    seed: int,
    star_amplitude: float = 2000.0,
    star_positions: list[tuple[float, float]] | None = None,
    star_sigma: float = 2.0,
) -> np.ndarray:
    """Build a flat sky with white noise and bright round stars.

    Parameters
    ----------
    noise_sigma : `float`
        The standard deviation of the white noise, per pixel.
    seed : `int`
        Seed of the noise.
    star_amplitude : `float`, optional
        The peak of each star above the sky.
    star_positions : `list` of `tuple`, optional
        The (column, row) of each star. A regular grid if left out.
    star_sigma : `float`, optional
        The Gaussian width of each star in pixels. The amplitude is scaled so
        the star's total light is the same whatever the width.

    Returns
    -------
    image : `numpy.ndarray`
        The image, float32.
    """
    positions = STAR_POSITIONS if star_positions is None else star_positions
    generator = np.random.default_rng(seed)
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    image = SKY_LEVEL + generator.normal(0.0, noise_sigma, (SIZE, SIZE))
    # The amplitudes differ, because the star finder skips stars as bright as
    # the brightest pixels (it takes those for saturated).
    amplitudes = np.linspace(0.3, 1.0, len(positions)) * star_amplitude * (2.0 / star_sigma) ** 2
    for (x, y), amplitude in zip(positions, amplitudes, strict=True):
        image += amplitude * np.exp(-((columns - x) ** 2 + (rows - y) ** 2) / (2 * star_sigma**2))
    return image.astype(np.float32)


def _write(path: Path, image: np.ndarray, header: fits.Header | None) -> str:
    """Write an image to a FITS file.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write.
    image : `numpy.ndarray`
        The pixels.
    header : `astropy.io.fits.Header` or `None`
        The header, or `None` for a bare one.

    Returns
    -------
    path : `str`
        The file path.
    """
    fits.PrimaryHDU(image, header=header).writeto(path, overwrite=True)
    return str(path)


def test_noise_of_white_sky_is_recovered_within_its_uncertainty(tmp_path: Path) -> None:
    """The measured spread matches the one the aperture area predicts."""
    stack = _write(tmp_path / "stack.fits", _sky_image(2.0, seed=1), _sky_header())
    outcome = measure_stack_noise(stack, None, str(tmp_path / APERTURE_FILE_NAME))
    assert outcome.result is not None
    area = math.pi * APERTURE_RADIUS_PX**2
    ring_pixels = math.pi * (RING_OUTER_PX**2 - RING_INNER_PX**2)
    expected = 2.0 * math.sqrt(area + area**2 / ring_pixels)
    result = outcome.result
    assert result.aperture_count >= 90
    assert abs(result.noise_adu - expected) < 3 * result.noise_uncertainty_adu
    # The spread is 1.4826 x MAD, so its error is that of the MAD, not of a
    # standard deviation: 1.1664 / sqrt(n).
    assert result.noise_uncertainty_adu / result.noise_adu == pytest.approx(
        MAD_SIGMA_ERROR_FACTOR / math.sqrt(result.aperture_count)
    )
    assert result.reference_star_count == 10
    assert result.reference_flux_adu > 1000
    assert result.normalized_noise == pytest.approx(result.noise_adu / result.reference_flux_adu)
    assert result.wcs_source == "stack header"
    assert result.apertures_created is True


def test_apertures_are_reused_by_a_later_stack_of_the_target(tmp_path: Path) -> None:
    """A second stack reuses the apertures; noise follows its pixels."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    first = _write(tmp_path / "first.fits", _sky_image(2.0, seed=1), _sky_header())
    second = _write(tmp_path / "second.fits", _sky_image(4.0, seed=2), _sky_header())
    first_outcome = measure_stack_noise(first, None, apertures)
    stored = Path(apertures).read_text()
    second_outcome = measure_stack_noise(second, None, apertures)
    assert Path(apertures).read_text() == stored
    assert first_outcome.result is not None and second_outcome.result is not None
    assert second_outcome.result.apertures_created is False
    ratio = second_outcome.result.noise_adu / first_outcome.result.noise_adu
    assert ratio == pytest.approx(2.0, rel=0.25)
    assert second_outcome.result.reference_flux_adu == pytest.approx(
        first_outcome.result.reference_flux_adu, rel=0.02
    )
    assert load_apertures(apertures).created_from == "first.fits"
    stored_set = next(iter(json.loads(stored)["sets"].values()))
    assert stored_set["aperture_radius_px"] == APERTURE_RADIUS_PX


def test_apertures_follow_the_sky_when_the_stack_is_shifted(tmp_path: Path) -> None:
    """A stack shifted by 30 pixels is measured at the same sky positions."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    first = _write(tmp_path / "first.fits", _sky_image(2.0, seed=1), _sky_header())
    measure_stack_noise(first, None, apertures)
    shifted_header = _sky_header()
    shifted_header["CRPIX1"] = SIZE / 2 + 30
    shifted_image = np.roll(_sky_image(2.0, seed=3), 30, axis=1)
    shifted = _write(tmp_path / "shifted.fits", shifted_image, shifted_header)
    outcome = measure_stack_noise(shifted, None, apertures)
    assert outcome.result is not None
    assert outcome.result.reference_star_count >= 5


def test_a_stack_with_no_solution_and_no_reference_frame_is_not_measured(tmp_path: Path) -> None:
    """Without a WCS the reason says so and no apertures are made."""
    stack = _write(tmp_path / "stack.fits", _sky_image(2.0, seed=1), None)
    outcome = measure_stack_noise(stack, None, str(tmp_path / APERTURE_FILE_NAME))
    assert outcome.result is None
    assert outcome.reason == NO_WCS_REASON
    assert not (tmp_path / APERTURE_FILE_NAME).exists()


def test_the_reference_frames_solution_is_used_when_the_stars_line_up(tmp_path: Path) -> None:
    """A stack with no solution takes the reference frame's, trimmed."""
    reference = _write(tmp_path / "reference.fits", _sky_image(2.0, seed=5), _sky_header())
    trimmed = _sky_image(2.0, seed=6)[20:, 10:]
    stack = _write(tmp_path / "stack.fits", trimmed, None)
    outcome = measure_stack_noise(stack, reference, str(tmp_path / APERTURE_FILE_NAME), crop_offset=(10, 20))
    assert outcome.result is not None
    assert outcome.result.wcs_source == "registration reference frame"
    stored = load_apertures(str(tmp_path / APERTURE_FILE_NAME))
    assert stored is not None
    assert stored.wcs_source == "registration reference frame"


def test_a_reference_frame_that_does_not_fit_the_stack_is_refused(tmp_path: Path) -> None:
    """If the bright stars do not match, no noise is measured."""
    reference_image = _sky_image(2.0, seed=5)[::-1].copy()
    reference = _write(tmp_path / "reference.fits", reference_image, _sky_header())
    stack = _write(tmp_path / "stack.fits", _sky_image(2.0, seed=6), None)
    outcome = measure_stack_noise(stack, reference, str(tmp_path / APERTURE_FILE_NAME))
    assert outcome.result is None
    assert "does not line up" in outcome.reason
    assert not (tmp_path / APERTURE_FILE_NAME).exists()


def test_a_stack_at_another_pixel_scale_is_refused(tmp_path: Path) -> None:
    """Apertures are refused at a very different pixel scale."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    first = _write(tmp_path / "first.fits", _sky_image(2.0, seed=1), _sky_header())
    measure_stack_noise(first, None, apertures)
    other = _write(tmp_path / "other.fits", _sky_image(2.0, seed=2), _sky_header(scale_arcsec=3.0))
    outcome = measure_stack_noise(other, None, apertures)
    assert outcome.result is None
    assert "pixel scale" in outcome.reason


def test_an_aperture_that_reaches_empty_edge_pixels_is_dropped() -> None:
    """Ring pixels at exactly zero (an empty stack edge) spoil a sum."""
    image = np.full((100, 100), 50.0)
    assert signed_aperture_sum(image, 50.0, 50.0) == pytest.approx(0.0, abs=1e-9)
    image[45:55, 45:55] = 0.0
    assert signed_aperture_sum(image, 50.0, 50.0) is None
    assert signed_aperture_sum(np.full((100, 100), 50.0), 5.0, 50.0) is None


def _scattered_stars(seed: int) -> list[tuple[float, float]]:
    """Draw star positions at random, well inside the image.

    Parameters
    ----------
    seed : `int`
        Seed of the draw.

    Returns
    -------
    positions : `list` of `tuple`
        48 (column, row) positions, 100 px or more from every edge.
    """
    generator = np.random.default_rng(seed)
    return [(float(x), float(y)) for x, y in generator.uniform(100, SIZE - 100, (48, 2))]


def test_bright_stars_the_frames_own_percentile_would_cut_still_match(tmp_path: Path) -> None:
    """The check keeps the frame's brightest stars.

    A stack's 99.99th percentile can be set by a compact bright feature, so its
    brightest stars are kept. The single frame has no such feature, and its own
    percentile would cut its brightest stars as saturated. About seven of the
    stack's ten brightest stars are in that group, so the check needs them.
    """
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    feature = 9000.0 * np.exp(-((columns - 450) ** 2 + (rows - 90) ** 2) / (2 * 8.0**2))
    reference = _write(tmp_path / "reference.fits", _sky_image(2.0, seed=5), _sky_header())
    stack = _write(tmp_path / "stack.fits", (_sky_image(2.0, seed=6) + feature).astype(np.float32), None)
    outcome = measure_stack_noise(stack, reference, str(tmp_path / APERTURE_FILE_NAME))
    assert outcome.result is not None, outcome.reason
    assert outcome.result.wcs_source == "registration reference frame"


def test_a_first_guess_solution_is_moved_onto_the_stored_reference_stars(tmp_path: Path) -> None:
    """A later stack with a first-guess header moves onto the stored stars.

    The camera software writes the commanded pointing, which is wrong by tens
    of pixels. The second stack's stars sit 30 rows and 25 columns from where
    the same header predicts them for the first stack.
    """
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    # Real stars are not on a grid; on a grid several shifts fit equally well.
    scattered = _scattered_stars(seed=3)
    first = _write(tmp_path / "first.fits", _sky_image(2.0, seed=1, star_positions=scattered), _sky_header())
    first_outcome = measure_stack_noise(first, None, apertures)
    assert first_outcome.result is not None
    moved_image = np.roll(_sky_image(2.0, seed=7, star_positions=scattered), shift=(30, -25), axis=(0, 1))
    reference = _write(tmp_path / "reference.fits", moved_image, _sky_header())
    stack = _write(tmp_path / "stack.fits", moved_image, None)
    outcome = measure_stack_noise(stack, reference, apertures)
    assert outcome.result is not None, outcome.reason
    assert not outcome.result.apertures_created
    assert outcome.result.reference_star_count >= 5
    assert outcome.result.reference_flux_adu == pytest.approx(
        first_outcome.result.reference_flux_adu, rel=0.1
    )
    assert outcome.result.normalized_noise == pytest.approx(first_outcome.result.normalized_noise, rel=0.3)


def test_a_first_guess_solution_that_finds_no_stored_stars_is_refused(tmp_path: Path) -> None:
    """With the stored reference stars missing, nothing is measured."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    first = _write(tmp_path / "first.fits", _sky_image(2.0, seed=1), _sky_header())
    assert measure_stack_noise(first, None, apertures).result is not None
    other = _sky_image(2.0, seed=8, star_positions=_scattered_stars(seed=4))
    reference = _write(tmp_path / "reference.fits", other, _sky_header())
    stack = _write(tmp_path / "stack.fits", other, None)
    outcome = measure_stack_noise(stack, reference, apertures)
    assert outcome.result is None
    assert "agree on a shift" in outcome.reason


def _fake_camera_profile(saturation_adu: float) -> object:
    """Build a stand-in camera profile with a chosen saturation level.

    Parameters
    ----------
    saturation_adu : `float`
        The saturation threshold, in ADU.

    Returns
    -------
    profile : `types.SimpleNamespace`
        An object with the fields the saturation check reads.
    """
    return SimpleNamespace(
        saturation_threshold_adu=SimpleNamespace(value=saturation_adu),
        photometric_linearity_limit_adu=None,
        camera_name="Test camera",
        is_generic_fallback=False,
    )


def test_the_spread_error_formula_matches_a_simulation() -> None:
    """The error of 1.4826 MAD is 1.1664 sigma / sqrt(n)."""
    generator = np.random.default_rng(7)
    samples = generator.normal(0.0, 1.0, (20000, 100))
    spreads = 1.4826 * np.median(np.abs(samples - np.median(samples, axis=1, keepdims=True)), axis=1)
    assert np.std(spreads) * math.sqrt(100) == pytest.approx(MAD_SIGMA_ERROR_FACTOR, rel=0.03)
    # The standard deviation's formula would give 0.71, a third too small.
    assert 1 / math.sqrt(2 * 99) * math.sqrt(100) < 0.75 * MAD_SIGMA_ERROR_FACTOR


def test_the_flux_scale_is_each_stars_ratio_to_its_first_stack_value(tmp_path: Path) -> None:
    """Stars twice as bright give a zero point of 2 and a small error."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    scattered = _scattered_stars(seed=3)
    first = _write(tmp_path / "first.fits", _sky_image(2.0, seed=1, star_positions=scattered), _sky_header())
    bright = _write(
        tmp_path / "bright.fits",
        _sky_image(2.0, seed=2, star_positions=scattered, star_amplitude=4000.0),
        _sky_header(),
    )
    first_result = measure_stack_noise(first, None, apertures).result
    bright_result = measure_stack_noise(bright, None, apertures).result
    assert first_result is not None and bright_result is not None
    assert first_result.reference_zero_point == pytest.approx(1.0)
    assert first_result.reference_zero_point_uncertainty == pytest.approx(0.0, abs=1e-9)
    assert bright_result.reference_zero_point == pytest.approx(2.0, rel=0.02)
    assert bright_result.reference_flux_adu == pytest.approx(2.0 * first_result.reference_flux_adu, rel=0.02)
    # Stars of very different brightness no longer add to the error: the
    # ratios scatter by the noise only.
    assert bright_result.reference_zero_point_uncertainty < 0.01


def test_the_flux_scale_does_not_change_when_the_seeing_does(tmp_path: Path) -> None:
    """The same stars blurred from 2 px to 3 px keep the same zero point."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    scattered = _scattered_stars(seed=3)
    sharp = _write(tmp_path / "sharp.fits", _sky_image(2.0, seed=1, star_positions=scattered), _sky_header())
    blurred = _write(
        tmp_path / "blurred.fits",
        _sky_image(2.0, seed=2, star_positions=scattered, star_sigma=3.0),
        _sky_header(),
    )
    sharp_result = measure_stack_noise(sharp, None, apertures).result
    blurred_result = measure_stack_noise(blurred, None, apertures).result
    assert sharp_result is not None and blurred_result is not None
    assert sharp_result.reference_aperture_radius_px >= 8.0
    assert blurred_result.reference_zero_point == pytest.approx(1.0, abs=0.03)


def test_stars_saturated_in_the_reference_frame_are_never_reference_stars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stars at the saturation level are skipped when choosing."""
    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.post_processing.stack_noise.resolve_camera_profile",
        lambda camera_name: _fake_camera_profile(1500.0),
    )
    scattered = _scattered_stars(seed=3)
    image = _sky_image(2.0, seed=1, star_positions=scattered)
    frame = _write(tmp_path / "frame.fits", image, _sky_header())
    stack = _write(tmp_path / "stack.fits", image, None)
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    outcome = measure_stack_noise(stack, frame, apertures)
    assert outcome.result is not None, outcome.reason
    chosen = load_apertures(apertures)
    assert chosen is not None
    wcs = WCS(_sky_header()).celestial
    columns, rows = wcs.world_to_pixel_values(chosen.reference_sky[:, 0], chosen.reference_sky[:, 1])
    for column, row in zip(columns, rows, strict=True):
        window = image[round(row) - 3 : round(row) + 4, round(column) - 3 : round(column) + 4]
        assert window.max() < 1500.0
    assert "1500" in chosen.saturation_source
    assert outcome.result.saturated_reference_stars == 0


def test_a_stored_star_that_saturates_on_a_later_stack_is_left_out_of_its_zero_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a later stack, a stored star at the saturation level is not used."""
    scattered = _scattered_stars(seed=3)
    image = _sky_image(2.0, seed=1, star_positions=scattered)
    first = _write(tmp_path / "first.fits", image, _sky_header())
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    assert measure_stack_noise(first, None, apertures).result is not None
    chosen = load_apertures(apertures)
    assert chosen is not None
    wcs = WCS(_sky_header()).celestial
    column, row = (float(value[0]) for value in wcs.world_to_pixel_values(*chosen.reference_sky[:1].T))
    peak = float(image[round(row), round(column)])
    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.post_processing.stack_noise.resolve_camera_profile",
        lambda camera_name: _fake_camera_profile(peak - 1.0),
    )
    frame = _write(tmp_path / "frame.fits", image, _sky_header())
    stack = _write(tmp_path / "stack.fits", image, _sky_header())
    result = measure_stack_noise(stack, frame, apertures).result
    assert result is not None
    assert result.saturated_reference_stars >= 1
    assert result.reference_star_count == len(chosen.reference_sky) - result.saturated_reference_stars


def test_a_header_solution_that_is_a_copied_guess_is_moved_onto_the_stored_stars(tmp_path: Path) -> None:
    """A header solution that is 30 px wrong is checked and moved."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    scattered = _scattered_stars(seed=3)
    first = _write(tmp_path / "first.fits", _sky_image(2.0, seed=1, star_positions=scattered), _sky_header())
    first_result = measure_stack_noise(first, None, apertures).result
    assert first_result is not None
    # The header is the first stack's, but the stars have moved: a copied
    # pointing guess, not a plate solution.
    moved_image = np.roll(_sky_image(2.0, seed=7, star_positions=scattered), shift=(30, -25), axis=(0, 1))
    stack = _write(tmp_path / "stack.fits", moved_image, _sky_header())
    result = measure_stack_noise(stack, None, apertures).result
    assert result is not None
    assert result.wcs_source == "stack header"
    assert "moved by +30.0 rows and -25.0 columns" in result.position_note
    assert "px rms" in result.position_note
    assert result.reference_zero_point == pytest.approx(1.0, abs=0.03)
    assert first_result.position_note == ""


def test_a_stack_rotated_beyond_the_limit_is_refused(tmp_path: Path) -> None:
    """A rotation of 0.5 degree cannot follow by a shift: no noise."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    scattered = _scattered_stars(seed=3)
    first = _write(tmp_path / "first.fits", _sky_image(2.0, seed=1, star_positions=scattered), _sky_header())
    assert measure_stack_noise(first, None, apertures).result is not None
    angle = math.radians(0.5)
    center = SIZE / 2
    rotated = [
        (
            center + (x - center) * math.cos(angle) - (y - center) * math.sin(angle),
            center + (x - center) * math.sin(angle) + (y - center) * math.cos(angle),
        )
        for x, y in scattered
    ]
    stack = _write(tmp_path / "rotated.fits", _sky_image(2.0, seed=2, star_positions=rotated), _sky_header())
    outcome = measure_stack_noise(stack, None, apertures)
    assert outcome.result is None
    assert "rotated by" in outcome.reason
    assert "0.1 deg" in outcome.reason


def test_apertures_are_at_least_the_separation_apart(tmp_path: Path) -> None:
    """No two apertures overlap, so no two sky rings share a pixel."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    stack = _write(tmp_path / "stack.fits", _sky_image(2.0, seed=1), _sky_header())
    assert measure_stack_noise(stack, None, apertures).result is not None
    chosen = load_apertures(apertures)
    assert chosen is not None
    wcs = WCS(_sky_header()).celestial
    columns, rows = wcs.world_to_pixel_values(chosen.aperture_sky[:, 0], chosen.aperture_sky[:, 1])
    points = np.column_stack([columns, rows])
    distances = np.hypot(*(points[:, None, :] - points[None, :, :]).transpose(2, 0, 1))
    np.fill_diagonal(distances, np.inf)
    assert distances.min() >= APERTURE_SEPARATION_PX - 1e-6
    assert APERTURE_SEPARATION_PX >= 2 * RING_OUTER_PX


def test_each_camera_and_optic_keeps_its_own_apertures_and_they_can_be_reset(tmp_path: Path) -> None:
    """Other equipment starts a new set; the old set stays."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    scattered = _scattered_stars(seed=3)
    header_a = _sky_header()
    header_a["INSTRUME"] = "Camera A"
    header_a["FOCALLEN"] = 400.0
    header_b = _sky_header()
    header_b["INSTRUME"] = "Camera B"
    header_b["FOCALLEN"] = 400.0
    stack_a = _write(tmp_path / "a.fits", _sky_image(2.0, seed=1, star_positions=scattered), header_a)
    stack_b = _write(tmp_path / "b.fits", _sky_image(2.0, seed=2, star_positions=scattered), header_b)
    first = measure_stack_noise(stack_a, None, apertures).result
    other = measure_stack_noise(stack_b, None, apertures).result
    assert first is not None and other is not None
    assert first.apertures_created and other.apertures_created
    sets = json.loads(Path(apertures).read_text())["sets"]
    assert sorted(sets) == ["camera a|?|400", "camera b|?|400"]
    assert load_apertures(apertures, "camera a|?|400").created_from == "a.fits"
    # Forgetting one camera leaves the other; forgetting all deletes the file.
    assert reset_apertures(apertures, "camera a|?|400") is True
    assert sorted(json.loads(Path(apertures).read_text())["sets"]) == ["camera b|?|400"]
    assert reset_apertures(apertures, "camera a|?|400") is False
    assert reset_apertures(apertures) is True
    assert not Path(apertures).exists()
    assert measure_stack_noise(stack_a, None, apertures).result.apertures_created is True


def test_an_aperture_file_from_the_first_development_version_is_replaced(tmp_path: Path) -> None:
    """A version-1 file is replaced by a new set."""
    apertures = tmp_path / APERTURE_FILE_NAME
    apertures.write_text(json.dumps({"version": 1, "apertures_ra_dec_deg": []}))
    stack = _write(tmp_path / "stack.fits", _sky_image(2.0, seed=1), _sky_header())
    result = measure_stack_noise(stack, None, str(apertures)).result
    assert result is not None and result.apertures_created
    assert json.loads(apertures.read_text())["version"] == APERTURE_FILE_VERSION


def _faint_field(seed: int, star_count: int, nebula: bool) -> tuple[np.ndarray, np.ndarray]:
    """Build unit noise, then add faint stars and a nebula to a copy.

    Parameters
    ----------
    seed : `int`
        Seed of the noise and the stars.
    star_count : `int`
        How many faint Gaussian stars (width 2.2 px FWHM) to add. Their peaks
        run from 0.2 to 30 times the noise of one pixel, evenly in the
        logarithm.
    nebula : `bool`
        Whether to add a halo 60 px wide and a few small knots.

    Returns
    -------
    noise, image : `numpy.ndarray`
        The noise alone and the noise plus the sources.
    """
    generator = np.random.default_rng(seed)
    noise = generator.normal(size=(SIZE, SIZE))
    image = noise.copy()
    width = 2.2 / 2.3548
    offsets = np.arange(-10, 11)
    kernel = np.exp(-(offsets[:, None] ** 2 + offsets[None, :] ** 2) / (2 * width**2))
    peaks = np.exp(generator.uniform(np.log(0.2), np.log(30.0), star_count))
    for peak in peaks:
        row, column = generator.integers(12, SIZE - 12, 2)
        image[row - 10 : row + 11, column - 10 : column + 11] += peak * kernel
    if nebula:
        rows, columns = np.mgrid[0:SIZE, 0:SIZE]
        image += np.exp(-((rows - 450) ** 2 + (columns - 450) ** 2) / (2 * 60.0**2))
        for _ in range(6):
            knot_row, knot_column = generator.uniform(100, SIZE - 100, 2)
            image += 0.6 * np.exp(-((rows - knot_row) ** 2 + (columns - knot_column) ** 2) / (2 * 6.0**2))
    return noise, image


def _spread_of_sums(plane: np.ndarray, positions: list[tuple[float, float]]) -> float:
    """Give the robust spread of the aperture sums at the given positions.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The image.
    positions : `list` [`tuple` [`float`, `float`]]
        The (row, column) of each aperture.

    Returns
    -------
    spread : `float`
        1.4826 times the median absolute deviation of the sums.
    """
    sums = np.array([signed_aperture_sum(plane, column, row) for row, column in positions])
    return 1.4826 * float(np.median(np.abs(sums - np.median(sums))))


def _unselected_spread(noise: np.ndarray) -> float:
    """Give the noise's own aperture spread at unselected positions.

    Parameters
    ----------
    noise : `numpy.ndarray`
        The noise alone.

    Returns
    -------
    spread : `float`
        The robust spread of 400 sums at random positions.
    """
    generator = np.random.default_rng(7)
    positions = [tuple(generator.uniform(20, SIZE - 20, 2)) for _ in range(400)]
    return _spread_of_sums(noise, positions)


def test_a_faint_source_keeps_the_apertures_away() -> None:
    """A source far below the star finder's limit still clears its area."""
    generator = np.random.default_rng(11)
    image = generator.normal(size=(SIZE, SIZE))
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    image += 1.5 * np.exp(-((rows - 300) ** 2 + (columns - 600) ** 2) / (2 * 3.0**2))
    distance = faint_source_distance(image)
    assert distance[300, 600] == 0
    positions = place_empty_apertures(image, np.empty((0, 2)))
    assert len(positions) >= MINIMUM_USABLE_APERTURES
    nearest = min(math.hypot(row - 300, column - 600) for row, column in positions)
    assert nearest > FAINT_SOURCE_CLEARANCE_PX


def test_the_mask_on_pure_noise_leaves_the_noise_unbiased_within_its_error() -> None:
    """On noise alone the rule keeps the apertures and does not pull low."""
    ratios = []
    for seed in range(8):
        noise = np.random.default_rng(300 + seed).normal(size=(SIZE, SIZE))
        positions = place_empty_apertures(noise, np.empty((0, 2)))
        assert len(positions) >= 90
        ratios.append(_spread_of_sums(noise, positions) / _unselected_spread(noise))
    error = float(np.std(ratios)) / math.sqrt(len(ratios))
    # The measured bias is in validation entry STK-36; the test only guards a
    # large bias (5% covers the mean's error and the rule's own bias).
    assert abs(float(np.mean(ratios)) - 1.0) < max(0.05, 3 * error)


def test_faint_stars_and_a_nebula_no_longer_inflate_the_noise() -> None:
    """The new placement recovers the injected noise; the old does not."""
    old_excess, new_ratios = [], []
    for seed in range(4):
        noise, image = _faint_field(500 + seed, star_count=3300, nebula=True)
        truth = _unselected_spread(noise)
        # The old rule: no faint-source mask (the finder sees bright stars).
        generator = np.random.default_rng(5)
        old_positions = [tuple(generator.uniform(20, SIZE - 20, 2)) for _ in range(300)]
        old_excess.append(_spread_of_sums(image, old_positions) / truth)
        new_positions = place_empty_apertures(image, np.empty((0, 2)))
        assert len(new_positions) >= MINIMUM_USABLE_APERTURES
        new_ratios.append(_spread_of_sums(image, new_positions) / truth)
    assert float(np.mean(old_excess)) > 1.15
    # Measured over 6 fields: the ratio is within 1.10 (the margin covers the
    # mean's error of about 0.03 and the rule's own small bias).
    assert abs(float(np.mean(new_ratios)) - 1.0) < 0.10


def test_too_few_clean_apertures_is_a_reason_not_a_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A field with no clean sky gives a reason, never the old placement."""
    monkeypatch.setattr(
        empty_apertures,
        "analyze_faint_sources",
        lambda plane: FaintSourceAnalysis(np.zeros(plane.shape), (1.0, 1.0, 1.0), 0.3, 1.0, 0.8),
    )
    stack = _write(tmp_path / "stack.fits", _sky_image(2.0, seed=1), _sky_header())
    outcome = measure_stack_noise(stack, None, str(tmp_path / APERTURE_FILE_NAME))
    assert outcome.result is None
    assert "faint sources" in outcome.reason
    assert "flagged 30% of the image" in outcome.reason
    assert "excludes 80% of it" in outcome.reason
    # The stored row, not only the outcome, carries the reason.
    from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
    from astrometricslib.pipelines.stacking.post_processing.record_quality import noise_measurements

    context = PipelineRunContext("M 99", "stacking", "1.3.0", "run", "run", datetime(2026, 10, 1, tzinfo=UTC))
    rows = noise_measurements(outcome, context, context.stack_subject("stack.fits"))
    assert rows and all("excludes 80% of it" in row.reason for row in rows)
    assert not (tmp_path / APERTURE_FILE_NAME).exists()


def test_an_aperture_file_of_version_two_is_replaced(tmp_path: Path) -> None:
    """Apertures placed by stars alone are never reused."""
    apertures = tmp_path / APERTURE_FILE_NAME
    apertures.write_text(json.dumps({"version": 2, "sets": {"unspecified": {}}}))
    stack = _write(tmp_path / "stack.fits", _sky_image(2.0, seed=1), _sky_header())
    result = measure_stack_noise(stack, None, str(apertures)).result
    assert result is not None and result.apertures_created
    assert json.loads(apertures.read_text())["version"] == APERTURE_FILE_VERSION


def _identity_stack(tmp_path: Path, name: str, seed: int, center_ra: float = 150.0) -> str:
    """Write a synthetic stack whose coordinate solution has the given center.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The folder.
    name : `str`
        The file name.
    seed : `int`
        Seed of the noise.
    center_ra : `float`, optional
        The right ascension of the image center, in degrees.

    Returns
    -------
    path : `str`
        The stack's path.
    """
    header = _sky_header()
    header["CRVAL1"] = center_ra
    return _write(tmp_path / name, _sky_image(2.0, seed=seed), header)


def test_the_mask_clearance_covers_the_rounding_of_the_center() -> None:
    """The clearance is the ring's outer radius plus one pixel."""
    assert FAINT_SOURCE_CLEARANCE_PX == pytest.approx(RING_OUTER_PX + 1.0)


def test_the_mask_scale_ignores_the_empty_edge() -> None:
    """A wide empty (zero) border does not pull the noise scale down."""
    noise = np.random.default_rng(21).normal(size=(SIZE, SIZE)).astype(np.float32) + 50.0
    bordered = noise.copy()
    bordered[:, :450] = 0.0
    full_scale = analyze_faint_sources(noise).noise_scales
    border_scale = analyze_faint_sources(bordered).noise_scales
    assert analyze_faint_sources(bordered).usable_fraction == pytest.approx(0.5)
    for full, edge in zip(full_scale, border_scale, strict=True):
        assert edge == pytest.approx(full, rel=0.15)


def test_a_set_has_an_identity_that_survives_the_file_and_names_its_depth(tmp_path: Path) -> None:
    """The identity is stored, repeats on reload and names the depth."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    stack = _identity_stack(tmp_path, "a.fits", seed=1)
    result = measure_stack_noise(stack, None, apertures, depth=StackDepth(61, 40.5)).result
    stored = load_apertures(apertures)
    assert result.aperture_identity == stored.identity
    assert stored.identity.startswith(f"v{APERTURE_FILE_VERSION}-")
    assert stored.defining_frame_count == 61
    assert stored.defining_effective_frame_count == pytest.approx(40.5)
    assert len(stored.mask_noise_scales) == 3 and all(scale > 0 for scale in stored.mask_noise_scales)
    assert 0.0 <= stored.flagged_fraction <= stored.excluded_fraction < 0.5
    assert "61 frames (effective 40.5)" in result.aperture_definition_note
    assert stored.identity in result.aperture_definition_note


def test_a_much_deeper_library_stack_replaces_the_set_and_a_close_one_does_not(tmp_path: Path) -> None:
    """Twice the effective frame count makes a new set with a new identity."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    first = _identity_stack(tmp_path, "first.fits", seed=1)
    old = measure_stack_noise(first, None, apertures, depth=StackDepth(10, 10.0)).result
    nearly = _identity_stack(tmp_path, "nearly.fits", seed=2, center_ra=150.3)
    kept = measure_stack_noise(
        nearly, None, apertures, depth=StackDepth(19, 0.99 * REPLACEMENT_DEPTH_FACTOR * 10.0)
    ).result
    assert kept.aperture_identity == old.aperture_identity and not kept.apertures_created
    deep = _identity_stack(tmp_path, "deep.fits", seed=3, center_ra=150.3)
    new = measure_stack_noise(
        deep, None, apertures, depth=StackDepth(20, REPLACEMENT_DEPTH_FACTOR * 10.0)
    ).result
    assert new.apertures_created and new.aperture_identity != old.aperture_identity
    assert load_apertures(apertures).identity == new.aperture_identity
    assert load_apertures(apertures).defining_effective_frame_count == pytest.approx(20.0)


def test_a_set_with_no_recorded_depth_is_never_replaced(tmp_path: Path) -> None:
    """With no recorded depth there is nothing to compare; the set stays."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    first = measure_stack_noise(_identity_stack(tmp_path, "a.fits", seed=1), None, apertures).result
    deep = _identity_stack(tmp_path, "b.fits", seed=2, center_ra=150.3)
    second = measure_stack_noise(deep, None, apertures, depth=StackDepth(500, 500.0)).result
    assert second.aperture_identity == first.aperture_identity and not second.apertures_created


def test_a_stack_that_may_not_choose_never_makes_or_replaces_a_set(tmp_path: Path) -> None:
    """A subset stack uses the stored set, or says why it cannot."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    stack = _identity_stack(tmp_path, "a.fits", seed=1)
    none = measure_stack_noise(stack, None, apertures, may_choose_apertures=False)
    assert none.result is None and "no stored noise apertures" in none.reason
    assert not (tmp_path / APERTURE_FILE_NAME).exists()
    first = measure_stack_noise(stack, None, apertures, depth=StackDepth(10, 10.0)).result
    deep = _identity_stack(tmp_path, "deep.fits", seed=2, center_ra=150.3)
    second = measure_stack_noise(
        deep, None, apertures, may_choose_apertures=False, depth=StackDepth(500, 500.0)
    ).result
    assert second.aperture_identity == first.aperture_identity and not second.apertures_created


def test_a_version_two_file_is_refused_with_a_reason_by_a_stack_that_may_not_choose(tmp_path: Path) -> None:
    """A subset stack leaves a version-2 file alone and says why."""
    apertures = tmp_path / APERTURE_FILE_NAME
    body = json.dumps({"version": 2, "sets": {"unspecified": {}}})
    apertures.write_text(body)
    stack = _identity_stack(tmp_path, "a.fits", seed=1)
    outcome = measure_stack_noise(stack, None, str(apertures), may_choose_apertures=False)
    assert outcome.result is None and outcome.reason == OLD_APERTURES_REASON
    assert "predate version 3" in outcome.reason and "library stack chooses them" in outcome.reason
    assert apertures.read_text() == body
    assert load_apertures_with_reason(str(apertures)) == (None, OLD_APERTURES_REASON)


def test_the_saved_sums_carry_the_identity_and_paired_comparison_refuses_another(tmp_path: Path) -> None:
    """Sums from different sets are never paired, whatever the names."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    stack = _identity_stack(tmp_path, "a.fits", seed=1)
    result = measure_stack_noise(stack, None, apertures).result
    save_noise_sums(stack, result)
    sums, flux, identity = load_noise_sums(stack)
    assert identity == result.aperture_identity
    assert paired_noise_difference(sums, flux, identity, sums, flux, identity) is not None
    with pytest.raises(ApertureSetMismatchError, match="different apertures"):
        paired_noise_difference(sums, flux, identity, sums, flux, "v3-other")
    with pytest.raises(ApertureSetMismatchError):
        paired_noise_ratio(sums, flux, "", sums, flux, "")
    assert json.loads(Path(noise_sums_path(stack)).read_text())["aperture_identity"] == identity
    discard_noise_sums(stack)
    assert not Path(noise_sums_path(stack)).exists()
    discard_noise_sums(stack)


def test_the_paired_ratio_of_a_stack_with_itself_is_one_with_no_error(tmp_path: Path) -> None:
    """The ratio of identical sums is 1, and of sums scaled by 2 is 2."""
    generator = np.random.default_rng(3)
    sums = generator.normal(size=100)
    ratio, error, count = paired_noise_ratio(sums, 1.0, "v3-x", sums, 1.0, "v3-x")
    assert ratio == pytest.approx(1.0) and error == pytest.approx(0.0, abs=1e-12) and count == 100
    ratio, error, _ = paired_noise_ratio(sums, 1.0, "v3-x", 2.0 * sums, 1.0, "v3-x")
    assert ratio == pytest.approx(2.0) and error == pytest.approx(0.0, abs=1e-12)
    noisy = sums + 0.5 * generator.normal(size=100)
    _, error, _ = paired_noise_ratio(sums, 1.0, "v3-x", noisy, 1.0, "v3-x")
    assert 0.0 < error < 0.2
