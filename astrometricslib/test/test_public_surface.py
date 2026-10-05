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

import astrometricslib

# The public surface. Changing this set on purpose -- adding, removing, or
# renaming an export -- is a real, visible change to the library's contract
# with the rest of the repo, so it should be a deliberate edit to this list,
# not a side effect of moving code around internally.
EXPECTED_PUBLIC_NAMES = frozenset({
    "AbstractButler",
    "AbstractCatalogAccess",
    "Activity",
    "ActivityDescription",
    "Agent",
    "AgentType",
    "AnalysisResult",
    "AppConfiguration",
    "AppliedCameraProfile",
    "AsteroidDetectionCandidate",
    "Astrometrics",
    "AstrometricsError",
    "AstrometryPipelineQualityMetrics",
    "AstrometryQualitySummary",
    "BatchRunSummary",
    "Butler",
    "CalibrationCatalog",
    "CalibrationQueryResult",
    "CatalogAccess",
    "Collection",
    "ConfigFile",
    "ConfigFileDescription",
    "ConfigurationError",
    "ConflictError",
    "DEFAULT_DARK_TEMPERATURE_TOLERANCE_C",
    "DatasetDescription",
    "DatasetEntity",
    "DatasetSpec",
    "DbLogHandler",
    "DeviceInUseError",
    "Entity",
    "EntityDescription",
    "ErrorInfo",
    "ExposureGroupSummary",
    "ExternalServiceError",
    "FileItem",
    "FilterType",
    "FitsHeaderEntry",
    "FrameRecord",
    "FrameSelection",
    "GenerationDescription",
    "GroupedFrameStat",
    "HardwareError",
    "ImageProcessing",
    "InputQualityReport",
    "InvalidArgumentError",
    "Jobs",
    "LoggerInterface",
    "MovingObjectConfig",
    "NotFoundError",
    "NumpyEncoder",
    "Parameter",
    "ParameterDescription",
    "PermissionDeniedError",
    "PhotometryResult",
    "PlateSolveFailedError",
    "PlotData",
    "PreviewRemakeResult",
    "ProcessTargetResult",
    "ProcessingError",
    "ProcessingJob",
    "ProcessingPipelines",
    "ProvenanceStore",
    "QualityDiagnostics",
    "QuarantinePreview",
    "RPC_CODES",
    "RawFrameCheckReport",
    "ReindexReport",
    "RenderedImage",
    "RestoreReport",
    "SATURATED_BLOB_MINIMUM_PIXELS",
    "SATURATED_FRAME_FRACTION",
    "SetAsideFrame",
    "SpectralFrameCheckReport",
    "SpectroscopyResult",
    "StackQualityReport",
    "StackResult",
    "StackSummary",
    "StarIdentifier",
    "StarQueryResult",
    "StellarCatalog",
    "StellarObject",
    "StorageError",
    "StorageNotMountedError",
    "Target",
    "TargetCatalog",
    "TargetFilesResponse",
    "TargetQueryResult",
    "TargetReindexChange",
    "TargetSessionContribution",
    "UsageDescription",
    "Used",
    "ValueDescription",
    "ValueEntity",
    "VariableCandidate",
    "ViewableImage",
    "Visualization",
    "WasAssociatedWith",
    "WasAttributedTo",
    "WasConfiguredBy",
    "WasGeneratedBy",
    "acquire_resource_slot",
    "background_job",
    "capture_job_logs",
    "classify_and_sort_fits_files",
    "close_interrupted_jobs",
    "configure_logging",
    "connect_db",
    "derive_field_centers",
    "derive_target_sessions",
    "export_target_lineage_as_prov_xml",
    "file_lock",
    "frame_is_spectral",
    "get_configuration",
    "get_current_job",
    "get_log_context",
    "is_path_inside",
    "log_context",
    "new_request_id",
    "observing_night_id",
    "parse_coordinate_string",
    "parse_iso_time",
    "require_mounted_storage",
    "resolve_camera_profile",
    "resolve_mounted_path",
    "safe_json_dumps",
    "select_library_frames",
    "to_error_info",
})

# Internal names the root still exports because code outside the library
# imports them from here. wayfindinglib may import only the package root
# (the import-linter contract), and the backend uses these until the
# library consistency work moves them. Each one should leave the root once
# its last outside caller has a public alternative.
KEPT_FOR_OUTSIDE_CALLERS = {
    "DbLogHandler": "the backend",
    "FrameSelection": "wayfindinglib",
    "ImageProcessing": "the backend",
    "LoggerInterface": "wayfindinglib and the backend",
    "SATURATED_BLOB_MINIMUM_PIXELS": "wayfindinglib",
    "SATURATED_FRAME_FRACTION": "wayfindinglib",
    "StarIdentifier": "the backend",
    "background_job": "wayfindinglib",
    "capture_job_logs": "wayfindinglib and the backend",
    "classify_and_sort_fits_files": "wayfindinglib",
    "close_interrupted_jobs": "the backend",
    "connect_db": "wayfindinglib",
    "derive_field_centers": "wayfindinglib",
    "derive_target_sessions": "wayfindinglib",
    "file_lock": "wayfindinglib",
    "frame_is_spectral": "wayfindinglib and the backend",
    "get_current_job": "wayfindinglib",
    "parse_iso_time": "wayfindinglib",
    "safe_json_dumps": "wayfindinglib",
    "select_library_frames": "wayfindinglib",
}


def test_public_all_matches_the_pinned_name_set():  # ruff: ignore[missing-return-type-undocumented-public-function]
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


def test_every_public_name_actually_resolves():  # ruff: ignore[missing-return-type-undocumented-public-function]
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
        "catalog_access",
        "create",
        "delete",
        "delete_images",
        "get",
        "get_frame",
        "get_header",
        "imaged_field_centers",
        "list",
        "query",
        "read_saved",
        "reindex_frames",
        "save",
    },
    "stars": {
        "analyze_periodicity",
        "catalog_access",
        "create",
        "delete",
        "detect_point_sources",
        "find_or_create_by_position",
        "get",
        "query",
        "save_all",
        "tune_spectroscopy_calibration",
        "update",
    },
    "processing": {
        "acquire_analysis_slot",
        "acquire_stacking_slot",
        "calibration",
        "diagnostics",
        "discard_previous_stack",
        "process_target",
        "remake_preview",
        "restore_excluded_frames",
        "run_spectroscopy_by_session",
        "stack",
        "stack_summary",
        "swap_with_previous_stack",
    },
    "processing.calibration": {"add", "assess_flats", "get", "library", "query", "refresh", "save"},
    "processing.diagnostics": {
        "flag_value_outliers",
        "frame_quality",
        "spectral_frame_check",
        "spectral_registration_thresholds",
        "stack_quality",
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
        "catalog_access",
        "config",
        "jobs",
        "processing",
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
    assert astrometrics.processing.diagnostics._targets is astrometrics.targets
    assert astrometrics.processing.calibration._targets is astrometrics.targets
    assert astrometrics.visualization._stars is astrometrics.stars
