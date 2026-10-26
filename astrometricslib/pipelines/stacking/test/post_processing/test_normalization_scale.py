"""Tests for the scale Siril's normalization gives a stack.

The scale is the stack's star flux over the registration reference frame's,
divided by what the flux ratio would be with no scaling. The tests build
synthetic stacks from stars of a known brightness, and check that a known scale
is recovered within its error, that a stack of two exposure groups with a gain
gives 1 when Siril applied no scale (and the reference group's scale when it
did), that too few stars give "not measured", and that the expected noise rows
are multiplied by the scale and say so.
"""

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.quality.measurement import (
    MeasurementStatus,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.stacking.metrics import (
    STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT,
    STACKING_EXPECTED_READ_NOISE_STACK_UNIT,
    STACKING_EXPECTED_SKY_LEVEL_STACK_UNIT,
    STACKING_EXPECTED_SKY_NOISE_STACK_UNIT,
    STACKING_NORMALIZATION_SCALE,
)
from astrometricslib.pipelines.stacking.post_processing.empty_apertures import APERTURE_FILE_NAME
from astrometricslib.pipelines.stacking.post_processing.expected_noise import (
    STACK_UNITS_PER_ADU,
    ExpectedNoise,
    ExpectedNoiseOutcome,
    ExposureGroupNoiseFacts,
)
from astrometricslib.pipelines.stacking.post_processing.normalization_scale import (
    NormalizationScale,
    NormalizationScaleOutcome,
    apply_normalization_scale,
    measure_normalization_scale,
)
from astrometricslib.pipelines.stacking.post_processing.record_science import (
    expected_noise_measurements,
    normalization_scale_measurements,
    scale_expected_noise,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import (
    ReferenceFrameFluxRatio,
    StackNoiseOutcome,
    StackNoiseResult,
    measure_stack_noise,
)
from astrometricslib.pipelines.stacking.processing.exposure_groups import combine_exposure_group_images

CONTEXT = PipelineRunContext(
    "M 99", "stacking", "1.3.0", "job-1", "job-1", datetime(2026, 10, 26, tzinfo=UTC)
)
SUBJECT = QualitySubject(level=SubjectLevel.STACK, subjectId="M 99/stack.fits", targetId="M 99")

IMAGE_SIZE = 1200
GRID_SPACING = 80
SKY_ADU = 400.0
NOISE_ADU = 6.0


def _star_positions() -> list[tuple[float, float]]:
    """Lay out stars on a jittered grid, away from the edges.

    Returns
    -------
    positions : `list` [`tuple` [`float`, `float`]]
        (row, column) of each star, the same on every call.
    """
    generator = np.random.default_rng(3)
    grid = range(100, IMAGE_SIZE - 100, GRID_SPACING)
    return [
        (row + generator.uniform(-8, 8), column + generator.uniform(-8, 8)) for row in grid for column in grid
    ]


def _star_rates_adu_per_second(count: int) -> np.ndarray:
    """Give each star a peak rate, spread evenly in brightness.

    Parameters
    ----------
    count : `int`
        The number of stars.

    Returns
    -------
    rates : `numpy.ndarray`
        Peak heights from 20 to 360 ADU per second, in a fixed random order.
    """
    rates = np.linspace(20.0, 360.0, count)
    np.random.default_rng(4).shuffle(rates)
    return rates


def _image_adu(exposure_seconds: float, scale: float, seed: int) -> np.ndarray:
    """Build an image in ADU: a flat sky, white noise and Gaussian stars.

    Parameters
    ----------
    exposure_seconds : `float`
        The exposure. The stars grow with it, the sky does not (the sky is a
        pedestal here, which is taken off by the aperture's sky ring).
    scale : `float`
        Siril's scale for this image, applied to the stars and the noise.
    seed : `int`
        Seed of the noise.

    Returns
    -------
    image : `numpy.ndarray`
        The image in ADU, float32.
    """
    rows, columns = np.mgrid[0:IMAGE_SIZE, 0:IMAGE_SIZE]
    image = np.random.default_rng(seed).normal(0.0, NOISE_ADU, (IMAGE_SIZE, IMAGE_SIZE))
    positions = _star_positions()
    for (row, column), rate in zip(positions, _star_rates_adu_per_second(len(positions)), strict=True):
        window = (slice(int(row) - 30, int(row) + 31), slice(int(column) - 30, int(column) + 31))
        image[window] += (
            rate
            * exposure_seconds
            * np.exp(-((rows[window] - row) ** 2 + (columns[window] - column) ** 2) / (2 * 2.5**2))
        )
    return ((image * scale) + SKY_ADU).astype(np.float32)


def _header(exposure_seconds: float | None) -> fits.Header:
    """Build a header with a plain tangent-plane sky solution.

    Parameters
    ----------
    exposure_seconds : `float` or `None`
        The ``EXPTIME``, or `None` to leave it out.

    Returns
    -------
    header : `astropy.io.fits.Header`
        The header.
    """
    header = fits.Header()
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["CRPIX1"] = IMAGE_SIZE / 2
    header["CRPIX2"] = IMAGE_SIZE / 2
    header["CRVAL1"] = 150.0
    header["CRVAL2"] = 2.0
    header["CDELT1"] = -2.0 / 3600.0
    header["CDELT2"] = 2.0 / 3600.0
    if exposure_seconds is not None:
        header["EXPTIME"] = exposure_seconds
    return header


def _noise_outcome(
    tmp_path: Path, stack: np.ndarray, frame: np.ndarray, frame_seconds: float
) -> StackNoiseOutcome:
    """Run the noise step on a synthetic stack and its reference frame.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The folder.
    stack : `numpy.ndarray`
        The stack's pixels, in stack units.
    frame : `numpy.ndarray`
        The reference frame's pixels, in ADU.
    frame_seconds : `float`
        The reference frame's exposure.

    Returns
    -------
    outcome : `StackNoiseOutcome`
        The outcome. Its result holds the stars' flux ratio.
    """
    fits.PrimaryHDU(stack, header=_header(None)).writeto(tmp_path / "stack.fits", overwrite=True)
    fits.PrimaryHDU(frame, header=_header(frame_seconds)).writeto(tmp_path / "frame.fits", overwrite=True)
    return measure_stack_noise(
        str(tmp_path / "stack.fits"), str(tmp_path / "frame.fits"), str(tmp_path / APERTURE_FILE_NAME)
    )


def test_a_known_scale_is_recovered_within_its_error(tmp_path: Path) -> None:
    """A stack of one group whose frames Siril scaled by 1.35 gives 1.35."""
    frame = _image_adu(60.0, 1.0, seed=1)
    stack = ((_image_adu(60.0, 1.35, seed=2) - SKY_ADU) * STACK_UNITS_PER_ADU + 0.006).astype(np.float32)
    outcome = _noise_outcome(tmp_path, stack, frame, 60.0)
    scale = measure_normalization_scale(outcome)
    assert scale.result is not None, scale.reason
    assert scale.result.star_count >= 5
    assert scale.result.uncertainty > 0
    assert abs(scale.result.value - 1.35) < max(3.0 * scale.result.uncertainty, 0.007)
    assert "reference frame" in scale.result.note and "clouds" in scale.result.note.lower()


def test_no_siril_scale_gives_one(tmp_path: Path) -> None:
    """With no Siril scale at all, the stack gives a scale of 1."""
    frame = _image_adu(60.0, 1.0, seed=1)
    stack = ((_image_adu(60.0, 1.0, seed=2) - SKY_ADU) * STACK_UNITS_PER_ADU + 0.006).astype(np.float32)
    scale = measure_normalization_scale(_noise_outcome(tmp_path, stack, frame, 60.0))
    assert scale.result is not None, scale.reason
    assert abs(scale.result.value - 1.0) < max(3.0 * scale.result.uncertainty, 0.007)


def _combined_group_stack(
    group_scales: tuple[float, float],
) -> tuple[np.ndarray, list[ExposureGroupNoiseFacts]]:
    """Combine a 60 s and a 30 s group the way the pipeline does.

    Each group's stack is in stack units, and Siril's scale for the group
    multiplies its stars and noise. The two groups have 10 frames each, so the
    60 s group has more weight and is the reference.

    Parameters
    ----------
    group_scales : `tuple` [`float`, `float`]
        Siril's scale for the 60 s and the 30 s group.

    Returns
    -------
    image : `numpy.ndarray`
        The combined image, as the combine writes it.
    facts : `list` [`ExposureGroupNoiseFacts`]
        What the combine reports about each group (its weight, exposure,
        frame count and the gain it applied).
    """
    exposures = [60.0, 30.0]
    images = [
        ((_image_adu(seconds, scale, seed=20 + index) - SKY_ADU) * STACK_UNITS_PER_ADU + 0.006).astype(
            np.float32
        )
        for index, (seconds, scale) in enumerate(zip(exposures, group_scales, strict=True))
    ]
    combination = combine_exposure_group_images(
        images, exposures, frame_counts=[10, 10], frame_noises=[NOISE_ADU, NOISE_ADU]
    )
    assert combination.reference_index == 0
    facts = [
        ExposureGroupNoiseFacts(
            combine_weight=combination.weights[index],
            exposure_seconds=exposures[index],
            frame_count=10,
            gain=combination.gains[index],
            gain_measured=combination.gains_measured[index],
            sky_level_per_second=combination.sky_levels[index],
        )
        for index in range(2)
    ]
    return combination.image, facts


def test_a_group_stack_with_two_exposures_and_a_gain_gives_one_when_siril_scaled_nothing(
    tmp_path: Path,
) -> None:
    """The gain and mean exposure are divided out, so no scale reads 1."""
    image, facts = _combined_group_stack((1.0, 1.0))
    assert facts[1].gain_measured
    outcome = _noise_outcome(tmp_path, image, _image_adu(60.0, 1.0, seed=1), 60.0)
    scale = measure_normalization_scale(outcome, facts)
    assert scale.result is not None, scale.reason
    assert abs(scale.result.value - 1.0) < max(3.0 * scale.result.uncertainty, 0.01)
    assert "mean exposure" in scale.result.note


def test_a_group_stack_shows_the_reference_groups_scale_not_the_others(tmp_path: Path) -> None:
    """Scales of 1.3 and 1.7 for the groups read as 1.3 (the reference)."""
    image, facts = _combined_group_stack((1.3, 1.7))
    outcome = _noise_outcome(tmp_path, image, _image_adu(60.0, 1.0, seed=1), 60.0)
    scale = measure_normalization_scale(outcome, facts)
    assert scale.result is not None, scale.reason
    assert abs(scale.result.value - 1.3) < max(3.0 * scale.result.uncertainty, 0.015)


def test_a_reference_frame_outside_the_reference_group_gives_no_scale(tmp_path: Path) -> None:
    """A 30 s reference frame, with a 60 s reference group, is not used."""
    image, facts = _combined_group_stack((1.0, 1.0))
    outcome = _noise_outcome(tmp_path, image, _image_adu(30.0, 1.0, seed=1), 30.0)
    scale = measure_normalization_scale(outcome, facts)
    assert scale.result is None
    assert "not in the reference exposure group" in scale.reason


def _outcome_with_ratio(ratio: ReferenceFrameFluxRatio | None) -> StackNoiseOutcome:
    """Build a noise outcome that holds the given flux ratio.

    Parameters
    ----------
    ratio : `ReferenceFrameFluxRatio` or `None`
        The ratio to hold.

    Returns
    -------
    outcome : `StackNoiseOutcome`
        An outcome with every other number filled in.
    """
    return StackNoiseOutcome(
        StackNoiseResult(
            noise_adu=0.002,
            noise_uncertainty_adu=0.0001,
            aperture_count=100,
            reference_flux_adu=0.4,
            reference_uncertainty_adu=0.01,
            reference_star_count=10,
            normalized_noise=0.005,
            normalized_uncertainty=0.0004,
            wcs_source="stack header",
            apertures_created=False,
            reference_frame_flux_ratio=ratio,
        )
    )


def test_too_few_unsaturated_stars_give_not_measured_with_the_reason() -> None:
    """The ratio's own reason is stored, and no value."""
    ratio = ReferenceFrameFluxRatio(
        None, star_count=3, saturated_count=7, reason="only 3 reference stars have a flux"
    )
    scale = measure_normalization_scale(_outcome_with_ratio(ratio))
    assert scale.result is None and "only 3 reference stars" in scale.reason
    rows = normalization_scale_measurements(scale, CONTEXT, SUBJECT)
    assert len(rows) == 1 and rows[0].metric == STACKING_NORMALIZATION_SCALE
    assert rows[0].status == MeasurementStatus.NOT_MEASURED and "only 3" in rows[0].reason


def test_a_missing_noise_step_or_ratio_gives_not_measured() -> None:
    """With no noise step, result or ratio there is a reason, no value."""
    assert "did not run" in measure_normalization_scale(None).reason
    failed = measure_normalization_scale(StackNoiseOutcome(None, "no solution"))
    assert failed.result is None and "no solution" in failed.reason
    assert measure_normalization_scale(_outcome_with_ratio(None)).result is None


def test_the_scale_row_carries_its_uncertainty_star_count_and_note() -> None:
    """A measured scale is stored with its error, star count and note."""
    ratio = ReferenceFrameFluxRatio(1.3 * STACK_UNITS_PER_ADU, 0.01 * STACK_UNITS_PER_ADU, 9, 1, 60.0)
    scale = measure_normalization_scale(_outcome_with_ratio(ratio))
    assert scale.result is not None
    assert scale.result.value == pytest.approx(1.3)
    row = normalization_scale_measurements(scale, CONTEXT, SUBJECT)[0]
    assert row.status == MeasurementStatus.MEASURED
    assert row.value == pytest.approx(1.3) and row.uncertainty == pytest.approx(0.01)
    assert row.sample_count == 9 and "1 saturated" in row.reason


def _expected(assumed_reason: str = "") -> ExpectedNoiseOutcome:
    """Build an expected noise before any scale.

    Parameters
    ----------
    assumed_reason : `str`, optional
        Why the numbers are assumed, when they are.

    Returns
    -------
    outcome : `ExpectedNoiseOutcome`
        A result with sky 8e-4, read 1e-4 and total 8.06e-4 stack units.
    """
    return ExpectedNoiseOutcome(
        ExpectedNoise(
            sky_noise=8e-4,
            read_noise=1e-4,
            total_noise=(8e-4**2 + 1e-4**2) ** 0.5,
            aperture_area_px=59.4,
            stack_units_per_adu=STACK_UNITS_PER_ADU,
            frame_count=30,
            predicted_sky_level=0.009,
            assumed_reason=assumed_reason,
        )
    )


def test_the_expected_noise_rows_are_multiplied_by_the_scale_and_say_so() -> None:
    """The sky, read and total rows are 1.3 times larger and name the scale."""
    scale = NormalizationScaleOutcome(NormalizationScale(1.3, 0.02, 9, "note"))
    scaled = apply_normalization_scale(_expected(), scale)
    assert scaled.result is not None
    assert scaled.result.sky_noise == pytest.approx(1.3 * 8e-4)
    assert scaled.result.read_noise == pytest.approx(1.3 * 1e-4)
    assert scaled.result.total_noise == pytest.approx(1.3 * _expected().result.total_noise)
    assert scaled.result.predicted_sky_level == pytest.approx(0.009)
    rows = {row.metric: row for row in expected_noise_measurements(scaled, CONTEXT, SUBJECT)}
    for name in (
        STACKING_EXPECTED_SKY_NOISE_STACK_UNIT,
        STACKING_EXPECTED_READ_NOISE_STACK_UNIT,
        STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT,
    ):
        assert rows[name].status == MeasurementStatus.MEASURED
        assert "normalization scale 1.300" in rows[name].reason
    assert rows[STACKING_EXPECTED_SKY_LEVEL_STACK_UNIT].reason == ""
    # The unscaled part can be derived from the stored scale.
    assert rows[STACKING_EXPECTED_SKY_NOISE_STACK_UNIT].value / 1.3 == pytest.approx(8e-4)


def test_without_a_scale_the_expected_noise_is_kept_and_says_it_is_not_corrected() -> None:
    """With no scale the numbers stand and the rows say so."""
    scaled = apply_normalization_scale(_expected(), NormalizationScaleOutcome(None, "no reference frame"))
    assert scaled.result is not None and scaled.result.sky_noise == pytest.approx(8e-4)
    rows = {row.metric: row for row in expected_noise_measurements(scaled, CONTEXT, SUBJECT)}
    reason = rows[STACKING_EXPECTED_SKY_NOISE_STACK_UNIT].reason
    assert "Not corrected" in reason and "no reference frame" in reason
    assert rows[STACKING_EXPECTED_SKY_NOISE_STACK_UNIT].status == MeasurementStatus.MEASURED


def test_an_assumed_expected_noise_keeps_its_reason_beside_the_scale_note() -> None:
    """Assumed inputs make the rows assumed, with both reasons."""
    scale = NormalizationScaleOutcome(NormalizationScale(1.2, 0.02, 9, "note"))
    scaled = apply_normalization_scale(_expected("the gain is assumed"), scale)
    rows = expected_noise_measurements(scaled, CONTEXT, SUBJECT)
    assert all(row.status == MeasurementStatus.ASSUMED for row in rows)
    assert "the gain is assumed" in rows[0].reason and "1.200" in rows[0].reason


def test_an_expected_noise_with_no_result_is_left_as_it_is() -> None:
    """There is nothing to multiply, so the reason stays."""
    nothing = ExpectedNoiseOutcome(None, "no stored sky measurement")
    scale = NormalizationScaleOutcome(NormalizationScale(1.2, 0.02, 9, "note"))
    assert apply_normalization_scale(nothing, scale) is nothing


def test_scaling_the_expected_noise_from_a_noise_outcome_and_the_run_diagnostics() -> None:
    """The record step's helper reads the groups from the run's diagnostics."""
    ratio = ReferenceFrameFluxRatio(1.5 * STACK_UNITS_PER_ADU, 0.005 * STACK_UNITS_PER_ADU, 9, 0, 60.0)
    scaled, scale = scale_expected_noise(_expected(), _outcome_with_ratio(ratio), {})
    assert scale.result is not None and scale.result.value == pytest.approx(1.5)
    assert scaled.result is not None and scaled.result.sky_noise == pytest.approx(1.5 * 8e-4)
    bad_entries = {"exposure_group_weights": [{"weight": 1.0}]}
    refused, refused_scale = scale_expected_noise(_expected(), _outcome_with_ratio(ratio), bad_entries)
    assert refused_scale.result is None and "lacks" in refused_scale.reason
    assert refused.result is not None and refused.result.sky_noise == pytest.approx(8e-4)
