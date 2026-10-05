"""Purpose: Unit tests for the ObservatoryControl astrometrics.

Description: Verifies equipment activation/resolution round-trips,
correction methods delegate with resolved calibration (raising when
none exists for the active pairing), the safety monitor's hysteresis
state is carried across calls made through the same astrometrics instance,
safe-state and capability-promotion delegation work end to end, and
that constructing the high-level interface and calling its non-hardware methods
never imports the INDI driver layer.
"""

import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from astrometricslib import ConflictError, PermissionDeniedError
from wayfindinglib.api.control_registry import ObservatoryControl
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.equipment_and_site.enclosure import Enclosure, EnclosureType
from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.models.policy.delegation import DelegationState, ObservatoryCapability
from wayfindinglib.models.policy.safety import SafetyRule, SafetyRuleSet, SafetyVerdict
from wayfindinglib.tasks.control_tasks.safe_state import SafeStateSteps

_NOW = datetime(2026, 8, 5, 4, 0, 0, tzinfo=UTC)


@pytest.fixture
def utc_host_timezone() -> Iterator[None]:
    """Pin the process's local time zone to UTC for the test, then restore it.

    PHD2/Ekos guide logs carry no time zone, so
    `wayfindinglib.drivers.phd2.guide_log_parser._parse_timestamp` reads
    them as local time on whatever machine is doing the parsing -- correct
    for a real observatory, but it means a test built around the guide
    log's and the analyze log's (which *does* carry an explicit zone)
    timestamps agreeing with each other would otherwise only pass on a
    machine whose local zone happens to match the fixture data's author,
    not in CI or for a contributor elsewhere.
    """
    original_tz = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    try:
        yield
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()


@pytest.fixture
def app_config(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Build a real, isolated AppConfiguration and matching DiskButler.

    Returns
    -------
    config : `AppConfiguration`
        A fresh, isolated configuration instance.
    """
    from astrometricslib import AppConfiguration

    config_path = tmp_path / "astrometrics.config.toml"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return config


@pytest.fixture
def control(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Build an ObservatoryControl backed by the isolated app_config/butler.

    Returns
    -------
    control : `ObservatoryControl`
        The constructed astrometrics.
    """
    butler = DiskButler(app_config=app_config)
    return ObservatoryControl(config=app_config, butler=butler)


def _configure_active_rig(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    app_config.update_config({
        "Observatory.Telescope": {"models": "Rig A", "active_telescope": "Rig A"},
        "Observatory.Telescope.Rig A": {"focal_length_mm": "450.0", "focal_ratio": "6.0"},
        "Observatory.Camera": {"models": "CamA", "default_primary_camera": "CamA"},
        "Observatory.Camera.CamA": {
            "pixel_size_μm": "3.76",
            "sensor_width_px": "6248",
            "sensor_height_px": "4176",
        },
    })


def test_set_active_telescope_and_camera_round_trip(control, app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify equipment activation records and resolves via active_*()."""
    _configure_active_rig(app_config)
    assert control.set_active_telescope("Rig A") is True
    assert control.set_active_camera("CamA") is True
    assert control.active_telescope().id == "Rig A"
    assert control.active_camera().id == "CamA"


def test_guider_plate_scale_falls_back_to_main_telescope_with_no_active_rig(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify guider_plate_scale_arcsec_per_px() is None with no active rig."""
    assert control.guider_plate_scale_arcsec_per_px() is None


def test_guider_plate_scale_uses_main_telescope_with_no_guide_scope_configured(control, app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the guide-aware plate scale matches the main one, by default."""
    _configure_active_rig(app_config)
    control.set_active_telescope("Rig A")
    control.set_active_camera("CamA")

    assert control.active_guide_scope() is None
    plate_scale = control.guider_plate_scale_arcsec_per_px()
    expected = 206.265 * 3.76 / 450.0
    assert plate_scale == pytest.approx(expected, abs=1e-4)


def test_guider_plate_scale_uses_active_guide_scope_focal_length(control, app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an active guide scope's focal length replaces the telescope's."""
    _configure_active_rig(app_config)
    app_config.update_config({
        "Observatory.GuideScope": {"models": "Orion 50mm", "active_guide_scope": "Orion 50mm"},
        "Observatory.GuideScope.Orion 50mm": {"focal_length_mm": "162.0"},
    })
    control.set_active_telescope("Rig A")
    control.set_active_camera("CamA")

    assert control.active_guide_scope().id == "Orion 50mm"
    plate_scale = control.guider_plate_scale_arcsec_per_px()
    main_plate_scale = 206.265 * 3.76 / 450.0
    guide_plate_scale = 206.265 * 3.76 / 162.0
    assert plate_scale == pytest.approx(guide_plate_scale, abs=1e-4)
    assert plate_scale != pytest.approx(main_plate_scale, abs=1e-4)


def test_guider_plate_scale_uses_the_active_guide_camera_pixel_size(control, app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a guide camera's pixel size is used, not the main one's."""
    _configure_active_rig(app_config)
    app_config.update_config({
        "Observatory.GuideScope": {"models": "Orion 50mm", "active_guide_scope": "Orion 50mm"},
        "Observatory.GuideScope.Orion 50mm": {"focal_length_mm": "162.0"},
        "Observatory.Camera": {"models": "CamA, GuideCam", "default_guide_camera": "GuideCam"},
        "Observatory.Camera.GuideCam": {
            "pixel_size_μm": "3.75",
            "sensor_width_px": "1280",
            "sensor_height_px": "960",
        },
    })
    control.set_active_telescope("Rig A")
    control.set_active_camera("CamA")

    assert control.active_guide_camera().id == "GuideCam"
    assert control.guider_plate_scale_arcsec_per_px() == pytest.approx(206.265 * 3.75 / 162.0, abs=1e-4)


_EKOS_GUIDE_LOG = (
    # Guide logs carry no time zone (see utc_host_timezone's docstring), so
    # these clock times are written as the true-UTC equivalent of the
    # analyze log's "2026-09-23 20:31:48.442 MDT" -- i.e. already shifted
    # +6h and a day later -- so they land inside its overlap window once
    # the test pins the host's local zone to UTC.
    "KStars version 3.8.3. PHD2 log version 2.5. Log enabled at 2026-09-24 02:43:05\n\n"
    "Guiding Begins at 2026-09-24 02:50:00\n"
    "Pixel scale = 6.39 arc-sec/px, Binning = 1, Focal length = 121.05 mm\n"
    "Frame,Time,mount,dx,dy,RARawDistance,DECRawDistance,RAGuideDistance,DECGuideDistance,"
    "RADuration,RADirection,DECDuration,DECDirection,XStep,YStep,StarMass,SNR,ErrorCode\n"
    '1,2.0,"Mount",0.1,0.1,0.100,0.200,0.1,0.1,10,E,10,N,,,250000,40.0,0\n'
    "Guiding Ends at 2026-09-24 02:55:00\n"
)
_EKOS_ANALYZE_LOG = (
    "#KStars version 3.8.3. Analyze log version 1.0.\n\n"
    "AnalyzeStartTime,2026-09-23 20:31:48.442,MDT\n"
    "CaptureComplete,1500.0,30.000,Luminance,1.262,/home/stellarmate/M_27_001.fits,238,460,0.452\n"
)


def test_ingest_ekos_session_logs_stores_samples_and_a_retrievable_session(  # ruff: ignore[missing-return-type-undocumented-public-function]
    control,  # ruff: ignore[missing-type-function-argument]
    app_config,  # ruff: ignore[missing-type-function-argument]
    tmp_path,  # ruff: ignore[missing-type-function-argument]
    utc_host_timezone,  # ruff: ignore[missing-type-function-argument]
):
    """Verify the whole chain with a real Butler and a real log database."""
    (tmp_path / "science_library").mkdir()
    app_config.update_config({"Image Library": {"path": str(tmp_path / "science_library")}})
    logs_directory = tmp_path / "ekos_logs"
    logs_directory.mkdir()
    (logs_directory / "guide_log-2026-09-23T20-43-05.txt").write_text(_EKOS_GUIDE_LOG, encoding="utf-8")
    (logs_directory / "ekos-2026-09-23T20-31-48.analyze").write_text(_EKOS_ANALYZE_LOG, encoding="utf-8")

    summary = control.ingest_ekos_session_logs(destination_dir=str(logs_directory), download=False)

    assert summary["guide_samples_stored"] == 1
    assert summary["session_contexts_stored"] == 1
    assert summary["nights"] == ["2026-09-23"]
    (listed,) = control.list_ekos_session_summaries()
    assert listed["id"] == "2026-09-23T20-31-48"
    assert listed["captures"] == 1
    assert "guide_focal_mm=121" in listed["equipmentFingerprint"]
    context = control.get_ekos_session_context("2026-09-23T20-31-48")
    assert context.equipment.guide_pixel_scale_arcsec_per_px == pytest.approx(6.39)
    (run,) = control.list_guiding_runs()
    assert run.session_id == "2026-09-23"
    assert control.list_guiding_runs("1999-01-01") == []


def test_ingest_ekos_session_logs_twice_does_not_duplicate_anything(  # ruff: ignore[missing-return-type-undocumented-public-function]
    control,  # ruff: ignore[missing-type-function-argument]
    app_config,  # ruff: ignore[missing-type-function-argument]
    tmp_path,  # ruff: ignore[missing-type-function-argument]
    utc_host_timezone,  # ruff: ignore[missing-type-function-argument]
):
    """Verify a repeat run leaves one sample and one session record."""
    (tmp_path / "science_library").mkdir()
    app_config.update_config({"Image Library": {"path": str(tmp_path / "science_library")}})
    logs_directory = tmp_path / "ekos_logs"
    logs_directory.mkdir()
    (logs_directory / "guide_log-2026-09-23T20-43-05.txt").write_text(_EKOS_GUIDE_LOG, encoding="utf-8")
    (logs_directory / "ekos-2026-09-23T20-31-48.analyze").write_text(_EKOS_ANALYZE_LOG, encoding="utf-8")

    control.ingest_ekos_session_logs(destination_dir=str(logs_directory), download=False)
    control.ingest_ekos_session_logs(destination_dir=str(logs_directory), download=False)

    assert len(control.list_ekos_session_summaries()) == 1
    assert len(control._logger_interface.get_guiding_logs()) == 1


def test_get_ekos_session_context_is_none_for_an_unknown_session(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify asking for a session that was never recorded gives None."""
    assert control.get_ekos_session_context("1999-01-01T00-00-00") is None


def _configure_two_rigs(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Configure two complete equipment setups, a short and a long one."""
    app_config.update_config({
        "Observatory.Telescope": {"models": "Short, Long", "active_telescope": "Short"},
        "Observatory.Telescope.Short": {"focal_length_mm": "400.0", "focal_ratio": "5.0"},
        "Observatory.Telescope.Long": {"focal_length_mm": "1000.0", "focal_ratio": "8.0"},
        "Observatory.GuideScope": {"models": "Guide 120, Guide 240", "active_guide_scope": "Guide 120"},
        "Observatory.GuideScope.Guide 120": {"focal_length_mm": "120.0", "aperture_mm": "30"},
        "Observatory.GuideScope.Guide 240": {"focal_length_mm": "240.0", "aperture_mm": "50"},
        "Observatory.Camera": {
            "models": "Main A, Main B, Guider",
            "default_primary_camera": "Main A",
            "default_guide_camera": "Guider",
        },
        "Observatory.Camera.Main A": {
            "pixel_size_μm": "3.76",
            "sensor_width_px": "3008",
            "sensor_height_px": "3008",
        },
        "Observatory.Camera.Main B": {
            "pixel_size_μm": "5.94",
            "sensor_width_px": "6248",
            "sensor_height_px": "4176",
        },
        "Observatory.Camera.Guider": {
            "pixel_size_μm": "3.75",
            "sensor_width_px": "1280",
            "sensor_height_px": "960",
        },
    })


def test_the_performance_envelope_is_none_without_active_equipment(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify no telescope and camera means no envelope, not a made-up one."""
    assert control.get_performance_envelope() is None


def test_changing_the_active_equipment_changes_every_limit_with_no_other_step(control, app_config, tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the limits follow the equipment automatically.

    The requirement this tests: nothing about the limits is stored, so
    swapping the telescope, camera or guide scope is all it takes for
    every dependent limit to change.
    """
    (tmp_path / "science_library").mkdir()
    app_config.update_config({"Image Library": {"path": str(tmp_path / "science_library")}})
    _configure_two_rigs(app_config)

    before = control.get_performance_envelope()
    app_config.update_config({
        "Observatory.Telescope": {"active_telescope": "Long"},
        "Observatory.Camera": {"default_primary_camera": "Main B"},
        "Observatory.GuideScope": {"active_guide_scope": "Guide 240"},
    })
    after = control.get_performance_envelope()

    assert before.value("imaging_plate_scale") == pytest.approx(206.265 * 3.76 / 400.0)
    assert after.value("imaging_plate_scale") == pytest.approx(206.265 * 5.94 / 1000.0)
    assert before.value("guide_plate_scale") == pytest.approx(206.265 * 3.75 / 120.0)
    assert after.value("guide_plate_scale") == pytest.approx(206.265 * 3.75 / 240.0)
    assert before.equipment_fingerprint != after.equipment_fingerprint
    assert "telescope=short" in before.equipment_fingerprint
    assert "telescope=long" in after.equipment_fingerprint


def test_the_envelope_is_honest_about_data_a_new_setup_does_not_have_yet(control, app_config, tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a setup with no frames or sessions gets no measured limits."""
    (tmp_path / "science_library").mkdir()
    app_config.update_config({"Image Library": {"path": str(tmp_path / "science_library")}})
    _configure_two_rigs(app_config)

    envelope = control.get_performance_envelope()

    for name in ("guiding_rms_limit", "trailing_limit", "guide_snr_low_limit", "guiding_rms_high_limit"):
        assert envelope.value(name) is None
        assert envelope.thresholds[name].status.value == "insufficient_data"


def test_compute_pointing_correction_delegates_with_astrometrics_config(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify compute_pointing_correction forwards to the task function."""
    correction = control.compute_pointing_correction("frame-1", 180.0, 0.0, 180.0, 0.0, iteration=1)
    assert correction.converged is True


def test_compute_guiding_correction_raises_without_calibration(control, app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify guiding correction raises with no calibration for the pairing."""
    _configure_active_rig(app_config)
    control.set_active_telescope("Rig A")
    control.set_active_camera("CamA")

    with pytest.raises(ValueError, match="No GuiderCalibration"):
        control.compute_guiding_correction("frame-1", 5.0, 0.0)


def test_compute_guiding_correction_succeeds_with_saved_calibration(control, app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify guiding correction resolves a saved calibration."""
    _configure_active_rig(app_config)
    control.set_active_telescope("Rig A")
    control.set_active_camera("CamA")

    calibration = GuiderCalibration(
        id="cal-1",
        camera_id="CamA",
        telescope_id="Rig A",
        arcsec_per_pixel=2.0,
        camera_angle_deg=0.0,
        ra_rate_arcsec_per_sec=10.0,
        dec_rate_arcsec_per_sec=10.0,
    )
    control.save_guider_calibration(calibration)

    correction = control.compute_guiding_correction("frame-1", 5.0, 0.0)
    assert correction.pulse_ra_ms > 0


def test_compute_guiding_correction_auto_resolves_persisted_mount_model(control, app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify guiding correction feeds forward the saved spectrum analysis.

    Unlike `pointing_model` (session-scoped, never auto-resolved), the
    active telescope's `GuidingSpectrumAnalysis` is standing state this
    object already holds, so `compute_guiding_correction` must resolve
    it automatically without the caller passing it explicitly (M7b).
    """
    from wayfindinglib.models.session.telemetry import GuidingSpectrumAnalysis

    _configure_active_rig(app_config)
    control.set_active_telescope("Rig A")
    control.set_active_camera("CamA")

    calibration = GuiderCalibration(
        id="cal-1",
        camera_id="CamA",
        telescope_id="Rig A",
        arcsec_per_pixel=2.0,
        camera_angle_deg=0.0,
        ra_rate_arcsec_per_sec=10.0,
        dec_rate_arcsec_per_sec=10.0,
    )
    control.save_guider_calibration(calibration)
    control.save_guiding_spectrum_analysis(
        GuidingSpectrumAnalysis(
            sample_count=100,
            duration_seconds=3600.0,
            dominant_period_seconds=480.0,
            periodic_error_peak_to_peak_arcsec=10.0,
        )
    )

    # At phase=0.25, the modeled sinusoid peaks -- a feedforward pulse
    # is issued even with zero measured drift, well within the deadband.
    correction = control.compute_guiding_correction("frame-2", 0.0, 0.0, elapsed_guiding_seconds=480.0 * 0.25)
    assert correction.pulse_ra_ms != 0
    assert correction.suppressed_by_deadband is False


def test_compute_pointing_correction_feeds_forward_a_passed_in_model(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify pointing correction feeds forward a caller-supplied model.

    Unlike guiding's mount model, `pointing_model` is never auto-resolved
    -- it is session-scoped state the caller must supply explicitly.
    """
    from wayfindinglib.models.session.telemetry import MountPointingModel

    model = MountPointingModel(
        sample_count=10, raw_rms_arcsec=8.0, residual_rms_arcsec=0.0, id_arcsec=8.0, confidence="high"
    )

    without_model = control.compute_pointing_correction(
        "frame-3", 180.0, 0.0, 180.0, -8.0 / 3600.0, iteration=1
    )
    with_model = control.compute_pointing_correction(
        "frame-3", 180.0, 0.0, 180.0, -8.0 / 3600.0, iteration=1, pointing_model=model
    )

    assert without_model.model_predicted_error_arcsec is None
    assert with_model.unexplained_residual_arcsec == pytest.approx(0.0, abs=1e-6)


def test_save_and_active_guiding_spectrum_analysis_round_trip(control, app_config, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the standing spectrum analysis persists keyed by telescope."""
    from wayfindinglib.models.session.telemetry import GuidingSpectrumAnalysis

    _configure_active_rig(app_config)
    control.set_active_telescope("Rig A")

    assert control.active_guiding_spectrum_analysis() is None

    analysis = GuidingSpectrumAnalysis(sample_count=10, duration_seconds=60.0)
    control.save_guiding_spectrum_analysis(analysis)

    persisted = control.active_guiding_spectrum_analysis()
    assert persisted is not None
    assert persisted.id == "Rig A"
    assert persisted.telescope_id == "Rig A"
    assert persisted.sample_count == 10


def test_ingest_guiding_log_file_delegates_to_the_task_module(mocker, control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify ingest_guiding_log_file forwards to guiding_log_ingestion."""
    from wayfindinglib.tasks.control_tasks import guiding_log_ingestion

    fake_logger_interface = mocker.Mock()
    control._logger_interface = fake_logger_interface
    mocker.patch.object(guiding_log_ingestion, "ingest_guide_log_file", return_value="analysis")

    result = control.ingest_guiding_log_file("/tmp/log.txt", target_name="M 81")

    assert result == "analysis"
    guiding_log_ingestion.ingest_guide_log_file.assert_called_once_with(
        control, fake_logger_interface, "/tmp/log.txt", "M 81"
    )


def test_fetch_and_ingest_new_guide_logs_delegates_to_the_task_module(mocker, control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify fetch_and_ingest_new_guide_logs forwards to the task module."""
    from wayfindinglib.tasks.control_tasks import guiding_log_ingestion

    fake_logger_interface = mocker.Mock()
    control._logger_interface = fake_logger_interface
    mocker.patch.object(guiding_log_ingestion, "fetch_and_ingest_new_guide_logs", return_value="analysis")

    result = control.fetch_and_ingest_new_guide_logs("/tmp/guiding", target_name="M 81")

    assert result == "analysis"
    guiding_log_ingestion.fetch_and_ingest_new_guide_logs.assert_called_once_with(
        control, fake_logger_interface, "/tmp/guiding", "M 81"
    )


def test_get_pointing_model_delegates_to_the_task_module(mocker, control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify get_pointing_model forwards to pointing_log_ingestion."""
    from wayfindinglib.tasks.control_tasks import pointing_log_ingestion

    fake_logger_interface = mocker.Mock()
    control._logger_interface = fake_logger_interface
    mocker.patch.object(pointing_log_ingestion, "compute_pointing_model", return_value="model")

    result = control.get_pointing_model(session_id="s1")

    assert result == "model"
    pointing_log_ingestion.compute_pointing_model.assert_called_once_with(
        control, fake_logger_interface, "s1"
    )


def test_logger_interface_lazily_builds_from_config(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify `_logger_interface` lazily builds a real LoggerInterface once."""
    from astrometricslib import LoggerInterface

    logger_interface = control._logger_interface
    assert isinstance(logger_interface, LoggerInterface)
    assert control._logger_interface is logger_interface


def test_run_guider_calibration_persists_the_derived_calibration(control, app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify run_guider_calibration sequences steps and saves the result."""
    from wayfindinglib.tasks.control_tasks.calibration_routines import GuiderCalibrationSteps

    _configure_active_rig(app_config)
    control.set_active_telescope("Rig A")
    control.set_active_camera("CamA")

    centroids = iter([(0.0, 0.0), (10.0, 0.0), (10.0, 0.0), (10.0, 8.0)])
    steps = GuiderCalibrationSteps(
        pulse_ra=lambda duration_sec: True,
        pulse_dec=lambda duration_sec: True,
        measure_guide_star_centroid=lambda: next(centroids),
    )

    assert control.active_guider_calibration() is None

    calibration = control.run_guider_calibration(steps, "cal-1", "CamA", "Rig A", arcsec_per_pixel=2.0)

    persisted = control.active_guider_calibration()
    assert persisted is not None
    assert persisted.id == calibration.id
    assert persisted.ra_rate_arcsec_per_sec == pytest.approx(calibration.ra_rate_arcsec_per_sec)


def test_run_backlash_calibration_delegates_to_the_task_module(mocker, control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify run_backlash_calibration forwards to calibration_routines."""
    from wayfindinglib.tasks.control_tasks import calibration_routines

    steps = mocker.Mock()
    mocker.patch.object(calibration_routines, "run_backlash_calibration", return_value=150.0)

    result = control.run_backlash_calibration(steps)

    assert result == pytest.approx(150.0)
    calibration_routines.run_backlash_calibration.assert_called_once_with(
        steps, "north", "south", 1.0, 0.05, 40, 0.5
    )


def test_run_polar_alignment_assist_delegates_to_the_task_module(mocker, control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify run_polar_alignment_assist forwards attempts and latitude."""
    from wayfindinglib.tasks.control_tasks import calibration_routines

    mocker.patch.object(calibration_routines, "run_polar_alignment_assist", return_value="model")
    attempts = [{"ra": 1.0, "dec": 2.0}]

    result = control.run_polar_alignment_assist(attempts, latitude_deg=39.7)

    assert result == "model"
    calibration_routines.run_polar_alignment_assist.assert_called_once_with(attempts, latitude_deg=39.7)


def test_run_polar_alignment_assist_defaults_latitude_from_observer_location(mocker, control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a missing explicit latitude falls back to observer location."""
    from wayfindinglib.tasks.control_tasks import calibration_routines

    mocker.patch.object(control, "get_observer_location", return_value={"latitude": 39.7})
    mocker.patch.object(calibration_routines, "run_polar_alignment_assist", return_value="model")
    attempts = [{"ra": 1.0, "dec": 2.0}]

    control.run_polar_alignment_assist(attempts)

    calibration_routines.run_polar_alignment_assist.assert_called_once_with(attempts, latitude_deg=39.7)


def test_assess_safety_hysteresis_persists_across_calls_on_same_astrometrics(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the interface's SafetyMonitor carries hysteresis across calls."""
    rule_set = SafetyRuleSet(
        id="default",
        rules=[
            SafetyRule(
                id="wind", measurement="wind_speed_kph", comparison="greater_than", unsafe_threshold=40.0
            )
        ],
        settling_period_sec=900,
    )
    control._butler.put(rule_set, "safety_rule_set", {"id": "default"})

    unsafe = control.assess_safety({"wind_speed_kph": (50.0, _NOW)}, now=_NOW)
    assert unsafe.verdict == SafetyVerdict.UNSAFE

    just_after = _NOW + timedelta(seconds=1)
    still_settling = control.assess_safety({"wind_speed_kph": (5.0, just_after)}, now=just_after)
    assert still_settling.verdict == SafetyVerdict.UNSAFE

    settled = _NOW + timedelta(seconds=900)
    cleared = control.assess_safety({"wind_speed_kph": (5.0, settled)}, now=settled)
    assert cleared.verdict == SafetyVerdict.SAFE


def test_refresh_safety_assessment_reflects_a_live_weather_reading(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify refresh_safety_assessment's verdict tracks a fed weather reading.

    Proof the M12 wiring is live, not just that `WeatherDriver` and
    `assess_safety` each exist independently
    (`Wayfinding_Library_Architecture.md` §2.5.4's verification note):
    changing what the injected driver reports changes the verdict
    `refresh_safety_assessment` returns.
    """
    rule_set = SafetyRuleSet(
        id="default",
        rules=[
            SafetyRule(
                id="wind", measurement="wind_speed_kph", comparison="greater_than", unsafe_threshold=40.0
            )
        ],
        settling_period_sec=1,
    )
    control._butler.put(rule_set, "safety_rule_set", {"id": "default"})

    class _FakeWeatherDriver:
        """A stand-in `WeatherDriver` returning a settable fixed reading."""

        def __init__(self) -> None:
            """Start with a safe wind reading."""
            self.reading = 5.0

        async def get_readings(self):  # ruff: ignore[missing-return-type-private-function]
            """Return the current fixed wind reading.

            Returns
            -------
            readings : `SensorReadings`
                A single `wind_speed_kph` reading, timestamped now.
            """
            return {"wind_speed_kph": (self.reading, datetime.now(UTC))}

    weather_driver = _FakeWeatherDriver()
    control.weather_driver = weather_driver

    safe = control.refresh_safety_assessment()
    assert safe.verdict == SafetyVerdict.SAFE

    weather_driver.reading = 50.0
    unsafe = control.refresh_safety_assessment()
    assert unsafe.verdict == SafetyVerdict.UNSAFE


def test_execute_safe_state_delegates_to_task_function(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify execute_safe_state runs the ordered sequence, returns outcome."""
    enclosure = Enclosure(
        id="enc-1",
        enclosure_type=EnclosureType.ROLL_OFF_ROOF,
        park_azimuth_deg=180.0,
        park_altitude_deg=0.0,
    )
    steps = SafeStateSteps(
        abandon_exposure=lambda: True,
        stop_guiding=lambda: True,
        park_mount=lambda: True,
        get_mount_position=lambda: (0.0, 180.0),
        close_enclosure=lambda: True,
        warm_sensor=lambda: True,
        close_session=lambda: True,
        enclosure=enclosure,
    )
    outcome = control.execute_safe_state("unsafe_verdict", steps)
    assert outcome.failed_step is None
    assert outcome.enclosure_closed is True


def test_apply_promotion_decision_and_summarize_divergence_evidence(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify promotion delegation records and evidence summarizes."""
    policy = control.apply_promotion_decision(
        ObservatoryCapability.PLATE_SOLVE_ALIGNMENT, DelegationState.SHADOWED
    )
    assert policy.state_for(ObservatoryCapability.PLATE_SOLVE_ALIGNMENT) == DelegationState.SHADOWED
    assert control.delegation_policy().state_for(ObservatoryCapability.PLATE_SOLVE_ALIGNMENT) == (
        DelegationState.SHADOWED
    )

    summary = control.summarize_divergence_evidence(ObservatoryCapability.PLATE_SOLVE_ALIGNMENT)
    assert summary.sample_count == 0


def test_enter_monitoring_mode_always_fully_succeeds(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify monitoring mode moves every capability to DELEGATED."""
    outcome = control.enter_monitoring_mode(evidence_note="going hands-off")

    assert outcome.rejected == {}
    assert all(state == DelegationState.DELEGATED for state in outcome.applied.values())
    assert len(outcome.applied) == len(ObservatoryCapability)


def test_enter_controller_mode_reports_partial_success(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify controller mode surfaces a partial BulkDelegationOutcome.

    Plan Verification (M8): from a fresh policy, only OBSERVATORY_SAFETY
    and MOUNT_CONTROL can legally reach AUTHORITATIVE without first
    passing through SHADOWED -- the rest must be visibly rejected, not
    silently skipped or forced.
    """
    outcome = control.enter_controller_mode()

    assert outcome.applied == {
        ObservatoryCapability.OBSERVATORY_SAFETY: DelegationState.AUTHORITATIVE,
        ObservatoryCapability.MOUNT_CONTROL: DelegationState.AUTHORITATIVE,
    }
    assert ObservatoryCapability.AUTOGUIDING in outcome.rejected
    assert control.delegation_policy().state_for(ObservatoryCapability.AUTOGUIDING) == (
        DelegationState.DELEGATED
    )


def test_enter_controller_mode_fully_succeeds_once_shadowed_and_calibrated(control, app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify controller mode fully succeeds once every precondition is met."""
    from wayfindinglib.models.equipment_and_site.focus_model import ApproachDirection, FocusModel

    _configure_active_rig(app_config)
    control.set_active_telescope("Rig A")
    control.set_active_camera("CamA")

    calibration = GuiderCalibration(
        id="cal-1",
        camera_id="CamA",
        telescope_id="Rig A",
        arcsec_per_pixel=2.0,
        camera_angle_deg=0.0,
        ra_rate_arcsec_per_sec=10.0,
        dec_rate_arcsec_per_sec=10.0,
    )
    control.save_guider_calibration(calibration)
    control.save_focus_model(
        FocusModel(
            id="focus-1",
            camera_id="CamA",
            telescope_id="Rig A",
            backlash_steps=0,
            approach_direction=ApproachDirection.INWARD,
        )
    )

    for capability in (
        ObservatoryCapability.PLATE_SOLVE_ALIGNMENT,
        ObservatoryCapability.AUTOGUIDING,
        ObservatoryCapability.AUTOFOCUS,
    ):
        control.apply_promotion_decision(capability, DelegationState.SHADOWED)

    outcome = control.enter_controller_mode()

    assert outcome.rejected == {}
    assert all(state == DelegationState.AUTHORITATIVE for state in outcome.applied.values())


def test_connect_lazily_initializes_every_configured_driver(control, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify connect() lazily initializes the shared driver and succeeds.

    `._driver` (a bare alias for `.driver`) was retired in M5: nothing
    in production reads it anymore -- `connect()` now loops over the
    five per-device-type driver properties (§4), each of which lazily
    builds the shared session via `.driver` on its own.
    """
    monkeypatch.setenv("ASTROMETRICS_TESTING", "1")

    assert control.connect() is True
    assert control.driver is not None


def test_sync_and_is_syncing_raise_without_configured_sync_service():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify sync()/is_syncing() raise in standalone mode."""
    control = ObservatoryControl(config=object())

    with pytest.raises(RuntimeError, match="standalone mode"):
        control.sync("M 81")
    with pytest.raises(RuntimeError, match="standalone mode"):
        control.is_syncing("M 81")


def test_remote_transfer_methods_delegate_to_the_task_module(mocker, control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the four remote-transfer methods forward to tasks."""
    from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

    mocker.patch.object(remote_transfer_tasks, "list_remote_targets", return_value=["M 81"])
    mocker.patch.object(remote_transfer_tasks, "list_remote_files", return_value=["frame1.fits"])
    mocker.patch.object(remote_transfer_tasks, "check_remote_connection", return_value=True)
    mocker.patch.object(remote_transfer_tasks, "download_remote_targets", return_value=True)

    assert control.list_remote_targets() == ["M 81"]
    remote_transfer_tasks.list_remote_targets.assert_called_once_with(control)

    assert control.list_remote_files("M 81") == ["frame1.fits"]
    remote_transfer_tasks.list_remote_files.assert_called_once_with(control, "M 81")

    assert control.check_remote_connection() is True
    remote_transfer_tasks.check_remote_connection.assert_called_once_with(control)

    assert control.download_remote_targets("M 81", local_path="/local/M81") is True
    remote_transfer_tasks.download_remote_targets.assert_called_once_with(
        control, "M 81", None, None, "/local/M81", True
    )


def test_remote_transfer_driver_lazily_builds_stellarmate_interface(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify remote_transfer_driver builds a cached StellarMateInterface.

    M7: `ObservatoryControl.remote_transfer_driver` resolves once from
    config (default ``"stellarmate"``) instead of every
    `remote_transfer_tasks.py` function constructing its own instance.
    """
    from wayfindinglib.drivers.protocols.remote_transfer_driver import RemoteTransferDriver
    from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface

    driver = control.remote_transfer_driver
    assert isinstance(driver, StellarMateInterface)
    assert isinstance(driver, RemoteTransferDriver)
    assert driver.driver_name == "stellarmate"
    assert control.remote_transfer_driver is driver


def test_remote_transfer_driver_can_be_injected_for_tests(mocker, control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the setter overrides the lazily-built driver."""
    fake_driver = mocker.Mock()
    control.remote_transfer_driver = fake_driver
    assert control.remote_transfer_driver is fake_driver


def test_discover_unassociated_remote_targets_delegates_via_astrometrics(mocker, control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify discovery constructs a high-level interface and delegates."""
    from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

    fake_astrometrics = mocker.Mock()
    mocker.patch("astrometricslib.Astrometrics", return_value=fake_astrometrics)
    mocker.patch.object(
        remote_transfer_tasks, "discover_unassociated_remote_targets", return_value=["Unassociated"]
    )

    assert control.discover_unassociated_remote_targets() == ["Unassociated"]
    remote_transfer_tasks.discover_unassociated_remote_targets.assert_called_once_with(
        control, fake_astrometrics.targets
    )


def test_list_camera_profiles_and_get_equipment_configuration(control, app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the two equipment-config methods report the active rig."""
    app_config.update_config({
        "Observatory.Telescope": {"focal_length_mm": "450.0", "focal_ratio": "6.0"},
        "Observatory.Camera": {"models": "CamA", "default_primary_camera": "CamA"},
        "Observatory.Camera.CamA": {
            "pixel_size_μm": "3.76",
            "sensor_width_px": "6248",
            "sensor_height_px": "4176",
        },
    })

    profiles = control.list_camera_profiles()
    assert len(profiles) == 1
    assert profiles[0]["name"] == "CamA"

    configuration = control.get_equipment_configuration()
    assert configuration is not None
    assert configuration["camera"]["name"] == "CamA"


def test_save_and_get_commissioning_runs_round_trips(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify commissioning runs record and are returned by get_all."""
    from wayfindinglib.models.policy.commissioning import CommissioningObservation, CommissioningRun

    run = CommissioningRun(
        id="run-1",
        phase=1,
        drill_name="phase1_device_state_survey",
        observations=[
            CommissioningObservation(
                criterion="Every device reports a summary state",
                observed_value="ENABLED",
                expected="any valid state",
                passed=True,
            )
        ],
    )
    control.save_commissioning_run(run)

    runs = control.get_commissioning_runs()
    assert len(runs) == 1
    assert runs[0].id == "run-1"
    assert runs[0].all_passed() is True


def test_get_safety_rule_set_returns_none_when_unconfigured(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an unconfigured safety rule set reports None, not a default."""
    assert control.get_safety_rule_set() is None


def test_save_and_get_safety_rule_set_round_trips(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a saved safety rule set is returned by get_safety_rule_set."""
    from wayfindinglib.models.policy.safety import SafetyRule, SafetyRuleSet

    rule_set = SafetyRuleSet(
        id="default",
        rules=[
            SafetyRule(
                id="rule-1",
                measurement="wind_speed_kph",
                comparison="greater_than",
                unsafe_threshold=40.0,
            )
        ],
    )
    control.save_safety_rule_set(rule_set)

    fetched = control.get_safety_rule_set()
    assert fetched is not None
    assert fetched.id == "default"
    assert fetched.rules[0].measurement == "wind_speed_kph"


def test_non_hardware_methods_do_not_reference_indi_driver():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify non-hardware methods never reference the INDI layer.

    A static source-text scan, mirroring
    `test_planning_registry.py::test_planning_module_tree_imports_no_device_driver`:
    a runtime `sys.modules` snapshot is order-dependent (other tests in
    the same session legitimately import INDI modules to exercise
    hardware operations), so this checks each non-hardware method's own
    source text instead, which is deterministic regardless of what else
    has run in the process. An operator inspecting safety, calibration,
    or delegation state should not need a connected telescope to do so.
    """
    import inspect

    non_hardware_methods = [
        ObservatoryControl.active_telescope,
        ObservatoryControl.active_camera,
        ObservatoryControl.set_active_telescope,
        ObservatoryControl.set_active_camera,
        ObservatoryControl.compute_pointing_correction,
        ObservatoryControl.compute_guiding_correction,
        ObservatoryControl.compute_focus_correction,
        ObservatoryControl.active_guider_calibration,
        ObservatoryControl.save_guider_calibration,
        ObservatoryControl.active_focus_model,
        ObservatoryControl.save_focus_model,
        ObservatoryControl.assess_safety,
        ObservatoryControl.active_enclosure,
        ObservatoryControl.execute_safe_state,
        ObservatoryControl.cooling_ramp_rate,
        ObservatoryControl.summarize_device,
        ObservatoryControl.delegation_policy,
        ObservatoryControl.apply_promotion_decision,
        ObservatoryControl.summarize_divergence_evidence,
    ]
    forbidden_substrings = ("wayfindinglib.drivers.indi", "import PyIndi", "from PyIndi")

    offending = []
    for method in non_hardware_methods:
        source = inspect.getsource(method)
        if any(needle in source for needle in forbidden_substrings):
            offending.append(method.__name__)

    assert offending == []


def test_mount_driver_slew_end_to_end_against_simulator(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Exercise slew/park/unpark/tracking/abort through the real driver stack.

    `ObservatoryControl.slew_to_coordinates()` -> `hardware_operations` ->
    the `MountDriver` ABC -> `IndiMountDriver` -> `SimulatorIndiInterface`,
    confirming parity with pre-redesign behavior end to end (per the
    plan's Verification section) rather than only against a
    duck-typed fake manager.
    """
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface

    control.driver = SimulatorIndiInterface(config=control._config)
    control.apply_promotion_decision(ObservatoryCapability.MOUNT_CONTROL, DelegationState.AUTHORITATIVE)

    assert control.slew_to_coordinates(10.0, 20.0) is True
    assert control.set_tracking(True) is True
    assert control.park() is True
    assert control.unpark() is True
    assert control.abort_motion() is True


def test_mount_driver_commands_refused_when_not_authoritative(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify mount commands are refused by default (monitoring mode).

    `MOUNT_CONTROL` defaults to `DELEGATED`, not `AUTHORITATIVE` -- this
    is the concrete mechanism behind "wayfindinglib starts in
    monitoring mode": hardware commands fail closed until the operator
    explicitly promotes the capability.
    """
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface

    control.driver = SimulatorIndiInterface(config=control._config)

    with pytest.raises(PermissionDeniedError, match="MOUNT_CONTROL"):
        control.slew_to_coordinates(10.0, 20.0)


def test_focuser_and_filter_wheel_end_to_end_against_simulator(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Exercise focuser/filter-wheel commands through the real driver stack.

    `ObservatoryControl.focus_move()`/`.set_filter()` -> `hardware_operations`
    -> the `FocuserDriver`/`FilterWheelDriver` ABCs -> the INDI adapters ->
    `SimulatorIndiInterface`, confirming parity with pre-redesign behavior
    (per the plan's Verification section).
    """
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface
    from wayfindinglib.models.policy.delegation import CapabilityDelegation, DelegationPolicy

    control.driver = SimulatorIndiInterface(config=control._config)
    # Persisted directly rather than via apply_promotion_decision(): AUTOFOCUS
    # and CAPTURE_ORCHESTRATION each have their own multi-step validity-rule
    # promotion path (covered by their own dedicated tests), orthogonal to
    # what this test verifies -- that the driver plumbing works once a
    # capability is AUTHORITATIVE.
    control._butler.put(
        DelegationPolicy(
            id="default",
            capability_delegations=[
                CapabilityDelegation(
                    capability=ObservatoryCapability.AUTOFOCUS, state=DelegationState.AUTHORITATIVE
                ),
                CapabilityDelegation(
                    capability=ObservatoryCapability.CAPTURE_ORCHESTRATION,
                    state=DelegationState.AUTHORITATIVE,
                ),
            ],
        ),
        "delegation_policy",
        {"id": "default"},
    )

    assert control.focus_move(50) is True
    assert control.get_focuser_position() == 0
    assert control.set_filter("Luminance") is True
    assert "Luminance" in control.get_filter_names()


def test_focuser_and_filter_wheel_commands_refused_when_not_authoritative(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify focuser/filter-wheel commands fail closed by default."""
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface

    control.driver = SimulatorIndiInterface(config=control._config)

    with pytest.raises(PermissionDeniedError, match="AUTOFOCUS"):
        control.focus_move(50)
    with pytest.raises(PermissionDeniedError, match="CAPTURE_ORCHESTRATION"):
        control.set_filter("Luminance")


def test_get_telescope_status_golden_output_matches_pre_redesign_shape(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the four-call-assembled status matches the old single-call one.

    Regression-sensitive point (plan Verification, M4): `get_telescope_status`
    now reassembles its result from four separate driver calls
    (`mount_driver`, `filter_wheel_driver`, `focuser_driver`, `camera_driver`)
    instead of one `IndiInterface.get_status()` call. Since nothing here
    changes the simulator's state between the two reads, both must
    produce byte-identical output.
    """
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface

    control.driver = SimulatorIndiInterface(config=control._config)

    golden = control.driver.get_status().model_dump(by_alias=True)
    golden["guidingHistory"] = []  # Populated by a guiding service, not configured here.

    from wayfindinglib.tasks.control_tasks import hardware_operations

    reassembled = hardware_operations.get_telescope_status(control)

    assert reassembled == golden


def test_capture_image_and_guide_camera_end_to_end_against_simulator(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Exercise capture_image/guide_expose/sync_coordinates end to end.

    `ObservatoryControl.capture_image()`/`.guide_expose()`/`.sync_coordinates()`
    -> `hardware_operations` -> the `CameraDriver`/`MountDriver` ABCs ->
    the INDI adapters -> `SimulatorIndiInterface` (per the plan's
    Verification section for M2-M4).
    """
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface
    from wayfindinglib.models.policy.delegation import CapabilityDelegation, DelegationPolicy

    control.driver = SimulatorIndiInterface(config=control._config)
    control._butler.put(
        DelegationPolicy(
            id="default",
            capability_delegations=[
                CapabilityDelegation(
                    capability=ObservatoryCapability.CAPTURE_ORCHESTRATION,
                    state=DelegationState.AUTHORITATIVE,
                ),
                CapabilityDelegation(
                    capability=ObservatoryCapability.AUTOGUIDING, state=DelegationState.AUTHORITATIVE
                ),
                CapabilityDelegation(
                    capability=ObservatoryCapability.PLATE_SOLVE_ALIGNMENT,
                    state=DelegationState.AUTHORITATIVE,
                ),
            ],
        ),
        "delegation_policy",
        {"id": "default"},
    )

    assert control.capture_image(1.0) is True
    assert control.guide_expose(1.0) is True
    assert control.get_guide_image() is None
    assert control.sync_coordinates(10.0, 20.0) is True


def test_capture_image_and_sync_coordinates_refused_when_not_authoritative(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify capture/sync commands fail closed by default."""
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface

    control.driver = SimulatorIndiInterface(config=control._config)

    with pytest.raises(PermissionDeniedError, match="CAPTURE_ORCHESTRATION"):
        control.capture_image(1.0)
    with pytest.raises(PermissionDeniedError, match="AUTOGUIDING"):
        control.guide_expose(1.0)
    with pytest.raises(PermissionDeniedError, match="PLATE_SOLVE_ALIGNMENT"):
        control.sync_coordinates(10.0, 20.0)


def test_enclosure_state_and_commands_against_simulator(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Exercise enclosure state/authority gating through the real driver stack.

    `ObservatoryControl.get_enclosure_state()`/`.open_enclosure()`/
    `.close_enclosure()` -> `hardware_operations` -> `EnclosureDriver` ->
    `IndiEnclosureDriver` -> `SimulatorIndiInterface` (M6). No dome/roof
    device is simulated today, so `get_enclosure_state()` honestly
    reports `UNKNOWN` (the "Unknown Is Unsafe" invariant) rather than a
    fabricated open/closed value.
    """
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface
    from wayfindinglib.models.equipment_and_site.enclosure import EnclosureState

    control.driver = SimulatorIndiInterface(config=control._config)

    assert control.get_enclosure_state() == EnclosureState.UNKNOWN


def test_enclosure_commands_refused_when_not_authoritative(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify open_enclosure/close_enclosure fail closed by default."""
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface

    control.driver = SimulatorIndiInterface(config=control._config)

    with pytest.raises(PermissionDeniedError, match="OBSERVATORY_SAFETY"):
        control.open_enclosure()
    with pytest.raises(PermissionDeniedError, match="OBSERVATORY_SAFETY"):
        control.close_enclosure()


def test_close_enclosure_refuses_when_mount_outside_clearance(control, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify close_enclosure refuses rather than closing on the mount.

    Plan Verification (M6): asserts refusal, not just the happy path --
    a mount position far from the configured park position must reject
    the close through the real `ObservatoryControl.close_enclosure` ->
    `hardware_operations.close_enclosure` ->
    `enclosure_control.can_close_enclosure` interlock chain, not just
    the unit-level fakes in `test_hardware_operations.py`. The mount's
    reported position is stubbed directly (rather than driven through
    the simulator's real slew physics) so the test asserts the
    interlock wiring, not incidental simulator behavior.
    """
    from wayfindinglib.drivers.protocols.mount_driver import MountStatus
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface
    from wayfindinglib.models.equipment_and_site.enclosure import Enclosure, EnclosureType
    from wayfindinglib.models.policy.delegation import CapabilityDelegation, DelegationPolicy

    control.driver = SimulatorIndiInterface(config=control._config)
    stub_mount_driver = mocker.Mock()
    stub_mount_driver.get_status = mocker.AsyncMock(
        return_value=MountStatus(
            ra="00:00:00",
            dec="+00:00:00",
            altitude="0° 0′ 0″",
            azimuth="0° 0′ 0″",
            trackingStatus="Tracking",
        )
    )
    control.mount_driver = stub_mount_driver
    control._butler.put(
        DelegationPolicy(
            id="default",
            capability_delegations=[
                CapabilityDelegation(
                    capability=ObservatoryCapability.OBSERVATORY_SAFETY,
                    state=DelegationState.AUTHORITATIVE,
                ),
            ],
        ),
        "delegation_policy",
        {"id": "default"},
    )
    control._butler.put(
        Enclosure(
            id="enc-1",
            enclosure_type=EnclosureType.ROLL_OFF_ROOF,
            park_azimuth_deg=180.0,
            park_altitude_deg=45.0,
            clearance_tolerance_deg=2.0,
        ),
        "enclosure",
        {"id": "enc-1"},
    )

    with pytest.raises(ConflictError, match="clearance"):
        control.close_enclosure()
