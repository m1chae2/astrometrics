"""Purpose: Gather the recorded guiding data for the mount-error measurements.

Description: Reads the guiding runs and the measured guide samples of the
recorded nights, leaves out the nights a caller excludes, and hands them to
the analyses in `analytics.unguided_mount_error` and
`analytics.guiding_spectrum`.
Nothing here writes. Leaving a night out is how a leave-one-out check is made:
fit the mount from every night except the one under test.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Any

import numpy as np

from astrometricslib.reports import observing_night_id
from wayfindinglib.analytics.guiding_spectrum import analyze_guiding_telemetry
from wayfindinglib.analytics.unguided_mount_error import GuideRunSeries, measure_run, summarize_runs
from wayfindinglib.models.session.telemetry import MEASURED_GUIDING_SAMPLE_SOURCES, GuidingSpectrumAnalysis
from wayfindinglib.models.session.unguided_mount_error import UnguidedMountError

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext
    from wayfindinglib.models.session.guiding_run import GuidingRunSummary

_SAMPLE_READ_LIMIT = 200000
"""Most samples read for one night. A long night at a 3 second cadence holds
about 10,000 (the envelope reads the same limit)."""

_MEASURED_SOURCES = [source.value for source in MEASURED_GUIDING_SAMPLE_SOURCES]


def _night_samples(context: ControlContext, night: str) -> list[dict[str, Any]]:
    """Read one night's measured guide samples, oldest first.

    Returns
    -------
    samples : `list` [`dict`]
        Samples from a real guide star only; estimates reconstructed from
        pulses are left out.
    """
    return context.records.get_guiding_samples(
        session_id=night, limit=_SAMPLE_READ_LIMIT, sources=_MEASURED_SOURCES
    )


def _run_series(
    run: GuidingRunSummary, night_samples: list[dict[str, Any]], run_end: float
) -> GuideRunSeries:
    """Cut one run's samples out of its night's samples.

    Parameters
    ----------
    run : `GuidingRunSummary`
        The run.
    night_samples : `list` [`dict`]
        The measured samples of the run's night.
    run_end : `float`
        The time the run is taken to end, in seconds since the Unix epoch:
        its recorded end, or the start of the next run when the log stopped
        early.

    Returns
    -------
    series : `GuideRunSeries`
        The run's samples in time order, with its guide rates.
    """
    chosen = sorted(
        (s for s in night_samples if run.started_at <= s["timestamp"] < run_end),
        key=lambda sample: sample["timestamp"],
    )
    return GuideRunSeries(
        run_id=run.id,
        session_id=run.session_id,
        times=np.array([s["timestamp"] for s in chosen], dtype=float),
        ra_error_arcsec=np.array([s["dra"] for s in chosen], dtype=float),
        dec_error_arcsec=np.array([s["ddec"] for s in chosen], dtype=float),
        ra_pulse_ms=np.array([s.get("pulse_ra") or 0.0 for s in chosen], dtype=float),
        dec_pulse_ms=np.array([s.get("pulse_dec") or 0.0 for s in chosen], dtype=float),
        ra_rate_arcsec_per_second=run.ra_rate_arcsec_per_second,
        dec_rate_arcsec_per_second=run.dec_rate_arcsec_per_second,
    )


def measure_unguided_mount_error(
    context: ControlContext,
    session_id: str | None = None,
    exclude_nights: Collection[str] | None = None,
) -> UnguidedMountError:
    """Measure the mount's unguided error on the recorded guiding runs.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the guiding runs and samples.
    session_id : `str`, optional
        Measure only this night. Every recorded night when omitted.
    exclude_nights : `Collection` [`str`], optional
        Nights to leave out, for a leave-one-out check.

    Returns
    -------
    result : `UnguidedMountError`
        One entry per run that has guide rates and samples, and the pooled
        summary.
    """
    excluded = sorted(set(exclude_nights or ()))
    runs: list[GuidingRunSummary] = sorted(context.butler.get_all("guiding_run"), key=lambda r: r.started_at)
    runs = [
        run
        for run in runs
        if run.session_id not in excluded and (session_id is None or run.session_id == session_id)
    ]
    cache: dict[str, list[dict[str, Any]]] = {}
    measured = []
    for index, run in enumerate(runs):
        later = [other.started_at for other in runs[index + 1 :] if other.session_id == run.session_id]
        run_end = run.ended_at if run.ended_at is not None else (later[0] if later else float("inf"))
        if run.session_id not in cache:
            cache[run.session_id] = _night_samples(context, run.session_id)
        series = _run_series(run, cache[run.session_id], run_end)
        if series.times.size == 0:
            continue
        measured.append(measure_run(series))
    summary = summarize_runs(measured)
    if summary.periodic_error is not None:
        message = (
            f"Measured {len(measured)} guiding run(s) on {len(summary.nights_used)} night(s); the repeating "
            f"error is {summary.periodic_error.amplitude_arcsec:.2f} arcsec (zero to peak) every "
            f"{summary.periodic_error.period_seconds:.0f} s."
        )
    else:
        message = f"Measured {len(measured)} guiding run(s); no run shows a significant repeating error."
    return UnguidedMountError(runs=measured, summary=summary, excluded_nights=excluded, message=message)


def measure_guiding_spectrum(
    context: ControlContext,
    session_id: str | None = None,
    exclude_nights: Collection[str] | None = None,
    limit: int = 200000,
) -> GuidingSpectrumAnalysis:
    """Work out the guiding spectrum from recorded samples, storing nothing.

    Unlike `control.guiding.refit_spectrum` this never saves its result, so
    it can be run with nights left out and thrown away.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the measured guide samples.
    session_id : `str`, optional
        Use only this night. Every recorded night when omitted.
    exclude_nights : `Collection` [`str`], optional
        Nights to leave out.
    limit : `int`, optional
        Most samples read. Defaults to 200,000, which covers every recorded
        night; the oldest samples are read first.

    Returns
    -------
    analysis : `GuidingSpectrumAnalysis`
        The spectrum, as `analyze_guiding_telemetry` gives it.
    """
    excluded = set(exclude_nights or ())
    samples = context.records.get_guiding_samples(
        session_id=session_id, limit=limit, sources=_MEASURED_SOURCES
    )
    if excluded:
        samples = [
            sample
            for sample in samples
            if sample.get("session_id") not in excluded
            and observing_night_id(sample["timestamp"]) not in excluded
        ]
    return analyze_guiding_telemetry(samples)
