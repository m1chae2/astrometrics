"""Purpose: Tests for the efficiency calculation.

Description: Builds frames by hand and checks the references, the factors,
the caps, the goals, the regime, the depth view, the loss split and the
reading of stored measurements.
"""

import math
from datetime import UTC, datetime

import pytest

from astrometricslib import (
    PIXEL_BLUR_FWHM_SQUARED_FACTOR,
    CaptureSetting,
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
)
from wayfindinglib.analytics.result_quality import efficiency as eff
from wayfindinglib.models.quality.efficiency import EfficiencyReference, NoiseRegime, ScienceGoal

SETTING = CaptureSetting(
    camera="CamA", telescope="Scope", filter_name="L", exposure_seconds=60.0, gain_setting=100
)


def _observation(
    frame_id: str = "f1",
    width: float = 3.0,
    sky: float = 1.0,
    read: float = 2.0,
    exposure: float = 60.0,
    night: str = "n1",
    **extra: object,
) -> eff.FrameObservation:
    """Build one frame observation.

    Parameters
    ----------
    frame_id : `str`
        The frame id.
    width, sky, read, exposure : `float`
        Star width (arcsec), sky rate, read noise and exposure.
    night : `str`
        Night id.
    **extra
        Other fields.

    Returns
    -------
    observation : `FrameObservation`
        The frame.
    """
    return eff.FrameObservation(
        frame_id=frame_id,
        capture_setting=SETTING,
        night_id=night,
        exposure_seconds=exposure,
        star_width_effective_arcsec=width,
        sky_rate_e_per_s_per_px=sky,
        read_noise_e=read,
        **extra,
    )


def _reference(width: float = 2.0, sky: float = 0.5) -> EfficiencyReference:
    """Build a baseline-like reference with both values.

    Parameters
    ----------
    width, sky : `float`
        Reference width and sky rate.

    Returns
    -------
    reference : `EfficiencyReference`
        The reference.
    """
    nights = [
        eff.BaselineNight(
            night_id=f"n{i}",
            median_star_width_effective_arcsec=width,
            median_sky_rate_e_per_s_per_px=sky,
            frame_count=5,
        )
        for i in range(3)
    ]
    outcome = eff.baseline_reference(nights, eff.baseline_key(SETTING))
    assert outcome.reference is not None
    return outcome.reference


def test_diffraction_limit_of_a_75_mm_aperture() -> None:
    """The Airy core of 75 mm at 550 nm is about 1.6 arcseconds."""
    assert eff.diffraction_fwhm_arcsec(75.0) == pytest.approx(1.029 * 550e-6 / 75.0 * 206264.806)
    assert eff.diffraction_fwhm_arcsec(75.0) == pytest.approx(1.56, abs=0.01)


def test_physical_reference_adds_diffraction_seeing_and_pixel_in_quadrature() -> None:
    """The reference width is the quadrature sum.

    With sky and transparency not included.
    """
    outcome = eff.physical_reference(100.0, 1.5, 2.0)
    assert outcome.reference is not None
    diffraction = eff.diffraction_fwhm_arcsec(100.0)
    expected = math.sqrt(diffraction**2 + 4.0 + PIXEL_BLUR_FWHM_SQUARED_FACTOR * 2.25)
    assert outcome.reference.factors[eff.STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC] == pytest.approx(expected)
    assert "sky_brightness" in outcome.reference.not_included
    assert "transparency" in outcome.reference.not_included
    assert eff.STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC not in outcome.reference.not_included


def test_physical_reference_without_seeing_has_no_width() -> None:
    """Without site seeing the seeing factor is not included, with a reason.

    The old reference used diffraction and pixel blur alone, a width no real
    night reaches, so every frame looked poor.
    """
    outcome = eff.physical_reference(100.0, 1.5)
    assert outcome.reference is not None
    assert eff.STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC not in outcome.reference.factors
    assert eff.STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC in outcome.reference.not_included
    result = eff.frame_efficiency(_observation(), ScienceGoal.FAINT_POINT_SOURCE, outcome.reference)
    assert result is not None
    seeing = next(factor for factor in result.factors if factor.cause == eff.CAUSE_SEEING)
    assert not seeing.included and seeing.value is None
    assert "seeing" in (seeing.not_included_reason or "")


def test_slitless_spectra_are_not_charged_sky_or_read_noise() -> None:
    """A source-limited slitless spectrum has no sky or read-noise factor."""
    reference = eff.physical_reference(100.0, 1.5, 2.0).reference
    assert reference is not None
    result = eff.frame_efficiency(_observation(), ScienceGoal.SLITLESS_SPECTRAL_RESOLUTION, reference)
    assert result is not None
    by_cause = {factor.cause: factor for factor in result.factors}
    assert not by_cause[eff.CAUSE_SKY].included
    assert not by_cause[eff.CAUSE_READ_NOISE].included


@pytest.mark.parametrize(("aperture", "scale"), [(None, 1.0), (0.0, 1.0), (100.0, None)])
def test_physical_reference_with_missing_input_gives_a_reason(
    aperture: float | None, scale: float | None
) -> None:
    """A missing aperture or pixel scale gives no reference and a reason."""
    outcome = eff.physical_reference(aperture, scale)
    assert outcome.reference is None
    assert outcome.reason


def test_baseline_needs_three_nights_and_takes_the_tenth_percentile() -> None:
    """Fewer than three nights give no baseline.

    Otherwise the 10th percentile is used.
    """
    two = [
        eff.BaselineNight(
            night_id=str(i), median_star_width_effective_arcsec=2.0 + i, median_sky_rate_e_per_s_per_px=1.0
        )
        for i in range(2)
    ]
    assert eff.baseline_reference(two, "k").reference is None
    five = [
        eff.BaselineNight(
            night_id=str(i),
            median_star_width_effective_arcsec=2.0 + i,
            median_sky_rate_e_per_s_per_px=1.0 + i,
        )
        for i in range(5)
    ]
    reference = eff.baseline_reference(five, "k").reference
    assert reference is not None
    assert reference.factors[eff.STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC] == pytest.approx(2.4)
    assert reference.name == "baseline:k"


def test_baseline_key_ignores_exposure_and_marks_missing_parts() -> None:
    """The key has camera, telescope, filter, gain, offset and binning only."""
    other = SETTING.model_copy(update={"exposure_seconds": 10.0})
    assert eff.baseline_key(SETTING) == eff.baseline_key(other)
    assert eff.baseline_key(SETTING).endswith("gain100|?|?")


def test_build_baseline_nights_uses_medians_and_skips_set_aside_frames() -> None:
    """Per-night medians come from kept frames only."""
    frames = [_observation(f"a{i}", width=w, night="n1") for i, w in enumerate([2.0, 3.0, 10.0])]
    frames.append(_observation("bad", width=99.0, night="n1", set_aside_reason="clouded"))
    nights = eff.build_baseline_nights(frames)
    assert len(nights) == 1
    assert nights[0].median_star_width_effective_arcsec == pytest.approx(3.0)
    assert nights[0].frame_count == 3


def test_noise_regime_thresholds() -> None:
    """Sky variance of 9 read-noise variances is the sky-limited boundary."""
    assert eff.noise_regime(6.0, 2.0).regime == NoiseRegime.SKY_LIMITED
    assert eff.noise_regime(5.9, 2.0).regime == NoiseRegime.READ_NOISE_LIMITED
    assert eff.noise_regime(6.0, 2.0, source_signal_e=100.0).regime == NoiseRegime.SOURCE_LIMITED


def test_noise_regime_without_read_noise_is_unknown_with_a_reason() -> None:
    """No measured read noise gives an unknown regime and a reason."""
    result = eff.noise_regime(6.0, None)
    assert result.regime == NoiseRegime.UNKNOWN
    assert "read noise" in (result.reason or "")


def test_frame_factors_match_the_formula() -> None:
    """Seeing squared.

    Sky ratio and the read-noise share multiply to the exact combined form.
    """
    reference = _reference(width=2.0, sky=0.5)
    frame = _observation(width=3.0, sky=1.0, read=2.0, exposure=60.0)
    result = eff.frame_efficiency(frame, ScienceGoal.FAINT_POINT_SOURCE, reference)
    assert result is not None
    seeing = (2.0 / 3.0) ** 2
    combined = 0.5 / (1.0 + 4.0 / 60.0)
    assert result.eta == pytest.approx(seeing * combined)


def test_better_than_reference_is_capped_at_one_with_a_reason() -> None:
    """A sharper frame than the reference is capped and says so."""
    reference = _reference(width=3.0, sky=2.0)
    result = eff.frame_efficiency(_observation(width=1.5, sky=1.0), ScienceGoal.FAINT_POINT_SOURCE, reference)
    assert result is not None
    seeing = next(f for f in result.factors if f.cause == "seeing")
    assert seeing.value == pytest.approx(1.0)
    assert seeing.capped_reason


def test_astrometry_exponent_depends_on_regime() -> None:
    """Astrometric precision uses 4 when sky-limited and 2 otherwise."""
    assert eff.seeing_exponent(ScienceGoal.ASTROMETRIC_PRECISION, NoiseRegime.SKY_LIMITED) == pytest.approx(
        4.0
    )
    assert eff.seeing_exponent(
        ScienceGoal.ASTROMETRIC_PRECISION, NoiseRegime.READ_NOISE_LIMITED
    ) == pytest.approx(2.0)
    assert eff.seeing_exponent(ScienceGoal.FAINT_POINT_SOURCE, NoiseRegime.UNKNOWN) == pytest.approx(2.0)
    assert eff.seeing_exponent(ScienceGoal.EXTENDED_OBJECT, NoiseRegime.SKY_LIMITED) is None


def test_seeing_is_not_a_time_loss_for_extended_objects() -> None:
    """Seeing is left out for an extended object."""
    reference = _reference()
    result = eff.frame_efficiency(_observation(width=9.0), ScienceGoal.EXTENDED_OBJECT, reference)
    assert result is not None
    seeing = next(f for f in result.factors if f.cause == "seeing")
    assert not seeing.included
    assert seeing.recoverable_with_time is False


def test_goals_with_no_time_factors_give_no_eta() -> None:
    """Period detection has nothing included, so eta is None, with reasons."""
    assessment = eff.assess_efficiency([_observation()], ScienceGoal.PERIOD_DETECTION, _reference())
    assert assessment.result.eta is None
    assert assessment.frames_without_efficiency


def test_missing_read_noise_is_listed_as_not_included_not_invented() -> None:
    """Without read noise the factor is not included, and eta uses the rest."""
    frame = _observation(read=None, missing_reasons={"read_noise_e": "assumed"})
    frame = eff.FrameObservation(**{**frame.model_dump(), "read_noise_e": None})
    result = eff.frame_efficiency(frame, ScienceGoal.FAINT_POINT_SOURCE, _reference())
    assert result is not None
    read = next(f for f in result.factors if f.cause == "read_noise")
    assert not read.included
    assert result.regime.regime == NoiseRegime.UNKNOWN
    assert result.eta is not None


def test_physical_reference_leaves_sky_out() -> None:
    """Against the physical reference the sky factor is not included.

    Read noise still is.
    """
    reference = eff.physical_reference(100.0, 1.5, 2.0).reference
    assert reference is not None
    result = eff.frame_efficiency(_observation(), ScienceGoal.FAINT_POINT_SOURCE, reference)
    assert result is not None
    by_cause = {f.cause: f for f in result.factors}
    assert not by_cause["sky_brightness"].included
    assert by_cause["read_noise"].included
    assert not by_cause["transparency"].included


def test_assessment_effective_time_and_set_aside_are_kept_apart() -> None:
    """Effective time is the sum of exposure times eta.

    Set-aside frames are counted separately.
    """
    reference = _reference()
    frames = [_observation("a"), _observation("b", width=4.0), _observation("c", set_aside_reason="clouded")]
    assessment = eff.assess_efficiency(frames, ScienceGoal.FAINT_POINT_SOURCE, reference)
    assert assessment.frames_set_aside == 1
    assert assessment.set_aside_seconds == pytest.approx(60.0)
    assert assessment.result.captured_seconds == pytest.approx(120.0)
    etas = [item.eta for item in assessment.frame_results]
    assert assessment.result.efficiency_effective_seconds == pytest.approx(60.0 * sum(etas))
    assert assessment.result.eta == pytest.approx(sum(etas) / 2)


def test_loss_split_adds_up_exactly() -> None:
    """The shares of the causes add up to captured minus effective time."""
    reference = _reference()
    frames = [_observation(f"f{i}", width=2.5 + i * 0.4, sky=0.8 + 0.3 * i) for i in range(5)]
    assessment = eff.assess_efficiency(frames, ScienceGoal.FAINT_POINT_SOURCE, reference)
    shares = eff.split_kept_frame_deficit(assessment.frame_results)
    deficit = assessment.result.captured_seconds - (assessment.result.efficiency_effective_seconds or 0.0)
    assert sum(share.seconds for share in shares) == pytest.approx(deficit)
    assert {share.cause for share in shares} <= {"seeing", "sky_brightness", "read_noise"}


def test_moon_share_of_the_sky_loss_is_model_derived_and_adds_up() -> None:
    """With moonless rates the sky loss splits into Moon and other.

    The total is unchanged.
    """
    reference = _reference(width=3.0, sky=0.5)
    frames = [_observation(f"f{i}", width=3.0, sky=1.0) for i in range(3)]
    assessment = eff.assess_efficiency(frames, ScienceGoal.FAINT_POINT_SOURCE, reference)
    rates = {f.frame_id: 1.0 for f in frames}
    moonless = {f.frame_id: 0.7 for f in frames}
    plain = eff.split_kept_frame_deficit(assessment.frame_results)
    split = eff.split_kept_frame_deficit(
        assessment.frame_results, moonless_sky_rate_by_frame=moonless, sky_rate_by_frame=rates
    )
    by_cause = {share.cause: share for share in split}
    assert by_cause["sky_brightness_moon"].model_derived
    assert not by_cause["sky_brightness_other"].model_derived
    assert sum(s.seconds for s in split) == pytest.approx(sum(s.seconds for s in plain))
    sky_total = next(s.seconds for s in plain if s.cause == "sky_brightness")
    assert by_cause["sky_brightness_moon"].seconds / sky_total == pytest.approx(
        math.log(1.0 / 0.7) / math.log(2.0)
    )


def test_depth_loss_only_for_a_sky_limited_point_source() -> None:
    """The depth view applies to one goal and regime only."""
    assert eff.depth_loss_magnitudes(
        0.5, ScienceGoal.FAINT_POINT_SOURCE, NoiseRegime.SKY_LIMITED
    ) == pytest.approx(1.25 * math.log10(2.0))
    assert (
        eff.depth_loss_magnitudes(0.5, ScienceGoal.FAINT_POINT_SOURCE, NoiseRegime.READ_NOISE_LIMITED) is None
    )
    assert eff.depth_loss_magnitudes(0.5, ScienceGoal.EXTENDED_OBJECT, NoiseRegime.SKY_LIMITED) is None
    assert eff.depth_loss_magnitudes(None, ScienceGoal.FAINT_POINT_SOURCE, NoiseRegime.SKY_LIMITED) is None


def _row(
    frame_id: str,
    metric: str,
    value: float | None,
    version: int | None = None,
    status: MeasurementStatus = MeasurementStatus.MEASURED,
    minute: int = 0,
    reason: str = "",
    channel: str | None = None,
) -> QualityMeasurement:
    """Build one stored frame measurement.

    Parameters
    ----------
    frame_id, metric : `str`
        Subject and metric names.
    value : `float` or `None`
        The value.
    version : `int` or `None`
        Definition version; the pinned one when None.
    status : `MeasurementStatus`
        The status.
    minute : `int`
        Minute of the hour it was measured, to order rows.
    reason : `str`
        The reason for a missing or assumed value.
    channel : `str` or `None`
        Color plane.

    Returns
    -------
    row : `QualityMeasurement`
        The measurement.
    """
    return QualityMeasurement(
        metric=metric,
        definition_version=version or eff.PINNED_METRIC_VERSIONS[metric],
        subject=QualitySubject(
            level="frame",
            subject_id=frame_id,
            target_id="T",
            observing_night_id="n1",
            capture_setting_id=1,
            channel=channel,
        ),
        status=status,
        value=value,
        reason=reason,
        origin=MeasurementOrigin.MEASURED,
        run_id="r",
        measured_at=datetime(2026, 10, 1, 12, minute, tzinfo=UTC),
    )


def test_observations_from_measurements_pin_versions_and_use_the_newest_row() -> None:
    """The newest row wins.

    Another version is 'remeasure', assumed read noise is missing.
    """
    rows = [
        _row("f1", eff.STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC, 9.0, minute=1),
        _row("f1", eff.STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC, 3.0, minute=2),
        _row("f1", eff.SKY_RATE_METRIC, 1.0, version=1),
        _row("f1", eff.READ_NOISE_METRIC, 2.0, status=MeasurementStatus.ASSUMED, reason="profile"),
        _row("f1", eff.SKY_NOISE_METRIC, 5.0, status=MeasurementStatus.ASSUMED, reason="no temperature"),
        _row("f2", eff.STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC, 2.0, channel="R"),
        _row("f2", eff.STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC, 4.0, channel="G"),
        _row("f2", eff.STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC, 6.0, channel="B"),
        _row("f3", eff.SKY_RATE_METRIC, None, status=MeasurementStatus.NOT_MEASURED, reason="no gain"),
    ]
    found = {o.frame_id: o for o in eff.frame_observations_from_measurements(rows, {1: SETTING})}
    assert found["f1"].star_width_effective_arcsec == pytest.approx(3.0)
    assert found["f1"].sky_rate_e_per_s_per_px is None
    assert "remeasure" in found["f1"].missing_reasons["sky_rate_e_per_s_per_px"].lower()
    assert found["f1"].read_noise_e is None
    assert found["f1"].sky_noise_e == pytest.approx(5.0)
    assert found["f1"].assumed_fields == ("sky_noise_e",)
    assert found["f1"].capture_setting == SETTING
    assert found["f2"].star_width_effective_arcsec == pytest.approx(4.0)
    assert found["f3"].missing_reasons["sky_rate_e_per_s_per_px"] == "no gain"
