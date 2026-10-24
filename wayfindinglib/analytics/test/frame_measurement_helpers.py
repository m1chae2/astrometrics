"""Purpose: Fakes of the science library's stored frame measurements.

Description: The capture history and the performance envelope read each
frame's star width, shape and saturation from the stored quality
measurements (`astrometrics.quality.query`). These helpers stand in for that
read with made-up rows, so tests state only which values a frame has.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from astrometricslib import (
    MeasurementStatus,
    QualityMeasurement,
    frame_subject_id,
)
from astrometricslib.models.quality.measurement import MeasurementOrigin, QualitySubject, SubjectLevel


def stored_measurement(
    metric: str,
    subject_id: str,
    value: float | None,
    row_id: int,
    definition_version: int = 1,
    channel: str | None = None,
) -> QualityMeasurement:
    """Build one stored frame measurement.

    Returns
    -------
    measurement : `QualityMeasurement`
        A measured row, or a not-measured row when `value` is `None`.
    """
    return QualityMeasurement(
        metric=metric,
        definition_version=definition_version,
        subject=QualitySubject(level=SubjectLevel.FRAME, subject_id=subject_id, channel=channel),
        status=MeasurementStatus.MEASURED if value is not None else MeasurementStatus.NOT_MEASURED,
        value=value,
        reason="" if value is not None else "no stars found",
        origin=MeasurementOrigin.MEASURED,
        run_id="test-run",
        measured_at=datetime(2026, 9, 24, tzinfo=UTC),
        row_id=row_id,
    )


class FakeAstrometrics:
    """A science-library stand-in: one target, its frames, their stored rows.

    Parameters
    ----------
    frames : `list`
        Frame records (anything with the attributes a capture frame reads).
    values_by_path : `dict`, optional
        For a frame path, its stored values by metric name. A frame not listed
        has no stored rows.
    target_id : `str`, optional
        The target's id.
    """

    def __init__(
        self,
        frames: list[Any],
        values_by_path: dict[str, dict[str, float | None]] | None = None,
        target_id: str = "Target",
    ) -> None:
        """Keep the frames and build the stored rows."""
        self.rows: list[QualityMeasurement] = []
        row_id = 1
        for path, values in (values_by_path or {}).items():
            for metric, value in values.items():
                self.rows.append(stored_measurement(metric, frame_subject_id(path, target_id), value, row_id))
                row_id += 1
        target = SimpleNamespace(id=target_id, frames=frames)
        self.targets = SimpleNamespace(list=lambda: [target])
        self.quality = SimpleNamespace(query=self._query)

    def _query(
        self, metric: str | None = None, level: str | None = None, **_: Any
    ) -> list[QualityMeasurement]:
        """Answer `astrometrics.quality.query` from the made-up rows.

        Returns
        -------
        rows : `list` [`QualityMeasurement`]
            The rows of the asked metric (all rows when none is asked).
        """
        return [row for row in self.rows if metric is None or row.metric == metric]
