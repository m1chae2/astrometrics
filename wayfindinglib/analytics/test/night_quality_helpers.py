"""Purpose: Made-up science library data for the night review tests.

Description: Builds targets with light frames on several nights, the stored
frame measurements for them (good nights and one bad night), and a small
stand-in for the science library's ``targets`` and ``quality`` calls. The
night review tests in the Control tasks and in the facade share it.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from astrometricslib import (
    CaptureSetting,
    FrameRecord,
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
    Target,
    frame_subject_id,
)
from wayfindinglib.analytics.result_quality.efficiency import (
    READ_NOISE_METRIC,
    SKY_NOISE_METRIC,
    SKY_RATE_METRIC,
    STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC,
)

SETTING = CaptureSetting(
    camera="CamA",
    telescope="Scope",
    filter_name="L",
    exposure_seconds=60.0,
    gain_setting=100,
    offset=10,
    binning=1,
)
FIRST_NIGHT = datetime(2026, 9, 24, 6, 0, tzinfo=UTC)
REVIEWED = (FIRST_NIGHT + timedelta(days=3)).date().isoformat()
STAR = ("HD 1", "09h 55m 33s", "+69° 03′ 55″")
OTHER = ("HD 2", "22h 37m 04s", "+34° 24′ 56″")


def make_target(spec: tuple[str, str, str], nights: int, frames_per_night: int = 4) -> Target:
    """Build a library target with light frames on several nights.

    Returns
    -------
    target : `Target`
        The target.
    """
    target_id, ra, dec = spec
    frames = []
    for night in range(nights):
        for index in range(frames_per_night):
            moment = FIRST_NIGHT + timedelta(days=night, minutes=2 * index)
            frames.append(
                FrameRecord(
                    path=f"/lib/lights/{target_id}/Scope/CamA/n{night}_{index:03d}.fits",
                    timestamp=moment.timestamp(),
                    exposure="60",
                )
            )
    return Target(id=target_id, ra=ra, dec=dec, frames=frames)


def make_row(
    metric: str,
    version: int,
    value: float,
    target: Target,
    frame: FrameRecord,
    action_taken: str | None = None,
) -> QualityMeasurement:
    """Build one stored frame row.

    Returns
    -------
    row : `QualityMeasurement`
        The row.
    """
    night = datetime.fromtimestamp(frame.timestamp or 0.0, tz=UTC).date().isoformat()
    return QualityMeasurement(
        metric=metric,
        definition_version=version,
        subject=QualitySubject(
            level=SubjectLevel.FRAME,
            subject_id=frame_subject_id(frame.path, target.id),
            target_id=target.id,
            observing_night_id=night,
            capture_setting_id=1,
        ),
        status=MeasurementStatus.MEASURED,
        value=value,
        origin=MeasurementOrigin.MEASURED,
        run_id="frames",
        measured_at=FIRST_NIGHT,
        action_taken=action_taken,
        row_id=abs(hash((metric, frame.path))) % 10_000_000,
    )


def make_rows(
    target: Target, bad_night: str, width: float = 3.0, elongation: float = 1.0
) -> list[QualityMeasurement]:
    """Build the frame rows of a target: good nights and one bad night.

    Parameters
    ----------
    target : `Target`
        The target.
    bad_night : `str`
        The night with the wide stars and bright sky.
    width : `float`, optional
        The star width on the bad night.
    elongation : `float`, optional
        The star elongation on the bad night.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        Four metrics for each frame, and the elongation of the bad night.
    """
    rows = []
    for frame in target.frames:
        night = datetime.fromtimestamp(frame.timestamp or 0.0, tz=UTC).date().isoformat()
        bad = night == bad_night
        sky = 1.0 if bad else 0.5
        for metric, version, value in (
            (STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC, 1, width if bad else 2.0),
            (SKY_RATE_METRIC, 2, sky),
            (SKY_NOISE_METRIC, 2, (sky * 60.0) ** 0.5),
            (READ_NOISE_METRIC, 1, 3.0),
        ):
            rows.append(make_row(metric, version, value, target, frame))
        if bad:
            rows.append(make_row("frame.star_elongation", 1, elongation, target, frame))
    return rows


class FakeLibrary:
    """A stand-in for the science library's ``targets`` and ``quality``."""

    def __init__(self, targets: list[Target], rows: list[QualityMeasurement]) -> None:
        """Keep the targets and the stored rows.

        Parameters
        ----------
        targets : `list` [`Target`]
            The library targets.
        rows : `list` [`QualityMeasurement`]
            Every stored measurement.
        """
        self.rows = rows
        self.targets = SimpleNamespace(list=lambda: targets)
        self.quality = SimpleNamespace(query=self._query, capture_settings=lambda: {1: SETTING})

    def _query(
        self,
        metric: str | None = None,
        level: str | None = None,
        target_id: str | None = None,
        observing_night_id: str | None = None,
        **_: Any,
    ) -> list[QualityMeasurement]:
        """Answer ``quality.query`` from the stored rows.

        Returns
        -------
        rows : `list` [`QualityMeasurement`]
            The rows that match.
        """
        return [
            row
            for row in self.rows
            if (metric is None or row.metric == metric)
            and (level is None or row.subject.level.value == level)
            and (target_id is None or row.subject.target_id == target_id)
            and (observing_night_id is None or row.subject.observing_night_id == observing_night_id)
        ]
