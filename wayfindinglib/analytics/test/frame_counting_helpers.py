"""Purpose: Stand-ins for the frame counting tests.

Description: Builds frame records, frame-level quality rows and a small fake
science library (`targets.get` and `quality.query`) so the counting, progress
and suggestion tests run with no real data. The fake keeps its frames in
memory and never touches a file or a database.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from astrometricslib import (
    FilterType,
    FrameRecord,
    LimitTier,
    MeasurementOrigin,
    MeasurementStatus,
    QualityLimit,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
    Target,
    frame_subject_id,
)
from wayfindinglib.analytics.frame_counting import EquipmentNames

NIGHT_START = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)
"""A time during the night of 2026-10-24, used as frame time zero."""

EQUIPMENT = EquipmentNames.from_names(["cam1", "Test Camera"], ["scope1", "Test Scope"])
"""The names the fake session's camera and telescope go by."""


def make_frame(
    index: int,
    *,
    target_id: str = "M 81",
    filter_value: FilterType | str = FilterType.L,
    exposure: float = 60.0,
    iso: str = "120",
    offset: str = "10",
    binning: int | None = 1,
    camera: str = "Test Camera",
    telescope: str = "Test Scope",
    role: str = "LIGHT",
    taken: datetime | None = None,
    folder: str = "",
) -> FrameRecord:
    """Build one frame record.

    Parameters
    ----------
    index : `int`
        The frame's number. Sets its file name and, when `taken` is not
        given, its time (one minute after `NIGHT_START` for each number).
    target_id : `str`, optional
        The target folder name.
    filter_value : `FilterType` or `str`, optional
        The frame's filter.
    exposure : `float`, optional
        The exposure in seconds.
    iso : `str`, optional
        The gain, as the record keeps it.
    offset : `str`, optional
        The offset, as the record keeps it.
    binning : `int` or `None`, optional
        The binning factor.
    camera, telescope : `str`, optional
        The names in the record.
    role : `str`, optional
        ``"LIGHT"``, ``"DARK"``, and so on.
    taken : `datetime`, optional
        When the frame was taken.
    folder : `str`, optional
        An extra folder in the path, such as ``"_excluded"``.

    Returns
    -------
    frame : `FrameRecord`
        The frame.
    """
    when = taken or NIGHT_START + timedelta(minutes=index)
    parts = [
        "/data",
        "lights",
        target_id,
        telescope,
        camera,
        *([folder] if folder else []),
        f"f{index:04d}.fits",
    ]
    return FrameRecord(
        path="/".join(parts),
        filter=filter_value,
        role=role,
        iso=iso,
        offset=offset,
        exposure=str(exposure),
        timestamp=when.timestamp(),
        camera=camera,
        telescope=telescope,
        binning=binning,
    )


def judgment_row(
    frame: FrameRecord, target_id: str = "M 81", *, failed: bool = False, row_id: int = 1
) -> QualityMeasurement:
    """Build a frame-level quality row for one frame.

    Parameters
    ----------
    frame : `FrameRecord`
        The frame the row is about.
    target_id : `str`, optional
        The target.
    failed : `bool`, optional
        Whether the row's self-check failed (the frame is flagged).
    row_id : `int`, optional
        The row id.

    Returns
    -------
    row : `QualityMeasurement`
        The row.
    """
    return QualityMeasurement(
        metric="frame.quarantine_roundness",
        definition_version=1,
        subject=QualitySubject(
            level=SubjectLevel.FRAME,
            subject_id=frame_subject_id(frame.path, target_id),
            target_id=target_id,
        ),
        status=MeasurementStatus.MEASURED,
        value=0.9,
        check_outcome="failed" if failed else "passed",
        check_limit=QualityLimit(
            value=0.8, unit="", tier=LimitTier.DESIGN_ESTIMATE, derivation="a test limit"
        ),
        origin=MeasurementOrigin.MEASURED,
        run_id="run",
        measured_at=NIGHT_START,
        row_id=row_id,
    )


class FakeLibrary:
    """A science library stand-in that holds frames and quality rows in memory.

    Parameters
    ----------
    frames_by_target : `dict` [`str`, `list` [`FrameRecord`]]
        The frames of each target.
    rows : `list` [`QualityMeasurement`], optional
        Every stored row. `quality.query` filters them.
    object_types : `dict` [`str`, `str`], optional
        The stored SIMBAD object type of each target. A target left out has
        none.
    """

    def __init__(
        self,
        frames_by_target: dict[str, list[FrameRecord]],
        rows: list[QualityMeasurement] | None = None,
        object_types: dict[str, str] | None = None,
    ) -> None:
        self.frames_by_target = frames_by_target
        self.rows = rows or []
        self.object_types = object_types or {}
        self.read_failures: set[str] = set()
        self.targets = SimpleNamespace(get=self._get_target, list=self._list_targets)
        self.quality = SimpleNamespace(
            query=self._query,
            set_aside_frames=lambda _target: [],
            capture_settings=lambda ids=None: {},
        )

    def _get_target(self, target_id: str, refresh: bool = False) -> Target | None:
        """Give a target holding the frames, or `None` for an unknown id.

        Parameters
        ----------
        target_id : `str`
            The target.
        refresh : `bool`, optional
            Ignored.

        Returns
        -------
        target : `Target` or `None`
            The target.

        Raises
        ------
        OSError
            For a target listed in `read_failures`.
        """
        if target_id in self.read_failures:
            raise OSError("disk unreadable")
        if target_id not in self.frames_by_target:
            return None
        return Target(
            id=target_id,
            frames=list(self.frames_by_target[target_id]),
            simbad_object_type=self.object_types.get(target_id),
        )

    def _list_targets(self) -> list[Target]:
        """Give every target.

        Returns
        -------
        targets : `list` [`Target`]
            The targets.
        """
        return [Target(id=target_id) for target_id in self.frames_by_target]

    def _query(self, **arguments: Any) -> list[QualityMeasurement]:
        """Filter the stored rows the way `quality.query` does.

        Parameters
        ----------
        **arguments : `Any`
            The `quality.query` arguments. Only `metric`, `level` and
            `target_id` are used.

        Returns
        -------
        rows : `list` [`QualityMeasurement`]
            The matching rows.
        """
        return [
            row
            for row in self.rows
            if (arguments.get("metric") is None or row.metric == arguments["metric"])
            and (arguments.get("level") is None or row.subject.level.value == arguments["level"])
            and (arguments.get("target_id") is None or row.subject.target_id == arguments["target_id"])
        ]
