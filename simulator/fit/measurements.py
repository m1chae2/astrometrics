"""Purpose: Read the fit's measurements through the libraries' public API.

Description: `collect_measurements` asks a `Wayfinder` (opened on the
simulator world's library copy) for what the recorded nights say about the
equipment, and returns plain `Candidate` records. Every candidate names the
public call and parameters that produced it, the nights it rests on, a source
kind from the fallback order (newest real record, history fit, envelope,
default), and a date. Nothing is stored and the library is not written
(the calls pass ``register_job=False`` where they could record a job).

The fallback order is, from best to worst: the newest real record, a fit to
the recorded history, the performance envelope, then the simulator's own
default. A row may offer several candidates; `simulator.fit.fitting` takes the
first one in that order that has data.

A reply marked ``truncated`` is refused (`TruncatedReplyError`): a cut-short
list could be missing the newest record, and the fit must not use half an
answer. A failed or empty call is a status, not an error: it adds a note and
no candidate, and the row falls back one step.

Read this file with the library's guiding vocabulary in mind: a run is one
stretch of guiding, a night is an observing night, and every length is in
arcseconds on the sky unless a name says otherwise.
"""

import logging
import math
import statistics
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from astrometricslib.exceptions import AstrometricsError

if TYPE_CHECKING:
    from wayfindinglib import Wayfinder

LOGGER = logging.getLogger(__name__)

__all__ = [
    "SOURCE_ORDER",
    "Candidate",
    "Measurements",
    "SourceKind",
    "TruncatedReplyError",
    "collect_measurements",
]

SourceKind = Literal["newest real record", "history fit", "performance envelope", "simulator default"]
#: The fallback order, best first.
SOURCE_ORDER: tuple[SourceKind, ...] = (
    "newest real record",
    "history fit",
    "performance envelope",
    "simulator default",
)
#: The most recent guiding runs the controller settings are read from.
GUIDING_RUN_LIMIT = 50
#: The run counts asked for in turn when a reply is too large and cut short.
GUIDING_RUN_LIMITS = (GUIDING_RUN_LIMIT, 20, 5)
#: Sidereal hours per day of Universal Time, and Greenwich mean sidereal time
#: (hours) at 2000 January 1, 12:00 UT (the standard linear formula; error
#: well under a minute of time, which is enough to point at the meridian).
SIDEREAL_HOURS_PER_DAY = 24.06570982441908
SIDEREAL_HOURS_AT_J2000 = 18.697374558
#: Unix time of 2000 January 1, 12:00 UT.
J2000_EPOCH_SECONDS = 946_728_000.0
#: The fewest first attempts a go-to offset is fitted from.
GOTO_MINIMUM_ATTEMPTS = 5
#: A gap longer than this between two solves of one target starts a new
#: target session (the same rule as the library's grouping of attempts).
GOTO_SESSION_GAP_SECONDS = 7200.0
#: The night argument that reads every night.
GOTO_SESSION_ID = "all"
#: If the median offset is smaller than this fraction of the median error,
#: the errors scatter in direction and the systematic part is small.
GOTO_SYSTEMATIC_FRACTION = 0.5
#: Calibrations whose note starts with this were made for a simulator, not
#: measured on the real mount.
SIMULATOR_NOTE_PREFIX = "Simulator"


class TruncatedReplyError(RuntimeError):
    """A library reply was cut short, so the fit refuses to use it."""


@dataclass(frozen=True)
class Candidate:
    """One measurement that could set a row of the fit.

    Attributes
    ----------
    kind : `SourceKind`
        Where it ranks in the fallback order.
    values : `dict` [`str`, `float`]
        The measured numbers, named as the row's converter expects.
    call : `str`
        The public call that produced it.
    parameters : `dict`
        The call's parameters.
    nights : `list` [`str`]
        The observing nights it rests on (empty when the call does not say).
    note : `str`
        One plain sentence about the number, including its limits.
    date : `str` or `None`
        The date of the record or of the newest night it rests on.
    """

    kind: SourceKind
    values: dict[str, float]
    call: str
    parameters: dict[str, Any]
    nights: list[str]
    note: str
    date: str | None = None


@dataclass
class Measurements:
    """What the fit found for each row.

    Attributes
    ----------
    candidates : `dict` [`str`, `list` [`Candidate`]]
        For each row name, the candidates found, in any order.
    notes : `list` [`str`]
        Statuses: a call that failed, an empty answer, a refused reply.
    """

    candidates: dict[str, list[Candidate]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def add(self, row: str, candidate: Candidate) -> None:
        """Add a candidate to a row.

        Parameters
        ----------
        row : `str`
            The row name.
        candidate : `Candidate`
            The measurement.
        """
        self.candidates.setdefault(row, []).append(candidate)


def _date(epoch_seconds: float | None) -> str | None:
    """Write an epoch time as a UTC date.

    Returns
    -------
    day : `str` or `None`
        ``YYYY-MM-DD``, or `None` for no time.
    """
    if epoch_seconds is None:
        return None
    return datetime.fromtimestamp(epoch_seconds, UTC).date().isoformat()


def _pick(row: Mapping[str, Any], name: str) -> Any:
    """Read a field of a plain-data row, in snake case or camel case.

    Returns
    -------
    value : `object`
        The value, or `None` when the row lacks both spellings.
    """
    if name in row:
        return row[name]
    parts = name.split("_")
    camel = parts[0] + "".join(part.title() for part in parts[1:])
    return row.get(camel)


def _refuse_truncated(reply: Any, call: str) -> None:
    """Raise when a reply says it was cut short.

    Parameters
    ----------
    reply : `object`
        The reply, which may have a ``truncated`` flag.
    call : `str`
        The call's name, for the message.

    Raises
    ------
    TruncatedReplyError
        If ``reply.truncated`` is true.
    """
    if getattr(reply, "truncated", False):
        raise TruncatedReplyError(f"{call} returned a truncated reply; it is not used.")


def _guard(measurements: Measurements, label: str, work: Callable[[], None]) -> None:
    """Run one collection step; a failure becomes a note.

    Parameters
    ----------
    measurements : `Measurements`
        Receives the note.
    label : `str`
        The step's name.
    work : `~collections.abc.Callable`
        The step.
    """
    try:
        work()
    except (AstrometricsError, TruncatedReplyError, OSError, ValueError, AttributeError, KeyError) as error:
        LOGGER.warning("Fit: %s was not available: %s", label, error)
        measurements.notes.append(f"{label}: {type(error).__name__}: {error}")


def _guide_rate(wayfinder: Wayfinder, measurements: Measurements) -> None:
    """Collect the guide rate: the newest real calibration, then a run header.

    Parameters
    ----------
    wayfinder : `Wayfinder`
        The entry point.
    measurements : `Measurements`
        Receives the candidates.
    """
    records = [
        record
        for record in wayfinder.control.guiding.query_calibrations()
        if not record.source_note.startswith(SIMULATOR_NOTE_PREFIX)
    ]
    if records:
        newest = records[-1]
        measurements.add(
            "guide_rate",
            Candidate(
                "newest real record",
                {"dec_rate_arcsec_per_s": newest.dec_rate_arcsec_per_sec},
                "wayfinder.control.guiding.query_calibrations",
                {},
                [],
                f"Guider calibration {newest.id!r}: the mount's Dec guide speed.",
                _date(newest.calibrated_at.timestamp()),
            ),
        )
    reply, limit = None, GUIDING_RUN_LIMIT
    for limit in GUIDING_RUN_LIMITS:
        # A reply too large to send is cut short; ask again for fewer runs
        # rather than use a cut one (the newest runs are the ones used).
        reply = wayfinder.review.query(kind="guiding_runs", limit=limit, register_job=False)
        if not getattr(reply, "truncated", False):
            break
    _refuse_truncated(reply, "wayfinder.review.query(kind='guiding_runs')")
    runs = [run for run in (reply.runs or []) if isinstance(run, dict)]
    for run in reversed(runs):
        rate = _pick(run, "dec_rate_arcsec_per_second")
        if rate:
            measurements.add(
                "guide_rate",
                Candidate(
                    "history fit",
                    {"dec_rate_arcsec_per_s": float(rate)},
                    "wayfinder.review.query",
                    {"kind": "guiding_runs", "limit": limit},
                    [str(_pick(run, "session_id"))],
                    "The Dec guide speed in the newest guiding run's log header.",
                    _date(_pick(run, "started_at")),
                ),
            )
            break
    controller = _controller_candidate(runs, limit)
    if controller is not None:
        measurements.add("guide_controller", controller)


def _controller_candidate(runs: list[dict], limit: int = GUIDING_RUN_LIMIT) -> Candidate | None:
    """Build the controller candidate from the newest PHD2 run that has one.

    Parameters
    ----------
    runs : `list` [`dict`]
        Guiding run summaries, oldest first.
    limit : `int`, optional
        The run count the query asked for (recorded with the candidate).

    Returns
    -------
    candidate : `Candidate` or `None`
        The settings of the newest run that was guided by PHD2 and gave an
        aggressiveness, or `None`.
    """
    for run in reversed(runs):
        if _pick(run, "written_by_ekos"):
            continue
        aggressiveness = _pick(run, "ra_aggressiveness")
        if aggressiveness is None:
            continue
        values: dict[str, float] = {"aggressiveness": float(aggressiveness)}
        for key, name in (
            ("minimum_move", "ra_minimum_move_px"),
            ("hysteresis", "ra_hysteresis"),
            ("exposure_seconds", "guide_exposure_seconds"),
        ):
            value = _pick(run, name)
            if value is not None:
                values[key] = float(value)
        return Candidate(
            "newest real record",
            values,
            "wayfinder.review.query",
            {"kind": "guiding_runs", "limit": limit},
            [str(_pick(run, "session_id"))],
            "The guide controller settings in the newest PHD2 run's log header (right ascension axis).",
            _date(_pick(run, "started_at")),
        )
    return None


def _mount_error(wayfinder: Wayfinder, measurements: Measurements) -> None:
    """Collect the periodic error and the slow and random disturbance.

    Parameters
    ----------
    wayfinder : `Wayfinder`
        The entry point.
    measurements : `Measurements`
        Receives the candidates.
    """
    result = wayfinder.review.measure_unguided_mount_error()
    summary = result.summary
    nights = list(summary.nights_used)
    date = nights[-1] if nights else None
    call = "wayfinder.review.measure_unguided_mount_error"
    periodic = summary.periodic_error
    if periodic is not None:
        measurements.add(
            "periodic_error",
            Candidate(
                "history fit",
                {
                    "amplitude_arcsec": periodic.amplitude_arcsec,
                    "period_seconds": periodic.period_seconds,
                },
                call,
                {},
                nights,
                f"Pooled over {periodic.runs_used} runs; zero to peak, on the sky. The phase cannot be set.",
                date,
            ),
        )
    if summary.ra is not None and summary.dec is not None:
        measurements.add(
            "guiding_disturbance",
            Candidate(
                "history fit",
                {
                    "ra_drift_arcsec_per_min": summary.ra.drift_median_arcsec_per_minute,
                    "dec_drift_arcsec_per_min": summary.dec.drift_median_arcsec_per_minute,
                    "ra_random_rms_arcsec": summary.ra.random_step_median_arcsec / math.sqrt(2.0),
                    "dec_random_rms_arcsec": summary.dec.random_step_median_arcsec / math.sqrt(2.0),
                },
                call,
                {},
                nights,
                "Median signed drift over the runs, and the frame-to-frame random part after the repeating "
                "error (the median step between frames divided by the square root of 2, the size of a "
                "jitter that is new every frame); the random terms include the guide star's measurement "
                "noise, so they are upper bounds.",
                date,
            ),
        )


def _backlash(wayfinder: Wayfinder, measurements: Measurements) -> None:
    """Collect the Dec backlash estimate.

    Parameters
    ----------
    wayfinder : `Wayfinder`
        The entry point.
    measurements : `Measurements`
        Receives the candidate.
    """
    analysis = wayfinder.review.measure_guiding_spectrum()
    if analysis.dec_backlash_estimate_ms is None:
        measurements.notes.append(f"Dec backlash: no estimate ({analysis.message or 'no message'}).")
        return
    measurements.add(
        "dec_backlash",
        Candidate(
            "history fit",
            {"backlash_ms": analysis.dec_backlash_estimate_ms},
            "wayfinder.review.measure_guiding_spectrum",
            {},
            [],
            "The Dec backlash estimate from the guiding spectrum analysis.",
            None,
        ),
    )


def _polar(wayfinder: Wayfinder, measurements: Measurements) -> None:
    """Collect the newest polar alignment record, as one measurement.

    Parameters
    ----------
    wayfinder : `Wayfinder`
        The entry point.
    measurements : `Measurements`
        Receives the candidate.
    """
    status = wayfinder.control.mount.get_polar_alignment()
    if status.total_error_arcsec is None:
        measurements.notes.append("Polar alignment: no stored run.")
        return
    measurements.add(
        "polar_error",
        Candidate(
            "newest real record",
            {"total_error_arcsec": status.total_error_arcsec},
            "wayfinder.control.mount.get_polar_alignment",
            {},
            [],
            "A single polar alignment measurement, not a trend.",
            _date(status.timestamp),
        ),
    )


def _median_absolute_deviation(values: list[float]) -> float:
    """Give the median absolute deviation of a list of numbers.

    Parameters
    ----------
    values : `list` [`float`]
        The numbers (not empty).

    Returns
    -------
    deviation : `float`
        The median of the distances from the median.
    """
    centre = statistics.median(values)
    return statistics.median(abs(value - centre) for value in values)


def _first_attempts(attempts: list[Any]) -> list[Any]:
    """Pick the first usable solve of each target session.

    A target session is one target's solves with no gap above
    `GOTO_SESSION_GAP_SECONDS`, so the same target on two nights counts
    twice. The first solve is the error before any correction.

    Parameters
    ----------
    attempts : `list`
        Alignment attempts with ``target_name``, ``timestamp``,
        ``delta_ra_arcsec``, ``delta_dec_arcsec`` and ``dec``.

    Returns
    -------
    first : `list`
        One attempt for each target session, in time order.
    """
    usable = [
        attempt
        for attempt in attempts
        if attempt.delta_ra_arcsec is not None
        and attempt.delta_dec_arcsec is not None
        and attempt.dec is not None
        and attempt.timestamp is not None
        and attempt.status != "failed"
    ]
    last_seen: dict[str, float] = {}
    first: list[Any] = []
    for attempt in sorted(usable, key=lambda item: item.timestamp):
        key = attempt.target_name or "unnamed"
        previous = last_seen.get(key)
        if previous is None or attempt.timestamp - previous > GOTO_SESSION_GAP_SECONDS:
            first.append(attempt)
        last_seen[key] = attempt.timestamp
    return first


def _mount_model(wayfinder: Wayfinder, measurements: Measurements) -> None:
    """Collect the mount's constant go-to offset from first-frame errors.

    Reads every recorded plate solve (all nights), keeps the first solve of
    each target session (the error before any correction) and takes the
    median signed offset on each axis: the systematic part of the go-to
    error. ``delta_ra_arcsec`` is a distance on the sky (already scaled by
    cos(dec)), so it is divided by cos(dec) of the solve to get the offset
    in right ascension itself. The median absolute deviation, the count and
    the typical error size go into the note. With fewer than
    `GOTO_MINIMUM_ATTEMPTS` first solves it adds a note and no candidate.

    Parameters
    ----------
    wayfinder : `Wayfinder`
        The entry point.
    measurements : `Measurements`
        Receives the candidate.
    """
    records = wayfinder.review.query_alignment_records(session_id=GOTO_SESSION_ID)
    first = _first_attempts(list(records.attempts))
    if len(first) < GOTO_MINIMUM_ATTEMPTS:
        measurements.notes.append(
            f"Go-to error: {len(first)} first attempts recorded; at least {GOTO_MINIMUM_ATTEMPTS} are needed."
        )
        return
    # Right ascension offset as an angle (arcsec): undo the record's cos(dec).
    ra_angle = [item.delta_ra_arcsec / max(math.cos(math.radians(item.dec)), 1e-3) for item in first]
    dec_offset = [item.delta_dec_arcsec for item in first]
    sky_ra = [item.delta_ra_arcsec for item in first]
    median_ra, median_dec = statistics.median(ra_angle), statistics.median(dec_offset)
    typical = statistics.median(math.hypot(a, b) for a, b in zip(sky_ra, dec_offset, strict=True))
    systematic = math.hypot(statistics.median(sky_ra), median_dec)
    note = (
        f"Median signed offset of {len(first)} first attempts (solved minus commanded): "
        f"{median_ra:.0f} arcsec in right ascension (MAD {_median_absolute_deviation(ra_angle):.0f}), "
        f"{median_dec:.0f} arcsec in declination (MAD {_median_absolute_deviation(dec_offset):.0f}); "
        f"the typical error is {typical:.0f} arcsec. Only this constant offset is simulated; "
        "the scatter around it is not."
    )
    if systematic < GOTO_SYSTEMATIC_FRACTION * typical:
        note += " The systematic part is small next to the typical error: the errors scatter in direction."
    nights = sorted({str(item.session_id) for item in first if item.session_id})
    measurements.add(
        "mount_model",
        Candidate(
            "history fit",
            {
                "ra_angle_median_arcsec": median_ra,
                "dec_median_arcsec": median_dec,
                "ra_angle_mad_arcsec": _median_absolute_deviation(ra_angle),
                "dec_mad_arcsec": _median_absolute_deviation(dec_offset),
                "typical_error_arcsec": typical,
                "count": float(len(first)),
            },
            "wayfinder.review.query_alignment_records",
            {"session_id": GOTO_SESSION_ID},
            nights,
            note,
            nights[-1] if nights else None,
        ),
    )


def _envelope(wayfinder: Wayfinder, measurements: Measurements) -> None:
    """Collect the median star width from the performance envelope.

    Parameters
    ----------
    wayfinder : `Wayfinder`
        The entry point.
    measurements : `Measurements`
        Receives the candidate.
    """
    envelope = wayfinder.review.get_performance_envelope()
    if envelope is None:
        measurements.notes.append("Performance envelope: no active telescope and camera.")
        return
    width = envelope.limit_inputs.get("guiding_rms_limit", {}).get("fwhm_arcsec")
    if width is None:
        measurements.notes.append("Performance envelope: no star width measured yet.")
        return
    measurements.add(
        "seeing",
        Candidate(
            "performance envelope",
            {"fwhm_arcsec": float(width)},
            "wayfinder.review.get_performance_envelope",
            {},
            [],
            "The median measured star width (Moffat full width at half maximum) of the equipment's light "
            "frames; it includes guiding blur and focus error, so it is an upper bound on the seeing.",
            None,
        ),
    )


def meridian_ra_degrees(epoch_seconds: float, longitude_deg: float) -> float:
    """Give the right ascension on the site's meridian at a time.

    This is the local mean sidereal time, from the standard linear formula
    for Greenwich mean sidereal time plus the east longitude.

    Parameters
    ----------
    epoch_seconds : `float`
        The time, as Unix seconds.
    longitude_deg : `float`
        The site's longitude, degrees east (negative west).

    Returns
    -------
    ra_deg : `float`
        Right ascension, 0 to 360 degrees.
    """
    days = (epoch_seconds - J2000_EPOCH_SECONDS) / 86_400.0
    greenwich_hours = SIDEREAL_HOURS_AT_J2000 + SIDEREAL_HOURS_PER_DAY * days
    return (greenwich_hours * 15.0 + longitude_deg) % 360.0


def _sky(
    wayfinder: Wayfinder,
    measurements: Measurements,
    epoch_seconds: float,
    ra_deg: float,
    dec_deg: float,
    longitude_deg: float | None = None,
) -> None:
    """Collect the predicted sky rate at the pointing and time.

    When the pointing is below the horizon at that time and the site's
    longitude is known, the prediction is made on the meridian at the same
    declination instead (the simulator's sky glow is one number for the
    whole sky, so the height the target would have at its best is the fair
    choice). The note says so.

    Parameters
    ----------
    wayfinder : `Wayfinder`
        The entry point.
    measurements : `Measurements`
        Receives the candidate.
    epoch_seconds : `float`
        The time of the prediction.
    ra_deg, dec_deg : `float`
        The pointing.
    longitude_deg : `float`, optional
        The site's longitude, degrees east, for the meridian fallback.
    """
    prediction = wayfinder.review.get_sky_rate_prediction(epoch_seconds, ra_deg, dec_deg)
    moved = ""
    if prediction.sky_rate_adu_per_s_per_px is None and prediction.setting_key and longitude_deg is not None:
        ra_deg = meridian_ra_degrees(epoch_seconds, longitude_deg)
        prediction = wayfinder.review.get_sky_rate_prediction(epoch_seconds, ra_deg, dec_deg)
        moved = " The target was below the horizon, so the rate is for its declination on the meridian."
    if prediction.sky_rate_adu_per_s_per_px is None:
        measurements.notes.append(f"Sky rate: {prediction.message}")
        return
    error = prediction.relative_error
    measurements.add(
        "sky_glow",
        Candidate(
            "history fit",
            {"sky_rate_adu_per_s_per_px": prediction.sky_rate_adu_per_s_per_px},
            "wayfinder.review.get_sky_rate_prediction",
            {"epoch_seconds": epoch_seconds, "ra_deg": ra_deg, "dec_deg": dec_deg},
            [],
            f"Sky model fitted to {prediction.nights_used} nights"
            + (f", error {error:.0%} on unseen nights" if error is not None else "")
            + ("" if prediction.reliable else "; the Moon is up and the model has no Moon term")
            + "."
            + moved,
            None,
        ),
    )


def _focus(wayfinder: Wayfinder, measurements: Measurements) -> None:
    """Collect the focus temperature slope (recorded only: it cannot be set).

    Parameters
    ----------
    wayfinder : `Wayfinder`
        The entry point.
    measurements : `Measurements`
        Receives the candidate.
    """
    fit = wayfinder.review.measure_focus_temperature_coefficient()
    if fit.coefficient_steps_per_c is None:
        measurements.notes.append(f"Focus drift: {fit.message}")
        return
    measurements.add(
        "focus_drift",
        Candidate(
            "history fit",
            {"steps_per_c": fit.coefficient_steps_per_c},
            "wayfinder.review.measure_focus_temperature_coefficient",
            {},
            list(fit.nights_used),
            f"Best focus moves {fit.coefficient_steps_per_c:.2f} focuser steps per degree C.",
            fit.nights_used[-1] if fit.nights_used else None,
        ),
    )


def collect_measurements(
    wayfinder: Wayfinder,
    ra_deg: float,
    dec_deg: float,
    epoch_seconds: float | None = None,
    longitude_deg: float | None = None,
) -> Measurements:
    """Read every measurement the fit can use.

    Parameters
    ----------
    wayfinder : `Wayfinder`
        The entry point, opened on the simulator world's library copy.
    ra_deg, dec_deg : `float`
        Where the simulated telescope points (for the sky rate).
    epoch_seconds : `float`, optional
        The time of the sky rate prediction. Default: now.
    longitude_deg : `float`, optional
        The site's longitude, degrees east. With it, a pointing below the
        horizon is moved to the meridian for the sky rate.

    Returns
    -------
    measurements : `Measurements`
        The candidates found and the notes about what was missing.
    """
    moment = time.time() if epoch_seconds is None else epoch_seconds
    result = Measurements()
    steps: list[tuple[str, Callable[[], None]]] = [
        ("guide rate and controller", lambda: _guide_rate(wayfinder, result)),
        ("unguided mount error", lambda: _mount_error(wayfinder, result)),
        ("Dec backlash", lambda: _backlash(wayfinder, result)),
        ("polar alignment", lambda: _polar(wayfinder, result)),
        ("mount pointing model", lambda: _mount_model(wayfinder, result)),
        ("performance envelope", lambda: _envelope(wayfinder, result)),
        ("sky rate", lambda: _sky(wayfinder, result, moment, ra_deg, dec_deg, longitude_deg)),
        ("focus temperature slope", lambda: _focus(wayfinder, result)),
    ]
    for label, work in steps:
        _guard(result, label, work)
    return result
