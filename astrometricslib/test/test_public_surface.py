"""Guards the one list every other package in the repo depends on.

`astrometricslib/__init__.py` is the only door into this library that
anything outside it is supposed to use. The UI backend imports from
`astrometricslib` directly and never reaches into a submodule, and the
Sphinx documentation only ever documents this top-level namespace --
so the names listed in `__all__` are, in a very real sense, the entire
contract this library makes with the rest of the repository.

That contract is easy to break by accident during a refactor. Moving a
class to a new home, renaming it, or forgetting to re-export it after
splitting a module all look like internal cleanup, but any one of them
silently changes what `from astrometricslib import Whatever` gives you
-- or whether it works at all. Several of these names are also
resolved on demand through `__init__.py`'s `__getattr__`, specifically
so that importing `astrometricslib` does not have to pull in every
heavy dependency up front; a plain `import astrometricslib` does not
exercise those at all, so a broken deferred export can pass right by
a casual smoke test.

So this file pins both halves of the contract: the exact set of names
`__all__` promises, and the promise that every one of them actually
resolves to something. It also pins the public members of the root
`Astrometrics` object (which holds only its sub-APIs) and of each sub-API,
so a method renamed or folded into another shows up here. Together they
are the one test a large, multi-step reorganization of this library's
internal layers can be checked against without re-deriving, each time,
whether the outside world would notice.

The last tests check every public method against the public API design rules
(`documentation/library_design/Public_API_Design_Guide.md`), with the rule
code in `astrometricslib._internal.public_api_rules`, which wayfindinglib's
surface test shares. Known departures are listed in
`public_api_rules_baseline.txt` next to this file; a new departure fails, and
so does a listed one that no longer occurs.
"""

import ast
import importlib
from pathlib import Path

import pytest

import astrometricslib
from astrometricslib._internal.public_api_rules import (
    PublicMethod,
    PublicObject,
    baseline_problems,
    find_violations,
    walk_public_api,
)
from astrometricslib._internal.testing import astrometrics_with_drivers

BASELINE = Path(__file__).with_name("public_api_rules_baseline.txt")
"""Known departures from the public API design rules, emptied by issue #206."""

# The public surface. Changing this set on purpose -- adding, removing, or
# renaming an export -- is a real, visible change to the library's contract
# with the rest of the repo, so it should be a deliberate edit to this list,
# not a side effect of moving code around internally.
EXPECTED_PUBLIC_NAMES = frozenset({
    "AppConfiguration",
    "Astrometrics",
    "CalibrationCatalog",
    "Camera",
    "FilterType",
    "FilterWheel",
    "Focuser",
    "FrameRecord",
    "GuideScope",
    "Mount",
    "Observatories",
    "ObservatoryConfiguration",
    "ProcessingPipelines",
    "QualityRecords",
    "StellarCatalog",
    "StellarObject",
    "Target",
    "TargetCatalog",
    "Telescope",
    "Visualization",
    "configure_logging",
    "configure_offline_iers",
    "get_configuration",
    "warm_earth_orientation_data",
})

EXPECTED_MODULE_NAMES = {
    "astrometricslib.exceptions": frozenset({
        "FITS_READ_ERRORS",
        "AstrometricsError",
        "ConfigurationError",
        "ConflictError",
        "DeviceInUseError",
        "ErrorInfo",
        "ExternalServiceError",
        "HardwareError",
        "InvalidArgumentError",
        "NotFoundError",
        "OfflineModeError",
        "PermissionDeniedError",
        "ParameterUnavailableError",
        "PlateSolveFailedError",
        "ProcessingError",
        "StorageError",
        "StorageNotMountedError",
        "error_from_info",
        "to_error_info",
        "MovedError",
        "wrap_outside_error",
    }),
    "astrometricslib.arguments": frozenset({
        "FrameChoice",
        "Page",
        "TimeInput",
        "check_choice",
        "check_include",
        "check_limit",
        "observing_night_id",
        "reject_unused_arguments",
        "to_epoch_seconds",
    }),
    "astrometricslib.reports": frozenset({
        "EARLIER_NIGHTS_VARIANT",
        "AstrometryPipelineQualityMetrics",
        "AstrometryQualitySummary",
        "CaptureSetting",
        "ExposureGroupSummary",
        "InputQualityReport",
        "LimitTier",
        "MeasurementReference",
        "MeasurementStatus",
        "QualityLimit",
        "QualityMeasurement",
        "RawFrameCheckReport",
        "SetAsideFrame",
        "SetAsideFrameRecord",
        "SpectralFrameCheckReport",
        "StackQualityReport",
        "StackSummary",
        "StarResultReference",
        "SubjectLevel",
    }),
    "astrometricslib.jobs": frozenset({
        "DbLogHandler",
        "JobStore",
        "Jobs",
        "ProcessingJob",
        "RunAbandonedError",
        "RunAbandonment",
        "SkipJobStoredRecords",
        "background_job",
        "background_log_context",
        "bind_abandonment",
        "capture_job_logs",
        "close_interrupted_jobs",
        "current_process_identity",
        "get_current_job",
        "log_context",
        "new_request_id",
        "process_is_alive",
        "raise_if_abandoned",
        "registered_job",
        "report_started_jobs",
        "run_as_background_job",
        "running_as_job",
    }),
    "astrometricslib.parameters": frozenset({
        "ConfigurationValues",
        "EquipmentKind",
        "EquipmentValues",
        "HardcodedKind",
        "ParameterRegistry",
        "ParameterRole",
        "ParameterSource",
        "ResolvedValue",
        "RunBasis",
        "TunedParameter",
        "TunedValues",
        "WhenMissing",
        "AMBIGUOUS_RMS_GAP",
        "ChangeNotice",
        "ConfigTunedValues",
        "LOW_STAR_FRACTION",
        "MOUNT_PERIODIC_ERROR_PERIOD_SECONDS",
        "NO_GOOD_MATCH_RMS",
        "STRETCHED_STAR_WARNING_ELONGATION",
        "ParameterContext",
        "TYPICAL_GUIDE_CYCLE_SECONDS",
        "SetupEquipmentValues",
        "Tuner",
        "TunedRecord",
        "TuningInputs",
        "TuningReport",
        "TuningResult",
        "TuningSkippedError",
        "collect_tuners",
        "read_tuned_record",
        "run_tuning",
        "tune",
        "collect_parameters",
        "current_parameter_context",
        "equipment_values_to_write",
        "factor_for",
        "frames_parameter_context",
        "parameter_context",
        "record_basis",
        "resolve_parameter",
        "run_basis_value",
        "setup_parameter_context",
    }),
    "astrometricslib.drivers": frozenset({
        "LIBRARY_DATA_KINDS",
        "OFFLINE_VARIABLE",
        "AbstractButler",
        "Butler",
        "DatasetSpec",
        "LaunchIdentity",
        "LibraryFolders",
        "VersionedCache",
        "build_pixel_query",
        "bump_data_version",
        "connect_db",
        "existing_stack_path",
        "file_lock",
        "get_camera_data_versions",
        "get_frame_metric_signs",
        "get_gaia_archive",
        "get_library_data_version",
        "get_simbad_driver",
        "get_stack_quality_data_version",
        "get_target_camera_names",
        "get_target_data_version",
        "get_target_document_signs",
        "header_is_plate_solved",
        "healpix_pixels_of_points",
        "is_loopback_host",
        "is_path_inside",
        "launch_identity_from_settings",
        "library_folders",
        "locks_directory",
        "network_is_offline",
        "pixel_source_id_range",
        "pixels_near_circles",
        "read_data_versions",
        "read_launch_identity",
        "require_local_host",
        "require_mounted_storage",
        "require_online",
        "resolve_mounted_path",
        "scratch_directory",
        "stop_process_group",
        "tile_of_source_id",
        "tiles_for_cone",
        "tiles_for_footprint",
    }),
}
"""Names each public module of astrometricslib offers (its `__all__`)."""


def test_public_all_matches_the_pinned_name_set() -> None:
    """Verify `astrometricslib.__all__` is exactly the pinned name set.

    A mismatch in either direction is a real change to the library's
    public contract -- a name silently added, removed, or renamed --
    and should be caught here rather than discovered downstream in
    `backend/` or in the Sphinx build.
    """
    actual_names = frozenset(astrometricslib.__all__)
    added_names = actual_names - EXPECTED_PUBLIC_NAMES
    removed_names = EXPECTED_PUBLIC_NAMES - actual_names

    assert not added_names, (
        f"astrometricslib.__all__ has new name(s) not in EXPECTED_PUBLIC_NAMES: "
        f"{sorted(added_names)}. If this addition is intentional, update "
        f"EXPECTED_PUBLIC_NAMES in this file to match."
    )
    assert not removed_names, (
        f"astrometricslib.__all__ is missing name(s) EXPECTED_PUBLIC_NAMES expects: "
        f"{sorted(removed_names)}. If this removal is intentional, update "
        f"EXPECTED_PUBLIC_NAMES in this file to match."
    )


def test_every_public_name_actually_resolves() -> None:
    """Verify every name in `__all__` can actually be fetched.

    Several exports are only resolved on demand through `__init__.py`'s
    `__getattr__`, so a plain `import astrometricslib` does not prove
    they work -- only fetching each one by name does. This is what
    would have caught a rename that updated `__all__` but not the
    deferred-export table underneath it, or vice versa.
    """
    unresolvable_names = []
    for name in astrometricslib.__all__:
        try:
            getattr(astrometricslib, name)
        except AttributeError:
            unresolvable_names.append(name)

    assert not unresolvable_names, (
        f"astrometricslib.__all__ lists name(s) that do not actually resolve: "
        f"{sorted(unresolvable_names)}. Each one is declared as part of the public "
        f"surface but fetching it raises AttributeError."
    )


SUB_API_MEMBERS = {
    "observatories": {"configurations"},
    "observatories.configurations": {"create", "get", "query", "scan", "update"},
    "targets": {
        "add",
        "add_frames",
        "sessions",
        "create",
        "delete",
        "display_turn",
        "delete_images",
        "get",
        "get_frame",
        "imaged_field_centers",
        "query",
        "read_frame_header",
        "reindex_frames",
        "save",
        "select_frames",
    },
    "stars": {
        "count_by_class",
        "count_by_target",
        "create",
        "delete",
        "get",
        "get_spectrum",
        "get_statistics",
        "measure_periodicity",
        "query",
        "record_periodicity",
        "update",
    },
    "processing": {
        "ANALYSIS_STAGES",
        "DEFAULT_ANALYSIS_STAGES",
        "SELECTABLE_ANALYSIS_STAGES",
        "acquire_job_slot",
        "calibration",
        "find_stack_configuration",
        "get_stack_identification",
        "identify_stack",
        "list_preview_bright_objects",
        "process_target",
        "remake_preview",
        "measure_noise",
        "open_in_siril",
        "plate_solve",
        "predict_stack",
        "predict_stack_weights",
        "set_preview_bright_object",
        "stack",
        "stack_summary",
        "stop_stacking_programs",
    },
    "processing.calibration": {
        "add_frames",
        "assess",
        "camera_profile",
        "measure_frames",
        "measure_sensor_noise",
        "query",
        "reindex_frames",
        "save",
    },
    "quality": {
        "blocks",
        "calibration_identities",
        "choose_blink_pair",
        "frame_statuses",
        "get_aperture_record",
        "capture_settings",
        "forget",
        "map_position",
        "match_frame_positions",
        "measure_detections",
        "measure_folder",
        "measure_frames",
        "measure_stack",
        "measure_star_profile",
        "measure_tuned_parameters",
        "metrics",
        "put_back_set_aside_frames",
        "query",
        "record_frame_measurements",
        "resolve_rows",
        "set_aside_frames",
    },
    "quality.blocks": {
        "find_stars",
        "measure_aperture",
        "measure_sky",
        "measure_sky_gradient",
        "robust_sigma",
        "sigma_clip",
        "sky_corners",
        "solved_stars",
        "star_widths",
    },
    "visualization": {
        "get_last_captured_image",
        "get_star_overlay",
        "place_stack_picture_on_sky",
        "plot",
        "render_fits",
        "stack_sky_picture_files",
    },
    "jobs": {"query"},
}
"""Each sub-API of `Astrometrics`, by attribute path, and its members."""


def _public_members(instance: object) -> set[str]:
    """Return the public attribute names of an object's class and itself.

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


def test_the_root_object_only_holds_its_sub_apis() -> None:
    """`Astrometrics` has no methods: only settings, storage and sub-APIs."""
    from unittest.mock import MagicMock

    astrometrics = astrometrics_with_drivers(astrometricslib.AppConfiguration(), MagicMock())
    assert _public_members(astrometrics) == {
        "config",
        "jobs",
        "observatories",
        "processing",
        "quality",
        "stars",
        "targets",
        "visualization",
    }


def test_each_sub_api_offers_exactly_its_pinned_members() -> None:
    """Every sub-API's public members match the pinned list."""
    from unittest.mock import MagicMock

    astrometrics = astrometrics_with_drivers(astrometricslib.AppConfiguration(), MagicMock())
    for path, expected in SUB_API_MEMBERS.items():
        instance = astrometrics
        for part in path.split("."):
            instance = getattr(instance, part)
        assert _public_members(instance) == expected, path


def test_the_root_takes_only_the_settings() -> None:
    """`Astrometrics(config)` exposes no private storage or driver type."""
    import inspect

    assert list(inspect.signature(astrometricslib.Astrometrics).parameters) == ["config"]


def test_target_catalog_and_processing_share_one_calibration_catalog() -> None:
    """The sub-APIs share the children they are built with."""
    from unittest.mock import MagicMock

    astrometrics = astrometrics_with_drivers(astrometricslib.AppConfiguration(), MagicMock())
    assert astrometrics.processing._targets is astrometrics.targets
    assert astrometrics.quality._targets is astrometrics.targets
    assert astrometrics.processing.calibration._targets is astrometrics.targets
    assert astrometrics.visualization._stars is astrometrics.stars


def test_each_public_module_offers_exactly_its_pinned_names() -> None:
    """Verify each public module's `__all__` is exactly its pinned set.

    The public modules are the places a script imports the errors, reports,
    job tools and storage tools from. A name added or removed there is a
    change to the library's contract, like one at the root.
    """
    for module_name, expected in EXPECTED_MODULE_NAMES.items():
        module = importlib.import_module(module_name)
        actual = frozenset(module.__all__)
        assert not actual - expected, f"{module_name} offers new name(s): {sorted(actual - expected)}"
        assert not expected - actual, f"{module_name} lost name(s): {sorted(expected - actual)}"


def test_every_name_of_every_public_module_resolves() -> None:
    """Verify each name in each public module's `__all__` can be fetched."""
    unresolvable = [
        f"{module_name}.{name}"
        for module_name in EXPECTED_MODULE_NAMES
        for name in importlib.import_module(module_name).__all__
        if not hasattr(importlib.import_module(module_name), name)
    ]
    assert not unresolvable, f"names that do not resolve: {unresolvable}"


def test_no_code_outside_the_libraries_imports_the_private_package() -> None:
    """Verify the private package is imported only where it may be.

    Only astrometricslib, its scripts and the tests of either library may
    import `astrometricslib._internal`. The backend, the MCP servers and
    wayfindinglib use the root and the public modules only.
    """
    repository = Path(__file__).resolve().parents[2]
    offenders = []
    for folder in ("wayfindinglib", "backend", "mcp_servers"):
        for path in sorted((repository / folder).rglob("*.py")):
            parts = path.relative_to(repository).parts
            if {"test", "tests", "scripts", "__pycache__"} & set(parts[:-1]) or parts[-1].startswith((
                "test_",
                "conftest",
            )):
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
                if isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                if any(name.startswith("astrometricslib._internal") for name in names):
                    offenders.append(f"{path.relative_to(repository)}:{node.lineno}")
    assert not offenders, f"imports of astrometricslib._internal outside the library: {offenders}"


def _rule_violations() -> set[str]:
    """Check every public method of `Astrometrics` against the design rules.

    Returns
    -------
    violations : `set` [`str`]
        Lines of the form ``<qualified name>: <rule>``.
    """
    from unittest.mock import MagicMock

    astrometrics = astrometrics_with_drivers(astrometricslib.AppConfiguration(), MagicMock())
    methods, objects = walk_public_api(
        astrometrics, "astrometrics", lambda value: type(value).__module__.startswith("astrometricslib.api.")
    )
    assert len(methods) > 50, "the walk from the root object found too few public methods"
    return find_violations(methods, objects, fold_exempt_parents=("astrometrics.processing",))


def test_public_methods_follow_the_design_rules_or_are_in_the_baseline() -> None:
    """Every departure from the rules is listed; every listed one occurs."""
    problems = baseline_problems(_rule_violations(), BASELINE)
    assert not problems, "\n\n".join(problems)


@pytest.mark.parametrize("library", ["astrometricslib", "wayfindinglib"])
def test_each_baseline_file_says_issue_206_empties_it(library: str) -> None:
    """Both baseline files exist and say in their header #206 empties them."""
    path = Path(__file__).resolve().parents[2] / library / "test" / "public_api_rules_baseline.txt"
    assert path.exists(), f"missing {path}"
    header = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("#"))
    assert "#206" in header and "empty this file" in header, path


class _FakeApi:
    """A sub-API that breaks each design rule once, to test the rule code."""

    def get(self, star_id: str) -> int | None:
        """Read one item.

        Owner: astrometricslib._internal.public_api_rules.read_baseline

        Returns
        -------
        item : `int` or `None`
            Always `None`.
        """
        return None

    def fetch_things(
        self, target: str, paths: list[str], telescope: str, register_job: bool = True
    ) -> int | str:
        """Break the verb, default, banned-argument, union and owner rules.

        Returns
        -------
        value : `int` or `str`
            Always 0.
        """
        return 0

    def set_tracking(self, enabled: bool, *, on_done: object = None, delay_seconds: float = 0.0) -> bool:
        """Break the callback, unit and bool-return rules.

        Owner: astrometricslib.no_such_module.no_such_function

        Returns
        -------
        done : `bool`
            Always `True`.
        """
        return True

    def stop(self) -> None:
        """Break the return-annotation rule, once its annotation is removed.

        Owner: astrometricslib._internal.public_api_rules.find_violations
        """


# A missing return annotation is the case under test, so remove it here.
del _FakeApi.stop.__annotations__["return"]


def test_the_rule_code_finds_each_kind_of_departure() -> None:
    """Each rule fires on the fake sub-API; a method that keeps them passes."""
    api = _FakeApi()
    methods = [PublicMethod(f"lib.area.thing.{name}", getattr(api, name)) for name in ("get", "fetch_things")]
    methods += [PublicMethod(f"lib.area.{name}", getattr(api, name)) for name in ("set_tracking", "stop")]
    methods.append(PublicMethod("lib.area.thing.deep.get", api.get))
    objects = [
        PublicObject("lib.area", 2),
        PublicObject("lib.area.thing", 2),
        PublicObject("lib.processing.x", 1),
    ]
    found = find_violations(methods, objects, fold_exempt_parents=("lib.processing",))
    assert found == {
        "lib.area.thing.fetch_things: verb",
        "lib.area.thing.fetch_things: no-default(telescope)",
        "lib.area.thing.fetch_things: banned-argument(register_job)",
        "lib.area.thing.fetch_things: return-union",
        "lib.area.thing.fetch_things: owner",
        "lib.area.set_tracking: banned-argument(on_done)",
        "lib.area.set_tracking: unit-suffix(delay_seconds)",
        "lib.area.set_tracking: bool-return",
        "lib.area.set_tracking: owner",
        "lib.area.stop: no-return-annotation",
        "lib.area.thing.deep.get: depth",
        "lib.area.thing: fold",
    }


def test_the_baseline_comparison_reports_new_and_stale_entries(tmp_path: Path) -> None:
    """A departure not listed and a listed line no longer found both fail."""
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("# header\n\na.b.c: verb\na.b.d: owner\n", encoding="utf-8")
    assert baseline_problems({"a.b.c: verb", "a.b.d: owner"}, baseline) == []
    problems = baseline_problems({"a.b.c: verb", "a.b.e: verb"}, baseline)
    assert len(problems) == 2
    assert "a.b.e: verb" in problems[0] and "a.b.d: owner" in problems[1]
