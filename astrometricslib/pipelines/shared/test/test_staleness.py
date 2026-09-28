"""Tests for the tri-valued per-result staleness check.

`is_result_stale` and its three per-domain wrappers must distinguish
"no provenance recorded" (`None`, unknown) from a genuine fresh/stale
verdict -- a legacy result predating this system is not the same thing
as a result known to be current.
"""

from astrometricslib.drivers.provenance_store import ProvenanceStore
from astrometricslib.models.astrometry_quality import CatalogMatchQuality
from astrometricslib.models.provenance import Activity, Agent, WasAssociatedWith
from astrometricslib.models.stellar_source import PhotometryResult, SpectroscopyResult
from astrometricslib.pipelines.shared.staleness import (
    catalog_match_quality_is_stale,
    is_result_stale,
    photometry_result_is_stale,
    spectroscopy_result_is_stale,
)


def make_store(tmp_path) -> ProvenanceStore:  # ruff: ignore[missing-type-function-argument]
    """Build a store backed by a throwaway database.

    Returns
    -------
    store : `ProvenanceStore`
        A fresh store with its tables already created.
    """
    return ProvenanceStore(str(tmp_path / "provenance.db"))


def record_run(store: ProvenanceStore, job_id: str, pipeline_name: str, pipeline_version: str) -> None:
    """Record a minimal activity/agent pair, matching `record_pipeline_run`."""
    agent_id = f"agent:{pipeline_name}:{pipeline_version}"
    store.record_agent(Agent(id=agent_id, name=f"astrometricslib.{pipeline_name} v{pipeline_version}"))
    store.record_activity(Activity(id=job_id, name=pipeline_name), target_id="M13")
    store.record_was_associated_with(job_id, WasAssociatedWith(agent=agent_id))


def test_is_result_stale_is_unknown_with_no_job_id(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Check a result with no recorded provenance reads as unknown."""
    store = make_store(tmp_path)
    assert is_result_stale(None, "1.2.0", store) is None


def test_is_result_stale_is_unknown_when_the_activity_has_no_agent(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Check a job id with no resolvable agent also reads as unknown."""
    store = make_store(tmp_path)
    assert is_result_stale("job-does-not-exist", "1.2.0", store) is None


def test_is_result_stale_is_false_when_the_version_matches(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Check a result from the current pipeline version reads as fresh."""
    store = make_store(tmp_path)
    record_run(store, "job-1", "photometry", "1.2.0")
    assert is_result_stale("job-1", "1.2.0", store) is False


def test_is_result_stale_is_true_when_the_version_differs(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Check a result from an older pipeline version reads as stale."""
    store = make_store(tmp_path)
    record_run(store, "job-1", "photometry", "1.2.0")
    assert is_result_stale("job-1", "1.3.0", store) is True


def test_photometry_result_is_stale_reads_its_own_generated_by_job_id(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Check the photometry wrapper compares against the live version."""
    from astrometricslib.models.quality_summary import PHOTOMETRY_PIPELINE_VERSION

    store = make_store(tmp_path)
    record_run(store, "job-1", "photometry", PHOTOMETRY_PIPELINE_VERSION)
    result = PhotometryResult(generated_by_job_id="job-1")
    assert photometry_result_is_stale(result, store) is False
    assert photometry_result_is_stale(PhotometryResult(), store) is None


def test_spectroscopy_result_is_stale_reads_its_own_generated_by_job_id(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Check the spectroscopy wrapper compares against the live version."""
    from astrometricslib.models.quality_summary import SPECTROSCOPY_PIPELINE_VERSION

    store = make_store(tmp_path)
    record_run(store, "job-1", "spectroscopy", "0.9.0")
    result = SpectroscopyResult(generated_by_job_id="job-1")
    assert spectroscopy_result_is_stale(result, store) is True
    assert SPECTROSCOPY_PIPELINE_VERSION != "0.9.0"


def test_catalog_match_quality_is_stale_reads_its_own_generated_by_job_id(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Check the astrometry wrapper compares against the live version."""
    from astrometricslib.models.quality_summary import ASTROMETRY_PIPELINE_VERSION

    store = make_store(tmp_path)
    record_run(store, "job-1", "astrometry", ASTROMETRY_PIPELINE_VERSION)
    result = CatalogMatchQuality(generated_by_job_id="job-1")
    assert catalog_match_quality_is_stale(result, store) is False
