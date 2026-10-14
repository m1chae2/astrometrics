"""Purpose: Unit tests for the equipment-derived performance envelope.

Description: Verifies that every limit is worked out from the equipment and
its measurements, that changing the equipment changes the limits with no
other step, that a limit without enough data says so instead of guessing,
and that history-based limits are not thrown off by a few bad sessions.
"""

import math
from typing import Any

import pytest

from wayfindinglib.analytics.performance_envelope import (
    DEFAULT_BLUR_TOLERANCE_FRACTION,
    SIDEREAL_RATE_ARCSEC_PER_SECOND,
    SIGMA_TO_FWHM,
    MeasuredImageQuality,
    SensorLimits,
    derive_performance_envelope,
    robust_median_and_spread,
)
from wayfindinglib.models.equipment_and_site.equipment import (
    Camera,
    EquipmentConfiguration,
    GuideScope,
    Telescope,
)
from wayfindinglib.models.equipment_and_site.performance_envelope import (
    PerformanceEnvelope,
    ThresholdStatus,
    ThresholdTier,
)


def _equipment(focal_length_mm: float = 405.0, pixel_size_um: float = 3.76) -> EquipmentConfiguration:
    """Build an imaging telescope and camera.

    Returns
    -------
    equipment : `EquipmentConfiguration`
        The defaults are this observatory's telescope and camera.
    """
    telescope = Telescope(id="t", name="Apertura 75Q", focal_length_mm=focal_length_mm)
    camera = Camera(
        id="c",
        name="ZWO ASI 533MM Pro",
        pixel_size_um=pixel_size_um,
        sensor_width_px=3008,
        sensor_height_px=3008,
    )
    return EquipmentConfiguration(telescope=telescope, camera=camera)


def _guide_scope(focal_length_mm: float = 121.05) -> GuideScope:
    """Build a guide scope.

    Returns
    -------
    guide_scope : `GuideScope`
        Defaults to this observatory's 32 mm guide scope.
    """
    return GuideScope(id="g", name="Apertura 32mm", focal_length_mm=focal_length_mm, aperture_mm=32.0)


def _guide_camera(pixel_size_um: float = 3.75, width_px: int = 1280) -> Camera:
    """Build a guide camera.

    Returns
    -------
    guide_camera : `Camera`
        Defaults to this observatory's ASI120MC-S.
    """
    return Camera(
        id="gc",
        name="ZWO ASI120MC-S",
        pixel_size_um=pixel_size_um,
        sensor_width_px=width_px,
        sensor_height_px=960,
    )


def _envelope(**overrides: Any) -> PerformanceEnvelope:
    """Derive an envelope for the default equipment, with overrides.

    Returns
    -------
    envelope : `PerformanceEnvelope`
        The derived envelope.
    """
    arguments = {
        "equipment": _equipment(),
        "guide_scope": _guide_scope(),
        "guide_camera": _guide_camera(),
        "equipment_fingerprint": "fingerprint-a",
    }
    arguments.update(overrides)
    return derive_performance_envelope(**arguments)


def test_plate_scales_follow_from_the_optics_and_pixels() -> None:
    """Verify both scales match 206.265 x pixel size / focal length."""
    envelope = _envelope()

    assert envelope.value("imaging_plate_scale") == pytest.approx(206.265 * 3.76 / 405.0)
    assert envelope.value("guide_plate_scale") == pytest.approx(206.265 * 3.75 / 121.05)


def test_the_guide_field_limit_is_half_the_guide_frame() -> None:
    """Verify an error bigger than half the frame counts as a wrong star."""
    envelope = _envelope()

    assert envelope.value("max_credible_guide_error") == pytest.approx(206.265 * 3.75 / 121.05 * 1280 / 2)


def test_the_guide_speed_limit_is_the_sidereal_rate() -> None:
    """Verify no guide speed above how fast the sky turns is accepted."""
    envelope = _envelope()

    assert envelope.value("max_credible_guide_speed") == pytest.approx(15.041, abs=0.001)
    assert envelope.value("max_credible_guide_speed") == pytest.approx(SIDEREAL_RATE_ARCSEC_PER_SECOND)


def test_the_guiding_limit_widens_the_star_by_exactly_the_tolerance() -> None:
    """Verify star plus guiding blur equals (1 + tolerance) x the star."""
    fwhm = 5.5
    envelope = _envelope(measured_image_quality=MeasuredImageQuality(fwhm, 100))

    guiding_blur = SIGMA_TO_FWHM * envelope.value("guiding_rms_limit")
    widened = math.hypot(fwhm, guiding_blur)

    assert widened == pytest.approx(fwhm * (1.0 + DEFAULT_BLUR_TOLERANCE_FRACTION))


def test_the_trailing_limit_widens_the_star_by_exactly_the_tolerance() -> None:
    """Verify trailing at the limit widens the star by the tolerance."""
    fwhm = 5.5
    envelope = _envelope(measured_image_quality=MeasuredImageQuality(fwhm, 100))

    widened = math.hypot(fwhm, envelope.value("trailing_limit"))

    assert widened == pytest.approx(fwhm * (1.0 + DEFAULT_BLUR_TOLERANCE_FRACTION))


def test_the_budget_limits_scale_with_the_measured_star_width() -> None:
    """Verify sharper equipment gets a tighter limit, blurrier a looser one."""
    sharp = _envelope(measured_image_quality=MeasuredImageQuality(3.0, 100))
    blurry = _envelope(measured_image_quality=MeasuredImageQuality(6.0, 100))

    assert blurry.value("guiding_rms_limit") == pytest.approx(2.0 * sharp.value("guiding_rms_limit"))
    assert blurry.value("trailing_limit") == pytest.approx(2.0 * sharp.value("trailing_limit"))


def test_the_budget_limits_grow_with_the_tolerance() -> None:
    """Verify a looser tolerance allows more guiding error."""
    quality = MeasuredImageQuality(5.5, 100)
    strict = _envelope(measured_image_quality=quality, blur_tolerance_fraction=0.05)
    loose = _envelope(measured_image_quality=quality, blur_tolerance_fraction=0.20)

    assert loose.value("guiding_rms_limit") > strict.value("guiding_rms_limit")


def test_the_observatorys_measured_star_width_gives_the_guiding_error_it_achieves() -> None:
    """Verify the documented cross-check: 5.6 arcsec gives about 1.09 arcsec.

    The observatory's 19 real guide logs show about 0.8 to 1.4 arcseconds on
    good nights, so the default tolerance puts the limit where good nights
    sit, not somewhere arbitrary.
    """
    envelope = _envelope(measured_image_quality=MeasuredImageQuality(5.59, 361))

    assert envelope.value("guiding_rms_limit") == pytest.approx(1.09, abs=0.01)


def test_changing_the_imaging_camera_changes_the_imaging_scale_and_nothing_stale_remains() -> None:
    """Verify a camera swap is enough; no other step updates the limits."""
    before = _envelope()
    after = _envelope(equipment=_equipment(pixel_size_um=5.94), equipment_fingerprint="fingerprint-b")

    assert after.value("imaging_plate_scale") != pytest.approx(before.value("imaging_plate_scale"))
    assert after.value("imaging_plate_scale") == pytest.approx(206.265 * 5.94 / 405.0)
    assert after.equipment_fingerprint == "fingerprint-b"


def test_changing_the_guide_scope_changes_the_guide_limits() -> None:
    """Verify a longer guide scope tightens the guide limits."""
    before = _envelope()
    after = _envelope(guide_scope=_guide_scope(focal_length_mm=240.0))

    assert after.value("guide_plate_scale") == pytest.approx(206.265 * 3.75 / 240.0)
    assert after.value("guide_plate_scale") < before.value("guide_plate_scale")
    assert after.value("max_credible_guide_error") < before.value("max_credible_guide_error")


def test_changing_the_guide_camera_changes_the_guide_field() -> None:
    """Verify a guide camera with a wider sensor sees a larger field."""
    before = _envelope()
    after = _envelope(guide_camera=_guide_camera(width_px=1936))

    assert after.value("max_credible_guide_error") > before.value("max_credible_guide_error")


def test_without_a_separate_guide_train_the_main_optics_are_used() -> None:
    """Verify guiding through the main scope uses its own scale."""
    envelope = _envelope(guide_scope=None, guide_camera=None)

    assert envelope.value("guide_plate_scale") == pytest.approx(envelope.value("imaging_plate_scale"))


def test_a_changed_star_width_changes_the_limits_with_no_other_step() -> None:
    """Verify re-measuring after new optics is all it takes."""
    old_optics = _envelope(measured_image_quality=MeasuredImageQuality(5.5, 100))
    new_optics = _envelope(measured_image_quality=MeasuredImageQuality(2.5, 100))

    assert new_optics.value("guiding_rms_limit") < old_optics.value("guiding_rms_limit")


def test_budget_limits_say_so_when_no_frames_have_been_measured() -> None:
    """Verify missing image quality gives no value, never a default."""
    envelope = _envelope()

    for name in ("guiding_rms_limit", "trailing_limit"):
        threshold = envelope.thresholds[name]
        assert threshold.status == ThresholdStatus.INSUFFICIENT_DATA
        assert threshold.value is None
        assert "unknown" in threshold.derivation


def test_budget_limits_need_enough_frames() -> None:
    """Verify a star width from a handful of frames is not trusted."""
    envelope = _envelope(measured_image_quality=MeasuredImageQuality(5.5, 3))

    threshold = envelope.thresholds["guiding_rms_limit"]
    assert threshold.status == ThresholdStatus.INSUFFICIENT_DATA
    assert threshold.sample_count == 3
    assert "at least 20" in threshold.derivation


def test_sensor_limits_come_from_the_profile_with_their_provenance() -> None:
    """Verify saturation levels and where they came from pass through."""
    limits = SensorLimits(
        camera_name="ZWO ASI 533MM Pro",
        clip_ceiling_adu=65532.0,
        clip_ceiling_source="measured: maximum of saturated raw frames",
        saturation_threshold_adu=65000.0,
        saturation_threshold_source="assumed: just under 65535",
        is_generic_fallback=False,
    )
    envelope = _envelope(sensor_limits=limits)

    assert envelope.value("clip_ceiling") == pytest.approx(65532.0)
    assert envelope.thresholds["clip_ceiling"].tier == ThresholdTier.SENSOR_PROFILE
    assert "measured: maximum of saturated raw frames" in envelope.thresholds["clip_ceiling"].derivation
    assert "assumed" in envelope.thresholds["saturation_threshold"].derivation


def test_a_camera_with_no_profile_is_flagged_as_using_stand_in_numbers() -> None:
    """Verify the generic fallback is never taken for camera facts."""
    limits = SensorLimits("Unlisted", 65535.0, "assumed", 65000.0, "assumed", is_generic_fallback=True)
    envelope = _envelope(sensor_limits=limits)

    assert "no profile of its own" in envelope.thresholds["saturation_threshold"].derivation


def test_sensor_limits_say_so_when_no_profile_is_supplied() -> None:
    """Verify an absent profile gives no saturation value."""
    envelope = _envelope()

    assert envelope.thresholds["saturation_threshold"].status == ThresholdStatus.INSUFFICIENT_DATA


def test_baseline_limits_are_not_judged_until_there_are_enough_sessions() -> None:
    """Verify four sessions are not enough to call anything unusual."""
    envelope = _envelope(baseline_values={"guide_snr": [300.0, 310.0, 290.0, 305.0]})

    threshold = envelope.thresholds["guide_snr_low_limit"]
    assert threshold.status == ThresholdStatus.INSUFFICIENT_DATA
    assert threshold.sample_count == 4


def test_the_snr_limit_is_below_the_typical_session() -> None:
    """Verify the low limit is the median times exp(-3 x log spread)."""
    values = [300.0, 310.0, 290.0, 305.0, 295.0, 315.0]
    log_median, log_spread = robust_median_and_spread([math.log(value) for value in values])
    envelope = _envelope(baseline_values={"guide_snr": values})

    assert envelope.value("guide_snr_low_limit") == pytest.approx(math.exp(log_median - 3.0 * log_spread))
    assert envelope.value("guide_snr_low_limit") < math.exp(log_median)


def test_the_rms_limit_is_above_the_typical_session() -> None:
    """Verify the high limit is the median times exp(+3 x log spread)."""
    values = [1.1, 1.3, 1.0, 1.2, 1.4, 1.2]
    log_median, log_spread = robust_median_and_spread([math.log(value) for value in values])
    envelope = _envelope(baseline_values={"guiding_rms": values})

    assert envelope.value("guiding_rms_high_limit") == pytest.approx(math.exp(log_median + 3.0 * log_spread))


def test_baseline_limits_are_not_dragged_by_a_few_bad_sessions() -> None:
    """Verify two terrible sessions barely move the limit.

    This is why the baseline uses the median and a robust spread: the
    equipment's history contains bad nights, and they must not make a bad
    night look normal.
    """
    good = [1.1, 1.3, 1.0, 1.2, 1.4, 1.2, 1.1, 1.3]
    with_bad_nights = [*good, 45.0, 60.0]

    clean_limit = _envelope(baseline_values={"guiding_rms": good}).value("guiding_rms_high_limit")
    dirty_limit = _envelope(baseline_values={"guiding_rms": with_bad_nights}).value("guiding_rms_high_limit")

    assert dirty_limit < 3.0 * clean_limit


def test_non_finite_baseline_values_are_ignored() -> None:
    """Verify NaN or infinite session values do not poison the baseline."""
    values = [1.1, 1.3, 1.0, 1.2, 1.4, math.nan, math.inf]
    envelope = _envelope(baseline_values={"guiding_rms": values})

    assert envelope.thresholds["guiding_rms_high_limit"].sample_count == 5
    assert math.isfinite(envelope.value("guiding_rms_high_limit"))


def test_every_limit_records_how_it_was_obtained() -> None:
    """Verify each limit has a tier, a derivation and its inputs."""
    envelope = _envelope(measured_image_quality=MeasuredImageQuality(5.5, 100))

    for threshold in envelope.thresholds.values():
        assert threshold.derivation
        assert isinstance(threshold.tier, ThresholdTier)
    assert envelope.thresholds["guiding_rms_limit"].inputs["fwhm_arcsec"] == pytest.approx(5.5)
    assert envelope.thresholds["guiding_rms_limit"].sample_count == 100


def test_the_envelope_is_a_pure_function_of_its_inputs() -> None:
    """Verify the same inputs always give the same limits."""
    quality = MeasuredImageQuality(5.5, 100)

    first = _envelope(measured_image_quality=quality)
    second = _envelope(measured_image_quality=quality)

    assert first == second


def test_a_baseline_limit_for_a_positive_quantity_is_never_negative() -> None:
    """Verify two very different regimes cannot produce a negative SNR limit.

    Regression test, found on real data: this observatory's guide SNR was
    250 to 480 in one period and 29 to 85 in another. A limit of median
    minus 3 x spread came out at -591. On logarithms the same history gives
    a positive, meaningful limit.
    """
    snr_history = [325.0, 271.0, 306.0, 269.0, 484.0, 330.0, 380.0, 64.0, 245.0, 29.0, 85.0, 45.0]

    limit = _envelope(baseline_values={"guide_snr": snr_history}).value("guide_snr_low_limit")

    assert limit > 0.0
    assert limit < 100.0


def test_image_quality_records_the_exposure_cut_it_used() -> None:
    """Verify the minimum exposure behind a star width is on the record."""
    quality = MeasuredImageQuality(5.6, 361, minimum_exposure_seconds=9.6)
    envelope = _envelope(measured_image_quality=quality)

    assert envelope.thresholds["guiding_rms_limit"].inputs["minimum_exposure_seconds"] == pytest.approx(9.6)


def test_the_excursion_limit_is_the_larger_of_five_sigma_and_one_guide_pixel() -> None:
    """Verify both ingredients are equipment-derived and the larger wins."""
    envelope = _envelope(measured_image_quality=MeasuredImageQuality(5.59, 361))
    statistical = 5.0 * envelope.value("guiding_rms_limit")
    pixel = envelope.value("guide_plate_scale")

    assert envelope.value("guide_excursion_limit") == pytest.approx(max(statistical, pixel))
    assert envelope.value("guide_excursion_limit") >= pixel


def test_a_coarse_guide_pixel_sets_the_excursion_limit() -> None:
    """Verify the limit is never finer than the guider can resolve."""
    sharp = _envelope(measured_image_quality=MeasuredImageQuality(1.0, 361))

    assert 5.0 * sharp.value("guiding_rms_limit") < sharp.value("guide_plate_scale")
    assert sharp.value("guide_excursion_limit") == pytest.approx(sharp.value("guide_plate_scale"))


def test_the_excursion_limit_falls_back_to_one_pixel_without_a_star_width() -> None:
    """Verify an unknown star width still gives a usable, labelled limit."""
    envelope = _envelope()

    assert envelope.value("guide_excursion_limit") == pytest.approx(envelope.value("guide_plate_scale"))
    assert envelope.thresholds["guide_excursion_limit"].tier == ThresholdTier.GEOMETRY


def test_the_excursion_limit_follows_the_guide_optics() -> None:
    """Verify a change of guide scope changes the excursion limit too."""
    before = _envelope()
    after = _envelope(guide_scope=_guide_scope(focal_length_mm=240.0))

    assert after.value("guide_excursion_limit") < before.value("guide_excursion_limit")


def test_a_fraction_limit_is_a_percentile_of_the_equipments_own_nights() -> None:
    """Verify a night is unusual if worse than 90% of earlier ones."""
    lost_fractions = [0.0, 0.0, 0.0, 0.0, 0.001, 0.0, 0.002, 0.0, 0.0, 0.26]

    limit = _envelope(baseline_values={"guide_lost_fraction": lost_fractions}).value(
        "guide_lost_fraction_high_limit"
    )

    assert limit == pytest.approx(0.002)
    assert 0.26 > limit


def test_a_fraction_limit_works_when_the_typical_night_loses_nothing() -> None:
    """Verify a mostly-zero history does not make every loss unusual.

    Regression guard for the reason a percentile is used: a median plus a
    spread would give a zero spread here, and any lost frame at all would
    have counted as unusual.
    """
    envelope = _envelope(baseline_values={"guide_lost_fraction": [0.0] * 8 + [0.004, 0.3]})

    assert envelope.value("guide_lost_fraction_high_limit") == pytest.approx(0.004)


def test_fraction_limits_are_not_judged_without_enough_nights() -> None:
    """Verify four nights are not enough for a percentile either."""
    envelope = _envelope(baseline_values={"guide_excursion_fraction": [0.0, 0.1, 0.0, 0.0]})

    threshold = envelope.thresholds["guide_excursion_fraction_high_limit"]
    assert threshold.status == ThresholdStatus.INSUFFICIENT_DATA
    assert threshold.value is None


def test_the_night_star_width_limit_is_above_the_equipments_usual_width() -> None:
    """Verify a width above the limit is unusual and the usual one is not."""
    widths = [5.2, 5.6, 5.4, 5.8, 5.5, 5.3]

    threshold = _envelope(baseline_values={"night_star_width": widths}).thresholds[
        "night_star_width_high_limit"
    ]

    assert threshold.status == ThresholdStatus.DERIVED
    assert threshold.tier == ThresholdTier.OWN_BASELINE
    assert threshold.value > max(widths)
    assert threshold.value < 3 * max(widths)


def test_the_night_star_roundness_limit_is_below_the_equipments_usual_roundness() -> None:
    """Verify a roundness under the limit is unusual, and stays positive."""
    roundness = [0.86, 0.88, 0.85, 0.87, 0.86, 0.89]

    limit = _envelope(baseline_values={"night_star_roundness": roundness}).value(
        "night_star_roundness_low_limit"
    )

    assert 0.0 < limit < min(roundness)


def test_star_quality_limits_are_not_judged_without_enough_nights() -> None:
    """Verify four nights of star measurements are not enough."""
    envelope = _envelope(baseline_values={"night_star_width": [5.0, 5.2, 5.1, 5.3]})

    assert envelope.thresholds["night_star_width_high_limit"].status == ThresholdStatus.INSUFFICIENT_DATA
    assert envelope.value("night_star_roundness_low_limit") is None


def test_the_abort_fraction_limit_is_a_percentile_of_the_equipments_own_nights() -> None:
    """Verify a night is unusual if it cancelled more than 90% of nights."""
    fractions = [0.0, 0.0, 0.01, 0.0, 0.02, 0.0, 0.03, 0.0, 0.0, 0.3]

    limit = _envelope(baseline_values={"capture_abort_fraction": fractions}).value(
        "capture_abort_fraction_high_limit"
    )

    assert limit == pytest.approx(0.03)


def test_the_shortest_exposure_that_shows_guiding_error_is_a_few_guide_cycles() -> None:
    """Verify the cut-off follows the equipment's own guide cycle."""
    slow = _envelope(guide_cadence_seconds=6.0).thresholds["minimum_star_measurement_exposure"]
    fast = _envelope(guide_cadence_seconds=2.0).thresholds["minimum_star_measurement_exposure"]

    assert slow.value == pytest.approx(18.0)
    assert fast.value == pytest.approx(6.0)
    assert slow.unit == "s"
    assert slow.tier == ThresholdTier.OWN_BASELINE


def test_the_shortest_exposure_is_not_guessed_before_the_equipment_has_guided() -> None:
    """Verify a missing guide cycle gives a limit marked as lacking data."""
    threshold = _envelope().thresholds["minimum_star_measurement_exposure"]

    assert threshold.status == ThresholdStatus.INSUFFICIENT_DATA
    assert threshold.value is None


def test_the_guide_star_brightness_limit_is_below_the_equipments_usual_brightness() -> None:
    """Verify a much fainter night is unusual and the limit stays positive."""
    masses = [250000.0, 300000.0, 280000.0, 320000.0, 290000.0, 310000.0]

    threshold = _envelope(baseline_values={"guide_star_mass": masses}).thresholds["guide_star_mass_low_limit"]

    assert threshold.status == ThresholdStatus.DERIVED
    assert 0.0 < threshold.value < min(masses)
    assert threshold.unit == "counts"
    assert threshold.inputs["median"] == pytest.approx(295000.0, rel=0.05)


def test_the_guide_star_brightness_limit_needs_enough_nights() -> None:
    """Verify four nights are not enough to call a night dim."""
    envelope = _envelope(baseline_values={"guide_star_mass": [250000.0, 300000.0, 280000.0, 320000.0]})

    assert envelope.thresholds["guide_star_mass_low_limit"].status == ThresholdStatus.INSUFFICIENT_DATA
