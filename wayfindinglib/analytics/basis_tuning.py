"""Purpose: Tuners for the guide cycle and the mount's periodic-error period.

Description: Issue #175, Phase E3. The guiding values that scale with the
guide cycle (pause limits, segment breaks, the active-guider window) and the
one that scales with the periodic-error period (the shortest run for a
declination correction rate) read those bases from the setup when a run has
not measured them: `TYPICAL_GUIDE_CYCLE_SECONDS` and
`MOUNT_PERIODIC_ERROR_PERIOD_SECONDS` (public in `astrometricslib.parameters`).
These tuners derive both from this observatory's stored guiding data:

- the typical guide cycle is the median time between consecutive measured
  guide samples of one session, counting only gaps shorter than the pause
  limit, so pauses are left out;
- the periodic-error period is the median dominant period of the stored
  guiding-spectrum analyses.

Guiding records do not say which mount took them, so every stored record of
this observatory counts.
"""

import itertools
import statistics
from collections.abc import Iterable, Sequence
from typing import Any

from astrometricslib.parameters import (
    MOUNT_PERIODIC_ERROR_PERIOD_SECONDS,
    TYPICAL_GUIDE_CYCLE_SECONDS,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    Tuner,
    TuningInputs,
    TuningResult,
    TuningSkippedError,
    resolve_parameter,
)
from wayfindinglib.analytics.tuning_support import butler_of, records_of
from wayfindinglib.models.session.telemetry import MEASURED_GUIDING_SAMPLE_SOURCES

# A gap between guide samples at least this long is a pause, not one guide
# cycle (WAY-D126 calls 60 s or more a pause). It is a fixed limit, not the
# scaled cadence-gap value, because the scaled value is fitted to the cycle
# this tuner measures.
GUIDE_PAUSE_LIMIT_SECONDS = TunedParameter(
    name="wayfinding.tuning_guide_pause_limit_seconds",
    default=60.0,
    unit="seconds",
    description="Shortest gap between guide samples counted as a pause when the guide cycle is tuned.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="WAY-D126",
)


def cycle_gaps(times_by_session: Iterable[Sequence[float]], pause_limit: float) -> list[float]:
    """List the gaps between consecutive guide samples that are not pauses.

    Parameters
    ----------
    times_by_session : `Iterable` [`Sequence` [`float`]]
        Each session's sample times, in seconds.
    pause_limit : `float`
        Gaps of this length or more are pauses and are left out.

    Returns
    -------
    gaps : `list` [`float`]
        Every gap above zero and below the limit.
    """
    gaps: list[float] = []
    for times in times_by_session:
        ordered = sorted(times)
        gaps.extend(
            gap
            for first, second in itertools.pairwise(ordered)
            if 0.0 < (gap := second - first) < pause_limit
        )
    return gaps


def tune_typical_guide_cycle(inputs: TuningInputs) -> TuningResult:
    """Derive the typical guide cycle from the stored guide samples.

    Parameters
    ----------
    inputs : `TuningInputs`
        The setup and the ``"wayfinder"`` service.

    Returns
    -------
    result : `TuningResult`
        The median gap between guide samples, resting on the number of gaps.

    Raises
    ------
    TuningSkippedError
        If no session has two measured samples closer than a pause.
    """
    records = records_of(inputs.service("wayfinder"))
    sources = [source.value for source in MEASURED_GUIDING_SAMPLE_SOURCES]
    rows = records.get_guiding_samples(limit=5_000_000, sources=sources)
    by_session: dict[str, list[float]] = {}
    for row in rows:
        by_session.setdefault(str(row.get("session_id")), []).append(float(row["timestamp"]))
    gaps = cycle_gaps(by_session.values(), float(resolve_parameter(GUIDE_PAUSE_LIMIT_SECONDS).value))
    if not gaps:
        raise TuningSkippedError("no stored guide samples closer together than a pause")
    return TuningResult(
        values={TYPICAL_GUIDE_CYCLE_SECONDS.name: statistics.median(gaps)},
        sample=len(gaps),
        source=f"Median gap between stored guide samples, {len(gaps)} gaps in {len(by_session)} sessions",
    )


def dominant_periods(analyses: Iterable[Any]) -> list[float]:
    """Read the dominant period of each stored guiding-spectrum analysis.

    Parameters
    ----------
    analyses : `Iterable`
        Stored `GuidingSpectrumAnalysis` records.

    Returns
    -------
    periods : `list` [`float`]
        Each analysis's dominant period in seconds, when it has one above zero.
    """
    periods = []
    for analysis in analyses:
        period = getattr(analysis, "dominant_period_seconds", None)
        if period is not None and float(period) > 0.0:
            periods.append(float(period))
    return periods


def tune_periodic_error_period(inputs: TuningInputs) -> TuningResult:
    """Derive the mount's periodic-error period from the stored spectra.

    Parameters
    ----------
    inputs : `TuningInputs`
        The setup and the ``"wayfinder"`` service.

    Returns
    -------
    result : `TuningResult`
        The median dominant period, resting on the number of analyses.

    Raises
    ------
    TuningSkippedError
        If no guiding-spectrum analysis with a period is stored.
    """
    periods = dominant_periods(butler_of(inputs.service("wayfinder")).get_all("guiding_spectrum_analysis"))
    if not periods:
        raise TuningSkippedError("no stored guiding-spectrum analysis has a dominant period")
    return TuningResult(
        values={MOUNT_PERIODIC_ERROR_PERIOD_SECONDS.name: statistics.median(periods)},
        sample=len(periods),
        source=f"Median dominant period of {len(periods)} stored guiding-spectrum analyses",
    )


TYPICAL_GUIDE_CYCLE_TUNER = Tuner(
    name="wayfinding.typical_guide_cycle",
    parameters=(TYPICAL_GUIDE_CYCLE_SECONDS,),
    method="The median gap between consecutive stored guide samples, leaving out pauses.",
    tune=tune_typical_guide_cycle,
)

PERIODIC_ERROR_PERIOD_TUNER = Tuner(
    name="wayfinding.periodic_error_period",
    parameters=(MOUNT_PERIODIC_ERROR_PERIOD_SECONDS,),
    method="The median dominant period of the stored guiding-spectrum analyses.",
    tune=tune_periodic_error_period,
)

__all__ = [
    "PERIODIC_ERROR_PERIOD_TUNER",
    "TYPICAL_GUIDE_CYCLE_TUNER",
    "cycle_gaps",
    "dominant_periods",
]
