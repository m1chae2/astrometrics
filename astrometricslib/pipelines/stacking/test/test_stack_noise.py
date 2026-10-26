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

from astrometricslib.models.camera_profile import ProvenanceKind
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
    MEMBER_FRAME_WCS_SOURCE,
    NO_WCS_REASON,
    PLATE_SOLVE_WCS_SOURCE,
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
    # The solve of the stack itself was tried last (the stand-in fails it).
    assert outcome.reason.startswith(NO_WCS_REASON)
    assert "plate-solving the stack itself" in outcome.reason
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
        usable_peak_level_adu=SimpleNamespace(
            value=saturation_adu, provenance=SimpleNamespace(kind=ProvenanceKind.MEASURED)
        ),
        clip_ceiling_adu=SimpleNamespace(value=65532.0),
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
    assert sorted(sets) == ["camera a|?|400|exp=?", "camera b|?|400|exp=?"]
    assert load_apertures(apertures, "camera a|?|400|exp=?").created_from == "a.fits"
    # Forgetting one camera leaves the other; forgetting all deletes the file.
    assert reset_apertures(apertures, "camera a|?|400|exp=?") is True
    assert sorted(json.loads(Path(apertures).read_text())["sets"]) == ["camera b|?|400|exp=?"]
    assert reset_apertures(apertures, "camera a|?|400|exp=?") is False
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


def test_a_member_frames_solution_is_moved_onto_a_stack_with_no_reference_frame(tmp_path: Path) -> None:
    """A member frame 12 rows and -9 columns off the stack gives a solution.

    The stack's stars sit where the member frame's stars sit after the shift,
    so the solution is accepted, moved, and recorded with its source.
    """
    scattered = _scattered_stars(seed=11)
    stack_image = _sky_image(2.0, seed=21, star_positions=scattered)
    member_image = np.roll(_sky_image(2.0, seed=22, star_positions=scattered), shift=(-12, 9), axis=(0, 1))
    member = _write(tmp_path / "member.fits", member_image, _sky_header())
    stack = _write(tmp_path / "stack.fits", stack_image, None)
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    outcome = measure_stack_noise(stack, None, apertures, member_frame_paths=[member])
    assert outcome.result is not None, outcome.reason
    assert outcome.result.wcs_source.startswith(MEMBER_FRAME_WCS_SOURCE)
    assert "member.fits" in outcome.result.wcs_source
    assert "+12.0 rows" in outcome.result.wcs_source
    stored = load_apertures(apertures)
    assert stored is not None
    assert stored.wcs_source.startswith(MEMBER_FRAME_WCS_SOURCE)
    # The stack is the unrolled image: its true solution is the header's.
    truth = _write(tmp_path / "truth.fits", stack_image, _sky_header())
    truth_outcome = measure_stack_noise(truth, None, str(tmp_path / "truth_apertures.json"))
    assert truth_outcome.result is not None
    assert outcome.result.noise_adu == pytest.approx(truth_outcome.result.noise_adu, rel=0.2)


def test_a_member_frame_that_does_not_fit_is_skipped_and_a_later_one_used(tmp_path: Path) -> None:
    """A frame of another field is skipped; the next that lines up is used."""
    scattered = _scattered_stars(seed=11)
    wrong = _write(
        tmp_path / "wrong.fits",
        _sky_image(2.0, seed=23, star_positions=_scattered_stars(seed=12)),
        _sky_header(),
    )
    right = _write(
        tmp_path / "right.fits",
        np.roll(_sky_image(2.0, seed=24, star_positions=scattered), shift=(5, 5), axis=(0, 1)),
        _sky_header(),
    )
    stack = _write(tmp_path / "stack.fits", _sky_image(2.0, seed=25, star_positions=scattered), None)
    outcome = measure_stack_noise(
        stack, None, str(tmp_path / APERTURE_FILE_NAME), member_frame_paths=[wrong, right]
    )
    assert outcome.result is not None, outcome.reason
    assert "right.fits" in outcome.result.wcs_source


def test_member_frames_that_do_not_fit_the_stack_are_refused(tmp_path: Path) -> None:
    """No member frame lines up: no measurement, a reason, and no apertures."""
    wrong = _write(
        tmp_path / "wrong.fits",
        _sky_image(2.0, seed=23, star_positions=_scattered_stars(seed=12)),
        _sky_header(),
    )
    stack = _write(
        tmp_path / "stack.fits", _sky_image(2.0, seed=25, star_positions=_scattered_stars(seed=11)), None
    )
    outcome = measure_stack_noise(stack, None, str(tmp_path / APERTURE_FILE_NAME), member_frame_paths=[wrong])
    assert outcome.result is None
    assert "no member frame" in outcome.reason
    assert not (tmp_path / APERTURE_FILE_NAME).exists()


def test_member_frames_without_a_solution_leave_the_stack_unmeasured(tmp_path: Path) -> None:
    """A member frame with no plate solution gives no coordinates."""
    scattered = _scattered_stars(seed=11)
    bare = _write(tmp_path / "bare.fits", _sky_image(2.0, seed=24, star_positions=scattered), None)
    stack = _write(tmp_path / "stack.fits", _sky_image(2.0, seed=25, star_positions=scattered), None)
    outcome = measure_stack_noise(stack, None, str(tmp_path / APERTURE_FILE_NAME), member_frame_paths=[bare])
    assert outcome.result is None
    assert "no member frame" in outcome.reason


def _reference_pair(tmp_path: Path, name: str, exposure_seconds: float, seed: int = 1) -> tuple[str, str]:
    """Write a stack with no solution and a reference frame.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The folder.
    name : `str`
        A prefix for the two file names.
    exposure_seconds : `float`
        The ``EXPTIME`` of the reference frame.
    seed : `int`, optional
        Seed of the noise.

    Returns
    -------
    stack_path, frame_path : `tuple` [`str`, `str`]
        The stack and its registration reference frame.
    """
    image = _sky_image(2.0, seed=seed)
    header = _sky_header()
    header["EXPTIME"] = exposure_seconds
    frame = _write(tmp_path / f"{name}_frame.fits", image, header)
    stack = _write(tmp_path / f"{name}_stack.fits", image, None)
    return stack, frame


def test_a_set_chosen_on_a_60_second_reference_is_not_reused_for_a_120_second_reference(
    tmp_path: Path,
) -> None:
    """Each reference exposure gets its own set; the same one reuses it."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    stack_60, frame_60 = _reference_pair(tmp_path, "a60", 60.0)
    stack_120, frame_120 = _reference_pair(tmp_path, "b120", 120.0, seed=2)
    stack_60_again, frame_60_again = _reference_pair(tmp_path, "c60", 60.0, seed=3)
    first = measure_stack_noise(stack_60, frame_60, apertures).result
    second = measure_stack_noise(stack_120, frame_120, apertures).result
    third = measure_stack_noise(stack_60_again, frame_60_again, apertures).result
    assert first is not None and second is not None and third is not None
    assert first.apertures_created and second.apertures_created and not third.apertures_created
    assert second.aperture_identity != first.aperture_identity
    assert third.aperture_identity == first.aperture_identity
    # The set's identity is in the note of every stored row, so rows of the
    # two sets can be told apart.
    assert first.aperture_identity in first.aperture_definition_note
    assert second.aperture_identity in second.aperture_definition_note
    assert sorted(json.loads(Path(apertures).read_text())["sets"]) == [
        "unspecified|exp=120",
        "unspecified|exp=60",
    ]
    assert load_apertures(apertures, "unspecified|exp=120").created_from == "b120_stack.fits"


def test_a_group_stack_takes_its_reference_exposure_from_a_member_of_the_reference_group(
    tmp_path: Path,
) -> None:
    """With no reference frame, a member frame's exposure is the key."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    image = _sky_image(2.0, seed=1)
    member_header = _sky_header()
    member_header["EXPTIME"] = 120.0
    member = _write(tmp_path / "member.fits", image, member_header)
    stack = _write(tmp_path / "stack.fits", image, _sky_header())
    result = measure_stack_noise(stack, None, apertures, member_frame_paths=[member]).result
    assert result is not None and result.apertures_created
    assert list(json.loads(Path(apertures).read_text())["sets"]) == ["unspecified|exp=120"]


def test_an_aperture_file_keyed_without_an_exposure_is_never_used_and_is_replaced_once(
    tmp_path: Path,
) -> None:
    """A key with no exposure matches nothing; one new set replaces it."""
    apertures = Path(tmp_path / APERTURE_FILE_NAME)
    stack_a, frame_a = _reference_pair(tmp_path, "a", 60.0)
    assert measure_stack_noise(stack_a, frame_a, str(apertures)).result is not None
    data = json.loads(apertures.read_text())
    data["sets"] = {"unspecified": data["sets"]["unspecified|exp=60"]}
    apertures.write_text(json.dumps(data))
    assert load_apertures(str(apertures), "unspecified") is None
    assert load_apertures(str(apertures)) is None
    stack_b, frame_b = _reference_pair(tmp_path, "b", 60.0, seed=2)
    refused = measure_stack_noise(stack_b, frame_b, str(apertures), may_choose_apertures=False)
    assert refused.result is None and "no usable set" in refused.reason
    chosen = measure_stack_noise(stack_b, frame_b, str(apertures)).result
    assert chosen is not None and chosen.apertures_created
    assert list(json.loads(apertures.read_text())["sets"]) == ["unspecified|exp=60"]
    reused = measure_stack_noise(stack_b, frame_b, str(apertures)).result
    assert reused is not None and not reused.apertures_created


def test_a_stack_that_may_not_choose_uses_the_library_stacks_set_whatever_its_own_reference(
    tmp_path: Path,
) -> None:
    """A stack that may not choose finds the deepest set of its equipment."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    stack_60, frame_60 = _reference_pair(tmp_path, "a60", 60.0)
    stack_120, frame_120 = _reference_pair(tmp_path, "b120", 120.0, seed=2)
    shallow = measure_stack_noise(stack_60, frame_60, apertures, depth=StackDepth(10, 10.0)).result
    library = measure_stack_noise(stack_120, frame_120, apertures, depth=StackDepth(40, 30.0)).result
    assert shallow is not None and library is not None
    assert library.aperture_identity != shallow.aperture_identity
    stack_30, frame_30 = _reference_pair(tmp_path, "c30", 30.0, seed=3)
    subset = measure_stack_noise(stack_30, frame_30, apertures, may_choose_apertures=False).result
    bare = measure_stack_noise(stack_30, None, apertures, may_choose_apertures=False)
    assert subset is not None and subset.aperture_identity == library.aperture_identity
    assert not subset.apertures_created
    assert bare.result is None  # no solution at all, so no set is looked for
    assert load_apertures(apertures).identity == library.aperture_identity
    assert load_apertures_with_reason(apertures)[0].identity == library.aperture_identity
    assert sorted(json.loads(Path(apertures).read_text())["sets"]) == [
        "unspecified|exp=120",
        "unspecified|exp=60",
    ]


def _scaled_pair(
    tmp_path: Path, name: str, scale: float, scattered: list[tuple[float, float]]
) -> tuple[str, str]:
    """Write a reference frame and a stack with brighter stars.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The folder.
    name : `str`
        A prefix for the two file names.
    scale : `float`
        How many times brighter the stack's stars are than the frame's. The
        sky and the noise are the same, so only the stars change.
    scattered : `list` of `tuple`
        The (column, row) of the stars, the same in both images.

    Returns
    -------
    stack_path, frame_path : `tuple` [`str`, `str`]
        The stack (it has a solution) and its registration reference frame.
    """
    header = _sky_header()
    header["EXPTIME"] = 60.0
    frame = _write(
        tmp_path / f"{name}_frame.fits", _sky_image(2.0, seed=11, star_positions=scattered), header
    )
    stack = _write(
        tmp_path / f"{name}_stack.fits",
        _sky_image(2.0, seed=12, star_positions=scattered, star_amplitude=2000.0 * scale),
        _sky_header(),
    )
    return stack, frame


def test_the_stars_flux_ratio_against_the_reference_frame_recovers_a_known_scale(tmp_path: Path) -> None:
    """Stars 1.4 times as bright in the stack give a ratio of 1.4."""
    scattered = _scattered_stars(seed=3)
    stack, frame = _scaled_pair(tmp_path, "a", 1.4, scattered)
    result = measure_stack_noise(stack, frame, str(tmp_path / APERTURE_FILE_NAME)).result
    assert result is not None
    flux_ratio = result.reference_frame_flux_ratio
    assert flux_ratio is not None and flux_ratio.ratio is not None
    assert flux_ratio.star_count >= 5
    assert flux_ratio.uncertainty > 0.0
    assert flux_ratio.reference_exposure_seconds == pytest.approx(60.0)
    # The ratio's error is the standard error of the stars' scatter. With noise
    # of 2 against star fluxes of tens of thousands it is tiny, so allow the
    # larger of three errors and 0.5%.
    assert abs(flux_ratio.ratio - 1.4) < max(3.0 * flux_ratio.uncertainty, 0.007)


def test_the_flux_ratio_is_a_median_over_stars_and_ignores_one_wrong_star(tmp_path: Path) -> None:
    """One star far brighter in the stack does not move the median."""
    scattered = _scattered_stars(seed=3)
    stack, frame = _scaled_pair(tmp_path, "a", 1.4, scattered)
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    assert measure_stack_noise(stack, frame, apertures).result is not None
    chosen = load_apertures(apertures)
    assert chosen is not None
    wcs = WCS(_sky_header()).celestial
    column, row = (float(value[0]) for value in wcs.world_to_pixel_values(*chosen.reference_sky[:1].T))
    brighter = fits.getdata(stack).astype(np.float64)
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    brighter += 20000.0 * np.exp(-((columns - column) ** 2 + (rows - row) ** 2) / (2 * 2.0**2))
    second_stack = _write(tmp_path / "second_stack.fits", brighter.astype(np.float32), _sky_header())
    result = measure_stack_noise(second_stack, frame, apertures).result
    assert result is not None and result.reference_frame_flux_ratio is not None
    assert result.reference_frame_flux_ratio.ratio == pytest.approx(1.4, abs=0.02)


def test_with_no_named_reference_frame_the_flux_ratio_is_not_measured(tmp_path: Path) -> None:
    """With no reference frame the ratio is not measured, and says why."""
    scattered = _scattered_stars(seed=3)
    stack, _ = _scaled_pair(tmp_path, "a", 1.4, scattered)
    result = measure_stack_noise(stack, None, str(tmp_path / APERTURE_FILE_NAME)).result
    assert result is not None
    flux_ratio = result.reference_frame_flux_ratio
    assert flux_ratio is not None and flux_ratio.ratio is None
    assert "no registration reference frame" in flux_ratio.reason


def test_a_member_frame_standing_in_for_the_reference_gives_no_flux_ratio(tmp_path: Path) -> None:
    """A member frame has its own scale, so it is never the reference."""
    scattered = _scattered_stars(seed=3)
    stack, frame = _scaled_pair(tmp_path, "a", 1.4, scattered)
    bare_stack = _write(tmp_path / "bare.fits", fits.getdata(stack), None)
    result = measure_stack_noise(
        bare_stack, None, str(tmp_path / APERTURE_FILE_NAME), member_frame_paths=[frame]
    ).result
    assert result is not None and result.reference_frame_flux_ratio is not None
    assert result.reference_frame_flux_ratio.ratio is None


def test_too_few_unsaturated_stars_give_no_flux_ratio(tmp_path: Path) -> None:
    """With fewer than five stars measurable, the ratio is not measured."""
    scattered = _scattered_stars(seed=3)
    stack, frame = _scaled_pair(tmp_path, "a", 1.4, scattered)
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    assert measure_stack_noise(stack, frame, apertures).result is not None
    chosen = load_apertures(apertures)
    assert chosen is not None
    wcs = WCS(_sky_header()).celestial
    columns, rows = wcs.world_to_pixel_values(chosen.reference_sky[:, 0], chosen.reference_sky[:, 1])
    damaged = fits.getdata(frame).astype(np.float32)
    ring = int(chosen.reference_ring_inner_px) + 1
    # An exact zero in the sky ring makes the frame's flux unusable; spoil all
    # but four of the stars, leaving the stack untouched.
    for column, row in list(zip(columns, rows, strict=True))[4:]:
        damaged[round(row) + ring : round(row) + ring + 2, round(column) : round(column) + 2] = 0.0
    header = _sky_header()
    header["EXPTIME"] = 60.0
    damaged_frame = _write(tmp_path / "damaged_frame.fits", damaged, header)
    second_stack = _write(
        tmp_path / "second_stack.fits",
        _sky_image(2.0, seed=13, star_positions=scattered, star_amplitude=2800.0),
        _sky_header(),
    )
    result = measure_stack_noise(second_stack, damaged_frame, apertures).result
    assert result is not None and result.reference_frame_flux_ratio is not None
    flux_ratio = result.reference_frame_flux_ratio
    assert flux_ratio.ratio is None
    assert "only" in flux_ratio.reason and flux_ratio.star_count < 5


def test_a_reference_frame_that_does_not_line_up_gives_no_flux_ratio(tmp_path: Path) -> None:
    """A frame with the stars elsewhere is refused, not guessed."""
    scattered = _scattered_stars(seed=3)
    stack, frame = _scaled_pair(tmp_path, "a", 1.4, scattered)
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    assert measure_stack_noise(stack, frame, apertures).result is not None
    header = _sky_header()
    header["EXPTIME"] = 60.0
    elsewhere = _write(
        tmp_path / "elsewhere_frame.fits",
        np.roll(fits.getdata(frame), shift=(40, 40), axis=(0, 1)),
        header,
    )
    second_stack = _write(
        tmp_path / "second_stack.fits", _sky_image(2.0, seed=13, star_positions=scattered), _sky_header()
    )
    result = measure_stack_noise(second_stack, elsewhere, apertures).result
    assert result is not None and result.reference_frame_flux_ratio is not None
    assert result.reference_frame_flux_ratio.ratio is None
    assert "does not line up" in result.reference_frame_flux_ratio.reason


def _fake_stack_solver(monkeypatch: pytest.MonkeyPatch, solution: WCS | None, reason: str = "") -> list[str]:
    """Replace the stack plate solve with a fake and record what it was asked.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        The fixture that undoes the change.
    solution : `astropy.wcs.WCS` or `None`
        What the fake solve returns.
    reason : `str`, optional
        The reason it returns with no solution.

    Returns
    -------
    calls : `list` [`str`]
        Filled with the stack path of each solve.
    """
    calls: list[str] = []

    def fake(stack_path: str) -> tuple[WCS | None, str]:
        """Record the call and give the prepared answer.

        Parameters
        ----------
        stack_path : `str`
            The stack to solve.

        Returns
        -------
        solution : `tuple` [`astropy.wcs.WCS` or `None`, `str`]
            The prepared solution and reason.
        """
        calls.append(stack_path)
        return solution, reason

    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.post_processing.stack_noise._plate_solve_stack_wcs", fake
    )
    return calls


def test_a_stack_nobody_solved_is_plate_solved_as_the_last_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no solution anywhere, the stack's own solve gives positions."""
    calls = _fake_stack_solver(monkeypatch, WCS(_sky_header()).celestial)
    stack = _write(tmp_path / "stack.fits", _sky_image(2.0, seed=1), None)
    before = fits.getheader(stack)
    outcome = measure_stack_noise(stack, None, str(tmp_path / APERTURE_FILE_NAME))
    assert outcome.result is not None
    assert outcome.result.wcs_source == PLATE_SOLVE_WCS_SOURCE
    assert outcome.result.aperture_identity
    assert calls == [stack]
    # The stack's header is read only, never written.
    assert fits.getheader(stack) == before


def test_the_solve_of_the_stack_gives_the_reference_frame_flux_ratio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frame with no solution still gives the flux ratio."""
    scattered = _scattered_stars(seed=3)
    stack, frame = _scaled_pair(tmp_path, "a", 1.4, scattered)
    bare_frame = _write(tmp_path / "bare_frame.fits", fits.getdata(frame), None)
    bare_stack = _write(tmp_path / "bare_stack.fits", fits.getdata(stack), None)
    _fake_stack_solver(monkeypatch, WCS(_sky_header()).celestial)
    result = measure_stack_noise(bare_stack, bare_frame, str(tmp_path / APERTURE_FILE_NAME)).result
    assert result is not None
    assert result.wcs_source == PLATE_SOLVE_WCS_SOURCE
    assert result.reference_frame_flux_ratio is not None
    assert result.reference_frame_flux_ratio.ratio == pytest.approx(1.4, abs=0.02)


def test_the_solve_is_tried_after_the_member_frames_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Member frames with no solution leave the stack's own solve."""
    scattered = _scattered_stars(seed=11)
    bare = _write(tmp_path / "bare.fits", _sky_image(2.0, seed=24, star_positions=scattered), None)
    stack = _write(tmp_path / "stack.fits", _sky_image(2.0, seed=25, star_positions=scattered), None)
    calls = _fake_stack_solver(monkeypatch, WCS(_sky_header()).celestial)
    outcome = measure_stack_noise(stack, None, str(tmp_path / APERTURE_FILE_NAME), member_frame_paths=[bare])
    assert calls == [stack]
    assert outcome.result is not None
    assert outcome.result.wcs_source == PLATE_SOLVE_WCS_SOURCE


def test_a_failed_solve_of_the_stack_gives_a_reason_and_no_apertures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A solve with no answer leaves the stack unmeasured, with a reason."""
    _fake_stack_solver(monkeypatch, None, "plate-solving the stack itself was tried and found no solution")
    stack = _write(tmp_path / "stack.fits", _sky_image(2.0, seed=1), None)
    outcome = measure_stack_noise(stack, None, str(tmp_path / APERTURE_FILE_NAME))
    assert outcome.result is None
    assert NO_WCS_REASON in outcome.reason
    assert "was tried and found no solution" in outcome.reason
    assert not (tmp_path / APERTURE_FILE_NAME).exists()


def test_the_real_stack_solve_turns_an_exception_into_a_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A solver that raises gives a reason and never fails the stack."""
    from astrometricslib.pipelines.stacking.post_processing import stack_noise
    from astrometricslib.utilities.exceptions import PlateSolveFailedError

    class RaisingPipeline:
        """A stand-in astrometry pipeline whose solve fails."""

        def process(self, image: str, attempt_plate_solving: bool = True) -> None:
            """Fail as the star identifier does for an unsolvable field.

            Raises
            ------
            PlateSolveFailedError
                Always.
            """
            raise PlateSolveFailedError("Plate solving failed: could not solve field.")

    monkeypatch.setattr("astrometricslib.pipelines.astrometry.pipeline.AstrometryPipeline", RaisingPipeline)
    # The conftest stand-in keeps the real function for this test.
    real = stack_noise._plate_solve_stack_wcs.real_function  # type: ignore[attr-defined]
    wcs, reason = real(str(tmp_path / "stack.fits"))
    assert wcs is None
    assert "tried and failed" in reason
    assert "PlateSolveFailedError" in reason


def test_a_stack_with_a_header_solution_is_never_solved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stacks that already work keep their source, with no solve."""
    calls = _fake_stack_solver(monkeypatch, WCS(_sky_header()).celestial)
    stack = _write(tmp_path / "stack.fits", _sky_image(2.0, seed=1), _sky_header())
    outcome = measure_stack_noise(stack, None, str(tmp_path / APERTURE_FILE_NAME))
    assert outcome.result is not None
    assert outcome.result.wcs_source == "stack header"
    reference = _write(tmp_path / "reference.fits", _sky_image(2.0, seed=5), _sky_header())
    trimmed = _write(tmp_path / "trimmed.fits", _sky_image(2.0, seed=6)[20:, 10:], None)
    reference_outcome = measure_stack_noise(
        trimmed, reference, str(tmp_path / "other_apertures.json"), crop_offset=(10, 20)
    )
    assert reference_outcome.result is not None
    assert reference_outcome.result.wcs_source == "registration reference frame"
    assert calls == []


def test_a_header_solution_that_misses_the_stored_stars_falls_back_to_the_stack_solve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A solution 800 px off is replaced by the stack's own solve."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    scattered = _scattered_stars(seed=3)
    first = _write(tmp_path / "first.fits", _sky_image(2.0, seed=1, star_positions=scattered), _sky_header())
    assert measure_stack_noise(first, None, apertures).result is not None
    # The stars are where the first stack had them, but the header says they
    # are 800 px away: farther than the shift search reaches.
    wrong_header = _sky_header()
    wrong_header["CRPIX1"] = SIZE / 2 + 800
    stack = _write(tmp_path / "stack.fits", _sky_image(2.0, seed=7, star_positions=scattered), wrong_header)
    calls = _fake_stack_solver(monkeypatch, WCS(_sky_header()).celestial)
    result = measure_stack_noise(stack, None, apertures).result
    assert calls == [stack]
    assert result is not None
    assert result.wcs_source == PLATE_SOLVE_WCS_SOURCE


def test_a_missed_header_solution_with_a_failed_stack_solve_gives_both_reasons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the retry solve finds nothing too, the reason names both."""
    apertures = str(tmp_path / APERTURE_FILE_NAME)
    scattered = _scattered_stars(seed=3)
    first = _write(tmp_path / "first.fits", _sky_image(2.0, seed=1, star_positions=scattered), _sky_header())
    assert measure_stack_noise(first, None, apertures).result is not None
    wrong_header = _sky_header()
    wrong_header["CRPIX1"] = SIZE / 2 + 800
    stack = _write(tmp_path / "stack.fits", _sky_image(2.0, seed=7, star_positions=scattered), wrong_header)
    _fake_stack_solver(monkeypatch, None, "plate-solving the stack itself was tried and found no solution")
    outcome = measure_stack_noise(stack, None, apertures)
    assert outcome.result is None
    assert "stored reference stars" in outcome.reason
    assert "was tried and found no solution" in outcome.reason
