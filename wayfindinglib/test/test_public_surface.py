"""Purpose: Pin the public surface of wayfindinglib and its four sub-APIs.

Description: `wayfindinglib/__init__.py` is the only door into this library
that code outside it should use. `Wayfinder.control` is split into a root
and six children named by topic, `planning` holds four objects, and
`execution` is a single class. Renaming, adding or removing a public name
is a change to the library's contract with the backend, the MCP servers and
the UI, so it
must be a deliberate edit to the lists below rather than a side effect of
moving code. The tests also check that every export resolves, that
`control` stays under its size limit, and that the argument guards work:
`SkyPosition` checks its ranges, and `control.remote.list` refuses
arguments its `kind` does not use.

The last test checks every public method of `Wayfinder` against the public
API design rules (`documentation/library_design/Public_API_Design_Guide.md`),
with the rule code shared from `astrometricslib._internal.public_api_rules`.
Known departures are listed in `public_api_rules_baseline.txt` next to this
file; a new departure fails, and so does a listed one that no longer occurs.
"""

import importlib
from pathlib import Path

import pytest
from pydantic import ValidationError

import wayfindinglib
from astrometricslib import AppConfiguration
from astrometricslib._internal.public_api_rules import baseline_problems, find_violations, walk_public_api
from astrometricslib.exceptions import ExternalServiceError, InvalidArgumentError, NotFoundError
from wayfindinglib import ObservationExecution, ObservationPlanning, ObservationReview, ObservatoryControl
from wayfindinglib._internal.control import SkyPosition
from wayfindinglib.drivers.butler import DiskButler

BASELINE = Path(__file__).with_name("public_api_rules_baseline.txt")
"""Known departures from the public API design rules, emptied by issue #206."""

EXPECTED_EXPORTS = frozenset({
    "ChecklistItem",
    "DitherConfig",
    "ExposureRequest",
    "FrameEndDetector",
    "Framing",
    "HorizonZone",
    "MosaicPanel",
    "QueueRequest",
    "ObservationExecution",
    "ObservationPackage",
    "ObservationPlanning",
    "ObservationReview",
    "ObservationSession",
    "ObservatoryControl",
    "Wayfinder",
})
"""What `from wayfindinglib import ...` offers."""

EXPECTED_MODULE_NAMES = {
    "wayfindinglib.drivers": frozenset({
        "PHD2Client",
        "PHD2Session",
    }),
    "wayfindinglib.exceptions": frozenset({
        "DelegationPolicyValidationError",
        "PlanRevisionConflictError",
    }),
    "wayfindinglib.logs": frozenset({
        "SavedCapture",
        "ekos_utc_offset_hours",
        "read_ekos_captures",
    }),
    "wayfindinglib.reports": frozenset({
        "DepthGoalState",
        "DepthLevel",
        "PackageStatus",
        "RunState",
    }),
}
"""Names each public module of wayfindinglib offers (its `__all__`)."""

PLANNING_MEMBERS = frozenset({"sessions", "packages", "sky", "catalogs"})
"""The four objects of `ObservationPlanning`; it has no methods of its own."""

PLANNING_OBJECTS = {
    "sessions": frozenset({"get", "query", "create", "add", "remove", "update"}),
    "packages": frozenset({
        "get",
        "get_for_target",
        "query",
        "query_suggestion_log",
        "create",
        "update",
        "restore",
        "delete",
        "record_suggestions",
        "get_depth_goal",
        "set_depth_goal",
        "create_mosaic",
    }),
    "sky": frozenset({"get_visibility", "get_tonight_advice", "get_constellation_lines", "get_panels"}),
    "catalogs": frozenset({
        "query",
        "query_sources",
        "get_deep_status",
        "predict_deep_catalog",
        "record_deep_catalog",
    }),
}
"""Each object of `ObservationPlanning` and its public members."""

EXECUTION_MEMBERS = frozenset({
    "advance_session",
    "abort_session",
    "reconcile_session",
    "create_recorder",
})
"""The public members of `ObservationExecution`."""

REVIEW_MEMBERS = frozenset({"nights", "targets", "guiding", "performance", "alignment"})
"""The public members of `ObservationReview`: its five objects."""

REVIEW_CHILDREN = {
    "nights": {
        "query",
        "get",
        "get_target_changes",
        "get_capture",
        "get_sky_coverage",
        "get_recurring_issues",
        "query_ekos_sessions",
        "get_ekos_session",
        "get_live_status",
    },
    "targets": {"get_report", "get_result", "get_star_shapes", "get_frame_guiding"},
    "guiding": {"get_night", "query_runs", "measure_spectrum", "record_spectrum", "measure_unguided_error"},
    "performance": {"get_envelope", "predict_sky_rate", "measure_focus_temperature"},
    "alignment": {"query", "query_nights", "get_pointing_model", "get_polar_alignment", "record_external"},
}


ROOT_LIFECYCLE = frozenset({"close"})
"""Lifecycle methods on `control` itself, outside the operation count.

`close()` releases the background work `control` owns at program shutdown.
It does nothing to the observatory, so it is not one of the operations
`MAXIMUM_CONTROL_MEMBERS` limits, and it belongs on the object that owns the
work rather than on a child. Nothing else may join this set without the same
argument.
"""

ROOT_READS = frozenset({"library_path"})
"""Read-only properties on `control` itself, outside the operation count.

`library_path` names the folder of the wayfinding library, so a program that
writes records can check where they go. It reads one path and does nothing
to the observatory.
"""

CHILDREN = {
    "mount": {
        "status",
        "poll_external_alignment",
        "get_polar_alignment",
        "get_centering_exposure",
        "slew",
        "park",
        "unpark",
        "set_tracking",
        "manual_move",
        "abort_motion",
        "cancel_centering",
        "set_slew_rate",
    },
    "imaging": {
        "status",
        "capture_image",
        "stop_capture",
        "set_filter",
        "focus_move",
        "query_focus_models",
        "record_focus_model",
    },
    "guiding": {
        "status",
        "query_calibrations",
        "record_calibration",
        "expose",
        "refit_spectrum",
        "run_loop",
        "stop_loop",
    },
    "remote": {"list", "check_connection", "frame_locations", "sync_frames", "sync_logs"},
    "safety": {
        "status",
        "apply_promotion_decision",
        "enter_monitoring_mode",
        "enter_controller_mode",
    },
    "equipment": {
        "status",
        "connect",
        "disconnect",
        "apply_configuration",
        "get_active",
        "set_active",
        "set_pointing_limits",
        "set_device_property",
    },
}
"""Each child of `control` and its public members."""

MAXIMUM_CONTROL_MEMBERS = 72
"""Most operations `control` and its children may offer together. Raised from
70 for `guiding.stop_loop` and `mount.cancel_centering` (issue #95, decision
U4/20): monitor mode refuses a stop, and the guarantee lives in the library."""


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


def test_planning_offers_exactly_its_pinned_members(control: ObservatoryControl) -> None:
    """`ObservationPlanning` holds four objects with their pinned methods."""
    planning = ObservationPlanning(control._context.config, control._context.butler)
    assert _public_names(planning) == PLANNING_MEMBERS
    for name, members in PLANNING_OBJECTS.items():
        assert _public_names(getattr(planning, name)) == members, name


def test_execution_offers_exactly_its_pinned_members() -> None:
    """`ObservationExecution` offers exactly the pinned methods."""
    assert _public_names(ObservationExecution) == EXECUTION_MEMBERS


def test_review_offers_exactly_its_pinned_members(control: ObservatoryControl) -> None:
    """`ObservationReview` offers exactly the pinned methods."""
    assert _public_names(ObservationReview(control)) == REVIEW_MEMBERS


@pytest.mark.parametrize("child", sorted(REVIEW_CHILDREN))
def test_each_review_object_offers_exactly_its_pinned_members(
    control: ObservatoryControl, child: str
) -> None:
    """A review object's public members match the pinned list."""
    assert _public_names(getattr(ObservationReview(control), child)) == REVIEW_CHILDREN[child]


def test_review_shares_the_context_of_control(control: ObservatoryControl) -> None:
    """Review and its objects share control's context, not control."""
    review = ObservationReview(control)
    assert review._context is control._context
    assert control not in vars(review).values()
    for child in REVIEW_CHILDREN:
        assert getattr(review, child)._context is control._context, child


def test_no_background_job_can_skip_registering_a_job() -> None:
    """No `@background_job` method of planning takes a `register_job` switch.

    Every pipeline run records a job, whoever starts it.
    """
    import inspect

    from wayfindinglib.api.planning.catalogs import PlanningCatalogs
    from wayfindinglib.api.planning.packages import PlanningPackages

    for owner in (PlanningCatalogs, PlanningPackages):
        for name in dir(owner):
            method = getattr(owner, name)
            if getattr(method, "__background_job_type__", None) is not None:
                assert "register_job" not in inspect.signature(method).parameters, name


def test_control_root_holds_only_the_drivers_and_the_children(control: ObservatoryControl) -> None:
    """The root offers only the children, `close` and `library_path`."""
    assert _public_names(control) == ROOT_LIFECYCLE | ROOT_READS | set(CHILDREN)


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
    control._mount_driver = marker
    assert control._context.mount_driver is marker
    assert control._mount_driver is marker


def test_control_stays_under_its_size_limit(control: ObservatoryControl) -> None:
    """The children's operations number 72 or fewer."""
    total = sum(len(_public_names(getattr(control, child))) for child in CHILDREN)
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


@pytest.mark.parametrize(
    ("is_reachable", "error_type", "message"),
    [
        (False, ExternalServiceError, "not reachable"),
        (True, NotFoundError, "No folder named"),
    ],
)
def test_remote_list_files_tells_unreachable_from_missing_folder(
    control: ObservatoryControl,
    monkeypatch: pytest.MonkeyPatch,
    is_reachable: bool,
    error_type: type[Exception],
    message: str,
) -> None:
    """An unlisted folder is reported as offline or as absent, as it is."""
    from wayfindinglib.tasks.control_tasks import remote_transfer_tasks as tasks

    monkeypatch.setattr(tasks, "list_remote_targets", lambda context: [])
    monkeypatch.setattr(tasks, "matching_remote_folder", lambda context, name, folders: None)
    monkeypatch.setattr(tasks, "check_remote_connection", lambda context: is_reachable)
    with pytest.raises(error_type, match=message):
        control.remote.list("files", folder_name="NGC 7331")


def test_each_public_module_offers_exactly_its_pinned_names() -> None:
    """Verify each public module's `__all__` is exactly its pinned set."""
    for module_name, expected in EXPECTED_MODULE_NAMES.items():
        module = importlib.import_module(module_name)
        actual = frozenset(module.__all__)
        assert not actual - expected, f"{module_name} offers new name(s): {sorted(actual - expected)}"
        assert not expected - actual, f"{module_name} lost name(s): {sorted(expected - actual)}"
        assert all(hasattr(module, name) for name in actual), module_name


def test_public_methods_follow_the_design_rules_or_are_in_the_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every departure from the rules is listed; every listed one occurs."""
    from unittest.mock import MagicMock

    config_path = tmp_path / "astrometrics.config.toml"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    wayfinder = wayfindinglib.Wayfinder(config, astrometrics=MagicMock(), simulated=True)
    try:
        methods, objects = walk_public_api(
            wayfinder, "wayfinder", lambda value: type(value).__module__.startswith("wayfindinglib.api.")
        )
    finally:
        wayfinder.control.close()
    assert len(methods) > 50, "the walk from the root object found too few public methods"
    problems = baseline_problems(find_violations(methods, objects), BASELINE)
    assert not problems, "\n\n".join(problems)
