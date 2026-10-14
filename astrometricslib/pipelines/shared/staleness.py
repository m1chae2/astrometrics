"""Whether a star's own result still reflects the pipeline's current version.

`PipelineQualitySummaryBase.pipeline_version` already tells a caller
whether a whole run is stale (compare it to the live `*_PIPELINE_VERSION`
constant directly -- no helper needed). What is missing is the same
check *per star*: one star's `PhotometryResult` might be three pipeline
versions old while its `SpectroscopyResult` was just regenerated. These
functions answer that, one domain at a time, by looking up the run that
produced a given result through `ProvenanceStore` and comparing the
`Agent` it was associated with against the current version.
"""

import logging

from astrometricslib.drivers.provenance_store import ProvenanceStore
from astrometricslib.models.astrometry_quality import CatalogMatchQuality
from astrometricslib.models.quality_summary import (
    ASTROMETRY_PIPELINE_VERSION,
    PHOTOMETRY_PIPELINE_VERSION,
    SPECTROSCOPY_PIPELINE_VERSION,
)
from astrometricslib.models.stellar_source import PhotometryResult, SpectroscopyResult

logger = logging.getLogger(__name__)


def is_result_stale(
    generated_by_job_id: str | None, current_version: str, store: ProvenanceStore
) -> bool | None:
    """Check whether the run that produced a result is behind the live version.

    Three-valued on purpose: a result with no recorded provenance --
    either a legacy result saved before this system existed, or one
    from a run whose `job_id` was `None` -- is neither known-fresh nor
    known-stale, so it reads as unknown rather than silently `False`.

    Parameters
    ----------
    generated_by_job_id : `str` or `None`
        The result's own `generated_by_job_id`. `None` means unknown.
    current_version : `str`
        The pipeline's live `*_PIPELINE_VERSION` constant.
    store : `ProvenanceStore`
        Where to look up the producing run's agent.

    Returns
    -------
    is_stale : `bool` or `None`
        `None` when the producing run or its agent cannot be found.
        Otherwise `True` when the producing agent's version differs
        from `current_version`, `False` when it matches.
    """
    if generated_by_job_id is None:
        return None
    agent = store.get_agent_for_activity(generated_by_job_id)
    if agent is None:
        return None
    # Agent ids are "agent:<pipeline_name>:<pipeline_version>" --
    # `record_pipeline_run`'s own scheme -- so the version is the last
    # colon-separated segment, more robust to parse than the display name.
    agent_version = agent.id.rsplit(":", 1)[-1]
    return agent_version != current_version


def photometry_result_is_stale(result: PhotometryResult, store: ProvenanceStore) -> bool | None:
    """Check whether a star's `PhotometryResult` is behind the live pipeline.

    Returns
    -------
    is_stale : `bool` or `None`
        See `is_result_stale`.
    """
    return is_result_stale(result.generated_by_job_id, PHOTOMETRY_PIPELINE_VERSION, store)


def spectroscopy_result_is_stale(result: SpectroscopyResult, store: ProvenanceStore) -> bool | None:
    """Check whether a star's `SpectroscopyResult` is behind the live pipeline.

    Returns
    -------
    is_stale : `bool` or `None`
        See `is_result_stale`.
    """
    return is_result_stale(result.generated_by_job_id, SPECTROSCOPY_PIPELINE_VERSION, store)


def catalog_match_quality_is_stale(result: CatalogMatchQuality, store: ProvenanceStore) -> bool | None:
    """Check whether a star's `CatalogMatchQuality` is behind the live version.

    Returns
    -------
    is_stale : `bool` or `None`
        See `is_result_stale`.
    """
    return is_result_stale(result.generated_by_job_id, ASTROMETRY_PIPELINE_VERSION, store)
