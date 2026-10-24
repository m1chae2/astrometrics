"""Turn a run's catalog zero points into quality measurements.

The photometry analyzer finds a zero point for every frame it measures (see
`processing/catalog_zero_point.py`). This module writes them as measurements,
next to the run's other numbers, one set for each frame:

* The zero point in the session's catalog band, with its frame-to-frame
  precision as the uncertainty.
* The statistical error of the zero point's absolute level from the star
  sample, in the same band.
* The color slope, for sessions that used SIMBAD V.

A value that could not be found is a ``not_measured`` record with the reason,
never a default. A frame with no known path cannot be named, so it is left
out and the log says so. The numbers are measurements only. A night's
summary and any call about whether the night was photometric are made
elsewhere (``wayfindinglib``).
"""

import logging
from collections.abc import Sequence
from datetime import UTC

from astrometricslib.models.quality.measurement import (
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.pipelines.photometry.metrics import (
    PHOTOMETRY_CATALOG_ZERO_POINT_COLOR_SLOPE,
    PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG,
    PHOTOMETRY_CATALOG_ZERO_POINT_SIMBAD_V_MAG,
    PHOTOMETRY_CATALOG_ZERO_POINT_STAR_SAMPLE_ERROR_GAIA_G_MAG,
    PHOTOMETRY_CATALOG_ZERO_POINT_STAR_SAMPLE_ERROR_SIMBAD_V_MAG,
    PHOTOMETRY_METRICS,
)
from astrometricslib.pipelines.photometry.processing.catalog_zero_point import (
    CatalogBand,
    SessionZeroPoints,
)
from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject_id
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
)
from astrometricslib.utilities.observing_night import observing_night_id

logger = logging.getLogger(__name__)

_SPEC_BY_NAME = {spec.name: spec for spec in PHOTOMETRY_METRICS}

# The metric that holds each band's frame zero point. A session with no usable
# band is filed under the Gaia G metric, the preferred band, with the reason,
# so a missing zero point is still on record.
_ZERO_POINT_METRIC_BY_BAND: dict[CatalogBand | None, str] = {
    CatalogBand.GAIA_G: PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG,
    CatalogBand.SIMBAD_V: PHOTOMETRY_CATALOG_ZERO_POINT_SIMBAD_V_MAG,
    None: PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG,
}
_STAR_SAMPLE_ERROR_METRIC_BY_BAND: dict[CatalogBand, str] = {
    CatalogBand.GAIA_G: PHOTOMETRY_CATALOG_ZERO_POINT_STAR_SAMPLE_ERROR_GAIA_G_MAG,
    CatalogBand.SIMBAD_V: PHOTOMETRY_CATALOG_ZERO_POINT_STAR_SAMPLE_ERROR_SIMBAD_V_MAG,
}


ASSUMED_GAIN_REASON = (
    "the flux errors behind the signal-to-noise cut assumed a gain of 1 e-/ADU because neither the camera "
    "profile nor the frame header gave one"
)


def zero_point_measurements(
    sessions: Sequence[SessionZeroPoints], context: PipelineRunContext
) -> list[QualityMeasurement]:
    """Build the frame measurements of a run's zero points.

    Parameters
    ----------
    sessions : `Sequence` [`SessionZeroPoints`]
        The zero points of each session, from the analyzer.
    context : `PipelineRunContext`
        The run's shared facts.

    Returns
    -------
    measurements : `list` [`QualityMeasurement`]
        For each frame with a path, a zero point and, for a session with a
        band, the star sample error; for a SIMBAD V session also a color
        slope. A zero point whose flux errors assumed unit gain is marked
        ``assumed``. Frames with no path are left out.
    """
    measurements: list[QualityMeasurement] = []
    for session in sessions:
        zero_point_spec = _SPEC_BY_NAME[_ZERO_POINT_METRIC_BY_BAND[session.band]]
        error_spec = (
            _SPEC_BY_NAME[_STAR_SAMPLE_ERROR_METRIC_BY_BAND[session.band]]
            if session.band is not None
            else None
        )
        slope_spec = _SPEC_BY_NAME[PHOTOMETRY_CATALOG_ZERO_POINT_COLOR_SLOPE]
        unnamed_frame_count = 0
        for frame in session.frames:
            if frame.frame_path is None:
                unnamed_frame_count += 1
                continue
            night_id = observing_night_id(frame.timestamp.replace(tzinfo=UTC).timestamp())
            subject = QualitySubject(
                level=SubjectLevel.FRAME,
                subjectId=frame_subject_id(frame.frame_path, context.target_id),
                targetId=context.target_id,
                observingNightId=night_id,
            )
            estimate = frame.estimate
            zero_point = build_measurement(
                zero_point_spec,
                estimate.zero_point_mag,
                context,
                subject,
                reason=estimate.reason or session.band_reason,
                uncertainty=estimate.error_mag,
                sample_count=estimate.star_count,
            )
            if session.errors_assume_unit_gain and estimate.zero_point_mag is not None:
                zero_point = zero_point.model_copy(
                    update={"status": MeasurementStatus.ASSUMED, "reason": ASSUMED_GAIN_REASON}
                )
            measurements.append(zero_point)
            if error_spec is not None:
                measurements.append(
                    build_measurement(
                        error_spec,
                        estimate.star_sample_error_mag,
                        context,
                        subject,
                        reason=estimate.reason,
                        sample_count=session.star_set_size,
                    )
                )
            if session.band is CatalogBand.SIMBAD_V:
                measurements.append(
                    build_measurement(
                        slope_spec,
                        estimate.color_slope,
                        context,
                        subject,
                        reason=estimate.color_reason,
                        sample_count=estimate.star_count,
                    )
                )
        if unnamed_frame_count:
            logger.warning(
                "%s frames of session %s have no known path and got no zero point measurement.",
                unnamed_frame_count,
                session.session_id or "(unnamed)",
            )
    return measurements
