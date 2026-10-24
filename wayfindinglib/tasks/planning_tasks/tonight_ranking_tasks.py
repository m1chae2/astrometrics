"""Purpose: Rank the candidate targets for tonight by the gain from one hour.

Description: `rank_tonight` is the work behind
`ObservationPlanning.get_advisory(kind="tonight", at=...)`
(`QUALITY_METRICS_PLAN.md` Section 4.2, use cases UC6 and UC12). For each
library target that is above the horizon at the planning time, it asks: if the
user spent the next clock hour on this target, by how much would the noise of
its stack fall? The answer comes from the same forward model as "take N more"
(`noise_growth`), with three inputs that are specific to tonight.

* The sky. The new frames are given the sky rate that the user's own sky model
  predicts for this target, this time and this Moon (`sky_model`). The model
  is fitted to the sky rates of the user's own frames, never to a catalog
  value. When there is no model for an equipment setting, the target's past
  frames' own sky rates are used and the ranking says so.
* The clock. An hour holds fewer exposures than 3600 seconds of exposure,
  because each frame costs a download, a dither costs settling time and an
  autofocus run costs minutes (`capture_overheads`). The overheads come from
  the user's recorded Ekos sessions when there are enough, and from design
  estimates otherwise.
* The history. A target whose noise has stopped falling, from a floor fitted
  over two or more stacks, is ranked at zero with the reason
  (`forecast.target_stop_signal`).

Targets are ranked in separate lists for imaging, photometry and spectroscopy,
because their gains are not comparable. Asteroid searches, solar-system
targets and calibration folders are never ranked. A target with no frame
measurements yet is marked "new" and is not ranked, because it has no noise
history to rank from. When a target with little data leads a list, the ranking
says that this is partly because one hour matters more to a short history.

The gain is the fall in the noise of the whole stack. The sharpness of the
stack is not part of it. The gains are design estimates until the forecast
check has been run on the user's own nights (validation register entries
WAY-71 and WAY-72).

Planning code: this module reads only through the public root of the science
library and never imports Control.
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np
from astropy.time import Time

from astrometricslib import (
    CaptureSetting,
    MeasurementReference,
    Target,
    frame_is_spectral,
)
from wayfindinglib.analytics.result_quality.capture_overheads import (
    CaptureOverheads,
    estimate_capture_overheads,
    wall_clock_estimate,
)
from wayfindinglib.analytics.result_quality.efficiency import (
    SKY_RATE_METRIC,
    FrameObservation,
    baseline_key,
    frame_observations_from_measurements,
)
from wayfindinglib.analytics.result_quality.forecast import (
    FRAME_WEIGHT_METRIC,
    StackRunRecord,
    stack_frames_from_observations,
    stack_runs_from_measurements,
    target_stop_signal,
)
from wayfindinglib.analytics.result_quality.noise_growth import (
    WeightingMode,
    forecast_stack_noise,
    identify_weighting_mode,
    noise_ratio_after_adding,
    predict_weighting_mode,
)
from wayfindinglib.analytics.result_quality.observing_values import sky_geometry
from wayfindinglib.analytics.result_quality.reply_budget import MAXIMUM_REPLY_CHARACTERS, reply_characters
from wayfindinglib.analytics.result_quality.sky_model import SkyModel, predict_sky_rate_at
from wayfindinglib.analytics.result_quality.sky_samples import (
    collect_sky_samples,
    fit_sky_models,
    frame_times,
    target_direction,
)
from wayfindinglib.astronomy.coordinate_transforms import compute_altaz, earth_location
from wayfindinglib.models.equipment_and_site.equipment import Telescope
from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
from wayfindinglib.models.planning.tonight_ranking import (
    CandidateStatus,
    RankedCandidate,
    RankedList,
    RankedUse,
    SkySettingSummary,
    TonightRanking,
    TonightSkyModel,
)
from wayfindinglib.tasks.planning_tasks.visibility_tasks import effective_altitude_floor, visibility_state_at

TONIGHT_SKY_ENTRY = "WAY-71"
"""Register entry behind the tonight sky model."""

TONIGHT_RANKING_ENTRY = "WAY-72"
"""Register entry behind the ranking and its design-estimate row."""

CLOCK_HOUR_SECONDS = 3600.0
"""The span the gain is worked out for: one hour of real clock time."""

THIN_DATASET_SECONDS = 2.0 * 3600.0
"""A target with less exposure than this is "thin". One more hour then adds at
least half as much again as it has, so its gain per hour is large because its
history is short, not because it is a better target tonight. Design estimate,
register row WAY-D71."""

DITHER_EVERY_FRAMES = 3
"""Frames between dithers assumed in the hour. It is the cadence the plan uses
in its wall-clock example (60 frames of 30 s with a dither every 3 frames).
Design estimate, register row WAY-D71."""

AUTOFOCUS_RUNS_PER_HOUR = 1
"""Autofocus runs assumed in one hour. Ekos refocuses on a filter change or a
temperature change, about once an hour on a cooling night. Design estimate,
register row WAY-D71."""

GAIN_DESCRIPTIONS = {
    RankedUse.IMAGING: (
        "How much one clock hour of exposures cuts the noise of the stack, as a fraction of today's noise."
    ),
    RankedUse.PHOTOMETRY: (
        "How much one clock hour of exposures cuts the noise of the stack, as a fraction of today's noise. "
        "It is not a measure of the light-curve precision."
    ),
    RankedUse.SPECTROSCOPY: (
        "How much one clock hour of exposures cuts the noise of the stack, as a fraction of today's noise. "
        "It is not a measure of the spectral signal-to-noise ratio."
    ),
}
"""What the gain of each list means. The units differ, so the lists are never
merged."""

_NEVER_RANKED_TYPES = {
    "solar_system": "solar-system targets",
    "comet": "comets",
    "calibration": "calibration folders",
}
"""The kinds of target (`TargetObjectType` values) that are never ranked."""


def classify_use(target: Target) -> RankedUse | None:
    """Say which ranked list a target belongs to.

    Spectral frames make a spectroscopy target. Otherwise a target that has a
    photometry quality record is a photometry target. All others are imaging
    targets. Solar-system targets, comets, calibration folders and targets
    with asteroid-search results are never ranked (``None``).

    Parameters
    ----------
    target : `Target`
        The library target.

    Returns
    -------
    use : `RankedUse` or `None`
        The list, or ``None`` for a target that is never ranked.
    """
    if target.object_type.value in _NEVER_RANKED_TYPES:
        return None
    detection = target.asteroid_detection
    if detection.candidates or detection.quality_summary is not None:
        return None
    lights = [frame for frame in target.frames if frame.role.upper() == "LIGHT"]
    if lights and sum(frame_is_spectral(frame) for frame in lights) > len(lights) / 2:
        return RankedUse.SPECTROSCOPY
    if target.quality.photometry is not None:
        return RankedUse.PHOTOMETRY
    return RankedUse.IMAGING


def _new_candidate(
    target_id: str, use: RankedUse, altitude_deg: float | None, reason: str
) -> RankedCandidate:
    """Build the entry of a target that has no data yet.

    Returns
    -------
    candidate : `RankedCandidate`
        A candidate with status ``new`` and no rank.
    """
    return RankedCandidate(
        target_id=target_id,
        use=use,
        status=CandidateStatus.NEW,
        altitude_deg=altitude_deg,
        reason=reason,
    )


def _no_forecast(
    target_id: str, use: RankedUse, altitude_deg: float | None, reason: str, **extra: Any
) -> RankedCandidate:
    """Build the entry of a target whose forecast could not be made.

    Returns
    -------
    candidate : `RankedCandidate`
        A candidate with status ``no_forecast`` and no rank.
    """
    return RankedCandidate(
        target_id=target_id,
        use=use,
        status=CandidateStatus.NO_FORECAST,
        altitude_deg=altitude_deg,
        reason=reason,
        **extra,
    )


def _dominant_setting(observations: Sequence[FrameObservation]) -> CaptureSetting | None:
    """Find the capture setting with the most exposure time.

    Parameters
    ----------
    observations : `Sequence` [`FrameObservation`]
        The target's frames.

    Returns
    -------
    setting : `CaptureSetting` or `None`
        The setting, or ``None`` when no frame has an exposure time.
    """
    totals: dict[CaptureSetting, float] = defaultdict(float)
    for observation in observations:
        seconds = observation.effective_exposure_seconds
        if seconds:
            totals[observation.capture_setting] += seconds
    return max(totals, key=lambda setting: totals[setting]) if totals else None


def score_candidate(
    target_id: str,
    use: RankedUse,
    observations: Sequence[FrameObservation],
    runs: Sequence[StackRunRecord],
    *,
    epoch_seconds: float,
    direction: tuple[float, float],
    altitude_deg: float,
    site: SiteProfile,
    sky_models: Mapping[str, SkyModel],
    overheads_for: Callable[[CaptureSetting], CaptureOverheads],
) -> RankedCandidate:
    """Find the gain from one clock hour on one target.

    The gain is one minus the stack's noise after one more clock hour of
    frames, over its noise now (register row WAY-D72).

    Parameters
    ----------
    target_id : `str`
        The target.
    use : `RankedUse`
        Its list.
    observations : `Sequence` [`FrameObservation`]
        The measurements of its frames.
    runs : `Sequence` [`StackRunRecord`]
        Its library stack runs (`stack_runs_from_measurements`).
    epoch_seconds : `float`
        The planning time, in seconds since the Unix epoch.
    direction : `tuple` [`float`, `float`]
        Right ascension and declination, in degrees.
    altitude_deg : `float`
        The target's altitude at the planning time.
    site : `SiteProfile`
        The observing site.
    sky_models : `Mapping` [`str`, `SkyModel`]
        The fitted sky models by equipment key.
    overheads_for : `Callable`
        Gives the capture overheads for a capture setting.

    Returns
    -------
    candidate : `RankedCandidate`
        The target with its gain, or the reason it has none. The rank is set
        later, when the lists are made.
    """
    if not observations and not runs:
        return _new_candidate(
            target_id,
            use,
            altitude_deg,
            "No frame of this target has been measured yet, so it has no noise history.",
        )
    setting = _dominant_setting(observations)
    if setting is None:
        return _no_forecast(
            target_id, use, altitude_deg, "No measured frame of this target has an exposure time."
        )
    by_id = {observation.frame_id: observation for observation in observations}
    remarks: list[str] = []

    # The stack today: the last run's members when they are known, else every
    # measured frame of the main setting (never stacked yet).
    last_run = runs[-1] if runs else None
    mode = WeightingMode.UNKNOWN
    if last_run is not None and last_run.member_frame_ids:
        existing, problems = stack_frames_from_observations(last_run.member_frame_ids, by_id)
        if problems:
            return _no_forecast(
                target_id,
                use,
                altitude_deg,
                "Frame measurements of the last stack are missing: " + problems[0],
            )
        mode = identify_weighting_mode(existing, last_run.recorded_weights).mode
        if mode == WeightingMode.UNKNOWN:
            remarks.append(
                "The last stack's weights were not recorded, so the stacker's default rule predicts them."
            )
    else:
        same_setting = [o.frame_id for o in observations if o.capture_setting == setting]
        existing, problems = stack_frames_from_observations(same_setting, by_id)
        if not existing:
            return _no_forecast(
                target_id,
                use,
                altitude_deg,
                "No measured frame has the exposure, sky rate and read noise needed.",
            )
        remarks.append(
            "The target has no stack yet; the forecast assumes every measured frame will be stacked."
        )
    if mode == WeightingMode.UNKNOWN:
        mode = predict_weighting_mode(None, [frame.exposure_seconds for frame in existing])
    current = forecast_stack_noise(existing, mode)
    if current.noise is None:
        return _no_forecast(
            target_id, use, altitude_deg, current.reason or "The stack's noise could not be found."
        )
    captured_seconds = sum(frame.exposure_seconds for frame in existing)

    # The new frames follow the main setting's past frames, with tonight's sky.
    template = [
        frame for frame in existing if abs(frame.exposure_seconds - (setting.exposure_seconds or 0.0)) < 0.5
    ] or list(existing)
    exposure_seconds = float(
        setting.exposure_seconds or np.median([frame.exposure_seconds for frame in template])
    )
    sky_rate: float | None = None
    sky_source = "history"
    model = sky_models.get(baseline_key(setting))
    if model is not None:
        prediction = predict_sky_rate_at(
            model,
            epoch_seconds,
            direction[0],
            direction[1],
            site.latitude_deg,
            site.longitude_deg,
            site.elevation_m,
        )
        if prediction.sky_rate_e_per_s_per_px is None:
            return _no_forecast(
                target_id, use, altitude_deg, "The sky model puts the target below the horizon."
            )
        sky_rate = prediction.sky_rate_e_per_s_per_px
        sky_source = "model"
        template = [frame.model_copy(update={"sky_rate_e_per_s_per_px": sky_rate}) for frame in template]
        if not prediction.reliable:
            remarks.append(
                "The Moon is up but the sky model has no Moon term, so the real sky may be "
                "brighter than assumed."
            )
    else:
        sky_rate = float(np.median([frame.sky_rate_e_per_s_per_px for frame in template]))
        remarks.append(
            "No sky model exists for this equipment, so the sky of the target's past frames is used."
        )

    overheads = overheads_for(setting)
    per_frame_seconds = (
        exposure_seconds + overheads.download_seconds + overheads.dither_settle_seconds / DITHER_EVERY_FRAMES
    )
    hour_for_frames = CLOCK_HOUR_SECONDS - AUTOFOCUS_RUNS_PER_HOUR * overheads.autofocus_seconds
    frames_in_hour = int(hour_for_frames // per_frame_seconds) if hour_for_frames > 0.0 else 0
    if frames_in_hour < 1:
        return _no_forecast(
            target_id,
            use,
            altitude_deg,
            "One frame and its overheads do not fit in an hour.",
            capture_setting=setting,
            exposure_seconds=exposure_seconds,
        )
    wall = wall_clock_estimate(
        frames_in_hour,
        exposure_seconds,
        overheads,
        dither_every_frames=DITHER_EVERY_FRAMES,
        autofocus_runs=AUTOFOCUS_RUNS_PER_HOUR,
    )
    forecast = noise_ratio_after_adding(existing, template, frames_in_hour, mode, exposure_seconds)
    if forecast is None or forecast.noise is None:
        return _no_forecast(target_id, use, altitude_deg, "The noise after one more hour could not be found.")
    gain = max(0.0, 1.0 - forecast.noise / current.noise)

    evidence = (
        [MeasurementReference(row_id=last_run.normalized_row_id)]
        if last_run is not None and last_run.normalized_row_id is not None
        else []
    )
    common: dict[str, Any] = {
        "altitude_deg": altitude_deg,
        "capture_setting": setting,
        "frames_in_hour": frames_in_hour,
        "exposure_seconds": exposure_seconds,
        "overhead_fraction": wall.overhead_fraction,
        "effective_hours_so_far": captured_seconds / 3600.0,
        "sky_rate_e_per_s_per_px": sky_rate,
        "sky_source": sky_source,
        "thin_dataset": captured_seconds < THIN_DATASET_SECONDS,
        "evidence": evidence,
    }
    stop = target_stop_signal(runs)
    if stop.stopped:
        return RankedCandidate(
            target_id=target_id,
            use=use,
            status=CandidateStatus.STOPPED,
            gain_per_hour_fraction=0.0,
            reason=stop.reason,
            **common,
        )
    return RankedCandidate(
        target_id=target_id,
        use=use,
        status=CandidateStatus.RANKED,
        gain_per_hour_fraction=gain,
        reason=" ".join(remarks),
        **common,
    )


def build_lists(candidates: Iterable[RankedCandidate]) -> tuple[list[RankedList], list[str]]:
    """Order the candidates into one ranked list for each use.

    Parameters
    ----------
    candidates : `Iterable` [`RankedCandidate`]
        All the scored candidates, in any order.

    Returns
    -------
    lists, notes : `tuple`
        The lists (imaging, photometry, spectroscopy, those with candidates),
        and a note for each list that a thin dataset leads. In a list the
        ranked targets come first, best gain first, then the stopped targets
        at zero, then the new ones and those with no forecast, which have no
        rank.
    """
    by_use: dict[RankedUse, list[RankedCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_use[candidate.use].append(candidate)
    lists: list[RankedList] = []
    notes: list[str] = []
    for use in RankedUse:
        group = by_use.get(use)
        if not group:
            continue
        ranked = sorted(
            (c for c in group if c.status == CandidateStatus.RANKED),
            key=lambda c: (-(c.gain_per_hour_fraction or 0.0), c.target_id),
        )
        stopped = sorted((c for c in group if c.status == CandidateStatus.STOPPED), key=lambda c: c.target_id)
        unranked = sorted(
            (c for c in group if c.status in (CandidateStatus.NEW, CandidateStatus.NO_FORECAST)),
            key=lambda c: (c.status.value, c.target_id),
        )
        ordered = []
        for place, candidate in enumerate([*ranked, *stopped], start=1):
            ordered.append(candidate.model_copy(update={"rank": place}))
        ordered.extend(unranked)
        lists.append(RankedList(use=use, gain_description=GAIN_DESCRIPTIONS[use], candidates=ordered))
        if ranked and ranked[0].thin_dataset:
            lead = ranked[0]
            notes.append(
                f"{lead.target_id} leads the {use.value} list with a thin dataset "
                f"({lead.effective_hours_so_far or 0.0:.1f} h so far). One more hour cuts a short history "
                "more than a long one, so check that it is also the target you want."
            )
    return lists, notes


def fit_ranking_to_budget(ranking: TonightRanking, budget: int = MAXIMUM_REPLY_CHARACTERS) -> TonightRanking:
    """Leave out the lowest candidates until the ranking fits the reply budget.

    Parameters
    ----------
    ranking : `TonightRanking`
        The finished ranking.
    budget : `int`, optional
        The most characters its compact JSON may have.

    Returns
    -------
    ranking : `TonightRanking`
        The same ranking when it fits. Otherwise a copy that drops the last
        candidate of the longest list again and again (the unranked, then the
        stopped, then the lowest gains), with a note for each list that says
        how many targets were left out.
    """
    if reply_characters(ranking) <= budget:
        return ranking
    kept = {ranked_list.use: list(ranked_list.candidates) for ranked_list in ranking.lists}
    left_out: Counter[RankedUse] = Counter()
    fitted = ranking
    while reply_characters(fitted) > budget:
        longest = max(kept, key=lambda use: len(kept[use]))
        if not kept[longest]:
            break
        # Trim several at a time: the cut is large when the library is large.
        for _ in range(max(1, len(kept[longest]) // 10)):
            if kept[longest]:
                kept[longest].pop()
                left_out[longest] += 1
        fitted = ranking.model_copy(
            update={
                "lists": [
                    ranked_list.model_copy(update={"candidates": kept[ranked_list.use]})
                    for ranked_list in ranking.lists
                ],
            }
        )
    notes = [
        f"{count} {use.value} targets ranked lowest were left out to fit the reply budget "
        f"of {budget:,} characters."
        for use, count in left_out.items()
    ]
    return fitted.model_copy(update={"notes": [*ranking.notes, *notes]})


def summarize_sky(
    epoch_seconds: float,
    site: SiteProfile,
    models: Mapping[str, SkyModel],
    unavailable: Sequence[str],
) -> TonightSkyModel:
    """Describe the sky the ranking assumed.

    Parameters
    ----------
    epoch_seconds : `float`
        The planning time, in seconds since the Unix epoch.
    site : `SiteProfile`
        The observing site.
    models : `Mapping` [`str`, `SkyModel`]
        The fitted models by key.
    unavailable : `Sequence` [`str`]
        The settings that have no model, with reasons.

    Returns
    -------
    sky : `TonightSkyModel`
        The Moon at the planning time, the models' quality and the labels
        that mark it as a tentative model.
    """
    geometry = sky_geometry(
        [epoch_seconds], [0.0], [0.0], site.latitude_deg, site.longitude_deg, site.elevation_m
    )
    moon = geometry[0] if geometry else None
    return TonightSkyModel(
        at=datetime.fromtimestamp(epoch_seconds, tz=UTC),
        moon_altitude_deg=moon.moon_altitude_deg if moon else None,
        moon_illumination_fraction=moon.moon_illumination_fraction if moon else None,
        settings=[
            SkySettingSummary(
                setting_key=key,
                nights_used=model.nights_used,
                frames_used=model.frames_used,
                held_out_relative_error=model.held_out_relative_error,
                moon_fitted=model.moon_fitted,
            )
            for key, model in sorted(models.items())
        ],
        unavailable=list(unavailable),
    )


def _aware_utc(moment: datetime | None, now: datetime | None) -> datetime:
    """Make the planning time a time zone aware UTC time.

    Parameters
    ----------
    moment : `datetime` or `None`
        The requested time. A time with no zone counts as UTC.
    now : `datetime` or `None`
        The current time, for the caller to fix in tests.

    Returns
    -------
    moment : `datetime`
        The requested time, or now.
    """
    chosen = moment if moment is not None else (now or datetime.now(UTC))
    return chosen.replace(tzinfo=UTC) if chosen.tzinfo is None else chosen.astimezone(UTC)


def rank_tonight(
    at: datetime | None = None,
    *,
    astrometrics: Any,
    site_profile: SiteProfile | None = None,
    telescope: Telescope | None = None,
    butler: Any | None = None,
    config: Any | None = None,
    ekos_contexts: Sequence[Any] | None = None,
    candidate_target_ids: Sequence[str] | None = None,
    minimum_altitude_deg: float = 0.0,
    now: datetime | None = None,
) -> TonightRanking:
    """Rank the library targets that are up at a time by the gain from an hour.

    Nothing is stored. The ranking is worked out again on each call, so asking
    again later in the night with a new `at` re-ranks the targets.

    Parameters
    ----------
    at : `datetime`, optional
        The planning time. A time with no zone counts as UTC. The current time
        when omitted.
    astrometrics : `Astrometrics`
        The science library handle (keyword only). Its ``targets`` and
        ``quality`` are read.
    site_profile : `SiteProfile`, optional
        The observing site. When omitted it is read from `butler`
        (``wayfindinglib.data_access.site_profile_reader``), which needs
        `butler`.
    telescope : `Telescope`, optional
        The imaging telescope, for its altitude limits. When omitted, the
        active telescope of `config` is used if `config` is given.
    butler : `DiskButler`, optional
        Supplies the recorded Ekos sessions (for the overheads) and, when no
        site is given, the default site.
    config : `AppConfiguration`, optional
        Supplies the active telescope and the default site.
    ekos_contexts : `Sequence`, optional
        Recorded Ekos session records for the overheads. Read from `butler`
        when omitted; with neither, the overheads are the design estimates.
    candidate_target_ids : `Sequence` [`str`], optional
        Rank only these targets. Every library target when omitted.
    minimum_altitude_deg : `float`, optional
        The lowest altitude that counts as visible when no telescope limit is
        known. The telescope's own limits override it.
    now : `datetime`, optional
        The current time, used only when `at` is omitted.

    Returns
    -------
    ranking : `TonightRanking`
        One list for each use, the sky model's description, and notes.

    Raises
    ------
    ConfigurationError
        If no site is given and none can be read.
    """
    from astrometricslib import ConfigurationError

    when = _aware_utc(at, now)
    epoch_seconds = when.timestamp()
    if site_profile is None:
        if butler is None:
            raise ConfigurationError(
                "rank_tonight needs a site_profile, or a butler to read the default site from."
            )
        from wayfindinglib.data_access.site_profile_reader import get_or_seed_default_site_profile

        site_profile = get_or_seed_default_site_profile(butler, config)
    if telescope is None and config is not None:
        from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog

        telescope = get_equipment_catalog(config).active_telescope()
    if ekos_contexts is None:
        ekos_contexts = butler.get_all("ekos_session_context") if butler is not None else []

    targets = list(astrometrics.targets.list())
    capture_settings = astrometrics.quality.capture_settings()
    sky_rows = astrometrics.quality.query(metric=SKY_RATE_METRIC, level="frame", library_only=True)
    models, unavailable = fit_sky_models(
        collect_sky_samples(targets, sky_rows, capture_settings),
        site_profile.latitude_deg,
        site_profile.longitude_deg,
        site_profile.elevation_m,
    )

    location = earth_location(site_profile.latitude_deg, site_profile.longitude_deg, site_profile.elevation_m)
    floor = (
        effective_altitude_floor(telescope, None) if telescope is not None else float(minimum_altitude_deg)
    )
    overhead_cache: dict[tuple[float | None, str | None], CaptureOverheads] = {}

    def overheads_for(setting: CaptureSetting) -> CaptureOverheads:
        """Give the overheads for a capture setting, reading the records once.

        Parameters
        ----------
        setting : `CaptureSetting`
            The setting.

        Returns
        -------
        overheads : `CaptureOverheads`
            Measured from the records when there are enough, else the
            design estimates.
        """
        key = (setting.exposure_seconds, setting.filter_name)
        if key not in overhead_cache:
            overhead_cache[key] = estimate_capture_overheads(
                list(ekos_contexts or []),
                exposure_seconds=setting.exposure_seconds,
                filter_name=setting.filter_name,
            )
        return overhead_cache[key]

    wanted = set(candidate_target_ids) if candidate_target_ids is not None else None
    skipped: dict[str, int] = defaultdict(int)
    candidates: list[RankedCandidate] = []
    for target in targets:
        if wanted is not None and target.id not in wanted:
            continue
        use = classify_use(target)
        if use is None:
            skipped[_NEVER_RANKED_TYPES.get(target.object_type.value, "asteroid searches")] += 1
            continue
        direction = target_direction(target)
        if direction is None:
            skipped["targets with no verified position"] += 1
            continue
        altitude_deg, _azimuth_deg = compute_altaz(direction[0], direction[1], location, Time(when))
        if telescope is not None:
            visible, _zone = visibility_state_at(
                direction[0], direction[1], site_profile, telescope, when, floor
            )
        else:
            visible = altitude_deg >= floor
        if not visible:
            skipped["targets not visible at that time"] += 1
            continue
        frame_rows = astrometrics.quality.query(target_id=target.id, level="frame", library_only=True)
        observations = frame_observations_from_measurements(
            frame_rows,
            capture_settings,
            start_epoch_by_frame=frame_times(target),
            direction_by_frame=dict.fromkeys(frame_times(target), direction),
        )
        history = [
            *astrometrics.quality.query(
                target_id=target.id, level="stack", library_only=True, latest_only=False
            ),
            *astrometrics.quality.query(
                target_id=target.id,
                level="frame",
                metric=FRAME_WEIGHT_METRIC,
                library_only=True,
                latest_only=False,
            ),
        ]
        runs = stack_runs_from_measurements(history)
        candidates.append(
            score_candidate(
                target.id,
                use,
                observations,
                runs,
                epoch_seconds=epoch_seconds,
                direction=direction,
                altitude_deg=float(altitude_deg),
                site=site_profile,
                sky_models=models,
                overheads_for=overheads_for,
            )
        )
    lists, notes = build_lists(candidates)
    for kind, count in sorted(skipped.items()):
        notes.append(f"{count} {kind} not ranked.")
    return fit_ranking_to_budget(
        TonightRanking(
            at=when,
            lists=lists,
            sky=summarize_sky(epoch_seconds, site_profile, models, unavailable),
            notes=notes,
        )
    )


__all__ = [
    "AUTOFOCUS_RUNS_PER_HOUR",
    "CLOCK_HOUR_SECONDS",
    "DITHER_EVERY_FRAMES",
    "THIN_DATASET_SECONDS",
    "TONIGHT_RANKING_ENTRY",
    "TONIGHT_SKY_ENTRY",
    "build_lists",
    "classify_use",
    "rank_tonight",
    "score_candidate",
    "summarize_sky",
]
