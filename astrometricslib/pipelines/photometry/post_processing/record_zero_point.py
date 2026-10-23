"""Turn a run's catalog zero points into quality measurements.

The photometry analyzer finds a zero point for every frame it measures (see
`processing/catalog_zero_point.py`). This module writes them as measurements,
next to the run's other numbers:

* For each frame: the zero point in the session's catalog band, the color
  slope, and the airmass. A value that could not be found is a
  ``not_measured`` record with the reason, never a default.
* For each session: the spread of its frame zero points, filed under the
  session's night. A run that covers several nights gets one spread per
  session, and nothing is combined across sessions or targets.

The numbers are measurements only. Whether the night was photometric is
decided elsewhere (``wayfindinglib``).
"""

from collections.abc import Sequence
from datetime import UTC

from astrometricslib.models.quality.measurement import (
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.pipelines.photometry.metrics import (
    PHOTOMETRY_CATALOG_ZERO_POINT_COLOUR_SLOPE,
    PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG,
    PHOTOMETRY_CATALOG_ZERO_POINT_SCATTER_GAIA_G_MAG,
    PHOTOMETRY_CATALOG_ZERO_POINT_SCATTER_SIMBAD_V_MAG,
    PHOTOMETRY_CATALOG_ZERO_POINT_SIMBAD_V_MAG,
    PHOTOMETRY_FRAME_AIRMASS,
    PHOTOMETRY_METRICS,
)
from astrometricslib.pipelines.photometry.processing.catalog_zero_point import (
    CatalogBand,
    SessionZeroPoints,
    zero_point_scatter,
)
from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject_id
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
)
from astrometricslib.utilities.observing_night import observing_night_id

_SPEC_BY_NAME = {spec.name: spec for spec in PHOTOMETRY_METRICS}

# The metric that holds each band's frame zero point and night spread. A
# session with no usable band is filed under the Gaia G metrics, with the
# reason, so a missing zero point is still on record.
_ZERO_POINT_METRIC_BY_BAND: dict[CatalogBand | None, str] = {
    CatalogBand.GAIA_G: PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG,
    CatalogBand.SIMBAD_V: PHOTOMETRY_CATALOG_ZERO_POINT_SIMBAD_V_MAG,
    None: PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG,
}
_SCATTER_METRIC_BY_BAND: dict[CatalogBand | None, str] = {
    CatalogBand.GAIA_G: PHOTOMETRY_CATALOG_ZERO_POINT_SCATTER_GAIA_G_MAG,
    CatalogBand.SIMBAD_V: PHOTOMETRY_CATALOG_ZERO_POINT_SCATTER_SIMBAD_V_MAG,
    None: PHOTOMETRY_CATALOG_ZERO_POINT_SCATTER_GAIA_G_MAG,
}

NO_AIRMASS_REASON = (
    "the frame header gave neither an airmass nor an altitude, so the pipeline used a stand-in of 1.0"
)


def zero_point_measurements(
    sessions: Sequence[SessionZeroPoints], context: PipelineRunContext
) -> list[QualityMeasurement]:
    """Build the frame and session measurements of a run's zero points.

    Parameters
    ----------
    sessions : `Sequence` [`SessionZeroPoints`]
        The zero points of each session, from the analyzer.
    context : `PipelineRunContext`
        The run's shared facts.

    Returns
    -------
    measurements : `list` [`QualityMeasurement`]
        For each frame, a zero point, a color slope and an airmass. For each
        session with at least one frame, a spread of its zero points.
    """
    measurements: list[QualityMeasurement] = []
    for session in sessions:
        if not session.frames:
            continue
        zero_point_spec = _SPEC_BY_NAME[_ZERO_POINT_METRIC_BY_BAND[session.band]]
        slope_spec = _SPEC_BY_NAME[PHOTOMETRY_CATALOG_ZERO_POINT_COLOUR_SLOPE]
        airmass_spec = _SPEC_BY_NAME[PHOTOMETRY_FRAME_AIRMASS]
        night_ids = []
        for frame in session.frames:
            night_id = observing_night_id(frame.timestamp.replace(tzinfo=UTC).timestamp())
            night_ids.append(night_id)
            subject = QualitySubject(
                level=SubjectLevel.FRAME,
                subjectId=frame_subject_id(frame.frame_path, context.target_id),
                targetId=context.target_id,
                observingNightId=night_id,
            )
            estimate = frame.estimate
            measurements.append(
                build_measurement(
                    zero_point_spec,
                    estimate.zero_point_mag,
                    context,
                    subject,
                    reason=estimate.reason,
                    uncertainty=estimate.error_mag,
                    sample_count=estimate.star_count,
                )
            )
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
            measurements.append(
                build_measurement(airmass_spec, frame.airmass, context, subject, reason=NO_AIRMASS_REASON)
            )
        scatter = zero_point_scatter([
            frame.estimate.zero_point_mag
            for frame in session.frames
            if frame.estimate.zero_point_mag is not None
        ])
        run_subject = QualitySubject(
            level=SubjectLevel.RUN,
            subjectId=f"{context.run_subject().subject_id}/{session.session_id}",
            targetId=context.target_id,
            observingNightId=min(night_ids),
        )
        measurements.append(
            build_measurement(
                _SPEC_BY_NAME[_SCATTER_METRIC_BY_BAND[session.band]],
                scatter.value_mag,
                context,
                run_subject,
                reason=session.band_reason or scatter.reason,
                uncertainty=scatter.error_mag,
                sample_count=scatter.frame_count,
            )
        )
    return measurements
