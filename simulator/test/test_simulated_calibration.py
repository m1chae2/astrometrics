"""Purpose: Test the simulator's made-up guider calibration and focus model.

Description: Every test uses a temporary settings file and a temporary
wayfinding library, so no real library or database is read or written. The
starting policy is like the user's real one: one row, ``MOUNT_CONTROL``
``DELEGATED``. The backend's real-versus-simulated decision is tested in
``backend/tests/test_observatory_source.py``.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from astrometricslib import AppConfiguration
from astrometricslib.exceptions import ConfigurationError, PermissionDeniedError
from simulator import record_simulated_calibrations
from wayfindinglib import Wayfinder
from wayfindinglib.analytics.guide_pulses import GUIDE_RATE_ARCSEC_PER_S
from wayfindinglib.exceptions import GuardError
from wayfindinglib.models.equipment_and_site.focus_model import ApproachDirection, FocusModel
from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.models.policy.delegation import (
    CapabilityDelegation,
    DelegationPolicy,
    DelegationState,
    ObservatoryCapability,
)
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.test import monitor_mode_support as support


def _make_wayfinder(
    root: Path, monkeypatch: pytest.MonkeyPatch, library: Path | None = None, with_rig: bool = True
) -> Wayfinder:
    """Build a `Wayfinder` over a temporary library and a real-shaped policy.

    Parameters
    ----------
    root : `pathlib.Path`
        The stand-in simulator folder.
    monkeypatch : `pytest.MonkeyPatch`
        Points the settings file into `root`.
    library : `pathlib.Path`, optional
        The wayfinding library folder. Default: ``root / "wayfinding"``.
    with_rig : `bool`, optional
        Make one telescope and one camera active.

    Returns
    -------
    wayfinder : `Wayfinder`
        Its policy has one row, ``MOUNT_CONTROL`` ``DELEGATED``, and its
        drivers are recording stand-ins in ``wayfinder.recording``.
    """
    config_path = root / "astrometrics.config.toml"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(library or root / "wayfinding")}})
    if with_rig:
        config.update_config({
            "Observatory.Telescope": {"models": "Rig A", "active_telescope": "Rig A"},
            "Observatory.Telescope.Rig A": {"focal_length_mm": "450.0", "focal_ratio": "6.0"},
            "Observatory.Camera": {"models": "CamA", "default_primary_camera": "CamA"},
            "Observatory.Camera.CamA": {
                "pixel_size_μm": "3.76",
                "sensor_width_px": "6248",
                "sensor_height_px": "4176",
            },
        })
    wayfinder = Wayfinder(config)
    wayfinder.recording = support.RecordingDrivers(wayfinder.control._context)
    policy = DelegationPolicy(
        id="default",
        capability_delegations=[
            CapabilityDelegation(
                capability=ObservatoryCapability.MOUNT_CONTROL, state=DelegationState.DELEGATED
            )
        ],
    )
    wayfinder.control._context.butler.put(policy, "delegation_policy", {"id": "default"})
    return wayfinder


def _take_command(wayfinder: Wayfinder, root: Path) -> object:
    """Do what the backend does when the simulator is up.

    Returns
    -------
    outcome : `BulkDelegationOutcome`
        What `enter_controller_mode` reports.
    """
    record_simulated_calibrations(wayfinder.control, root)
    return wayfinder.control.safety.enter_controller_mode(evidence_note="simulator", shadow_first=True)


def test_a_simulated_start_makes_every_capability_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the made-up records the same rules let every capability through."""
    wayfinder = _make_wayfinder(tmp_path, monkeypatch)

    outcome = _take_command(wayfinder, tmp_path)

    assert outcome.rejected == {}
    assert set(outcome.applied) == set(ObservatoryCapability)
    policy = wayfinder.control.safety.status(include=["delegation_policy"]).delegation_policy
    assert all(policy.is_authoritative(capability) for capability in ObservatoryCapability)


def test_capture_focus_and_filter_commands_pass_the_safety_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused before the records exist, accepted after."""
    from wayfindinglib.tasks.control_tasks import hardware_operations

    wayfinder = _make_wayfinder(tmp_path, monkeypatch)
    context = wayfinder.control._context
    wayfinder.control.safety.enter_controller_mode(evidence_note="before", shadow_first=True)
    with pytest.raises(PermissionDeniedError, match="AUTOFOCUS"):
        wayfinder.control.imaging.focus_move(10)

    _take_command(wayfinder, tmp_path)

    hardware_operations._require_authoritative(context, ObservatoryCapability.CAPTURE_ORCHESTRATION)
    wayfinder.control.imaging.focus_move(10)
    wayfinder.control.imaging.set_filter("L")
    assert wayfinder.recording.commands_sent() != []


def _run(index: int, ra_rate: float | None, dec_rate: float | None) -> GuidingRunSummary:
    """Make one guide-log run, like those read from the user's logs.

    Returns
    -------
    run : `GuidingRunSummary`
        Run number `index`; later numbers are more recent.
    """
    return GuidingRunSummary(
        id=f"guide_log.txt#{index}",
        sessionId="2026-10-03",
        sourceFileName="guide_log.txt",
        writtenByEkos=False,
        startedAt=1_790_000_000.0 + index,
        pixelScaleArcsecPerPx=6.39,
        focalLengthMm=121.05,
        raRateArcsecPerSecond=ra_rate,
        decRateArcsecPerSecond=dec_rate,
    )


def test_without_real_records_the_values_come_from_the_real_guide_log_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guider follows the real runs; nonsense runs are left out."""
    wayfinder = _make_wayfinder(tmp_path, monkeypatch)
    butler = wayfinder.control._context.butler
    for index, (ra_rate, dec_rate) in enumerate([
        (7.0, 5.0),
        (8.0, 6.0),
        (6.0, 7.0),
        (473.7, 799.0),
        (1.0, 3.0),
    ]):
        butler.put(_run(index, ra_rate, dec_rate), "guiding_run", {})

    report = record_simulated_calibrations(wayfinder.control, tmp_path)

    calibration = wayfinder.control.guiding.status(include=["calibration"]).calibration
    focus_model = wayfinder.control.imaging.status(include=["focus_model"]).focus_model
    assert calibration == report.guider_calibration
    assert focus_model == report.focus_model
    assert calibration.arcsec_per_pixel == pytest.approx(6.39)
    assert calibration.ra_rate_arcsec_per_sec == pytest.approx(7.0)
    assert calibration.dec_rate_arcsec_per_sec == pytest.approx(6.0)
    assert calibration.source_note.startswith("Simulator: derived from")
    assert "3 sensible real guide-log runs" in calibration.source_note
    assert focus_model.source_note.startswith("Simulator: made-up focus model")
    assert focus_model.backlash_steps == 0
    assert report.focus_source == "simulator focuser defaults"


def test_with_no_runs_the_equipment_and_simulator_defaults_are_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no real data: the equipment's scale, the simulator's rate."""
    wayfinder = _make_wayfinder(tmp_path, monkeypatch)

    report = record_simulated_calibrations(wayfinder.control, tmp_path)

    calibration = report.guider_calibration
    assert calibration.arcsec_per_pixel == pytest.approx(206.265 * 3.76 / 450.0, rel=1e-3)
    assert calibration.ra_rate_arcsec_per_sec == GUIDE_RATE_ARCSEC_PER_S
    assert "equipment plate scale" in calibration.source_note


def test_the_newest_real_records_are_copied_and_follow_new_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real records win, renamed to the active equipment."""
    wayfinder = _make_wayfinder(tmp_path, monkeypatch)
    butler = wayfinder.control._context.butler

    def calibration(day: int, scale: float) -> GuiderCalibration:
        """Make a real calibration for other equipment names.

        Returns
        -------
        calibration : `GuiderCalibration`
            Calibrated on October `day`.
        """
        return GuiderCalibration(
            id=f"real-{day}",
            camera_id="Real Cam",
            telescope_id="Real Scope",
            arcsec_per_pixel=scale,
            camera_angle_deg=12.0,
            ra_rate_arcsec_per_sec=7.1,
            dec_rate_arcsec_per_sec=6.2,
            calibrated_at=datetime(2026, 10, day, tzinfo=UTC),
        )

    butler.put(calibration(1, 6.0), "guider_calibration", {})
    butler.put(calibration(3, 6.39), "guider_calibration", {})
    butler.put(
        FocusModel(
            id="real-focus",
            camera_id="Real Cam",
            telescope_id="Real Scope",
            backlash_steps=40,
            approach_direction=ApproachDirection.OUTWARD,
            thermal_coefficient_steps_per_c=-3.5,
            calibrated_at=datetime(2026, 10, 2, tzinfo=UTC),
        ),
        "focus_model",
        {},
    )

    report = record_simulated_calibrations(wayfinder.control, tmp_path)

    assert report.guider_calibration.arcsec_per_pixel == pytest.approx(6.39)
    assert report.guider_calibration.camera_angle_deg == pytest.approx(12.0)
    assert report.guider_calibration.telescope_id == "Rig A"
    assert report.guider_calibration.source_note == "Simulator: copied from real calibration of 2026-10-03."
    assert report.focus_model.backlash_steps == 40
    assert report.focus_model.camera_id == "CamA"
    assert report.focus_model.source_note == "Simulator: copied from real focus model of 2026-10-02."
    assert wayfinder.control.guiding.status(include=["calibration"]).calibration is not None


def test_recording_twice_keeps_one_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A second start replaces the records rather than adding more."""
    wayfinder = _make_wayfinder(tmp_path, monkeypatch)

    record_simulated_calibrations(wayfinder.control, tmp_path)
    record_simulated_calibrations(wayfinder.control, tmp_path)

    butler = wayfinder.control._context.butler
    assert len(butler.get_all("guider_calibration")) == 1
    assert len(butler.get_all("focus_model")) == 1


def test_a_library_outside_the_simulator_folder_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is recorded outside the simulator's own library."""
    root = tmp_path / "simulator"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    wayfinder = _make_wayfinder(tmp_path, monkeypatch, library=elsewhere)

    with pytest.raises(GuardError, match="not inside the simulator folder"):
        record_simulated_calibrations(wayfinder.control, root)

    butler = wayfinder.control._context.butler
    assert butler.get_all("guider_calibration") == []
    assert butler.get_all("focus_model") == []


def test_a_real_folder_is_refused_even_inside_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A library that is one of the real folders is refused."""
    wayfinder = _make_wayfinder(tmp_path, monkeypatch)

    with pytest.raises(GuardError, match="real folder"):
        record_simulated_calibrations(wayfinder.control, tmp_path, protected=[tmp_path / "wayfinding"])


def test_no_active_rig_is_a_configuration_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an active telescope and camera there is nothing to calibrate."""
    wayfinder = _make_wayfinder(tmp_path, monkeypatch, with_rig=False)

    with pytest.raises(ConfigurationError):
        record_simulated_calibrations(wayfinder.control, tmp_path)
