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
resolves to something. Together they are the one test a large,
multi-step reorganization of this library's internal layers can be
checked against without re-deriving, each time, whether the outside
world would notice.
"""

import astrometricslib

# The public surface as it exists today. Changing this set on purpose
# -- adding, removing, or renaming an export -- is a real, visible
# change to the library's contract with the rest of the repo, so it
# should be a deliberate edit to this list, not a side effect of moving
# code around internally.
EXPECTED_PUBLIC_NAMES = frozenset({
    "DEFAULT_DARK_TEMPERATURE_TOLERANCE_C",
    "RPC_CODES",
    "SATURATED_BLOB_MINIMUM_PIXELS",
    "SATURATED_FRAME_FRACTION",
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
    "AstrometryPipeline",
    "AstrometryPipelineQualityMetrics",
    "AstrometryQualitySummary",
    "BatchRunSummary",
    "Butler",
    "CalibrationCatalog",
    "CatalogAccess",
    "Collection",
    "ConfigFile",
    "ConfigFileDescription",
    "ConfigurationError",
    "ConflictError",
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
    "InvalidArgumentError",
    "JobHandle",
    "Jobs",
    "LoggerInterface",
    "MovingObjectConfig",
    "MovingObjectRecovery",
    "NotFoundError",
    "NumpyEncoder",
    "Parameter",
    "ParameterDescription",
    "PermissionDeniedError",
    "PhotometryResult",
    "PlateSolveFailedError",
    "PlotData",
    "ProcessingError",
    "ProcessingJob",
    "ProcessingPipelines",
    "ProvenanceStore",
    "QualityDiagnostics",
    "RenderedImage",
    "SpectroscopyResult",
    "StarIdentifier",
    "StellarCatalog",
    "StellarObject",
    "StorageError",
    "StorageNotMountedError",
    "Target",
    "TargetCatalog",
    "TargetFilesResponse",
    "TargetSessionContribution",
    "UsageDescription",
    "Used",
    "ValueDescription",
    "ValueEntity",
    "VariableCandidate",
    "Visualization",
    "WasAssociatedWith",
    "WasAttributedTo",
    "WasConfiguredBy",
    "WasGeneratedBy",
    "acquire_resource_slot",
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
    "get_log_context",
    "is_path_inside",
    "log_context",
    "new_request_id",
    "observing_night_id",
    "parse_coordinate_string",
    "parse_iso_time",
    "registered_job",
    "require_mounted_storage",
    "resolve_camera_profile",
    "resolve_mounted_path",
    "resolve_worker_counts",
    "run_parallel_batch",
    "run_siril_stack",
    "safe_json_dumps",
    "select_library_frames",
    "stack_frames",
    "to_error_info",
})


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
