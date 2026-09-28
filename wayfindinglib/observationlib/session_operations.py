"""Purpose: Quality-data conduit between ObservationSession and TargetSession.

Description: find_quality_contributions_for_session follows an
ObservationSession's target_session_id reference to read astrometricslib's
quality records directly -- the architecture discussion's stated conduit
between the two libraries, requiring no separate API since the data shape
(TargetSessionContribution, on every PipelineQualitySummaryBase subclass)
already exists.
"""

from typing import NamedTuple

from astrometricslib import Astrometrics, TargetSessionContribution


# Every place on a Target that might carry a quality summary with a
# matching target_session_breakdown entry -- kept as an explicit list
# rather than introspecting Target's fields, since only these five are
# quality summaries.
def _quality_summaries(target) -> list:  # ruff: ignore[missing-type-function-argument]
    """List every quality summary a target currently carries.

    Returns
    -------
    summaries : `list`
        The non-`None` quality summaries found on `target`.
    """
    candidates = (
        target.stacking.quality_summary,
        target.spectral_stacking.quality_summary,
        target.quality.astrometry,
        target.quality.photometry,
        target.quality.spectroscopy,
    )
    return [summary for summary in candidates if summary is not None]


class QualityContribution(NamedTuple):
    """One pipeline summary's contribution to a given TargetSession."""

    target_id: str
    pipeline_name: str
    contribution: TargetSessionContribution


def find_quality_contributions_for_session(
    target_session_id: str,
    app_config=None,  # ruff: ignore[missing-type-function-argument]
) -> list[QualityContribution]:
    """Find every quality-summary contribution recorded for a TargetSession.

    Scans every recorded Target's quality summaries for a
    target_session_breakdown entry matching target_session_id -- the
    quality-data conduit an ObservationSession's target_session_id
    reference is meant to follow.

    Parameters
    ----------
    target_session_id : `str`
        The `TargetSession.id` to look up contributions for.
    app_config : `AppConfiguration`, optional
        Application configuration object. If `None` (default), the
        process-wide singleton from `get_configuration` is used.

    Returns
    -------
    contributions : `list` [`QualityContribution`]
        One entry per matching (target, pipeline) quality-summary
        contribution found.
    """
    contributions: list[QualityContribution] = []
    for target in Astrometrics(app_config).targets.list():
        for summary in _quality_summaries(target):
            for entry in summary.target_session_breakdown:
                if entry.session_id == target_session_id:
                    contributions.append(
                        QualityContribution(
                            target_id=target.id, pipeline_name=summary.pipeline_name, contribution=entry
                        )
                    )
    return contributions
