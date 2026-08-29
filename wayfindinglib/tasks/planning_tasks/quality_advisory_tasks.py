"""Purpose: Target Quality Advisory Computation.

Description: Computed on demand from astrometricslib's public
high-level interface, never recorded
(`Wayfinding_Library_Architecture.md` §2.3.2). Reads
only fields already present on the target's science-side record: the
`flagged`/`flag_reasons` pair common to every pipeline quality summary,
and asteroid candidate counts (a candidate counts as confirmed once its
cascade stage reaches ephemeris match). The variable-star
cross-reference is deferred -- the science library's own stellar-object
listing does not yet filter by target identifier, and carrying a
workaround for that gap into this library's v1 was judged not worthwhile
(`Wayfinding_Library_Architecture.md` §2.3.2, §4).
"""

from wayfindinglib.models.planning.quality_advisory import (
    QualityFlagSummary,
    ScienceOutcomeSummary,
    TargetQualityAdvisory,
)


def build_target_quality_advisory(astrometrics, target_id: str) -> TargetQualityAdvisory:  # ruff: ignore[missing-type-function-argument]
    """Build a `TargetQualityAdvisory` from a target's existing science record.

    Parameters
    ----------
    astrometrics : `Any`
        The science library's public high-level interface.
    target_id : `str`
        The target to build the advisory for.

    Returns
    -------
    advisory : `TargetQualityAdvisory`
        Quality flags per pipeline and science outcome counts.

    Raises
    ------
    ValueError
        Raised if `target_id` does not resolve to an existing target.
    """
    target = astrometrics.targets.get(target_id)
    if not target:
        raise ValueError(f"Target {target_id} not found")

    quality_flags = []
    for pipeline_name, summary in (
        ("stacking", target.stack_quality_summary),
        ("spectral_stacking", target.spectral_stack_quality_summary),
        ("astrometry", target.astrometry_quality_summary),
        ("photometry", target.photometry_quality_summary),
        ("spectroscopy", target.spectroscopy_quality_summary),
        ("asteroid_recovery", target.asteroid_recovery_quality_summary),
    ):
        if summary is not None:
            quality_flags.append(
                QualityFlagSummary(
                    pipeline_name=pipeline_name,
                    flagged=summary.flagged,
                    flag_reasons=list(summary.flag_reasons),
                )
            )

    asteroid_candidate_count = len(target.asteroid_candidates)
    confirmed_asteroid_candidate_count = sum(
        1 for c in target.asteroid_candidates if c.cascade_stage.value == "ephemeris_matched"
    )

    return TargetQualityAdvisory(
        target_id=target_id,
        quality_flags=quality_flags,
        science_outcomes=ScienceOutcomeSummary(
            variable_star_candidate_count=0,  # deferred -- see module docstring
            asteroid_candidate_count=asteroid_candidate_count,
            confirmed_asteroid_candidate_count=confirmed_asteroid_candidate_count,
        ),
    )
