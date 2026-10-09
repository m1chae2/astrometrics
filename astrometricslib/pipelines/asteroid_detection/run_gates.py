"""Builds the gate record for one asteroid-detection run.

Asteroid detection searches a target's frames for sources that move in a
straight line across them, then asks a database of known asteroids (SkyBoT)
whether each mover is already known. A run that finds nothing says very
little unless the search could actually have found something, so the checks
here ask: were enough frames searched, did every frame have pointing data,
could the known-asteroid database be asked, and did any mover stay unknown.

Each check is returned as a `GateResult`. A check that could not look (too
few frames to form a track, no candidate to look up) is ``not_checked``; it
is not a pass, and an empty result from it is not a clean null result.
"""

from collections.abc import Mapping

from astrometricslib.models.gate_result import GateResult, failed_gate, passed_gate, unchecked_gate

SEARCH_FRAMES_GATE_NAME = "search_frames"
POINTING_METADATA_GATE_NAME = "pointing_metadata"
EPHEMERIS_GATE_NAME = "ephemeris_cross_match"
UNMATCHED_MOVERS_GATE_NAME = "unmatched_movers"


def asteroid_run_gates(
    metrics: Mapping[str, int], minimum_frames_for_a_track: int, candidates_awaiting_recovery: int
) -> list[GateResult]:
    """Build the gates for one asteroid-detection run.

    Parameters
    ----------
    metrics : `Mapping` [`str`, `int`]
        The pipeline's counts for the run (its ``last_run_metrics``).
    minimum_frames_for_a_track : `int`
        The fewest frames a moving object must appear in to count (the
        ``min_frames_for_persistence`` setting).
    candidates_awaiting_recovery : `int`
        Movers confirmed as moving in a straight line but not matched to a
        known asteroid.

    Returns
    -------
    gates : `list` [`GateResult`]
        Four gates, in a fixed order.
    """
    gates: list[GateResult] = []
    searched = int(metrics.get("frames_with_wcs_estimate", 0))
    excluded = int(metrics.get("frames_excluded_missing_pointing_metadata", 0))

    track_source = "the min_frames_for_persistence setting"
    if searched < minimum_frames_for_a_track:
        gates.append(
            unchecked_gate(
                SEARCH_FRAMES_GATE_NAME,
                f"only {searched} frame(s) could be searched and a track needs {minimum_frames_for_a_track}, "
                "so no mover could have been found",
                track_source,
            )
        )
    else:
        gates.append(
            passed_gate(
                SEARCH_FRAMES_GATE_NAME, float(searched), float(minimum_frames_for_a_track), track_source
            )
        )

    if excluded > 0:
        gates.append(
            failed_gate(
                POINTING_METADATA_GATE_NAME,
                f"{excluded} frame(s) excluded for missing RA/DEC/NAXIS pointing metadata",
                float(excluded),
                0.0,
            )
        )
    elif searched == 0:
        gates.append(unchecked_gate(POINTING_METADATA_GATE_NAME, "no frame was considered"))
    else:
        gates.append(passed_gate(POINTING_METADATA_GATE_NAME, 0.0, 0.0))

    confirmed = int(metrics.get("candidates_rate_linearity_confirmed", 0))
    attempted = int(metrics.get("ephemeris_queries_attempted", 0))
    failed = int(metrics.get("ephemeris_queries_failed", 0))
    matched = int(metrics.get("candidates_ephemeris_matched", 0))
    ephemeris_source = "one SkyBoT cone search of the field"
    if confirmed == 0:
        gates.append(
            unchecked_gate(EPHEMERIS_GATE_NAME, "no mover reached the known-asteroid check", ephemeris_source)
        )
    elif attempted == 0:
        gates.append(
            unchecked_gate(
                EPHEMERIS_GATE_NAME, "the known-asteroid database was never asked", ephemeris_source
            )
        )
    elif failed > 0:
        gates.append(
            failed_gate(
                EPHEMERIS_GATE_NAME,
                "the known-asteroid database could not be reached, so confirmed movers were not "
                "checked against known asteroids",
                float(failed),
                0.0,
                ephemeris_source,
            )
        )
    else:
        gates.append(
            passed_gate(
                EPHEMERIS_GATE_NAME,
                float(matched),
                limit_source=ephemeris_source,
                detail=f"{matched} of {confirmed} confirmed mover(s) matched a known asteroid",
            )
        )

    if confirmed == 0:
        gates.append(unchecked_gate(UNMATCHED_MOVERS_GATE_NAME, "no mover was confirmed"))
    elif candidates_awaiting_recovery > 0:
        gates.append(
            failed_gate(
                UNMATCHED_MOVERS_GATE_NAME,
                f"{candidates_awaiting_recovery} candidate(s) confirmed as movers but not "
                "matched to a known body -- worth a manual look",
                float(candidates_awaiting_recovery),
                0.0,
            )
        )
    else:
        gates.append(passed_gate(UNMATCHED_MOVERS_GATE_NAME, 0.0, 0.0))
    return gates
