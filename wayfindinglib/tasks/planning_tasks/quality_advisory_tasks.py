"""Purpose: Build the quality advisory for one target.

Description: `ObservationPlanning.get_advisory(kind="quality")` uses this
to sum up what the science library already knows about a target. It is
computed on demand and never stored. It reads only fields already on the
target's record: the ``flagged`` and ``flag_reasons`` pair that every
pipeline quality summary carries, and the asteroid candidate counts (a
candidate counts as confirmed once it matches a known orbit). The
variable-star count is always 0 for now, because the star catalog cannot
yet list a target's stars cheaply.
"""

from astrometricslib import Target
from wayfindinglib.models.planning.quality_advisory import (
    QualityFlagSummary,
    ScienceOutcomeSummary,
    TargetQualityAdvisory,
)


def build_target_quality_advisory(target: Target) -> TargetQualityAdvisory:
    """Build a `TargetQualityAdvisory` from a target's existing science record.

    Parameters
    ----------
    target : `Target`
        The library target to build the advisory for.

    Returns
    -------
    advisory : `TargetQualityAdvisory`
        Quality flags per pipeline and science outcome counts.
    """
    quality_flags = []
    for pipeline_name, summary in (
        ("stacking", target.stacking.quality_summary),
        ("spectral_stacking", target.spectral_stacking.quality_summary),
        ("astrometry", target.quality.astrometry),
        ("photometry", target.quality.photometry),
        ("spectroscopy", target.quality.spectroscopy),
        ("asteroid_detection", target.asteroid_detection.quality_summary),
    ):
        if summary is not None:
            quality_flags.append(
                QualityFlagSummary(
                    pipeline_name=pipeline_name,
                    flagged=summary.flagged,
                    flag_reasons=list(summary.flag_reasons),
                )
            )

    asteroid_candidate_count = len(target.asteroid_detection.candidates)
    confirmed_asteroid_candidate_count = sum(
        1 for c in target.asteroid_detection.candidates if c.cascade_stage.value == "ephemeris_matched"
    )

    return TargetQualityAdvisory(
        target_id=target.id,
        quality_flags=quality_flags,
        science_outcomes=ScienceOutcomeSummary(
            variable_star_candidate_count=0,  # deferred -- see module docstring
            asteroid_candidate_count=asteroid_candidate_count,
            confirmed_asteroid_candidate_count=confirmed_asteroid_candidate_count,
        ),
    )
