"""Purpose: Gather one night's data for the session-quality analysis.

Description: Reads the stored samples, guiding runs and session records for
one observing night and shapes them into the request the pure analysis needs.
The analysis stays free of storage (see `wayfindinglib/session_analysis/`);
this module is the only place that knows where the data lives.

It also decides how far the limits apply to the night. The limits are worked
out for the equipment in use now. A past night may have used other equipment,
and a limit for one setup says nothing reliable about another, so each night
is matched against the current setup and the match level travels with the
analysis.
"""

import statistics
from collections.abc import Iterable, Sequence
from typing import Any

from wayfindinglib.models.equipment_and_site.equipment_fingerprint import build_equipment_fingerprint
from wayfindinglib.models.session.ekos_session import EkosSessionContext
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.models.session.telemetry import MEASURED_GUIDING_SAMPLE_SOURCES

_SAMPLE_READ_LIMIT = 200000
"""Most samples read for one night. A long night holds about 10,000."""

_GUIDE_FINGERPRINT_FIELDS = 2
"""The last two fields of a fingerprint describe the guide optics."""


def _guide_part(fingerprint: str) -> str:
    """Take the guide-optics part of an equipment fingerprint.

    Returns
    -------
    guide_part : `str`
        The focal length and plate scale fields.
    """
    return "|".join(fingerprint.split("|")[-_GUIDE_FINGERPRINT_FIELDS:])


def equipment_match_level(
    active_fingerprint: str | None, night_fingerprints: Iterable[str], runs: Sequence[GuidingRunSummary]
) -> str:
    """Decide how far the current limits apply to a past night.

    Parameters
    ----------
    active_fingerprint : `str` or `None`
        Fingerprint of the equipment in use now, or `None` if no equipment
        is active.
    night_fingerprints : `Iterable` [`str`]
        Fingerprints of the night's session records.
    runs : `Sequence` [`GuidingRunSummary`]
        The night's guiding runs, which record the guide optics used.

    Returns
    -------
    level : `str`
        ``"exact"`` if a session that night used the current setup.
        ``"guide_optics_only"`` if the guide optics match but the imaging
        equipment is unknown or different. ``"none"`` otherwise, so no limit
        is applied.
    """
    if active_fingerprint is None:
        return "none"
    if active_fingerprint in set(night_fingerprints):
        return "exact"
    active_guide = _guide_part(active_fingerprint)
    for run in runs:
        run_guide = _guide_part(
            build_equipment_fingerprint(None, None, run.focal_length_mm, run.pixel_scale_arcsec_per_px)
        )
        if run_guide == active_guide:
            return "guide_optics_only"
    return "none"


def measured_night_samples(records: Any, session_id: str) -> list[dict[str, Any]]:
    """Read one night's measured guide samples.

    Returns
    -------
    samples : `list` [`dict`]
        Samples measured from a real guide star, oldest first. Estimates
        are never returned.
    """
    measured_sources = [source.value for source in MEASURED_GUIDING_SAMPLE_SOURCES]
    return records.get_guiding_samples(
        session_id=session_id, limit=_SAMPLE_READ_LIMIT, sources=measured_sources
    )


def guide_scale_agreement(contexts: Iterable[EkosSessionContext]) -> bool | None:
    """Combine the sessions' checks of the guide log against the configuration.

    Returns
    -------
    agrees : `bool` or `None`
        `False` if any session's log disagreed with the configuration,
        `True` if at least one agreed and none disagreed, `None` if none
        could be compared.
    """
    verdicts = [
        context.equipment.guide_scale_matches_configuration
        for context in contexts
        if context.equipment is not None and context.equipment.guide_scale_matches_configuration is not None
    ]
    if not verdicts:
        return None
    return all(verdicts)


def logged_guide_scale(runs: Sequence[GuidingRunSummary]) -> float | None:
    """Find the guide plate scale the night's logs recorded.

    Returns
    -------
    scale : `float` or `None`
        The most common plate scale among the runs, in arcseconds per pixel.
    """
    scales = [run.pixel_scale_arcsec_per_px for run in runs if run.pixel_scale_arcsec_per_px is not None]
    return statistics.mode(scales) if scales else None
