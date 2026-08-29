"""Guards the one list every other package in the repo depends on.

`astrometricslib/__init__.py` is the only door into this library that
anything outside it is supposed to use. The UI backend imports from
`astrometricslib` directly at 35 call sites and never reaches into a
submodule, and the Sphinx documentation only ever documents this
top-level namespace -- so the 47 names listed in `__all__` are, in a
very real sense, the entire contract this library makes with the rest
of the repository.

That contract is easy to break by accident during a refactor. Moving a
class to a new home, renaming it, or forgetting to re-export it after
splitting a module all look like internal cleanup, but any one of them
silently changes what `from astrometricslib import Whatever` gives you
-- or whether it works at all. Several of these names are also loaded
lazily through `__init__.py`'s `__getattr__`, specifically so that
importing `astrometricslib` does not have to pull in every heavy
dependency up front; a plain `import astrometricslib` does not exercise
those at all, so a broken lazy export can pass right by a casual smoke
test.

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
    "AbstractCatalogAccess",
    "AnalysisResult",
    "AppConfiguration",
    "AsteroidRecoveryCandidate",
    "Astrometrics",
    "AstrometryPipeline",
    "AstrometryPipelineQualityMetrics",
    "AstrometryQualitySummary",
    "BatchRunSummary",
    "CalibrationCatalog",
    "CatalogAccess",
    "DbLogHandler",
    "FileItem",
    "FilterType",
    "FitsHeaderEntry",
    "FrameRecord",
    "GroupedFrameStat",
    "ImageProcessing",
    "JobHandle",
    "LightCurve",
    "LoggerInterface",
    "MosaicInfo",
    "MovingObjectConfig",
    "MovingObjectRecovery",
    "PlotData",
    "ProcessingJob",
    "ProcessingPipelines",
    "QualityDiagnostics",
    "RenderedImage",
    "SpectralObservation",
    "StarIdentifier",
    "StellarCatalog",
    "StellarObject",
    "Target",
    "TargetCatalog",
    "TargetFilesResponse",
    "TargetSessionContribution",
    "VariableCandidate",
    "Visualization",
    "capture_job_logs",
    "classify_and_sort_fits_files",
    "derive_target_sessions",
    "get_configuration",
    "parse_coordinate_string",
    "registered_job",
    "resolve_worker_counts",
    "run_parallel_batch",
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

    Several exports are loaded lazily through `__init__.py`'s
    `__getattr__`, so a plain `import astrometricslib` does not prove
    they work -- only fetching each one by name does. This is what
    would have caught a rename that updated `__all__` but not the
    lazy-export table underneath it, or vice versa.
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
