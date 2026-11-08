"""Purpose: Test the fit of the INDI simulators' settings (phase S3).

Description: Checks, with synthetic measurements, that each conversion of
section 5 of the simulator plan gives the number the INDI 2.2.5 drivers need,
that a value without data falls back one step, that every value is clamped to
its property's range and says so, that the results file holds what the plan
requires, that a truncated reply is refused, and that the exposure driver
reads the properties from the same file. The INDI simulators are not started
here; the live checks are in `test_fit_live.py`.
"""

import json
import math
import os
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from simulator import device_settings
from simulator.device_settings import DeviceSetting, matches, merge_device_settings
from simulator.fit import driver_model
from simulator.fit.fitting import FitResult, fit_values
from simulator.fit.measurements import (
    GUIDING_RUN_LIMITS,
    J2000_EPOCH_SECONDS,
    Candidate,
    Measurements,
    TruncatedReplyError,
    collect_measurements,
    meridian_ra_degrees,
)
from simulator.fit.ranges import GUIDE_CAMERA, MAIN_CAMERA, MOUNT, clamp_to_range
from simulator.fit.results import (
    RESULTS_FILE_NAME,
    fingerprint_library_copy,
    fitted_controller_settings,
    fitted_device_settings,
    fitted_guide_rate_fraction,
    read_results,
    write_results,
)
from simulator.fit.world_setup import CameraSetup, WorldSetup, world_setup_from_settings

SETTINGS = "SIMULATOR_SETTINGS"
DECLINATION = 60.0
"""A declination whose cosine is exactly one half."""


def _camera(**changes: Any) -> CameraSetup:
    """Make a camera with the numbers of the user's ASI533 profile.

    Returns
    -------
    camera : `CameraSetup`
        The camera, with `changes` applied.
    """
    values = {
        "width_px": 3008,
        "height_px": 3008,
        "gain": 0.0,
        "bias_level_adu": 394.85,
        "read_noise_adu": 4.85,
        "gain_e_per_adu": 0.783,
        "clip_ceiling_adu": 65532.0,
    }
    values.update(changes)
    return CameraSetup(**values)


def _setup(**guide: Any) -> WorldSetup:
    """Make a world setup with a main camera and a guide camera.

    Returns
    -------
    setup : `WorldSetup`
        The cameras and a declination of 60 degrees.
    """
    return WorldSetup(
        main=_camera(),
        guide=_camera(
            width_px=1280, height_px=960, gain=50.0, bias_level_adu=None, read_noise_adu=None, **guide
        ),
        declination_degrees=DECLINATION,
    )


def _candidate(kind: str, values: dict[str, float], **changes: Any) -> Candidate:
    """Make a candidate with a fixed call and night.

    Returns
    -------
    candidate : `Candidate`
        The candidate.
    """
    fields = {
        "kind": kind,
        "values": values,
        "call": "wayfinder.review.test_call",
        "parameters": {"a": 1},
        "nights": ["2026-10-03"],
        "note": "a test measurement",
        "date": "2026-10-03",
    }
    fields.update(changes)
    return Candidate(**fields)


def _measurements(**rows: Candidate | list[Candidate]) -> Measurements:
    """Make measurements from candidates by row name.

    Returns
    -------
    measurements : `Measurements`
        The measurements.
    """
    measurements = Measurements()
    for row, entry in rows.items():
        for candidate in entry if isinstance(entry, list) else [entry]:
            measurements.add(row, candidate)
    return measurements


def _written(result: FitResult, device: str, property_name: str, element: str) -> float | None:
    """Give the value written for one property.

    Returns
    -------
    value : `float` or `None`
        The applied value.
    """
    return result.value_of(device, property_name, element)


# ----------------------------------------------------------------------
# Driver formulas
# ----------------------------------------------------------------------


def _forward_main_sky(glow: float, gain: float, width: int, height: int, maximum: float) -> float:
    """Work out the main simulator's median sky rate from its setting.

    Returns
    -------
    rate : `float`
        ADU per second per pixel, median over the frame.
    """
    flux = driver_model.ccd_flux(1.3 * glow, maximum, 17.0, 1.0)
    return (
        flux * driver_model.exposure_gain_factor(gain) * driver_model.vignetting_median(width, height, True)
    )


def test_main_sky_glow_inverts_the_drivers_flux_formula() -> None:
    """Putting the glow back through the driver's formula gives the target."""
    for target in (0.05, 1.5, 40.0):
        glow = driver_model.glow_for_main_camera(target, 50.0, 3008, 3008, 65000.0, 17.0, 1.0)
        assert _forward_main_sky(glow, 50.0, 3008, 3008, 65000.0) == pytest.approx(target, rel=1e-9)


def test_guide_sky_glow_inverts_the_guide_drivers_formula() -> None:
    """The guide simulator's natural-logarithm zero point is inverted too."""
    target, gain = 3.0, 50.0
    glow = driver_model.glow_for_guide_camera(target, gain, 1280, 960, 65000.0, 17.0, 1.0)
    flux = driver_model.guide_flux(glow, 65000.0, 17.0, 1.0)
    median = driver_model.vignetting_median(1280, 960, False)
    assert flux * driver_model.exposure_gain_factor(gain) * median == pytest.approx(target, rel=1e-9)


def test_the_flux_scale_has_the_drivers_two_anchor_points() -> None:
    """A saturation-magnitude source gives the maximum; a limiting one, 1."""
    assert driver_model.ccd_flux(1.0, 65000.0, 17.0, 1.0) == pytest.approx(65000.0)
    assert driver_model.ccd_flux(17.0, 65000.0, 17.0, 1.0) == pytest.approx(1.0)


def test_vignetting_is_one_at_the_centre_and_median_is_below_one() -> None:
    """The median factor lies between the corner value and one."""
    median = driver_model.vignetting_median(3008, 3008, True)
    assert math.exp(-1.4 * 0.5) < median < 1.0


def test_noise_limit_matches_the_read_noise() -> None:
    """The uniform 0..N-1 noise has an RMS close to the wanted one."""
    limit = driver_model.noise_limit_for_rms(4.85)
    assert limit == 17
    assert driver_model.noise_rms(limit) == pytest.approx(4.85, abs=0.1)
    assert driver_model.noise_mean(limit) == pytest.approx(8.0)
    assert driver_model.noise_limit_for_rms(0.0) == 1


def test_guide_seeing_is_the_drivers_profile_width() -> None:
    """exp(-1.4 d^2 / s^2) has a full width of 1.41 times s."""
    seeing = driver_model.guide_fwhm_to_seeing(4.0)
    half_width = seeing * math.sqrt(math.log(2.0) / 1.4)
    assert 2.0 * half_width == pytest.approx(4.0, rel=1e-9)


def test_on_sky_amounts_are_divided_by_cos_dec() -> None:
    """The drivers add right ascension amounts without cos(dec)."""
    assert driver_model.on_sky_to_ra_coordinate(3.0, 60.0) == pytest.approx(6.0)


# ----------------------------------------------------------------------
# Conversions
# ----------------------------------------------------------------------


def test_guide_rate_uses_the_telescope_simulators_unit() -> None:
    """One unit of GUIDE_RATE is 15 arcsec/s, not the sidereal 15.04."""
    result = fit_values(
        _measurements(guide_rate=_candidate("newest real record", {"dec_rate_arcsec_per_s": 12.0})), _setup()
    )
    assert _written(result, MOUNT, "GUIDE_RATE", "GUIDE_RATE_WE") == pytest.approx(0.8)
    assert _written(result, MOUNT, "GUIDE_RATE", "GUIDE_RATE_NS") == pytest.approx(0.8)
    assert result.guide_rate_fraction == {
        "GUIDE_RATE_WE": pytest.approx(0.8),
        "GUIDE_RATE_NS": pytest.approx(0.8),
    }


def test_a_guide_rate_above_the_range_is_clamped_and_says_so() -> None:
    """A speed above 15 arcsec/s is written as 1.0 with a clamp note."""
    result = fit_values(
        _measurements(guide_rate=_candidate("newest real record", {"dec_rate_arcsec_per_s": 20.0})), _setup()
    )
    entry = next(value for value in result.values if value.element == "GUIDE_RATE_WE")
    assert entry.value == pytest.approx(1.0)
    assert entry.requested == pytest.approx(20.0 / 15.0)
    assert entry.clamp_note is not None
    assert "outside 0 to 1" in entry.clamp_note


def test_periodic_error_is_zero_to_peak_divided_by_cos_dec() -> None:
    """The amplitude is not halved, and cos(dec) is taken out."""
    candidate = _candidate("history fit", {"amplitude_arcsec": 3.0, "period_seconds": 40.0})
    result = fit_values(_measurements(periodic_error=candidate), _setup())
    assert _written(result, MAIN_CAMERA, SETTINGS, "SIM_PEMAX") == pytest.approx(6.0)
    assert _written(result, MAIN_CAMERA, SETTINGS, "SIM_PEPERIOD") == pytest.approx(40.0)
    assert _written(result, GUIDE_CAMERA, SETTINGS, "SIM_PE_MAX") == pytest.approx(6.0)
    assert _written(result, GUIDE_CAMERA, SETTINGS, "SIM_PE_PERIOD") == pytest.approx(40.0)


def test_a_period_beyond_the_main_simulators_limit_keeps_the_peak_speed() -> None:
    """A 479 s worm period is clamped to 60 s; the amplitude shrinks too."""
    candidate = _candidate("history fit", {"amplitude_arcsec": 4.0, "period_seconds": 480.0})
    result = fit_values(_measurements(periodic_error=candidate), _setup())
    assert _written(result, MAIN_CAMERA, SETTINGS, "SIM_PEPERIOD") == pytest.approx(60.0)
    assert _written(result, MAIN_CAMERA, SETTINGS, "SIM_PEMAX") == pytest.approx(8.0 * 60.0 / 480.0)
    period = next(v for v in result.values if v.element == "SIM_PEPERIOD" and v.device == MAIN_CAMERA)
    assert period.clamp_note is not None
    assert "clamped" in period.conversion
    # The guide simulator accepts 1000 s: period and amplitude stay.
    assert _written(result, GUIDE_CAMERA, SETTINGS, "SIM_PE_PERIOD") == pytest.approx(480.0)
    assert _written(result, GUIDE_CAMERA, SETTINGS, "SIM_PE_MAX") == pytest.approx(8.0)


def test_the_guide_simulators_periodic_amplitude_is_clamped_to_100() -> None:
    """A huge amplitude is written as 100 arcsec and the clamp is recorded."""
    candidate = _candidate("history fit", {"amplitude_arcsec": 80.0, "period_seconds": 400.0})
    result = fit_values(_measurements(periodic_error=candidate), _setup())
    entry = next(v for v in result.values if v.element == "SIM_PE_MAX")
    assert entry.value == pytest.approx(100.0)
    assert entry.requested == pytest.approx(160.0)
    assert entry.clamp_note


def test_random_and_drift_terms_follow_the_drivers_conventions() -> None:
    """Random is RMS times sqrt(3); drift is per second; RA / cos(dec)."""
    candidate = _candidate(
        "history fit",
        {
            "ra_random_rms_arcsec": 0.2,
            "dec_random_rms_arcsec": 0.1,
            "ra_drift_arcsec_per_min": 3.0,
            "dec_drift_arcsec_per_min": 1.2,
        },
    )
    result = fit_values(_measurements(guiding_disturbance=candidate), _setup())
    assert _written(result, GUIDE_CAMERA, SETTINGS, "SIM_RA_RAND") == pytest.approx(0.2 * math.sqrt(3) * 2)
    assert _written(result, GUIDE_CAMERA, SETTINGS, "SIM_DEC_RAND") == pytest.approx(0.1 * math.sqrt(3))
    assert _written(result, GUIDE_CAMERA, SETTINGS, "SIM_RA_DRIFT") == pytest.approx(3.0 / 60.0 * 2)
    assert _written(result, GUIDE_CAMERA, SETTINGS, "SIM_DEC_DRIFT") == pytest.approx(1.2 / 60.0)


def test_polar_error_is_in_arcminutes_and_marked_as_one_measurement() -> None:
    """34 arcmin 25 arcsec becomes 34.42 arcmin on both cameras."""
    candidate = _candidate(
        "newest real record", {"total_error_arcsec": 34 * 60 + 25.0}, note="a single measurement"
    )
    result = fit_values(_measurements(polar_error=candidate), _setup())
    for device in (MAIN_CAMERA, GUIDE_CAMERA):
        assert _written(result, device, SETTINGS, "SIM_POLAR") == pytest.approx((34 * 60 + 25.0) / 60.0)
        assert _written(result, device, SETTINGS, "SIM_POLARDRIFT") == pytest.approx(1.0)
    entry = next(v for v in result.values if v.element == "SIM_POLAR")
    assert "single measurement" in entry.note


def test_backlash_is_written_in_milliseconds_and_clamped() -> None:
    """The estimate goes to DEC_BACKLASH; one above 5 s is clamped."""
    result = fit_values(
        _measurements(dec_backlash=_candidate("history fit", {"backlash_ms": 750.0})), _setup()
    )
    assert _written(result, MOUNT, "DEC_BACKLASH", "DEC_BACKLASH") == pytest.approx(750.0)
    result = fit_values(
        _measurements(dec_backlash=_candidate("history fit", {"backlash_ms": 9000.0})), _setup()
    )
    entry = next(v for v in result.values if v.element == "DEC_BACKLASH")
    assert (entry.value, entry.requested) == (5000.0, 9000.0)
    assert entry.clamp_note


def test_the_controller_settings_come_from_the_newest_run_and_are_checked() -> None:
    """Aggressiveness, hysteresis, minimum move and exposure go to PHD2."""
    candidate = _candidate(
        "newest real record",
        {"aggressiveness": 0.45, "hysteresis": 0.25, "minimum_move": 0.277, "exposure_seconds": 2.0},
    )
    result = fit_values(_measurements(guide_controller=candidate), _setup())
    assert result.controller_settings == {
        "aggressiveness": 0.45,
        "hysteresis": 0.25,
        "minimum_move": 0.277,
        "exposure_seconds": 2.0,
    }
    too_much = _candidate("newest real record", {"aggressiveness": 3.0})
    result = fit_values(_measurements(guide_controller=too_much), _setup())
    assert result.controller_settings == {"aggressiveness": 2.0}


def test_seeing_sets_both_cameras_in_their_own_units() -> None:
    """The main camera takes the width; the guide camera its own scale."""
    candidate = _candidate("performance envelope", {"fwhm_arcsec": 4.2})
    result = fit_values(_measurements(seeing=candidate), _setup())
    assert _written(result, MAIN_CAMERA, "SIM_FOCUSING", "SIM_SEEING") == pytest.approx(4.2)
    assert _written(result, GUIDE_CAMERA, SETTINGS, "SIM_SEEING") == pytest.approx(
        driver_model.guide_fwhm_to_seeing(4.2)
    )


def test_the_guide_star_is_never_narrower_than_phd2_can_use() -> None:
    """A sky width under one guide pixel is raised to a 3.5 pixel star."""
    setup = replace(_setup(), guide_pixel_scale_arcsec=6.39)
    narrow = _candidate("performance envelope", {"fwhm_arcsec": 3.9})
    result = fit_values(_measurements(seeing=narrow), setup)
    floor = device_settings.guide_star_seeing_floor(6.39)
    assert _written(result, GUIDE_CAMERA, SETTINGS, "SIM_SEEING") == pytest.approx(floor)
    assert _written(result, MAIN_CAMERA, "SIM_FOCUSING", "SIM_SEEING") == pytest.approx(3.9)
    wide = _candidate("performance envelope", {"fwhm_arcsec": 25.0})
    result = fit_values(_measurements(seeing=wide), setup)
    assert _written(result, GUIDE_CAMERA, SETTINGS, "SIM_SEEING") == pytest.approx(
        driver_model.guide_fwhm_to_seeing(25.0)
    )


def test_noise_bias_and_maximum_come_from_the_camera_profile() -> None:
    """The pedestal is the real bias minus the noise mean; ceiling clamps."""
    result = fit_values(Measurements(), _setup())
    assert _written(result, MAIN_CAMERA, SETTINGS, "SIM_NOISE") == 17
    assert _written(result, MAIN_CAMERA, "CCD_OFFSET", "OFFSET") == pytest.approx(394.85 - 8.0)
    maximum = next(v for v in result.values if v.element == "SIM_MAXVAL" and v.device == MAIN_CAMERA)
    assert maximum.value == pytest.approx(65000.0)
    assert maximum.requested == pytest.approx(65532.0)
    assert maximum.clamp_note
    # The guide camera's profile has no noise or bias here: nothing written.
    assert _written(result, GUIDE_CAMERA, SETTINGS, "SIM_NOISE") is None
    assert _written(result, GUIDE_CAMERA, SETTINGS, "SIM_BIAS") is None


def test_sky_glow_makes_the_driver_produce_the_predicted_rate() -> None:
    """The written glow, put through the driver, gives the predicted rate."""
    candidate = _candidate("history fit", {"sky_rate_adu_per_s_per_px": 2.5})
    result = fit_values(_measurements(sky_glow=candidate), _setup())
    glow = _written(result, MAIN_CAMERA, SETTINGS, "SIM_SKYGLOW")
    maximum = _written(result, MAIN_CAMERA, SETTINGS, "SIM_MAXVAL")
    assert glow is not None
    assert _forward_main_sky(glow, 0.0, 3008, 3008, maximum or 65000.0) == pytest.approx(2.5, rel=1e-6)
    guide = next(v for v in result.values if v.row == "sky_glow" and v.device == GUIDE_CAMERA)
    assert not guide.applied
    assert "guide camera" in guide.note


def test_focus_drift_and_download_time_are_recorded_but_not_applied() -> None:
    """The simulators cannot do these; the file says so."""
    candidate = _candidate("history fit", {"steps_per_c": -12.0})
    result = fit_values(_measurements(focus_drift=candidate), _setup())
    focus = next(v for v in result.values if v.row == "focus_drift")
    download = next(v for v in result.values if v.row == "download_time")
    assert not focus.applied
    assert "Not settable" in focus.note
    assert focus.source_kind == "history fit"
    assert not download.applied
    assert "no delay option" in download.note


# ----------------------------------------------------------------------
# Fallback order
# ----------------------------------------------------------------------


def test_the_best_source_in_the_fallback_order_wins() -> None:
    """A real record beats a history fit, which beats the envelope."""
    candidates = [
        _candidate("performance envelope", {"dec_rate_arcsec_per_s": 5.0}),
        _candidate("history fit", {"dec_rate_arcsec_per_s": 6.0}),
        _candidate("newest real record", {"dec_rate_arcsec_per_s": 7.5}),
    ]
    result = fit_values(_measurements(guide_rate=candidates), _setup())
    entry = next(v for v in result.values if v.element == "GUIDE_RATE_WE")
    assert entry.source_kind == "newest real record"
    assert entry.value == pytest.approx(0.5)
    candidates.pop()
    entry = next(
        v
        for v in fit_values(_measurements(guide_rate=candidates), _setup()).values
        if v.element == "GUIDE_RATE_WE"
    )
    assert entry.source_kind == "history fit"
    assert entry.value == pytest.approx(0.4)


def test_a_row_without_data_falls_back_one_step() -> None:
    """Zero where the plan says zero; the simulator's default otherwise."""
    result = fit_values(Measurements(), _setup())
    assert _written(result, MAIN_CAMERA, SETTINGS, "SIM_PEMAX") == pytest.approx(0.0)
    assert _written(result, GUIDE_CAMERA, SETTINGS, "SIM_PE_MAX") == pytest.approx(0.0)
    assert _written(result, MAIN_CAMERA, SETTINGS, "SIM_POLAR") == pytest.approx(0.0)
    assert _written(result, GUIDE_CAMERA, SETTINGS, "SIM_POLAR") == pytest.approx(0.0)
    assert _written(result, MOUNT, "DEC_BACKLASH", "DEC_BACKLASH") == pytest.approx(0.0)
    for device, property_name, element in (
        (MOUNT, "GUIDE_RATE", "GUIDE_RATE_WE"),
        (GUIDE_CAMERA, SETTINGS, "SIM_RA_RAND"),
        (MAIN_CAMERA, "SIM_FOCUSING", "SIM_SEEING"),
        (GUIDE_CAMERA, SETTINGS, "SIM_SEEING"),
        (MAIN_CAMERA, SETTINGS, "SIM_SKYGLOW"),
    ):
        entry = next(
            v
            for v in result.values
            if (v.device, v.property_name, v.element) == (device, property_name, element)
        )
        assert not entry.applied
        assert entry.source_kind == "simulator default"
    assert result.controller_settings is None
    assert result.guide_rate_fraction is None


def test_the_zero_fallback_is_labeled_as_a_default_not_as_a_measurement() -> None:
    """A zero written for lack of data does not claim a source."""
    entry = next(v for v in fit_values(Measurements(), _setup()).values if v.element == "SIM_POLAR")
    assert entry.source_kind == "simulator default"
    assert entry.call == ""
    assert "zero" in entry.note


def test_clamping_is_recorded_for_every_range_in_the_table() -> None:
    """The clamp helper keeps a value inside and names what it did."""
    assert clamp_to_range(MAIN_CAMERA, SETTINGS, "SIM_PEPERIOD", 30.0) == (30.0, None)
    value, note = clamp_to_range(MAIN_CAMERA, SETTINGS, "SIM_PEPERIOD", 400.0)
    assert value == pytest.approx(60.0)
    assert note is not None
    assert "drivers/ccd/ccd_simulator.cpp:140" in note
    value, _ = clamp_to_range(GUIDE_CAMERA, SETTINGS, "SIM_RA_DRIFT", -5.0)
    assert value == pytest.approx(-2.0)


# ----------------------------------------------------------------------
# Results file
# ----------------------------------------------------------------------


def _full_result() -> FitResult:
    """Make a fit with a value for most rows.

    Returns
    -------
    result : `FitResult`
        The fit.
    """
    measurements = _measurements(
        guide_rate=_candidate("newest real record", {"dec_rate_arcsec_per_s": 7.5}),
        periodic_error=_candidate("history fit", {"amplitude_arcsec": 3.0, "period_seconds": 40.0}),
        seeing=_candidate("performance envelope", {"fwhm_arcsec": 4.2}),
        sky_glow=_candidate("history fit", {"sky_rate_adu_per_s_per_px": 2.5}),
        guide_controller=_candidate("newest real record", {"aggressiveness": 0.45, "hysteresis": 0.25}),
    )
    return fit_values(measurements, _setup())


def test_the_results_file_holds_what_the_plan_requires(tmp_path: Path) -> None:
    """Each value has its call, nights, source, date, property and clamp."""
    path = tmp_path / RESULTS_FILE_NAME
    fingerprint = fingerprint_library_copy(tmp_path)
    write_results(path, _full_result(), fingerprint, {"note": "inputs"})
    document = read_results(path)
    assert document is not None
    assert document["library_fingerprint"] == fingerprint
    assert document["inputs"] == {"note": "inputs"}
    entry = next(v for v in document["values"] if v["element"] == "SIM_PEMAX" and v["device"] == MAIN_CAMERA)
    assert entry["call"] == "wayfinder.review.test_call"
    assert entry["parameters"] == {"a": 1}
    assert entry["nights"] == ["2026-10-03"]
    assert entry["source_kind"] == "history fit"
    assert entry["date"] == "2026-10-03"
    assert (entry["device"], entry["property_name"], entry["element"]) == (MAIN_CAMERA, SETTINGS, "SIM_PEMAX")
    assert entry["value"] == pytest.approx(6.0)
    assert entry["requested"] == pytest.approx(6.0)
    assert entry["clamp_note"] is None
    assert not list(tmp_path.glob(".*.writing"))


def test_the_exposure_driver_reads_the_same_values(tmp_path: Path) -> None:
    """The properties to set equal the values in the file."""
    path = tmp_path / RESULTS_FILE_NAME
    result = _full_result()
    write_results(path, result, fingerprint_library_copy(tmp_path))
    settings = fitted_device_settings(path)
    by_property = {(s.device, s.property_name): dict(s.elements) for s in settings}
    document = read_results(path)
    assert document is not None
    for entry in document["values"]:
        if entry["applied"] and entry["device"] != "PHD2 profile":
            assert by_property[entry["device"], entry["property_name"]][entry["element"]] == pytest.approx(
                entry["value"]
            )
    assert not any(device == "PHD2 profile" for device, _ in by_property)
    assert fitted_controller_settings(path) == {"aggressiveness": 0.45, "hysteresis": 0.25}
    assert fitted_guide_rate_fraction(path) == {"GUIDE_RATE_WE": 0.5, "GUIDE_RATE_NS": 0.5}
    glow = next(s for s in settings if s.device == MAIN_CAMERA and ("SIM_SKYGLOW" in dict(s.elements)))
    assert glow.read_back_decimals == 1


def test_a_missing_or_foreign_results_file_gives_nothing(tmp_path: Path) -> None:
    """The simulators then keep their own values."""
    assert fitted_device_settings(tmp_path / "none.json") == []
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    assert read_results(other) is None
    assert fitted_controller_settings(other) is None


def test_a_new_night_changes_the_fingerprint(tmp_path: Path) -> None:
    """A changed database changes the fingerprint, so values are refitted."""
    library = tmp_path / "library"
    library.mkdir()
    database = library / "astrometrics.db"
    database.write_bytes(b"a" * 100)
    before = fingerprint_library_copy(tmp_path)
    assert before["files"][0]["path"] == "library/astrometrics.db"
    assert before["files"][0]["size_bytes"] == 100
    assert fingerprint_library_copy(tmp_path) == before
    database.write_bytes(b"a" * 120)
    os.utime(database, (time.time() + 5, time.time() + 5))
    assert fingerprint_library_copy(tmp_path)["digest"] != before["digest"]


def test_merge_replaces_the_pedestal_and_adds_the_rest() -> None:
    """The fitted pedestal replaces the geometry step's; header value kept."""
    base = [
        DeviceSetting(MAIN_CAMERA, SETTINGS, (("SIM_XRES", 3008.0), ("SIM_YRES", 3008.0))),
        DeviceSetting(MAIN_CAMERA, "CCD_OFFSET", (("OFFSET", 394.85),), header_value=10.0),
    ]
    fitted = [
        DeviceSetting(MAIN_CAMERA, "CCD_OFFSET", (("OFFSET", 386.85),), read_back_decimals=0),
        DeviceSetting(MAIN_CAMERA, SETTINGS, (("SIM_NOISE", 17.0),), read_back_decimals=0),
    ]
    merged = merge_device_settings(base, fitted)
    assert len(merged) == 3
    assert merged[1].elements == (("OFFSET", 386.85),)
    assert merged[1].header_value == pytest.approx(10.0)
    assert merged[2].elements == (("SIM_NOISE", 17.0),)
    assert merged[0] == base[0]


def test_read_back_is_compared_at_the_displayed_precision() -> None:
    """The server echoes 19.57 as 19.6 when the format has one decimal."""
    assert matches(19.57, "19.6", 1)
    assert not matches(19.57, "19.8", 1)
    assert not matches(19.57, "19.6")
    assert matches(17.0, "17", 0)
    assert matches(386.85, "387", 0)


# ----------------------------------------------------------------------
# Measurements
# ----------------------------------------------------------------------


class _Reply(SimpleNamespace):
    """A review reply with the fields the fit reads."""


def _fake_wayfinder(**overrides: Any) -> SimpleNamespace:
    """Build a stand-in for the Wayfinder whose calls return fixed answers.

    Returns
    -------
    wayfinder : `~types.SimpleNamespace`
        With ``control`` and ``review`` as the fit uses them.
    """
    calibration = SimpleNamespace(
        id="cal-1",
        dec_rate_arcsec_per_sec=7.1,
        source_note="Measured by PHD2",
        calibrated_at=SimpleNamespace(timestamp=lambda: 1_790_000_000.0),
    )
    runs = [
        {
            "session_id": "2026-10-03",
            "started_at": 1_790_000_000.0,
            "written_by_ekos": False,
            "dec_rate_arcsec_per_second": 7.3,
            "ra_aggressiveness": 0.6,
            "ra_hysteresis": 0.1,
            "ra_minimum_move_px": 0.2,
            "guide_exposure_seconds": 2.0,
        }
    ]
    periodic = SimpleNamespace(amplitude_arcsec=3.0, period_seconds=479.0, runs_used=4)
    axis = SimpleNamespace(drift_median_arcsec_per_minute=0.5, random_step_median_arcsec=0.3)
    summary = SimpleNamespace(
        periodic_error=periodic, ra=axis, dec=axis, nights_used=["2026-10-02", "2026-10-03"]
    )
    answers: dict[str, Any] = {
        "calibrations": [calibration],
        "query": _Reply(runs=runs, truncated=False),
        "mount_error": SimpleNamespace(summary=summary),
        "spectrum": SimpleNamespace(dec_backlash_estimate_ms=None, message="too few samples"),
        "polar": SimpleNamespace(total_error_arcsec=2065.0, timestamp=1_790_000_000.0),
        "envelope": SimpleNamespace(limit_inputs={"guiding_rms_limit": {"fwhm_arcsec": 3.9}}),
        "sky": SimpleNamespace(
            sky_rate_adu_per_s_per_px=1.8, relative_error=0.2, nights_used=5, reliable=True, message=""
        ),
        "focus": SimpleNamespace(
            coefficient_steps_per_c=-14.0, nights_used=["2026-10-03"], message="", pairs_used=9
        ),
    }
    answers.update(overrides)
    calls: list[str] = []

    def record(name: str, answer: Any) -> Any:
        """Answer a call and remember it.

        Returns
        -------
        answer : `object`
            The fixed answer.
        """
        calls.append(name)
        return answer

    guiding = SimpleNamespace(
        query_calibrations=lambda: record("query_calibrations", answers["calibrations"])
    )
    mount = SimpleNamespace(get_polar_alignment=lambda: record("get_polar_alignment", answers["polar"]))
    review = SimpleNamespace(
        query=lambda **kwargs: record(f"query:{kwargs}", answers["query"]),
        measure_unguided_mount_error=lambda: record("measure_unguided_mount_error", answers["mount_error"]),
        measure_guiding_spectrum=lambda: record("measure_guiding_spectrum", answers["spectrum"]),
        get_performance_envelope=lambda: record("get_performance_envelope", answers["envelope"]),
        get_sky_rate_prediction=lambda *a, **k: record("get_sky_rate_prediction", answers["sky"]),
        measure_focus_temperature_coefficient=lambda: record("measure_focus", answers["focus"]),
    )
    return SimpleNamespace(control=SimpleNamespace(guiding=guiding, mount=mount), review=review, calls=calls)


def test_collect_reads_every_row_and_names_the_public_calls() -> None:
    """Each row gets a candidate from the call the plan names."""
    measurements = collect_measurements(_fake_wayfinder(), 250.4, 36.46, 1_790_000_000.0)
    rows = measurements.candidates
    assert rows["guide_rate"][0].kind == "newest real record"
    assert {c.kind for c in rows["guide_rate"]} == {"newest real record", "history fit"}
    assert rows["periodic_error"][0].call == "wayfinder.review.measure_unguided_mount_error"
    assert rows["periodic_error"][0].nights == ["2026-10-02", "2026-10-03"]
    assert rows["polar_error"][0].note.startswith("A single polar alignment measurement")
    assert rows["seeing"][0].kind == "performance envelope"
    assert rows["sky_glow"][0].parameters["dec_deg"] == pytest.approx(36.46)
    assert rows["guide_controller"][0].values["aggressiveness"] == pytest.approx(0.6)
    assert rows["focus_drift"][0].values["steps_per_c"] == pytest.approx(-14.0)
    disturbance = rows["guiding_disturbance"][0].values
    assert disturbance["ra_drift_arcsec_per_min"] == pytest.approx(0.5)
    assert disturbance["ra_random_rms_arcsec"] == pytest.approx(0.3 / 2**0.5)
    assert "dec_backlash" not in rows
    assert any("Dec backlash" in note for note in measurements.notes)


def test_a_simulator_calibration_is_not_a_real_record() -> None:
    """A simulator calibration is not used as the real guide speed."""
    simulated = SimpleNamespace(
        id="simulator-x",
        dec_rate_arcsec_per_sec=7.52,
        source_note="Simulator value, not the real camera's",
        calibrated_at=SimpleNamespace(timestamp=lambda: 1.0),
    )
    measurements = collect_measurements(_fake_wayfinder(calibrations=[simulated]), 0.0, 0.0)
    assert [c.kind for c in measurements.candidates["guide_rate"]] == ["history fit"]


def test_a_truncated_reply_is_refused_and_the_row_falls_back() -> None:
    """A cut-short run list is not used; two rows lose it."""
    truncated = _Reply(runs=[{"dec_rate_arcsec_per_second": 9.0}], truncated=True)
    wayfinder = _fake_wayfinder(query=truncated, calibrations=[])
    measurements = collect_measurements(wayfinder, 0.0, 0.0)
    assert "guide_rate" not in measurements.candidates
    assert "guide_controller" not in measurements.candidates
    assert any("truncated" in note for note in measurements.notes)
    assert issubclass(TruncatedReplyError, RuntimeError)


def test_a_cut_reply_is_asked_again_for_fewer_runs() -> None:
    """A run list too large to send is asked for again with a smaller limit."""
    wayfinder = _fake_wayfinder(calibrations=[])
    full = _Reply(runs=[{"dec_rate_arcsec_per_second": 9.0}], truncated=True)
    small = _Reply(
        runs=[{"dec_rate_arcsec_per_second": 7.3, "session_id": "n", "started_at": 1.0}], truncated=False
    )
    limits: list[int] = []

    def query(**kwargs: Any) -> _Reply:
        """Cut the reply unless few runs are asked for.

        Returns
        -------
        reply : `_Reply`
            The cut reply for the first limit, a whole one after.
        """
        limits.append(kwargs["limit"])
        return full if kwargs["limit"] == GUIDING_RUN_LIMITS[0] else small

    wayfinder.review.query = query
    measurements = collect_measurements(wayfinder, 0.0, 0.0)
    assert limits == list(GUIDING_RUN_LIMITS[:2])
    candidate = measurements.candidates["guide_rate"][0]
    assert candidate.values["dec_rate_arcsec_per_s"] == pytest.approx(7.3)
    assert candidate.parameters["limit"] == GUIDING_RUN_LIMITS[1]


def test_the_meridian_is_the_local_sidereal_time() -> None:
    """At 2000 January 1, 12:00 UT, Greenwich sidereal time is 18.697 h."""
    assert meridian_ra_degrees(J2000_EPOCH_SECONDS, 0.0) == pytest.approx(18.697374558 * 15.0)
    assert meridian_ra_degrees(J2000_EPOCH_SECONDS, -110.74) == pytest.approx(
        (18.697374558 * 15.0 - 110.74) % 360.0
    )


def test_a_target_below_the_horizon_is_moved_to_the_meridian() -> None:
    """The sky rate is asked again on the meridian when the target is down."""
    down = SimpleNamespace(
        sky_rate_adu_per_s_per_px=None,
        setting_key="ASI533MM|L",
        message="The target is below the horizon at that time.",
    )
    up = SimpleNamespace(
        sky_rate_adu_per_s_per_px=1.8,
        relative_error=0.2,
        nights_used=5,
        reliable=True,
        message="",
        setting_key="k",
    )
    asked: list[float] = []

    def predict(epoch_seconds: float, ra_deg: float, dec_deg: float) -> SimpleNamespace:
        """Say the target is down unless it is on the meridian.

        Returns
        -------
        prediction : `~types.SimpleNamespace`
            The fixed answer.
        """
        asked.append(ra_deg)
        return up if len(asked) > 1 else down

    wayfinder = _fake_wayfinder()
    wayfinder.review.get_sky_rate_prediction = predict
    measurements = collect_measurements(wayfinder, 250.4, 36.46, J2000_EPOCH_SECONDS, -110.74)
    candidate = measurements.candidates["sky_glow"][0]
    assert asked[1] == pytest.approx(meridian_ra_degrees(J2000_EPOCH_SECONDS, -110.74))
    assert candidate.parameters["ra_deg"] == pytest.approx(asked[1])
    assert "meridian" in candidate.note
    without_site = collect_measurements(_fake_wayfinder(sky=down), 250.4, 36.46, J2000_EPOCH_SECONDS)
    assert "sky_glow" not in without_site.candidates


def test_a_failing_call_is_a_note_and_other_rows_still_run() -> None:
    """One call that raises does not stop the others."""
    wayfinder = _fake_wayfinder()

    def fail() -> None:
        """Raise like a library call that found nothing.

        Raises
        ------
        ValueError
            Always.
        """
        raise ValueError("nothing recorded")

    wayfinder.review.measure_unguided_mount_error = fail
    measurements = collect_measurements(wayfinder, 0.0, 0.0)
    assert "periodic_error" not in measurements.candidates
    assert "seeing" in measurements.candidates
    assert any("nothing recorded" in note for note in measurements.notes)


def test_collect_registers_no_job() -> None:
    """The review query is asked not to record a job in the library."""
    wayfinder = _fake_wayfinder()
    collect_measurements(wayfinder, 0.0, 0.0)
    assert any("'register_job': False" in call for call in wayfinder.calls if call.startswith("query"))


# ----------------------------------------------------------------------
# World setup
# ----------------------------------------------------------------------


def test_world_setup_reads_the_camera_profiles() -> None:
    """Frame size, gain, bias, noise and clip ceiling come from the profile."""
    settings = {
        "Observatory.Camera": {"default_primary_camera": "Cam", "default_secondary_camera": "Guide"},
        "Observatory.Camera.Cam": {
            "sensor_width_px": "3008",
            "sensor_height_px": "3008",
            "clip_ceiling_adu": {"value": 65532.0, "kind": "measured", "source": "x"},
            "sensor_noise": [
                {
                    "gain_setting": "0",
                    "offset": "10",
                    "binning": "1x1",
                    "gain_e_per_adu": {"value": 0.783},
                    "read_noise_e": {"value": 3.8},
                }
            ],
            "bias_levels": [
                {
                    "gain_setting": "0",
                    "offset": "10",
                    "binning": "1x1",
                    "bias_level_adu": {"value": 394.85},
                }
            ],
        },
        "Observatory.Camera.Guide": {"sensor_width_px": "1280", "sensor_height_px": "960"},
    }
    setup = world_setup_from_settings(settings, 36.46)
    assert (setup.main.width_px, setup.main.height_px, setup.main.gain) == (3008, 3008, 0.0)
    assert setup.main.bias_level_adu == pytest.approx(394.85)
    assert setup.main.read_noise_adu == pytest.approx(3.8 / 0.783)
    assert setup.main.clip_ceiling_adu == pytest.approx(65532.0)
    assert setup.guide.gain == driver_model.GUIDE_DEFAULT_GAIN
    assert setup.guide.read_noise_adu is None
