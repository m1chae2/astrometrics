"""Purpose: Tests for the refraction metrics at quality checkpoint 0.

Description: Checks the four refraction metrics and the three flags that the
raw-frame checkpoint carries, first from hand-made refraction records, then
on a result that the real pipeline builds from a synthetic spectral frame
with a WCS, a time and an observatory site. The pipeline tests check that the
wavelengths move the right way, that the result stores the record, that a
missing site or a low target leaves the wavelengths alone, and that the
record survives a round trip through its camelCase form.
"""

import numpy as np
import pytest

from astrometricslib.foundation.observatory_site import ObservatorySite
from astrometricslib.models.spectroscopy_quality import StageQualityMetric
from astrometricslib.models.stellar_source import (
    DifferentialRefractionRecord,
    SpectroscopyResult,
    StellarObject,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.assess_raw_frame_quality import (
    DAR_ALONG_DISPERSION_LIMIT_ANGSTROM,
    assess_raw_frame_quality,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_refraction import (
    MINIMUM_ALTITUDE_DEGREES,
    AtmosphericConditions,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.differential_refraction import (
    REASON_NO_SITE,
    DifferentialRefraction,
    local_dispersion_angstrom_per_px,
    parallactic_angle_degrees,
)
from astrometricslib.pipelines.spectroscopy.test.pre_processing.test_differential_refraction import (
    CONDITIONS,
    EXPOSURE_SECONDS,
    EXPOSURE_START,
    MID_EXPOSURE,
    PIXEL_SCALE_ARCSEC,
    SITE,
    _target,
    _wcs_with_x_axis_at,
)
from astrometricslib.pipelines.spectroscopy.test.test_result_diagnostics import (
    FRAME_SHAPE,
    TARGET_AIRMASS,
    ZERO_ORDER_XY,
    _ArrayImage,
    _build_pipeline,
)
from astrometricslib.test.synthetic import make_spectral_frame

DAR_METRIC_NAMES = (
    "dar_along_dispersion_angstrom",
    "dar_across_dispersion_px",
    "target_altitude_degrees",
    "parallactic_to_dispersion_angle_degrees",
)

# Cold air at sea level bends light about a third more than the conditions
# of the test site, enough to push a low target past the 20 Angstrom limit.
COLD_DENSE_AIR = AtmosphericConditions(1040.0, -20.0, 0.0, "test")


def _checkpoint_metrics(refraction: DifferentialRefraction | None) -> dict[str, StageQualityMetric]:
    """Build checkpoint 0 around a refraction record and index its metrics.

    Parameters
    ----------
    refraction : `DifferentialRefraction` or `None`
        The record to pass in.

    Returns
    -------
    metrics : `dict` [`str`, `StageQualityMetric`]
        The metrics by name.
    """
    checkpoint = assess_raw_frame_quality(
        zero_order_saturated_pixel_fraction=0.0,
        valid_fraction=1.0,
        trail_width_px=[3.0],
        extraction_diagnostics=None,
        differential_refraction=refraction,
    )
    return {entry.name: entry for entry in checkpoint.metrics}


def _checkpoint_flags(refraction: DifferentialRefraction | None) -> list[str]:
    """Build checkpoint 0 around a refraction record and give its flags.

    Parameters
    ----------
    refraction : `DifferentialRefraction` or `None`
        The record to pass in.

    Returns
    -------
    flags : `list` [`str`]
        The checkpoint's flags.
    """
    return assess_raw_frame_quality(
        zero_order_saturated_pixel_fraction=0.0,
        valid_fraction=1.0,
        trail_width_px=[3.0],
        extraction_diagnostics=None,
        differential_refraction=refraction,
    ).flags


def _computed_record(**changes: float) -> DifferentialRefraction:
    """Build a computed refraction record.

    Parameters
    ----------
    **changes : `float`
        Fields to replace.

    Returns
    -------
    record : `DifferentialRefraction`
        A record with a 9 Angstrom spread, a target at 40 degrees and the
        red end toward the zenith.
    """
    fields = {
        "is_computed": True,
        "is_applied": True,
        "altitude_degrees": 40.0,
        "parallactic_to_dispersion_angle_degrees": 0.0,
        "along_dispersion_span_angstrom": 9.0,
        "across_dispersion_span_px": 0.0,
    }
    fields.update(changes)
    return DifferentialRefraction(**fields)


def test_a_checkpoint_built_without_a_refraction_record_has_no_refraction_metrics() -> None:
    """Older callers that pass no record see no new metrics or flags."""
    metrics = _checkpoint_metrics(None)

    assert not set(DAR_METRIC_NAMES) & set(metrics)
    assert not {"dar_large", "dar_not_computed", "target_altitude_low"} & set(_checkpoint_flags(None))


def test_the_four_refraction_metrics_carry_their_values_and_limits() -> None:
    """The along span and the altitude have limits; the others report only."""
    metrics = _checkpoint_metrics(
        _computed_record(
            along_dispersion_span_angstrom=9.0,
            across_dispersion_span_px=0.4,
            altitude_degrees=40.3,
            parallactic_to_dispersion_angle_degrees=-35.0,
        )
    )

    assert set(DAR_METRIC_NAMES) <= set(metrics)
    along = metrics["dar_along_dispersion_angstrom"]
    assert (along.value, along.unit, along.limit, along.passed) == (9.0, "angstrom", 20.0, True)
    assert "design" in along.note
    assert "half the resolution element" in along.note
    across = metrics["dar_across_dispersion_px"]
    assert (across.value, across.unit, across.limit, across.passed) == (0.4, "pixel", None, None)
    altitude = metrics["target_altitude_degrees"]
    assert (altitude.value, altitude.unit, altitude.limit, altitude.passed) == (40.3, "degree", 20.0, True)
    angle = metrics["parallactic_to_dispersion_angle_degrees"]
    assert (angle.value, angle.unit, angle.limit, angle.passed) == (-35.0, "degree", None, None)
    assert _checkpoint_flags(_computed_record()) == []


def test_the_limits_come_from_the_modules_that_define_them() -> None:
    """The metric limits equal the named constants, not copies."""
    metrics = _checkpoint_metrics(_computed_record())

    assert metrics["dar_along_dispersion_angstrom"].limit == DAR_ALONG_DISPERSION_LIMIT_ANGSTROM
    assert metrics["target_altitude_degrees"].limit == MINIMUM_ALTITUDE_DEGREES


def test_a_large_wavelength_spread_fails_its_limit_and_raises_dar_large() -> None:
    """Over 20 Angstroms fails; exactly 20 passes."""
    over = _computed_record(along_dispersion_span_angstrom=20.5)
    at_limit = _computed_record(along_dispersion_span_angstrom=20.0)

    assert _checkpoint_metrics(over)["dar_along_dispersion_angstrom"].passed is False
    assert "dar_large" in _checkpoint_flags(over)
    assert _checkpoint_metrics(at_limit)["dar_along_dispersion_angstrom"].passed is True
    assert "dar_large" not in _checkpoint_flags(at_limit)


def test_a_record_that_was_not_computed_has_empty_metrics_and_dar_not_computed() -> None:
    """With no site the metrics have no value and the flag is set."""
    record = DifferentialRefraction(False, False, REASON_NO_SITE)

    metrics = _checkpoint_metrics(record)

    assert set(DAR_METRIC_NAMES) <= set(metrics)
    assert all(metrics[name].value is None and metrics[name].passed is None for name in DAR_METRIC_NAMES)
    flags = _checkpoint_flags(record)
    assert "dar_not_computed" in flags
    assert "target_altitude_low" not in flags


def test_a_low_target_fails_the_altitude_limit_and_is_flagged() -> None:
    """Below 20 degrees the altitude metric fails and the model is refused."""
    record = DifferentialRefraction(False, False, "too low", altitude_degrees=15.0)

    metrics = _checkpoint_metrics(record)

    assert metrics["target_altitude_degrees"].value == pytest.approx(15.0)
    assert metrics["target_altitude_degrees"].passed is False
    assert metrics["dar_along_dispersion_angstrom"].value is None
    assert {"dar_not_computed", "target_altitude_low"} <= set(_checkpoint_flags(record))


def _build_pipeline_result(
    hour_angle_hours: float = 3.0,
    angle_from_zenith_degrees: float = 0.0,
    site: ObservatorySite | None = SITE,
    conditions: AtmosphericConditions | None = CONDITIONS,
    with_wcs: bool = True,
) -> tuple[SpectroscopyResult, np.ndarray]:
    """Extract a synthetic star with a WCS and time, and build its result.

    Parameters
    ----------
    hour_angle_hours : `float`, optional
        The target's hour angle at mid-exposure.
    angle_from_zenith_degrees : `float`, optional
        The dispersion's position angle minus the parallactic angle.
    site : `ObservatorySite` or `None`, optional
        The observatory given to the pipeline.
    conditions : `AtmosphericConditions` or `None`, optional
        The air's conditions given to the pipeline.
    with_wcs : `bool`, optional
        Whether the image carries a WCS.

    Returns
    -------
    result : `SpectroscopyResult`
        What the pipeline saved on the star.
    raw_wavelengths_angstrom : `numpy.ndarray`
        The wavelength scale before the refraction correction.
    """
    frame = make_spectral_frame(
        zero_order_xy=ZERO_ORDER_XY, angle_deg=2.0, lines=(), shape=FRAME_SHAPE, add_noise=False
    )
    image = _ArrayImage(
        frame.image,
        header={"AIRMASS": TARGET_AIRMASS, "DATE-OBS": EXPOSURE_START, "EXPTIME": EXPOSURE_SECONDS},
    )
    target = _target(hour_angle_hours, 10.0)
    if with_wcs:
        _, zenith_angle = parallactic_angle_degrees(target, MID_EXPOSURE, SITE)
        image._wcs = _wcs_with_x_axis_at(target, zenith_angle + angle_from_zenith_degrees)
    pipeline = _build_pipeline("traced")
    pipeline.observatory_site = site
    pipeline.atmospheric_conditions = conditions
    extraction = pipeline._process_single_star(image, ZERO_ORDER_XY, auto_detect_angle=False)
    star = StellarObject(id="TestStar")
    pipeline._apply_result_to_stellar_object(star, extraction, image)
    assert star.spectroscopy is not None
    return star.spectroscopy, np.array(extraction["wavelengths"]) * 10.0


def _raw_frame_metrics(result: SpectroscopyResult) -> dict[str, StageQualityMetric]:
    """Index the raw-frame checkpoint of a result by metric name.

    Parameters
    ----------
    result : `SpectroscopyResult`
        A pipeline-built result.

    Returns
    -------
    metrics : `dict` [`str`, `StageQualityMetric`]
        The metrics by name.
    """
    checkpoint = result.stage_quality[0]
    assert checkpoint.stage == "raw_frame"
    return {entry.name: entry for entry in checkpoint.metrics}


def test_a_pipeline_built_result_carries_the_refraction_metrics_at_checkpoint_zero() -> None:
    """Checkpoint 0 holds the four metrics, built from the stored record."""
    result, _ = _build_pipeline_result()
    metrics = _raw_frame_metrics(result)
    record = result.differential_refraction

    assert record is not None
    assert set(DAR_METRIC_NAMES) <= set(metrics)
    assert metrics["dar_along_dispersion_angstrom"].value == pytest.approx(
        record.along_dispersion_span_angstrom
    )
    # The fixture's grating has 100 lines/mm, about 22 Angstroms per pixel,
    # so the 0.8 pixel spread at this altitude is about 17 Angstroms.
    assert metrics["dar_along_dispersion_angstrom"].value == pytest.approx(16.7, abs=2.0)
    assert metrics["dar_along_dispersion_angstrom"].passed is True
    assert metrics["dar_across_dispersion_px"].value == pytest.approx(0.0, abs=0.02)
    assert metrics["target_altitude_degrees"].value == pytest.approx(40.3, abs=0.1)
    assert metrics["target_altitude_degrees"].passed is True
    assert metrics["parallactic_to_dispersion_angle_degrees"].value == pytest.approx(0.0, abs=0.01)
    assert not {"dar_large", "dar_not_computed", "target_altitude_low"} & set(result.stage_quality[0].flags)


def test_the_stored_record_holds_the_geometry_the_conditions_and_the_displacements() -> None:
    """The record names the angles, the air and the shifts at both ends."""
    result, _ = _build_pipeline_result()
    record = result.differential_refraction

    assert record is not None
    assert record.is_computed is True
    assert record.is_applied is True
    assert record.reason is None
    assert record.mid_exposure_utc is not None
    assert record.mid_exposure_utc.startswith("2025-06-01T04:00:00")
    assert record.altitude_degrees == pytest.approx(40.3, abs=0.1)
    assert record.parallactic_angle_degrees == pytest.approx(44.9, abs=1.0)
    assert record.pixel_scale_arcsec == pytest.approx(PIXEL_SCALE_ARCSEC, rel=1e-3)
    assert record.effective_wavelength_angstrom is not None
    assert 5000.0 < record.effective_wavelength_angstrom < 7500.0
    assert record.pressure_hpa == pytest.approx(CONDITIONS.pressure_hpa)
    assert record.temperature_c == pytest.approx(CONDITIONS.temperature_c)
    assert record.relative_humidity_percent == pytest.approx(CONDITIONS.relative_humidity_percent)
    assert record.atmosphere_source == CONDITIONS.source
    assert record.along_dispersion_arcsec_at_4200 is not None
    assert record.along_dispersion_arcsec_at_8000 is not None
    assert record.along_dispersion_arcsec_at_4200 > 0.5
    assert record.along_dispersion_arcsec_at_8000 < 0.0
    assert record.across_dispersion_arcsec_at_4200 == pytest.approx(0.0, abs=0.01)
    assert record.across_dispersion_arcsec_at_8000 == pytest.approx(0.0, abs=0.01)
    assert record.along_dispersion_angstrom_at_4200 is not None
    assert record.along_dispersion_angstrom_at_8000 is not None
    expected_blue_angstrom = (
        record.along_dispersion_arcsec_at_4200
        / PIXEL_SCALE_ARCSEC
        * float(local_dispersion_angstrom_per_px(np.array([4200.0]), 16.5, 100.0, 3.76)[0])
    )
    assert record.along_dispersion_angstrom_at_4200 == pytest.approx(expected_blue_angstrom, rel=0.02)


def test_the_pipeline_moves_the_blue_end_down_and_the_red_end_up() -> None:
    """Blue light lands too far toward the red, so its wavelength drops."""
    result, raw = _build_pipeline_result()
    corrected = np.array(result.wavelengths_angstrom)

    change = corrected - raw

    assert corrected.shape == raw.shape
    assert change[0] < -2.0
    assert change[-1] > 0.0
    assert np.abs(change).max() < 15.0
    assert np.all(np.diff(corrected) > 0.0)


def test_dispersion_across_the_vertical_changes_no_wavelength_but_widens_the_trail() -> None:
    """At 90 degrees the correction is zero along and the trail widens."""
    result, raw = _build_pipeline_result(angle_from_zenith_degrees=90.0)
    metrics = _raw_frame_metrics(result)

    np.testing.assert_allclose(np.array(result.wavelengths_angstrom), raw, atol=0.05)
    assert metrics["dar_along_dispersion_angstrom"].value == pytest.approx(0.0, abs=0.05)
    assert metrics["dar_across_dispersion_px"].value == pytest.approx(0.8, abs=0.15)
    assert abs(metrics["parallactic_to_dispersion_angle_degrees"].value) == pytest.approx(90.0, abs=0.05)


def test_a_low_cold_target_exceeds_the_limit_and_the_flag_is_raised() -> None:
    """At 23 degrees in cold dense air the spread is over 20 Angstroms."""
    result, raw = _build_pipeline_result(hour_angle_hours=4.6, conditions=COLD_DENSE_AIR)

    metrics = _raw_frame_metrics(result)

    assert metrics["dar_along_dispersion_angstrom"].value > DAR_ALONG_DISPERSION_LIMIT_ANGSTROM
    assert metrics["dar_along_dispersion_angstrom"].passed is False
    assert "dar_large" in result.stage_quality[0].flags
    assert result.stage_quality[0].has_failed_metric is True
    # The flag reports the size before the correction, and the correction
    # was applied.
    assert result.differential_refraction is not None
    assert result.differential_refraction.is_applied is True
    assert np.abs(np.array(result.wavelengths_angstrom) - raw).max() > 5.0


def test_a_target_below_twenty_degrees_is_refused_and_flagged() -> None:
    """At 14 degrees the wavelengths stay and the altitude metric fails."""
    result, raw = _build_pipeline_result(hour_angle_hours=5.5)

    metrics = _raw_frame_metrics(result)
    flags = result.stage_quality[0].flags

    np.testing.assert_array_equal(np.array(result.wavelengths_angstrom), raw)
    assert metrics["target_altitude_degrees"].value < MINIMUM_ALTITUDE_DEGREES
    assert metrics["target_altitude_degrees"].passed is False
    assert {"dar_not_computed", "target_altitude_low"} <= set(flags)
    assert result.differential_refraction is not None
    assert result.differential_refraction.is_applied is False


def test_without_a_site_the_pipeline_skips_the_correction_and_flags_it() -> None:
    """With no site there are no refraction numbers and no shift."""
    result, raw = _build_pipeline_result(site=None)

    metrics = _raw_frame_metrics(result)

    np.testing.assert_array_equal(np.array(result.wavelengths_angstrom), raw)
    assert "dar_not_computed" in result.stage_quality[0].flags
    assert all(metrics[name].value is None for name in DAR_METRIC_NAMES)
    assert result.differential_refraction is not None
    assert result.differential_refraction.reason == REASON_NO_SITE
    assert result.differential_refraction.is_applied is False


def test_without_a_wcs_the_pipeline_skips_the_correction_and_flags_it() -> None:
    """No WCS means no sky direction, so nothing is computed."""
    result, raw = _build_pipeline_result(with_wcs=False)

    np.testing.assert_array_equal(np.array(result.wavelengths_angstrom), raw)
    assert "dar_not_computed" in result.stage_quality[0].flags
    assert result.differential_refraction is not None
    assert result.differential_refraction.is_computed is False


def test_without_given_conditions_the_pipeline_uses_the_standard_atmosphere() -> None:
    """The record says the conditions came from the standard atmosphere."""
    result, _ = _build_pipeline_result(conditions=None)

    assert result.differential_refraction is not None
    assert (
        result.differential_refraction.atmosphere_source == "standard atmosphere scaled to the site elevation"
    )


def test_the_pipeline_defaults_to_no_site_when_given_a_config() -> None:
    """With an explicit config the pipeline has no site until given one."""
    pipeline = _build_pipeline("traced")

    assert pipeline.observatory_site is None
    assert pipeline.atmospheric_conditions is None


def test_the_refraction_record_round_trips_through_its_camel_case_form() -> None:
    """The saved JSON uses camelCase and loads back to the same record."""
    result, _ = _build_pipeline_result()

    dumped = result.model_dump(by_alias=True)
    loaded = SpectroscopyResult.model_validate(dumped)

    assert "differentialRefraction" in dumped
    assert "alongDispersionArcsecAt4200" in dumped["differentialRefraction"]
    assert loaded.differential_refraction == result.differential_refraction
    assert isinstance(loaded.differential_refraction, DifferentialRefractionRecord)


def test_a_result_saved_before_the_refraction_record_loads_without_one() -> None:
    """A stored spectrum with no refraction field has `None` for it."""
    assert SpectroscopyResult().differential_refraction is None
