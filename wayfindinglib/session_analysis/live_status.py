"""Purpose: Judge how the current observing session is going, right now.

Description: The after-the-night analyses in this package read stored
records. This module reads the live Ekos files instead (the analyze log
and the KStars text log) and answers the questions an observer asks during
a session: Is guiding steady? Did the last dither work? Which frames are
already ruined? It stores nothing and reads only what Ekos has written so
far.

Every threshold below is measured from the session's own data, so it adapts
to the equipment in use. Each carries a comment giving how it was chosen
and the data it was checked against.
"""

import math
import statistics
import time
from collections.abc import Sequence

from astrometricslib.parameters import (
    LOW_STAR_FRACTION,
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    resolve_parameter,
)
from wayfindinglib.analytics.line_fit import fit_slope, significant_slope
from wayfindinglib.analytics.tuning_support import percentile
from wayfindinglib.models.session.ekos_session import (
    EkosCapture,
    EkosGuideStat,
    EkosSessionContext,
    EkosStateEvent,
)
from wayfindinglib.models.session.live_session_status import (
    DitherEvent,
    DitherSettings,
    ExposureSummary,
    GuidingExcursion,
    GuidingWindowSummary,
    LiveSessionStatus,
)

EARLY_EXPOSURE_SECONDS = TunedParameter(
    name="wayfinding.early_exposure_seconds",
    default=30.0,
    unit="s",
    description="Length of the opening stretch of an exposure compared with the rest.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="WAY-D136",
    allowed_range=(5.0, 300.0),
    minimum_sample=10,  # a sample is one successful dither with guide samples after it
)
"""Length of the opening stretch of an exposure compared with the rest.

A dither's after-effects show in the first moments of the next exposure.
The tuner (`live_status_tuning.EARLY_EXPOSURE_TUNER`) takes the median time
after a successful dither at which the guide error is back within 1.5 times
the night's median error, over the stored dithers. The default of 30 s comes
from the 2026-10-02 M 27 session: the 30 s after a dither averaged 2.3 to
6.0 arcsec of guide error, against 1.3 to 1.7 arcsec for the rest.
"""

DITHER_FOLLOW_SECONDS = TunedParameter(
    name="wayfinding.dither_follow_seconds",
    default=130.0,
    unit="s",
    description='Fallback for how long before an exposure a dither counts as "just before" it.',
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    register_entry="WAY-D136",
)
"""Fallback for how long before an exposure a dither counts as before it.

The window is worked out on each run by `dither_follow_seconds`: the
capture's own exposure length plus the measured settle time of the dither
(or the session's 95th-percentile settle when the dither has none of its
own). This value is used only when neither a settle time nor an exposure
length is known. It is the 2026-10-02 session's case: a 120 s exposure plus
a settle of about 10 s. The code applies the value itself, so no run
basis multiplies it.
"""

SETTLE_PERCENTILE = TunedParameter(
    name="wayfinding.settle_percentile",
    default=95.0,
    unit="percent",
    description="Settle time used for a dither with no settle of its own: this percentile of the session's.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="WAY-D136",
)
"""Settle time used for a dither with no settle of its own.

The 95th percentile of the session's measured settles covers all but the
slowest one dither in twenty, so a dither that settled slowly is still
linked to the exposure that followed it. Register entry WAY-D136."""

EXCURSION_MEDIAN_MULTIPLE = TunedParameter(
    name="wayfinding.excursion_median_multiple",
    default=8.0,
    unit="multiple",
    description="An excursion is a guide error above this multiple of the session median.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="WAY-D136",
    allowed_range=(1.5, 30.0),
    minimum_sample=5,  # a sample is one observing night with enough guide samples
)
"""An excursion is a guide error above this multiple of the session median.

Using the session's own median keeps the limit tied to the equipment's
normal guiding. On 2026-10-02 the median total error was about 1.3 arcsec,
giving a limit near 10 arcsec. Quiet guiding peaked below 4 arcsec and
never reached it. The three real events (a 12 arcminute jump during a
dither, and two minutes of RA drift reaching 300 arcsec) all exceeded it by
a wide margin.
"""

EXCURSION_FLOOR_ARCSEC = TunedParameter(
    name="wayfinding.excursion_floor_arcsec",
    default=5.0,
    unit="arcsec",
    description="The smallest excursion limit, whatever the median is.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="WAY-D136",
    allowed_range=(0.5, 20.0),
    minimum_sample=5,  # a sample is one observing night with enough guide samples
)
"""The smallest excursion limit, whatever the median is.

A session with unusually tight guiding would otherwise flag ordinary
fluctuations of a couple of arcseconds.
"""

EXCURSION_JOIN_SECONDS = TunedParameter(
    name="wayfinding.excursion_join_seconds",
    default=20.0,
    unit="s",
    description="Two over-limit runs closer together than this are one excursion.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="WAY-D136",
    allowed_range=(2.0, 300.0),
    minimum_sample=8,  # a sample is one excursion (a run of over-limit guide samples)
)
"""Two over-limit runs closer together than this are one excursion.

The guider re-locks on its star after a large error, which briefly resets
the error to near zero. On 2026-10-02 the reset lasted a few seconds
between three runs of one drift, so a join time of 20 s merges them while
keeping the 21:15 and 22:17 events apart.
"""

SIDEREAL_RATE_ARCSEC_PER_SECOND = 15.0411
"""How fast the sky turns: arcseconds of right ascension per second."""

# LOW_STAR_FRACTION is imported above, not declared here. It is the same
# declaration the stacking frame check uses (``shared.low_star_fraction``), so
# both registries hold one object. A frame with fewer stars than this fraction
# of the usual count (the median of the frames before it) is flagged. Trailed
# or clouded frames lose their stars quickly: on 2026-10-02 a normal frame
# held about 240 stars, and the frame exposed during the two-minute drift held
# a small fraction of that.

MINIMUM_FRAMES_FOR_STAR_BASELINE = TunedParameter(
    name="wayfinding.minimum_frames_for_star_baseline",
    default=5,
    unit="frames",
    description="Frames needed before a star-count baseline means anything.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="WAY-D136",
    allowed_range=(2.0, 20.0),
    minimum_sample=100,  # a sample is one frame predicted from the frames before it
)
"""Frames needed before a star-count baseline means anything."""

MAXIMUM_FRAMES_IN_STAR_BASELINE = TunedParameter(
    name="wayfinding.maximum_frames_in_star_baseline",
    default=20,
    unit="frames",
    description="The most earlier frames the star-count baseline looks back over.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="WAY-D136",
    allowed_range=(5.0, 100.0),
    minimum_sample=100,  # a sample is one frame predicted from the frames before it
)
"""The most earlier frames the star-count baseline looks back over.

Twenty frames is about 40 minutes of 120 s exposures on the 2026-10-02 session,
enough to ride out a passing cloud without following a slow change of sky.
"""

JUMP_FIRST_SAMPLE_FRACTION = TunedParameter(
    name="wayfinding.jump_first_sample_fraction",
    default=0.5,
    unit="fraction",
    description="An excursion is a jump if its first sample is already this much of its peak.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="WAY-D136",
    allowed_range=(0.05, 0.95),
    minimum_sample=8,  # a sample is one excursion
)
"""An excursion is a jump if its first sample is already this much of its peak.

A drift builds up over many samples, so its first over-limit sample is a
small share of the peak (33 of 307 arcsec for the 21:53 drift on
2026-10-02). A jump arrives at full size in one step (the 21:15 Dec jump
was at its 739 arcsec peak from its first sample).
"""

MINIMUM_SAMPLES_FOR_DRIFT_FIT = TunedParameter(
    name="wayfinding.minimum_samples_for_drift_fit",
    default=3,
    unit="samples",
    description="Measurements needed to fit a straight line to part of an excursion.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="WAY-D136",
)
"""Measurements needed to fit a straight line to part of an excursion.

Three is the fewest that leave scatter to judge the slope by
(`line_fit.significant_slope`).
"""

EXCURSION_DRIFT_SIGNIFICANCE_SIGMAS = TunedParameter(
    name="wayfinding.excursion_drift_significance_sigmas",
    default=3.0,
    unit="standard errors",
    description="A piece's drift counts only when it stands this many standard errors from zero.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="WAY-D136",
)
"""A piece's drift counts only when this many standard errors from zero."""

LIVE_STATUS_WINDOW_SECONDS = TunedParameter(
    name="wayfinding.live_status_window_seconds",
    default=600.0,
    unit="s",
    description="Length of the recent-guiding window the live status reports on.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="WAY-D136",
)
"""Length of the recent-guiding window the live status reports on.

A method choice, not a fitted value. At a 3 s guide cycle, ten minutes
holds about 200 guide samples, enough for a steady root-mean-square (RMS)
error, and it is short enough to follow a change in guiding within a few
minutes. A caller may ask for another length. Design estimate, register
entry WAY-D136.
"""

RECENT_EXPOSURE_COUNT = 10
"""How many of the latest exposures the status lists."""

STALE_RECORD_SECONDS = TunedParameter(
    name="wayfinding.stale_record_seconds",
    default=300.0,
    unit="s",
    description="A file with no new record for this long is reported as stale.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="WAY-D136",
    allowed_range=(30.0, 3600.0),
    minimum_sample=1000,  # a sample is one gap between consecutive guide samples in a night
)
"""A file with no new record for this long is reported as stale.

Ekos writes a guide measurement every few seconds while guiding, so a long
silence means guiding or Ekos itself has stopped. The tuner
(`live_status_tuning.STALE_RECORD_TUNER`) sets it to 3 times the 99.9th
percentile of the gaps between consecutive guide samples in a night (that
percentile was about 105 s over 17 sessions, so about 315 s). The default of
300 s is that result rounded.
"""


def _rms(values: Sequence[float]) -> float | None:
    """Return the root-mean-square of `values`, or `None` if empty.

    Parameters
    ----------
    values : `Sequence` [`float`]
        The numbers to combine.

    Returns
    -------
    rms : `float` or `None`
        The root-mean-square.
    """
    if not values:
        return None
    return math.sqrt(sum(value * value for value in values) / len(values))


def _total_error(sample: EkosGuideStat) -> float:
    """Combine one sample's RA and Dec errors into a single distance.

    Parameters
    ----------
    sample : `EkosGuideStat`
        One guiding measurement.

    Returns
    -------
    error_arcsec : `float`
        The distance of the guide star from its target, in arcseconds.
    """
    return math.hypot(sample.ra_error_arcsec, sample.dec_error_arcsec)


def summarize_guiding_window(
    guide_stats: Sequence[EkosGuideStat], end_time: float, window_s: float
) -> GuidingWindowSummary:
    """Summarize guiding accuracy over the stretch ending at `end_time`.

    Parameters
    ----------
    guide_stats : `Sequence` [`EkosGuideStat`]
        All guiding measurements, in time order.
    end_time : `float`
        End of the window, in seconds since the Unix epoch.
    window_s : `float`
        Length of the window.

    Returns
    -------
    summary : `GuidingWindowSummary`
        Root-mean-square errors over the window.
    """
    inside = [s for s in guide_stats if end_time - window_s < s.timestamp <= end_time]
    return GuidingWindowSummary(
        window_s=window_s,
        sample_count=len(inside),
        ra_rms_arcsec=_rms([s.ra_error_arcsec for s in inside]),
        dec_rms_arcsec=_rms([s.dec_error_arcsec for s in inside]),
        total_rms_arcsec=_rms([_total_error(s) for s in inside]),
    )


def _axis_error(sample: EkosGuideStat, axis: str) -> float:
    """Return one sample's error along `axis`.

    Parameters
    ----------
    sample : `EkosGuideStat`
        One guiding measurement.
    axis : `str`
        ``"RA"`` or ``"Dec"``.

    Returns
    -------
    error_arcsec : `float`
        The signed error along that axis, in arcseconds.
    """
    return sample.ra_error_arcsec if axis == "RA" else sample.dec_error_arcsec


def _drift_pieces(
    run: Sequence[EkosGuideStat], reacquire_times: Sequence[float]
) -> list[list[EkosGuideStat]]:
    """Cut an excursion into pieces at every guider re-lock.

    Each re-lock resets the error, so one straight-line fit across a whole
    excursion would underestimate the drift.

    Parameters
    ----------
    run : `Sequence` [`EkosGuideStat`]
        The measurements inside one excursion, in time order.
    reacquire_times : `Sequence` [`float`]
        When the guider re-locked on its star.

    Returns
    -------
    pieces : `list` [`list` [`EkosGuideStat`]]
        The measurements between re-locks, long enough to fit a line.
    """
    cuts = sorted(t for t in reacquire_times if run[0].timestamp < t < run[-1].timestamp)
    pieces: list[list[EkosGuideStat]] = [[]]
    cut_index = 0
    for sample in run:
        while cut_index < len(cuts) and sample.timestamp > cuts[cut_index]:
            pieces.append([])
            cut_index += 1
        pieces[-1].append(sample)
    minimum = resolve_parameter(MINIMUM_SAMPLES_FOR_DRIFT_FIT).value
    return [piece for piece in pieces if len(piece) >= minimum]


def _piece_fits(pieces: Sequence[Sequence[EkosGuideStat]], axis: str) -> list[tuple[float, float]]:
    """Fit a line to the error along one axis in each piece.

    Parameters
    ----------
    pieces : `Sequence` [`Sequence` [`EkosGuideStat`]]
        Pieces of an excursion, as from `_drift_pieces`.
    axis : `str`
        ``"RA"`` or ``"Dec"``.

    Returns
    -------
    fits : `list` [`tuple` [`float`, `float`]]
        The slope and its standard error for each piece that could be fitted.
    """
    fits = []
    for piece in pieces:
        times = [sample.timestamp - piece[0].timestamp for sample in piece]
        errors = [sample.ra_error_arcsec if axis == "RA" else sample.dec_error_arcsec for sample in piece]
        fit = fit_slope(times, errors)
        if fit is not None:
            fits.append(fit)
    return fits


def _median_drift_rate(
    run: Sequence[EkosGuideStat], axis: str, reacquire_times: Sequence[float]
) -> float | None:
    """Estimate how fast the guide error grew during an excursion.

    The excursion is cut at every re-lock (see `_drift_pieces`), each piece
    is fitted on its own, and the median slope is returned.

    Parameters
    ----------
    run : `Sequence` [`EkosGuideStat`]
        The measurements inside one excursion, in time order.
    axis : `str`
        ``"RA"`` or ``"Dec"``: the axis to fit.
    reacquire_times : `Sequence` [`float`]
        When the guider re-locked on its star.

    Returns
    -------
    rate : `float` or `None`
        The drift in arcseconds per second, or `None` if no piece has at
        least three measurements.
    """
    sigmas = float(resolve_parameter(EXCURSION_DRIFT_SIGNIFICANCE_SIGMAS).value)
    slopes = []
    for piece in _drift_pieces(run, reacquire_times):
        times = [sample.timestamp - piece[0].timestamp for sample in piece]
        errors = [sample.ra_error_arcsec if axis == "RA" else sample.dec_error_arcsec for sample in piece]
        # A slope within the noise counts as no drift.
        slopes.append(abs(significant_slope(times, errors, sigmas)))
    return statistics.median(slopes) if slopes else None


def matches_stopped_mount(fits: Sequence[tuple[float, float]], declination_deg: float) -> bool | None:
    """Say whether a drift is statistically the drift of a stopped RA axis.

    A mount that stopped tracking lets the sky turn past at the sidereal
    rate times the cosine of the declination. The measured rate is the
    median over the pieces of an excursion of the fitted slope's size. It
    matches when it lies within `EXCURSION_DRIFT_SIGNIFICANCE_SIGMAS`
    standard errors of the stopped rate. The standard error of the median is
    taken as that of the middle piece (for an even count, of the average of
    the two middle pieces). A fit with no scatter has no standard error, so
    it matches only when its rate equals the stopped rate to rounding.

    Parameters
    ----------
    fits : `Sequence` [`tuple` [`float`, `float`]]
        The slope and its standard error of each piece of the excursion.
    declination_deg : `float`
        The mount's declination.

    Returns
    -------
    matches : `bool` or `None`
        Whether the drift matches a stopped axis, or `None` when no piece
        could be fitted.
    """
    if not fits:
        return None
    ranked = sorted(fits, key=lambda fit: abs(fit[0]))
    middle = len(ranked) // 2
    if len(ranked) % 2:
        rate = abs(ranked[middle][0])
        standard_error = ranked[middle][1]
    else:
        low, high = ranked[middle - 1], ranked[middle]
        rate = (abs(low[0]) + abs(high[0])) / 2.0
        standard_error = math.hypot(low[1], high[1]) / 2.0
    stopped_rate = SIDEREAL_RATE_ARCSEC_PER_SECOND * math.cos(math.radians(declination_deg))
    sigmas = float(resolve_parameter(EXCURSION_DRIFT_SIGNIFICANCE_SIGMAS).value)
    return abs(rate - stopped_rate) <= sigmas * standard_error + 1e-9 * stopped_rate


def find_guiding_excursions(
    guide_stats: Sequence[EkosGuideStat],
    reacquire_times: Sequence[float],
    declination_deg: float | None,
) -> list[GuidingExcursion]:
    """Find the stretches where the guide error ran far above normal.

    Parameters
    ----------
    guide_stats : `Sequence` [`EkosGuideStat`]
        All guiding measurements, in time order.
    reacquire_times : `Sequence` [`float`]
        When the guider re-locked on its star.
    declination_deg : `float` or `None`
        The mount's declination, used to say whether an RA drift matches a
        stopped mount.

    Returns
    -------
    excursions : `list` [`GuidingExcursion`]
        Each over-limit stretch, in time order.
    """
    if not guide_stats:
        return []
    median_error = statistics.median(_total_error(s) for s in guide_stats)
    limit = max(
        resolve_parameter(EXCURSION_MEDIAN_MULTIPLE).value * median_error,
        resolve_parameter(EXCURSION_FLOOR_ARCSEC).value,
    )

    runs: list[list[EkosGuideStat]] = []
    for sample in guide_stats:
        if _total_error(sample) <= limit:
            continue
        if (
            runs
            and sample.timestamp - runs[-1][-1].timestamp <= resolve_parameter(EXCURSION_JOIN_SECONDS).value
        ):
            runs[-1].append(sample)
        else:
            runs.append([sample])

    excursions = []
    for run in runs:
        ra_peak = max(abs(s.ra_error_arcsec) for s in run)
        dec_peak = max(abs(s.dec_error_arcsec) for s in run)
        axis = "RA" if ra_peak >= dec_peak else "Dec"
        rate = _median_drift_rate(run, axis, reacquire_times)
        matches_stopped = None
        if axis == "RA" and rate is not None and declination_deg is not None:
            fits = _piece_fits(_drift_pieces(run, reacquire_times), "RA")
            matches_stopped = matches_stopped_mount(fits, declination_deg)
        started_at, ended_at = run[0].timestamp, run[-1].timestamp
        peak_error = max(_total_error(s) for s in run)
        kind = (
            "jump"
            if _total_error(run[0]) >= resolve_parameter(JUMP_FIRST_SAMPLE_FRACTION).value * peak_error
            else "drift"
        )
        excursions.append(
            GuidingExcursion(
                started_at=started_at,
                ended_at=ended_at,
                peak_error_arcsec=peak_error,
                axis=axis,
                kind=kind,
                drift_rate_arcsec_per_s=rate,
                matches_stopped_ra_axis=matches_stopped,
                reacquire_count=sum(1 for t in reacquire_times if started_at - 10.0 <= t <= ended_at + 10.0),
            )
        )
    return excursions


def _target_name_from_path(file_path: str) -> str:
    """Read the target's name from where Ekos saved a frame.

    Ekos saves ``<root>/<target>/Light/<filter>/<file>``. The part before the
    ``Light`` folder is the target. A path with no ``Light`` folder gives an
    empty name, which then matches only other frames saved the same way.

    Parameters
    ----------
    file_path : `str`
        Where Ekos saved the frame.

    Returns
    -------
    target_name : `str`
        The target's name, or an empty string if the path does not show one.
    """
    parts = file_path.replace("\\", "/").split("/")
    for index, part in enumerate(parts):
        if part.lower() == "light" and index > 0:
            return parts[index - 1]
    return ""


def _baseline_key(capture: EkosCapture) -> tuple[str, str, float]:
    """Name the setting whose frames a capture should be compared with.

    Stars counts depend on the target, the filter and the exposure length, so
    a frame is compared only with frames that share all three.

    Parameters
    ----------
    capture : `EkosCapture`
        One finished exposure.

    Returns
    -------
    key : `tuple` [`str`, `str`, `float`]
        The target name, the filter name and the exposure in seconds
        (rounded to a millisecond).
    """
    return (
        _target_name_from_path(capture.file_path),
        capture.filter_name.strip().lower(),
        round(capture.exposure_s, 3),
    )


def star_count_baseline(captures: Sequence[EkosCapture], index: int) -> list[int]:
    """List the star counts the frame at `index` is compared with.

    The baseline is the unbroken run of frames just before this one that
    share its target, filter and exposure. It restarts when any of the three
    changes, so a switch to another target, filter or exposure never flags
    the first frames after it as having too few stars. At most
    `MAXIMUM_FRAMES_IN_STAR_BASELINE` frames are used.

    Parameters
    ----------
    captures : `Sequence` [`EkosCapture`]
        Finished exposures, in time order.
    index : `int`
        Position of the frame being judged.

    Returns
    -------
    star_counts : `list` [`int`]
        The star counts of the earlier frames of the run, oldest first.
    """
    key = _baseline_key(captures[index])
    start = index
    while (
        start > 0
        and index - start < resolve_parameter(MAXIMUM_FRAMES_IN_STAR_BASELINE).value
        and _baseline_key(captures[start - 1]) == key
    ):
        start -= 1
    return [capture.star_count for capture in captures[start:index]]


def measured_settle_seconds(guide_state_events: Sequence[EkosStateEvent]) -> list[tuple[float, float]]:
    """Measure each dither's settle time from the guider's state changes.

    Ekos logs ``Settling`` when the guider starts waiting for the star to
    steady after a move, and ``Dithering successful`` when it is done.

    Parameters
    ----------
    guide_state_events : `Sequence` [`EkosStateEvent`]
        The guider's state changes, in time order.

    Returns
    -------
    settles : `list` [`tuple` [`float`, `float`]]
        For each settle that finished, when it began and how long it took.
    """
    settles = []
    began_at: float | None = None
    for event in guide_state_events:
        if event.state == "Settling":
            began_at = event.timestamp
        elif event.state == "Dithering successful" and began_at is not None:
            settles.append((began_at, event.timestamp - began_at))
            began_at = None
        elif event.state == "Dithering":
            began_at = None
    return settles


def dither_settle_seconds(
    dithers: Sequence[DitherEvent], guide_state_events: Sequence[EkosStateEvent]
) -> list[float | None]:
    """Give each dither's settle time, with a session value for the gaps.

    A dither's own settle is the first measured settle that begins after the
    dither starts and before the next dither starts. A dither with none of
    its own gets the `SETTLE_PERCENTILE` percentile of the session's settles.

    Parameters
    ----------
    dithers : `Sequence` [`DitherEvent`]
        Dithers read from the KStars log, in time order.
    guide_state_events : `Sequence` [`EkosStateEvent`]
        The guider's state changes.

    Returns
    -------
    settles : `list` [`float` or `None`]
        One settle time in seconds per dither, or `None` where the session
        has no measured settle at all.
    """
    measured = measured_settle_seconds(guide_state_events)
    if not measured:
        return [None] * len(dithers)
    session_settle = percentile([duration for _began, duration in measured], SETTLE_PERCENTILE.default)
    settles: list[float | None] = []
    for index, dither in enumerate(dithers):
        end = dithers[index + 1].timestamp if index + 1 < len(dithers) else math.inf
        own = [duration for began, duration in measured if dither.timestamp <= began < end]
        settles.append(own[0] if own else session_settle)
    return settles


def dither_follow_seconds(exposure_s: float | None, settle_s: float | None) -> float:
    """Give how long before an exposure's start a dither still led into it.

    The exposure that follows a dither starts when the settle ends, and the
    settle starts when the dither does, so a dither led into an exposure if
    it began within the settle plus one exposure length of the exposure's
    start.

    Parameters
    ----------
    exposure_s : `float` or `None`
        The capture's own exposure length.
    settle_s : `float` or `None`
        The dither's settle time, or the session's when it has none.

    Returns
    -------
    seconds : `float`
        Exposure plus settle, or the fallback `DITHER_FOLLOW_SECONDS` when
        either is unknown.
    """
    if exposure_s is None or settle_s is None or exposure_s <= 0.0:
        return float(resolve_parameter(DITHER_FOLLOW_SECONDS).value)
    return exposure_s + settle_s


def summarize_exposures(
    captures: Sequence[EkosCapture],
    guide_stats: Sequence[EkosGuideStat],
    excursions: Sequence[GuidingExcursion],
    dithers: Sequence[DitherEvent],
    count: int = RECENT_EXPOSURE_COUNT,
    guide_state_events: Sequence[EkosStateEvent] = (),
) -> list[ExposureSummary]:
    """Summarize the latest exposures and flag the ones that look ruined.

    Parameters
    ----------
    captures : `Sequence` [`EkosCapture`]
        Finished exposures, in time order.
    guide_stats : `Sequence` [`EkosGuideStat`]
        All guiding measurements.
    excursions : `Sequence` [`GuidingExcursion`]
        Excursions already found in the session.
    dithers : `Sequence` [`DitherEvent`]
        Dithers read from the KStars log.
    count : `int`, optional
        How many of the latest exposures to return.
    guide_state_events : `Sequence` [`EkosStateEvent`], optional
        The guider's state changes, from which each dither's own settle time
        is measured. Without them a dither follows an exposure within the
        fallback `DITHER_FOLLOW_SECONDS`.

    Returns
    -------
    exposures : `list` [`ExposureSummary`]
        The latest `count` exposures, oldest first.
    """
    settles = dither_settle_seconds(dithers, guide_state_events)
    summaries = []
    for index in range(max(0, len(captures) - count), len(captures)):
        capture = captures[index]
        started_at = capture.completed_at - capture.exposure_s
        inside = [s for s in guide_stats if started_at <= s.timestamp <= capture.completed_at]
        early = [
            _total_error(s)
            for s in inside
            if s.timestamp < started_at + resolve_parameter(EARLY_EXPOSURE_SECONDS).value
        ]
        rest = [
            _total_error(s)
            for s in inside
            if s.timestamp >= started_at + resolve_parameter(EARLY_EXPOSURE_SECONDS).value
        ]

        flags: list[str] = []
        plain_flags: list[str] = []
        earlier_star_counts = star_count_baseline(captures, index)
        if len(earlier_star_counts) >= resolve_parameter(MINIMUM_FRAMES_FOR_STAR_BASELINE).value:
            usual = statistics.median(earlier_star_counts)
            if usual > 0 and capture.star_count < resolve_parameter(LOW_STAR_FRACTION).value * usual:
                flags.append(
                    f"Only {capture.star_count} stars against a usual {usual:.0f}: likely trailed or clouded."
                )
                plain_flags.append(
                    f"This frame shows only {capture.star_count} stars against about {usual:.0f} in the "
                    "frames before it, so it may be trailed or clouded."
                )
        for excursion in excursions:
            if excursion.ended_at >= started_at and excursion.started_at <= capture.completed_at:
                peak = excursion.peak_error_arcsec
                flags.append(f"A guiding excursion (peak {peak:.0f} arcsec) overlapped this exposure.")
                plain_flags.append(
                    f"The guiding drifted by up to {peak:.0f} arcsec during this frame, "
                    "so the stars may be smeared."
                )
                break

        summaries.append(
            ExposureSummary(
                frame_name=capture.file_path.rsplit("/", 1)[-1],
                completed_at=capture.completed_at,
                exposure_s=capture.exposure_s,
                star_count=capture.star_count,
                half_flux_radius_px=capture.half_flux_radius_px,
                eccentricity=capture.eccentricity,
                guide_rms_first_seconds_arcsec=_rms(early),
                guide_rms_rest_arcsec=_rms(rest),
                follows_dither=any(
                    0.0 <= started_at - d.timestamp <= dither_follow_seconds(capture.exposure_s, settle)
                    for d, settle in zip(dithers, settles, strict=True)
                ),
                flags=flags,
                plain_flags=plain_flags,
            )
        )
    return summaries


MOUNT_POSITION_UNAVAILABLE = (
    "The mount's right ascension, and the plate-solved position of the latest frame: the Ekos log "
    "records only declination, altitude, azimuth and pier side for the mount."
)
"""What the live status cannot say about where the mount points."""

GUIDE_SETTINGS_UNAVAILABLE = (
    "Guide algorithm and its settings (aggression, hysteresis): the PHD2 guide log that Ekos keeps does "
    "not record them, so they cannot be compared between nights."
)
"""Said in every status, because the logs never hold these values."""


def summarize_dither_settings(dithers: Sequence[DitherEvent], captures_completed: int) -> DitherSettings:
    """Describe how the session has been dithering.

    Parameters
    ----------
    dithers : `Sequence` [`DitherEvent`]
        Dithers read from the KStars log.
    captures_completed : `int`
        Finished exposures in the session.

    Returns
    -------
    settings : `DitherSettings`
        The count, failures, typical move and frames per dither.
    """
    if not dithers:
        return DitherSettings()
    amplitudes = [dither.amplitude_px for dither in dithers]
    return DitherSettings(
        dither_count=len(dithers),
        failed_count=sum(1 for dither in dithers if dither.succeeded is False),
        last_amplitude_px=amplitudes[-1],
        median_amplitude_px=statistics.median(amplitudes),
        exposures_per_dither=round(captures_completed / len(dithers), 1),
    )


def plain_verdict(plain_flags: Sequence[str]) -> str:
    """Say in everyday words how the session is going.

    The verdict is built from the plain flags only, so it never claims more
    than the checks found. It is a rough reading of Ekos' own records.

    Parameters
    ----------
    plain_flags : `Sequence` [`str`]
        The plain-language flags of the session, most important first.

    Returns
    -------
    verdict : `str`
        One sentence when nothing is flagged, otherwise the count of things
        to check and the first one.
    """
    if not plain_flags:
        return "No problems found so far in what Ekos has recorded."
    count = len(plain_flags)
    thing = "thing" if count == 1 else "things"
    return f"{count} {thing} to check. {plain_flags[0]}"


def summarize_live_session(
    context: EkosSessionContext,
    guide_stats: Sequence[EkosGuideStat],
    dithers: Sequence[DitherEvent],
    analyze_file: str,
    kstars_log_file: str | None = None,
    window_s: float | None = None,
    wall_clock_now: float | None = None,
) -> LiveSessionStatus:
    """Build the status of the current session from its Ekos records.

    Parameters
    ----------
    context : `EkosSessionContext`
        The parsed analyze log, without the guiding samples.
    guide_stats : `Sequence` [`EkosGuideStat`]
        The guiding measurements from the same log.
    dithers : `Sequence` [`DitherEvent`]
        Dithers read from the KStars log; empty if no log was available.
    analyze_file, kstars_log_file : `str`
        The files the records came from, for the report.
    window_s : `float`, optional
        Length of the recent-guiding window. `None` uses
        `LIVE_STATUS_WINDOW_SECONDS` (ten minutes).
    wall_clock_now : `float`, optional
        Current time, to measure how stale the file is. Defaults to the
        system clock; tests pass a fixed value.

    Returns
    -------
    status : `LiveSessionStatus`
        The session's current state and any problems found.
    """
    if window_s is None:
        window_s = resolve_parameter(LIVE_STATUS_WINDOW_SECONDS).value
    timestamps = [context.ended_at]
    if guide_stats:
        timestamps.append(guide_stats[-1].timestamp)
    as_of = max(timestamps)
    now = time.time() if wall_clock_now is None else wall_clock_now

    declination = context.mount_positions[-1].declination_deg if context.mount_positions else None
    reacquire_times = [e.timestamp for e in context.guide_state_events if e.state == "Reacquiring"]
    excursions = find_guiding_excursions(guide_stats, reacquire_times, declination)
    exposures = summarize_exposures(
        context.captures, guide_stats, excursions, dithers, guide_state_events=context.guide_state_events
    )

    successful_focus = [run for run in context.autofocus_runs if run.succeeded]
    last_focus = successful_focus[-1] if successful_focus else None
    last_guide_state = context.guide_state_events[-1] if context.guide_state_events else None

    flags: list[str] = []
    plain_flags: list[str] = []
    for excursion in reversed(excursions):
        peak = excursion.peak_error_arcsec
        plain_flags.append(
            f"The guiding {'jumped' if excursion.kind == 'jump' else 'drifted'} by up to {peak:.0f} arcsec "
            f"along the {excursion.axis} axis. Check the mount and the guide star."
        )
        text = (
            f"Guiding {excursion.kind}: {excursion.axis} error peaked at {peak:.0f} arcsec"
            f" with {excursion.reacquire_count} re-lock(s)."
        )
        if excursion.kind == "drift" and excursion.matches_stopped_ra_axis:
            text += (
                f" The {excursion.drift_rate_arcsec_per_s:.1f} arcsec/s drift matches an RA axis that"
                " stopped tracking."
            )
        flags.append(text)
    finished_dithers = [d for d in dithers if d.succeeded is not None]
    failed = [d for d in finished_dithers if d.succeeded is False]
    if failed:
        flags.append(f"{len(failed)} of {len(finished_dithers)} dithers failed.")
        plain_flags.append(
            f"{len(failed)} of {len(finished_dithers)} small telescope moves between frames failed. "
            "Check that the guider settles after each move."
        )
    flagged_frames = [e.frame_name for e in exposures if e.flags]
    if flagged_frames:
        flags.append(f"Frames to check: {', '.join(flagged_frames)}.")
        plain_flags.append(
            f"{len(flagged_frames)} recent frames are flagged. Look at them before you keep them."
        )
    if last_guide_state is not None and last_guide_state.state != "Guiding":
        flags.append(f"The guider is in state {last_guide_state.state!r}, not Guiding.")
        plain_flags.append(f"The guider is not guiding right now (it is {last_guide_state.state.lower()}).")
    silence = now - as_of
    if silence > resolve_parameter(STALE_RECORD_SECONDS).value:
        flags.append(
            f"No new record for {silence / 60:.0f} minutes: Ekos may have stopped, or the file is stale."
        )
        plain_flags.append(
            f"Ekos has written nothing for {silence / 60:.0f} minutes. It may have stopped, so check it."
        )

    return LiveSessionStatus(
        analyze_file=analyze_file,
        kstars_log_file=kstars_log_file,
        as_of=as_of,
        time_since_last_record_s=silence,
        guider_state=last_guide_state.state if last_guide_state else None,
        guider_state_since=last_guide_state.timestamp if last_guide_state else None,
        declination_deg=declination,
        temperature_c=context.temperatures[-1].temperature_c if context.temperatures else None,
        pier_side=context.mount_positions[-1].pier_side if context.mount_positions else None,
        captures_completed=len(context.captures),
        captures_aborted=len(context.aborted_captures),
        dither_settings=summarize_dither_settings(dithers, len(context.captures)),
        unavailable=[GUIDE_SETTINGS_UNAVAILABLE, MOUNT_POSITION_UNAVAILABLE],
        recent_guiding=summarize_guiding_window(guide_stats, as_of, window_s),
        exposures=exposures,
        excursions=excursions,
        dithers=list(dithers),
        last_autofocus_at=last_focus.timestamp if last_focus else None,
        last_autofocus_temperature_c=last_focus.temperature_c if last_focus else None,
        flags=flags,
        plain_flags=plain_flags,
        plain_verdict=plain_verdict(plain_flags),
    )
