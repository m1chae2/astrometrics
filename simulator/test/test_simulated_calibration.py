"""Purpose: Test the simulator's made-up guider calibration and focus model.

Description: Every test uses a temporary settings file and a temporary
wayfinding library, so no real library or database is read or written. The
starting policy is like the user's real one: one row, ``MOUNT_CONTROL``
``DELEGATED``. The backend's real-versus-simulated decision is tested in
``backend/tests/test_observatory_source.py``.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from astrometricslib import AppConfiguration
from astrometricslib.exceptions import ConfigurationError, PermissionDeniedError
from simulator import record_simulated_calibrations
from simulator.errors import GuardError
from simulator.simulated_calibration import (
    SIMULATED_GUIDE_RATE_ARCSEC_PER_SEC,
    build_simulated_calibrations,
    rpc_calls,
)
from wayfindinglib import Wayfinder
from wayfindinglib.analytics.guide_pulses import GUIDE_RATE_ARCSEC_PER_S
from wayfindinglib.models.equipment_and_site.focus_model import ApproachDirection, FocusModel
from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.models.policy.delegation import (
    CapabilityDelegation,
    DelegationPolicy,
    DelegationState,
    ObservatoryCapability,
)
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


def test_the_guider_calibration_holds_the_simulators_own_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Angle 0 and the 0.5x sidereal guide rate, whatever the real runs say."""
    wayfinder = _make_wayfinder(tmp_path, monkeypatch)
    wayfinder.control._context.butler.put(
        GuiderCalibration(
            id="real-1",
            camera_id="Real Cam",
            telescope_id="Real Scope",
            arcsec_per_pixel=6.39,
            camera_angle_deg=12.0,
            ra_rate_arcsec_per_sec=7.1,
            dec_rate_arcsec_per_sec=6.2,
            calibrated_at=datetime(2026, 10, 3, tzinfo=UTC),
        ),
        "guider_calibration",
        {},
    )

    report = record_simulated_calibrations(wayfinder.control, tmp_path)

    calibration = wayfinder.control.guiding.status(include=["calibration"]).calibration
    focus_model = wayfinder.control.imaging.status(include=["focus_model"]).focus_model
    assert calibration.id == report.guider_calibration.id
    assert calibration.camera_angle_deg == pytest.approx(0.0)
    assert calibration.arcsec_per_pixel == pytest.approx(206.265 * 3.76 / 450.0, rel=1e-3)
    assert (
        calibration.ra_rate_arcsec_per_sec
        == calibration.dec_rate_arcsec_per_sec
        == GUIDE_RATE_ARCSEC_PER_S
        == SIMULATED_GUIDE_RATE_ARCSEC_PER_SEC
    )
    assert calibration.source_note.startswith("Simulator value, not the real camera's")
    assert focus_model.id == report.focus_model.id
    assert focus_model.source_note.startswith("Simulator: made-up focus model")
    assert focus_model.backlash_steps == 0
    assert report.focus_source == "simulator focuser defaults"


def test_the_newest_real_focus_model_is_copied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real focus model wins, renamed to the active equipment."""
    wayfinder = _make_wayfinder(tmp_path, monkeypatch)
    wayfinder.control._context.butler.put(
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

    assert report.focus_model.backlash_steps == 40
    assert report.focus_model.camera_id == "CamA"
    assert report.focus_model.source_note == "Simulator: copied from real focus model of 2026-10-02."


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


def test_building_the_records_saves_nothing_and_the_rpc_calls_take_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The launcher's path: build first, then send the calls as RPCs."""
    wayfinder = _make_wayfinder(tmp_path, monkeypatch)
    control = wayfinder.control
    report = build_simulated_calibrations(control, tmp_path)
    assert control._context.butler.get_all("guider_calibration") == []
    assert control._context.butler.get_all("focus_model") == []

    calls = json.loads(json.dumps(rpc_calls(report)))  # plain JSON, as it crosses the process boundary
    assert [method for method, _ in calls] == [
        "observatory:record_guider_calibration",
        "observatory:record_focus_model",
    ]
    control.guiding.record_calibration(**calls[0][1])
    control.imaging.record_focus_model(**calls[1][1])
    outcome = control.safety.enter_controller_mode(evidence_note="simulator", shadow_first=True)

    assert outcome.rejected == {}
    assert set(outcome.applied) == set(ObservatoryCapability)


def test_the_command_line_prints_the_calls_for_the_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``python -m simulator.simulated_calibration`` prints one JSON line."""
    import astrometricslib
    from simulator import simulated_calibration

    wayfinder = _make_wayfinder(tmp_path, monkeypatch)
    monkeypatch.setattr(astrometricslib, "get_configuration", lambda: wayfinder.control._context.config)
    monkeypatch.setattr("wayfindinglib.Wayfinder", lambda config: wayfinder)

    assert simulated_calibration.main(["--root", str(tmp_path)]) == 0

    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert [method for method, _ in printed["calls"]] == [
        "observatory:record_guider_calibration",
        "observatory:record_focus_model",
    ]
    assert printed["guider_source"]
    assert printed["focus_source"]
