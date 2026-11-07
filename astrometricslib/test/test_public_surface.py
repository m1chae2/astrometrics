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
"""

import ast
import importlib
from pathlib import Path

import astrometricslib

# The public surface. Changing this set on purpose -- adding, removing, or
# renaming an export -- is a real, visible change to the library's contract
# with the rest of the repo, so it should be a deliberate edit to this list,
# not a side effect of moving code around internally.
EXPECTED_PUBLIC_NAMES = frozenset({
    "AppConfiguration",
    "Astrometrics",
    "CalibrationCatalog",
    "FilterType",
    "FrameRecord",
    "ProcessingPipelines",
    "QualityRecords",
    "StellarCatalog",
    "StellarObject",
    "Target",
    "TargetCatalog",
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
        "PermissionDeniedError",
        "PlateSolveFailedError",
        "ProcessingError",
        "StorageError",
        "StorageNotMountedError",
        "error_from_info",
        "to_error_info",
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
        "QuarantinePreview",
        "RawFrameCheckReport",
        "SetAsideFrame",
        "SetAsideFrameRecord",
        "SpectralFrameCheckReport",
        "StackQualityReport",
        "StackSummary",
        "StarResultReference",
        "SubjectLevel",
        "observing_night_id",
    }),
    "astrometricslib.jobs": frozenset({
        "DbLogHandler",
        "JobStore",
        "Jobs",
        "ProcessingJob",
        "background_job",
        "background_log_context",
        "capture_job_logs",
        "close_interrupted_jobs",
        "get_current_job",
        "log_context",
        "new_request_id",
        "registered_job",
        "run_as_background_job",
        "running_as_job",
    }),
    "astrometricslib.drivers": frozenset({
        "LIBRARY_DATA_KINDS",
        "SIMULATOR_FOLDER_NAME",
        "AbstractButler",
        "Butler",
        "DatasetSpec",
        "VersionedCache",
        "bump_data_version",
        "connect_db",
        "file_lock",
        "get_camera_data_versions",
        "get_library_data_version",
        "get_target_camera_names",
        "get_target_data_version",
        "is_path_inside",
        "locks_directory",
        "read_data_versions",
        "real_folders",
        "require_mounted_storage",
        "resolve_mounted_path",
        "scratch_directory",
        "stop_process_group",
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
    "targets": {
        "add",
        "add_frames",
        "sessions",
        "create",
        "delete",
        "delete_images",
        "get",
        "get_frame",
        "get_header",
        "get_stars",
        "imaged_field_centers",
        "list",
        "query",
        "read_frame_header",
        "read_saved",
        "reindex_frames",
        "resolve",
        "save",
        "select_frames",
    },
    "stars": {
        "analyze_periodicity",
        "create",
        "dataset_version",
        "delete",
        "find_or_create_by_position",
        "get",
        "get_spectrum",
        "query",
        "update",
    },
    "processing": {
        "acquire_job_slot",
        "calibration",
        "process_target",
        "remake_preview",
        "run_spectroscopy_by_session",
        "measure_noise",
        "open_in_siril",
        "plate_solve",
        "predict_stack_weights",
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
        "calibration_identities",
        "get_aperture_record",
        "capture_settings",
        "measure_frames",
        "measure_stack",
        "metrics",
        "query",
        "record_frame_measurements",
        "resolve_rows",
        "set_aside_frames",
    },
    "visualization": {"get_last_captured_image", "plot", "render_fits"},
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

    astrometrics = astrometricslib.Astrometrics(astrometricslib.AppConfiguration(), MagicMock())
    assert _public_members(astrometrics) == {
        "config",
        "jobs",
        "processing",
        "quality",
        "stars",
        "targets",
        "visualization",
    }


def test_each_sub_api_offers_exactly_its_pinned_members() -> None:
    """Every sub-API's public members match the pinned list."""
    from unittest.mock import MagicMock

    astrometrics = astrometricslib.Astrometrics(astrometricslib.AppConfiguration(), MagicMock())
    for path, expected in SUB_API_MEMBERS.items():
        instance = astrometrics
        for part in path.split("."):
            instance = getattr(instance, part)
        assert _public_members(instance) == expected, path


def test_target_catalog_and_processing_share_one_calibration_catalog() -> None:
    """The sub-APIs share the children they are built with."""
    from unittest.mock import MagicMock

    astrometrics = astrometricslib.Astrometrics(astrometricslib.AppConfiguration(), MagicMock())
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
