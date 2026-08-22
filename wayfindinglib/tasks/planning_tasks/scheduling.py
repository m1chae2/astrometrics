"""Purpose: Observation Package Placement.

Description: Implements the seven-step placement algorithm of
`Wayfinding_Library_Architecture.md` Table 4 /
`Wayfinding_Library_Architecture.md` §2.3.2: resolve the night
window, split by disposition, place fixed-time
anchors, apply advisory priority boosts, greedily place soonest-available
packages, diagnose every unplaced package against the full night, and
assemble the resulting `ObservationSession`.

Two calling-convention details are not fully pinned by the sequence
diagrams and are resolved here as documented implementation decisions:

- `ObservationPackage` carries no `start_time_mode` -- only
  `QueuedObservationPackage`, the *placed* instance, does. Disposition
  must therefore be supplied per package at placement time, alongside
  the package itself, matching how `add_to_queue` already takes
  `start_time_mode` as a separate parameter
  (`Wayfinding_Library_Sequences.md` §1.6). `PlacementRequest` below is
  that pairing -- a plain, unpersisted parameter object, not a new
  Foundation model, since nothing about it needs to survive past one
  `place()` call.
- `ObservationSession.camera_id` is required (Appendix B), but no
  sequence diagram threads a camera into `plan_observation_session`.
  Since the active camera plays no role in placement math -- only the
  telescope's pointing envelope and the site's obstructions do -- it is
  accepted as a plain identifier argument here, recorded for
  "Reproducible Placement Context" without influencing the algorithm.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from wayfindinglib.models.equipment_and_site.equipment import Telescope
from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
from wayfindinglib.models.planning.observation_package import ObservationPackage
from wayfindinglib.models.planning.planning_config import PlanningConfig
from wayfindinglib.models.planning.quality_advisory import TargetQualityAdvisory
from wayfindinglib.models.session.observation_session import (
    InfeasibilityReason,
    InfeasibilityReasonCode,
    ObservationSession,
    QueuedObservationPackage,
    SessionStatus,
    StartTimeMode,
)
from wayfindinglib.tasks.planning_tasks import visibility_tasks

_DEFAULT_STEP_MINUTES = 5


@dataclass
class PlacementRequest:
    """One observation package plus the starting disposition requested."""

    package: ObservationPackage
    start_time_mode: StartTimeMode
    requested_start_time: datetime | None = None
    ra_deg: float = 0.0
    dec_deg: float = 0.0


def _target_coordinates(astrometrics, target_id: str) -> tuple[float, float]:  # ruff: ignore[missing-type-function-argument]
    """Resolve a target's RA/Dec in decimal degrees, live, never cached.

    Per the "Live Target Resolution" invariant
    (`Wayfinding_Library_Architecture.md` §2.3.4).

    Returns
    -------
    ra_deg, dec_deg : `tuple` [`float`, `float`]
        The target's resolved RA/Dec, in decimal degrees.

    Raises
    ------
    ValueError
        Raised if `target_id` does not resolve to an existing target.
    """
    from astrometricslib import parse_coordinate_string

    target = astrometrics.targets.get(target_id)
    if not target:
        raise ValueError(f"Target {target_id} not found")
    return parse_coordinate_string(target.ra, is_ra=True), parse_coordinate_string(target.dec, is_ra=False)


def _apply_advisory_boost(
    request: PlacementRequest,
    quality_advisories: dict[str, TargetQualityAdvisory],
    planning_config: PlanningConfig,
) -> int:
    """Compute the bounded priority boost for one opted-in package.

    Never a decrease: a package that has not opted in, or whose target
    has no qualifying flag or outcome, receives a boost of zero
    (`Wayfinding_Library_Architecture.md` §2.3.4, "Advisory, Not Authority").

    Returns
    -------
    boost : `int`
        The bounded priority boost, zero if not opted in or unqualified.
    """
    if not request.package.quality_weighting_enabled:
        return 0
    advisory = quality_advisories.get(request.package.target_id)
    if advisory is None:
        return 0
    boost = 0
    if advisory.has_any_flagged():
        boost += planning_config.flagged_quality_priority_boost
    if (
        advisory.science_outcomes.confirmed_asteroid_candidate_count > 0
        or advisory.science_outcomes.variable_star_candidate_count > 0
    ):
        boost += planning_config.science_outcome_priority_boost
    return boost


def _diagnose_infeasibility(
    request: PlacementRequest,
    site_profile: SiteProfile,
    telescope: Telescope,
    window_start: datetime,
    window_end: datetime,
    effective_floor_deg: float,
    step: timedelta,
) -> InfeasibilityReasonCode:
    """Diagnose why a package could not be placed, against the full night.

    Distinguishes the four automated-placement outcomes by checking, in
    order: does it ever clear the altitude floor at all; if so, does it
    ever clear while also outside every avoidance zone; if so, is there
    a continuous span long enough; if a long-enough span exists in the
    full night (ignoring what other placements consumed), the package
    was crowded out rather than impossible.

    Returns
    -------
    reason_code : `InfeasibilityReasonCode`
        The specific reason the package could not be placed.
    """
    duration_sec = request.package.total_duration_sec()

    if not visibility_tasks.ever_clears_floor(
        request.ra_deg,
        request.dec_deg,
        site_profile,
        telescope,
        window_start,
        window_end,
        effective_floor_deg,
        step,
    ):
        return InfeasibilityReasonCode.NEVER_CLEARS_ALTITUDE

    span = visibility_tasks.find_earliest_visible_window(
        request.ra_deg,
        request.dec_deg,
        site_profile,
        telescope,
        window_start,
        window_end,
        effective_floor_deg,
        duration_sec,
        step,
    )
    if span is not None:
        return InfeasibilityReasonCode.NIGHT_FULLY_COMMITTED

    # It clears the floor somewhere, and no continuous span of sufficient
    # length exists anywhere in the full night. Distinguish "always inside
    # an avoidance zone when it clears" from "clears cleanly, just briefly"
    # by checking each sampled instant independently (ever_visible), not by
    # searching for a continuous run: find_earliest_visible_window cannot
    # detect a single isolated clear instant, since a run's first sample
    # always has zero elapsed time from itself.
    if not visibility_tasks.ever_visible(
        request.ra_deg,
        request.dec_deg,
        site_profile,
        telescope,
        window_start,
        window_end,
        effective_floor_deg,
        step,
    ):
        return InfeasibilityReasonCode.BLOCKED_BY_AVOIDANCE_ZONE
    return InfeasibilityReasonCode.WINDOW_TOO_SHORT


def place(
    requests: list[PlacementRequest],
    night_window: tuple[datetime, datetime],
    site_profile: SiteProfile,
    telescope: Telescope,
    quality_advisories: dict[str, TargetQualityAdvisory] | None = None,
    planning_config: PlanningConfig | None = None,
) -> tuple[list[QueuedObservationPackage], list[InfeasibilityReason]]:
    """Place every requested package into the night window.

    Parameters
    ----------
    requests : `list` [`PlacementRequest`]
        Every package requested for the night, each with its resolved
        target coordinates and starting disposition.
    night_window : `tuple` [`datetime`, `datetime`]
        The `(dusk, dawn)` window from `night_window.resolve_night_window`.
    site_profile : `SiteProfile`
        The observing site.
    telescope : `Telescope`
        The active telescope, supplying the pointing envelope.
    quality_advisories : `dict` [`str`, `TargetQualityAdvisory`], optional
        Pre-fetched advisories keyed by target id, consulted for
        packages with `quality_weighting_enabled`.
    planning_config : `PlanningConfig`, optional
        Supplies the time step and advisory boost magnitudes.

    Returns
    -------
    queue, diagnostics : `tuple` [`list` [`QueuedObservationPackage`],
        `list` [`InfeasibilityReason`]]
        Every successfully placed entry, and a specific diagnosis for
        every package that could not be placed.
    """
    planning_config = planning_config or PlanningConfig()
    quality_advisories = quality_advisories or {}
    step = timedelta(minutes=planning_config.night_window_time_step_min)
    window_start, window_end = night_window

    fixed_requests = [r for r in requests if r.start_time_mode == StartTimeMode.FIXED]
    soonest_requests = [r for r in requests if r.start_time_mode == StartTimeMode.SOONEST]

    queue: list[QueuedObservationPackage] = []
    diagnostics: list[InfeasibilityReason] = []
    occupied: list[tuple[datetime, datetime]] = []

    # --- Step 3: Fixed-Time Placement ---
    fixed_intervals: list[tuple[datetime, datetime, PlacementRequest]] = []
    for request in fixed_requests:
        duration_sec = request.package.total_duration_sec()
        start = request.requested_start_time
        end = start + timedelta(seconds=duration_sec)
        effective_floor = visibility_tasks.effective_altitude_floor(
            telescope, request.package.minimum_altitude_deg
        )
        continuously_visible = all(
            visibility_tasks.visibility_state_at(
                request.ra_deg, request.dec_deg, site_profile, telescope, t, effective_floor
            )[0]
            for t in _sample_points(start, end, step)
        )
        if not continuously_visible:
            diagnostics.append(
                InfeasibilityReason(
                    observation_package_id=request.package.id,
                    reason_code=InfeasibilityReasonCode.FIXED_TIME_CONFLICT,
                    detail="Target is not observable for the full requested duration at the requested time.",
                )
            )
            continue
        fixed_intervals.append((start, end, request))

    # Detect pairwise overlaps among otherwise-valid fixed requests.
    conflicting_ids: set[str] = set()
    for i, (start_a, end_a, req_a) in enumerate(fixed_intervals):
        for start_b, end_b, req_b in fixed_intervals[i + 1 :]:
            if start_a < end_b and start_b < end_a:
                conflicting_ids.add(req_a.package.id)
                conflicting_ids.add(req_b.package.id)

    for start, end, request in fixed_intervals:
        if request.package.id in conflicting_ids:
            diagnostics.append(
                InfeasibilityReason(
                    observation_package_id=request.package.id,
                    reason_code=InfeasibilityReasonCode.FIXED_TIME_CONFLICT,
                    detail="Overlaps another fixed-time request.",
                )
            )
            continue
        queue.append(_build_queue_entry(request, start, end, applied_boost=0))
        occupied.append((start, end))

    # --- Step 4: Advisory-Informed Priority ---
    boosts: dict[str, int] = {
        r.package.id: _apply_advisory_boost(r, quality_advisories, planning_config) for r in soonest_requests
    }

    # --- Step 5: Greedy Placement ---
    remaining = list(soonest_requests)
    while remaining:
        best_request: PlacementRequest | None = None
        best_start: datetime | None = None
        for request in remaining:
            duration_sec = request.package.total_duration_sec()
            effective_floor = visibility_tasks.effective_altitude_floor(
                telescope, request.package.minimum_altitude_deg
            )
            candidate = _earliest_unoccupied_visible_start(
                request,
                site_profile,
                telescope,
                window_start,
                window_end,
                effective_floor,
                duration_sec,
                step,
                occupied,
            )
            if candidate is None:
                continue
            if (
                best_start is None
                or candidate < best_start
                or (
                    candidate == best_start
                    and best_request is not None
                    and (request.package.priority + boosts[request.package.id])
                    > (best_request.package.priority + boosts[best_request.package.id])
                )
            ):
                best_request, best_start = request, candidate

        if best_request is None:
            break

        duration_sec = best_request.package.total_duration_sec()
        end = best_start + timedelta(seconds=duration_sec)
        queue.append(
            _build_queue_entry(best_request, best_start, end, applied_boost=boosts[best_request.package.id])
        )
        occupied.append((best_start, end))
        remaining.remove(best_request)

    # --- Step 6: Infeasibility Diagnosis ---
    for request in remaining:
        effective_floor = visibility_tasks.effective_altitude_floor(
            telescope, request.package.minimum_altitude_deg
        )
        reason_code = _diagnose_infeasibility(
            request, site_profile, telescope, window_start, window_end, effective_floor, step
        )
        diagnostics.append(
            InfeasibilityReason(
                observation_package_id=request.package.id,
                reason_code=reason_code,
                detail=_detail_for(reason_code),
            )
        )

    return queue, diagnostics


def _detail_for(reason_code: InfeasibilityReasonCode) -> str:
    """Return a human-readable detail string for one infeasibility code.

    Returns
    -------
    detail : `str`
        A human-readable detail string for `reason_code`.
    """
    return {
        InfeasibilityReasonCode.NEVER_CLEARS_ALTITUDE: (
            "Target does not rise above the effective altitude floor at any point in the night window."
        ),
        InfeasibilityReasonCode.BLOCKED_BY_AVOIDANCE_ZONE: (
            "Target clears the altitude floor only while within a site avoidance zone."
        ),
        InfeasibilityReasonCode.WINDOW_TOO_SHORT: (
            "A clear window exists but is shorter than the package's total duration."
        ),
        InfeasibilityReasonCode.NIGHT_FULLY_COMMITTED: (
            "A sufficient window existed in the full night but was consumed by earlier placements."
        ),
        InfeasibilityReasonCode.FIXED_TIME_CONFLICT: "Fixed-time placement failed.",
    }[reason_code]


def _sample_points(start: datetime, end: datetime, step: timedelta) -> list[datetime]:
    """Return sample points from `start` to `end` inclusive, at `step`.

    Returns
    -------
    points : `list` [`datetime`]
        Sample points from `start` to `end` inclusive, at `step`.
    """
    points = []
    t = start
    while t <= end:
        points.append(t)
        t += step
    if points[-1] != end:
        points.append(end)
    return points


def _earliest_unoccupied_visible_start(
    request: PlacementRequest,
    site_profile: SiteProfile,
    telescope: Telescope,
    window_start: datetime,
    window_end: datetime,
    effective_floor_deg: float,
    duration_sec: float,
    step: timedelta,
    occupied: list[tuple[datetime, datetime]],
) -> datetime | None:
    """Find the earliest unoccupied visible span of at least `duration_sec`.

    Returns
    -------
    start : `datetime` or `None`
        The earliest qualifying span's start time, or `None` if none
        exists.
    """
    run_start: datetime | None = None
    t = window_start
    while t <= window_end:
        in_occupied = any(occ_start <= t < occ_end for occ_start, occ_end in occupied)
        visible = (
            not in_occupied
            and visibility_tasks.visibility_state_at(
                request.ra_deg, request.dec_deg, site_profile, telescope, t, effective_floor_deg
            )[0]
        )
        if visible:
            if run_start is None:
                run_start = t
            if (t - run_start).total_seconds() >= duration_sec:
                return run_start
        else:
            run_start = None
        t += step
    return None


def _build_queue_entry(
    request: PlacementRequest, start: datetime, end: datetime, applied_boost: int
) -> QueuedObservationPackage:
    """Freeze a self-contained `QueuedObservationPackage` from a request.

    Returns
    -------
    entry : `QueuedObservationPackage`
        The frozen, self-contained queue entry.
    """
    package = request.package
    return QueuedObservationPackage(
        id=str(uuid.uuid4()),
        observation_package_id=package.id,
        target_id=package.target_id,
        exposure_requests=list(package.exposure_requests),
        dither_config=package.dither_config,
        minimum_altitude_deg=package.minimum_altitude_deg,
        priority=package.priority,
        applied_priority_boost=applied_boost,
        start_time_mode=request.start_time_mode,
        requested_start_time=request.requested_start_time,
        computed_start_time=start,
        computed_end_time=end,
    )


def plan_observation_session(
    astrometrics,  # ruff: ignore[missing-type-function-argument]
    requests: list[tuple[ObservationPackage, StartTimeMode, datetime | None]],
    site_profile: SiteProfile,
    telescope: Telescope,
    camera_id: str,
    night_date,  # ruff: ignore[missing-type-function-argument]
    quality_advisories: dict[str, TargetQualityAdvisory] | None = None,
    planning_config: PlanningConfig | None = None,
) -> ObservationSession:
    """Resolve the night window and place every requested package into it.

    The end-to-end entry point matching `Wayfinding_Library_Sequences.md`
    §1.5: resolves live target coordinates for each package, brackets
    the night, places every package, and assembles the resulting
    `ObservationSession`. Runs entirely against the arguments it is
    handed -- no device is contacted, so this is callable with nothing
    connected (`Wayfinding_Library_Architecture.md` §2.3.4, "Planning Is
    Hardware-Free").

    Returns
    -------
    session : `ObservationSession`
        The assembled session with its placed queue and unplaced-package
        diagnostics.
    """
    from wayfindinglib.tasks.planning_tasks.night_window import resolve_night_window

    placement_requests = []
    for package, start_time_mode, requested_start_time in requests:
        ra_deg, dec_deg = _target_coordinates(astrometrics, package.target_id)
        placement_requests.append(
            PlacementRequest(
                package=package,
                start_time_mode=start_time_mode,
                requested_start_time=requested_start_time,
                ra_deg=ra_deg,
                dec_deg=dec_deg,
            )
        )

    night_window = resolve_night_window(site_profile, night_date, planning_config)
    queue, diagnostics = place(
        placement_requests, night_window, site_profile, telescope, quality_advisories, planning_config
    )

    return ObservationSession(
        id=str(uuid.uuid4()),
        night_date=night_date,
        status=SessionStatus.PLANNED,
        site_profile_id=site_profile.id,
        telescope_id=telescope.id,
        camera_id=camera_id,
        queue=queue,
        unplaced_package_diagnostics=diagnostics,
    )
