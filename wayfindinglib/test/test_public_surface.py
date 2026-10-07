"""Purpose: Pin the public surface of wayfindinglib and its three sub-APIs.

Description: `wayfindinglib/__init__.py` is the only door into this library
that code outside it should use. `Wayfinder.control` is split into a root
and seven children named by topic, and `planning` and `execution` are
single classes. Renaming, adding or removing a public name is a change to
the library's contract with the backend, the MCP servers and the UI, so it
must be a deliberate edit to the lists below rather than a side effect of
moving code. The tests also check that every export resolves, that
`control` stays under its size limit, and that the argument guards work:
`SkyPosition` checks its ranges, and `control.remote.list` refuses
arguments its `kind` does not use.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

import wayfindinglib
from astrometricslib import AppConfiguration, InvalidArgumentError
from wayfindinglib import ObservationExecution, ObservationPlanning, ObservatoryControl, SkyPosition
from wayfindinglib.drivers.butler import DiskButler

EXPECTED_EXPORTS = frozenset({
    # The root object, the sub-APIs and the drivers callers put in.
    "Wayfinder",
    "ObservatoryControl",
    "ObservationPlanning",
    "ObservationExecution",
    "IndiInterface",
    "SimulatorIndiInterface",
    # Models.
    "AlignmentAttempt",
    "AlignmentTargetSession",
    "CalibrationAdvisory",
    "CaptureResult",
    "DeepCatalogEstimate",
    "DeepCatalogStatus",
    "DitherConfig",
    "EquipmentConfiguration",
    "EquipmentStatus",
    "ExposureRequest",
    "FrameType",
    "GuidingStatus",
    "HorizonZone",
    "ImagingStatus",
    "LiveGuidingStatus",
    "MeridianStatus",
    "MosaicPanel",
    "MosaicPlan",
    "MountPointingModel",
    "NightConditions",
    "ObjectVisibility",
    "ObservationPackage",
    "ObservationSession",
    "ObservationSessionSummary",
    "QueueRequest",
    "SafetyStatus",
    "SequenceItem",
    "SequencePlan",
    "SkySource",
    "SiteProfile",
    "SkyPosition",
    "StartTimeMode",
    "TargetQualityAdvisory",
    "Telescope",
    "VisibilityReport",
    "VisibilitySpan",
    # The library's own error class.
    "DelegationPolicyValidationError",
})
"""What `from wayfindinglib import ...` offers."""

PLANNING_MEMBERS = frozenset({
    "get_visibility",
    "get_advisory",
    "calculate_panels",
    "create_mosaic",
    "create_plan",
    "edit_queue",
    "get_plan",
    "deep_catalog_status",
    "build_deep_star_catalog",
    # Sky browsing around a point, and name lookups.
    "find_sources",
    "get_sources",
    "get_library_star_summaries",
    "get_online_catalog_sources",
    "list_catalog_driver_metadata",
    "get_constellation_lines",
    "get_imaged_field_centers",
    "lookup_coordinates",
    "resolve_target_coordinates",
})
"""The public members of `ObservationPlanning`."""

EXECUTION_MEMBERS = frozenset({
    "advance_session",
    "abort_session",
    "reconcile_session",
    "execute_meridian_flip",
    "recover_fault",
    "recover_guide_star_loss",
    "record_divergence",
    "create_recorder",
})
"""The public members of `ObservationExecution`."""

ROOT_DRIVERS = frozenset({
    "driver",
    "mount_driver",
    "focuser_driver",
    "filter_wheel_driver",
    "camera_driver",
    "guide_camera_driver",
    "enclosure_driver",
    "switch_driver",
    "weather_driver",
    "remote_transfer_driver",
    "guiding_driver",
})
"""The driver properties `control` keeps for injecting drivers."""

CHILDREN = {
    "mount": {
        "status",
        "slew",
        "sync",
        "park",
        "unpark",
        "set_tracking",
        "manual_move",
        "abort_motion",
        "set_slew_rate",
        "compute_pointing_correction",
        "run_polar_alignment_assist",
    },
    "imaging": {
        "status",
        "capture_image",
        "set_filter",
        "focus_move",
        "compute_focus_correction",
        "save_focus_model",
    },
    "guiding": {
        "status",
        "pulse",
        "expose",
        "get_image",
        "drain_external_pulses",
        "compute_correction",
        "run_calibration",
        "run_backlash_calibration",
        "run_exposure_test",
        "refit_spectrum",
        "save_calibration",
        "save_spectrum_analysis",
        "save_run",
        "run_loop",
    },
    "remote": {"list", "check_connection", "frame_status", "sync_frames", "sync_logs"},
    "history": {
        "query",
        "get_live_session_status",
        "frame_guiding",
        "get_performance_envelope",
        "save_ekos_session_context",
    },
    "safety": {
        "status",
        "assess",
        "save_rule_set",
        "execute_safe_state",
        "open_enclosure",
        "close_enclosure",
        "apply_promotion_decision",
        "enter_monitoring_mode",
        "enter_controller_mode",
    },
    "equipment": {
        "status",
        "connect",
        "disconnect",
        "set_active_telescope",
        "set_active_camera",
        "set_device_property",
        "cooling_ramp_rate",
        "summarize_device",
        "save_commissioning_run",
    },
}
"""Each child of `control` and its public members."""

MAXIMUM_CONTROL_MEMBERS = 70
"""Most operations `control` and its children may offer together."""


def _public_names(instance: object) -> set[str]:
    """Return the public attribute names of an object and its class.

    Parameters
    ----------
    instance : `object`
        The object to inspect.

    Returns
    -------
    names : `set` [`str`]
        Every name without a leading underscore.
    """
    return {name for name in dir(instance) if not name.startswith("_")}


@pytest.fixture
def control(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ObservatoryControl:
    """Build an `ObservatoryControl` over an isolated configuration.

    Returns
    -------
    control : `ObservatoryControl`
        Backed by a temporary configuration and butler.
    """
    config_path = tmp_path / "astrometrics.config.toml"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return ObservatoryControl(config=config, butler=DiskButler(app_config=config))


def test_the_package_exports_exactly_the_expected_names() -> None:
    """`__all__` matches the pinned list, and every name resolves."""
    assert set(wayfindinglib.__all__) == EXPECTED_EXPORTS
    for name in EXPECTED_EXPORTS:
        assert getattr(wayfindinglib, name) is not None, name


def test_planning_offers_exactly_its_pinned_members() -> None:
    """`ObservationPlanning` offers exactly the pinned methods."""
    assert _public_names(ObservationPlanning) == PLANNING_MEMBERS


def test_execution_offers_exactly_its_pinned_members() -> None:
    """`ObservationExecution` offers exactly the pinned methods."""
    assert _public_names(ObservationExecution) == EXECUTION_MEMBERS


def test_background_jobs_can_skip_registering_a_job() -> None:
    """Every `@background_job` method of planning takes `register_job`."""
    import inspect

    for name in PLANNING_MEMBERS:
        method = getattr(ObservationPlanning, name)
        if getattr(method, "__background_job_type__", None) is not None:
            assert inspect.signature(method).parameters["register_job"].default is True, name


def test_control_root_holds_only_the_drivers_and_the_children(control: ObservatoryControl) -> None:
    """The root offers only the driver properties and the seven children."""
    assert _public_names(control) == ROOT_DRIVERS | set(CHILDREN)


@pytest.mark.parametrize("child", sorted(CHILDREN))
def test_each_child_offers_exactly_its_pinned_members(control: ObservatoryControl, child: str) -> None:
    """A child's public members match the pinned list."""
    assert _public_names(getattr(control, child)) == CHILDREN[child]


def test_children_hold_the_shared_context_not_control(control: ObservatoryControl) -> None:
    """Every child shares one context and holds no reference to `control`."""
    for child in CHILDREN:
        instance = getattr(control, child)
        assert instance._context is control._context, child
        assert control not in vars(instance).values(), child


def test_setting_a_driver_on_control_reaches_every_child(control: ObservatoryControl) -> None:
    """Injecting a driver on the root updates the shared context."""
    marker = object()
    control.mount_driver = marker
    assert control._context.mount_driver is marker
    assert control.mount_driver is marker


def test_control_stays_under_its_size_limit(control: ObservatoryControl) -> None:
    """The drivers plus the children's operations number 70 or fewer."""
    total = len(ROOT_DRIVERS) + sum(len(_public_names(getattr(control, child))) for child in CHILDREN)
    assert total <= MAXIMUM_CONTROL_MEMBERS


def test_sky_position_checks_its_ranges_and_converts_to_hours() -> None:
    """A position keeps degrees, gives hours, and refuses bad values."""
    position = SkyPosition(ra_deg=150.0, dec_deg=-20.0)
    assert position.ra_hours == pytest.approx(10.0)
    assert SkyPosition.model_validate({"ra_deg": 150.0, "dec_deg": -20.0}) == position
    assert position.model_dump() == {"ra_deg": 150.0, "dec_deg": -20.0}
    with pytest.raises(ValidationError):
        SkyPosition(ra_deg=360.0, dec_deg=0.0)
    with pytest.raises(ValidationError):
        SkyPosition(ra_deg=0.0, dec_deg=91.0)


@pytest.mark.parametrize(
    ("kind", "arguments", "message"),
    [
        ("folders", {"sizes": True}, "sizes"),
        ("target_folders", {"sizes": True}, "sizes"),
        ("unassociated_folders", {"folder_name": "M 81", "sizes": True}, "sizes"),
        ("files", {}, "needs a folder_name"),
        ("everything", {}, "kind must be one of"),
    ],
)
def test_remote_list_refuses_arguments_its_kind_does_not_use(
    control: ObservatoryControl, kind: str, arguments: dict[str, object], message: str
) -> None:
    """`control.remote.list` refuses an argument its kind does not use."""
    with pytest.raises(InvalidArgumentError, match=message):
        control.remote.list(kind, **arguments)
