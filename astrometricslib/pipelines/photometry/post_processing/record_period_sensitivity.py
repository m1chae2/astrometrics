"""Store the strongest alias peaks of a run's observing schedule.

The window function of the frame times (see `processing/observing_window.py`)
says which period differences the run cannot separate. This module stores its
strongest three peaks (`WINDOW_ALIAS_RANKS`), each as a frequency and a power,
on the run. A run with fewer peaks, or only one night, stores the rest as not
measured with the reason.

The numbers describe the schedule. They do not choose which nights to add;
that belongs to the observing side (``wayfindinglib``).
"""

import logging
from collections.abc import Iterable
from datetime import datetime

import numpy as np

from astrometricslib.models.quality.measurement import QualityMeasurement
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.photometry.metrics import (
    PHOTOMETRY_METRICS,
    WINDOW_ALIAS_RANKS,
    window_alias_frequency_metric_name,
    window_alias_power_metric_name,
)
from astrometricslib.pipelines.photometry.processing.observing_window import strongest_window_peaks
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_and_record_measurements,
    build_measurement,
)

logger = logging.getLogger(__name__)

_SPEC_BY_NAME = {spec.name: spec for spec in PHOTOMETRY_METRICS}


def frame_times_days(stellar_objects: Iterable[StellarObject]) -> np.ndarray:
    """Collect the distinct exposure starts of every measured point of a run.

    Parameters
    ----------
    stellar_objects : `Iterable` [`StellarObject`]
        The run's stars, with their light curves.

    Returns
    -------
    time_days : `numpy.ndarray`
        The distinct exposure starts, in days since 1970-01-01 UTC, sorted.
        Empty when no star has a light curve.
    """
    stamps: set[datetime] = set()
    for star in stellar_objects:
        if star.photometry is not None:
            stamps.update(star.photometry.timestamps)
    if not stamps:
        return np.array([], dtype=float)
    # The light curves keep UTC exposure starts with no zone.
    epoch = datetime(1970, 1, 1)
    return np.array(
        sorted((stamp.replace(tzinfo=None) - epoch).total_seconds() / 86400.0 for stamp in stamps)
    )


def window_alias_measurements(time_days: np.ndarray, context: PipelineRunContext) -> list[QualityMeasurement]:
    """Build the alias-peak measurements of one run.

    Parameters
    ----------
    time_days : `numpy.ndarray`
        The distinct frame times of the run, in days.
    context : `PipelineRunContext`
        The run's shared facts.

    Returns
    -------
    measurements : `list` [`QualityMeasurement`]
        A frequency and a power for each rank. A rank the window does not
        have is ``not_measured`` with the reason.
    """
    subject = context.run_subject()
    peaks, reason = strongest_window_peaks(time_days, len(WINDOW_ALIAS_RANKS))
    measurements: list[QualityMeasurement] = []
    for rank in WINDOW_ALIAS_RANKS:
        peak = peaks[rank - 1] if rank <= len(peaks) else None
        missing = reason or "the window has no such peak"
        measurements.append(
            build_measurement(
                _SPEC_BY_NAME[window_alias_frequency_metric_name(rank)],
                None if peak is None else peak.frequency_per_day,
                context,
                subject,
                reason=missing,
                sample_count=len(time_days),
            )
        )
        measurements.append(
            build_measurement(
                _SPEC_BY_NAME[window_alias_power_metric_name(rank)],
                None if peak is None else peak.power_fraction,
                context,
                subject,
                reason=missing,
                sample_count=len(time_days),
            )
        )
    return measurements


def record_period_sensitivity_quality(
    stellar_objects: Iterable[StellarObject], context: PipelineRunContext, *, config: object = None
) -> int:
    """Find and store the run's alias peaks; no failure here fails the run.

    Parameters
    ----------
    stellar_objects : `Iterable` [`StellarObject`]
        The run's stars, with their light curves.
    context : `PipelineRunContext`
        The run's shared facts.
    config : `AppConfiguration`, optional
        Where the quality store lives. The process-wide one if omitted.

    Returns
    -------
    new_count : `int`
        How many rows were new. It is 0 when anything went wrong (logged).
    """
    return build_and_record_measurements(
        lambda: window_alias_measurements(frame_times_days(stellar_objects), context),
        f"photometry period sensitivity on {context.target_id}",
        config=config,
    )
