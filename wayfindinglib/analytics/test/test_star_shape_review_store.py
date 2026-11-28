"""Purpose: Check the star-shape reader against a real, made-up quality store.

Description: The reader `read_star_shape_rows` and the stack baseline
`read_stack_roundness_low_limit` are run on a small quality database written
with the science library's own store. The database holds the cases the
reader must handle: rows at another definition version, a color-channel row,
a row with no value, a newer row that replaces an older one, a subset stack,
a frame the quarantine set aside, and rows of other targets. The expected
numbers were taken from the reader as it was before it read the stored rows
once per level instead of once per metric, so these tests show the faster
reader gives the same answer. A further test checks that the kept rows are
used again until the target's data change. The last tests add rows to the
store and check that bringing kept rows up to date
(`update_stored_shape_rows`) gives exactly what a new full read gives, and
that a removed row or a row it cannot find makes it give up.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import CaptureSetting
from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib._internal.quality import MeasurementOrigin, QualitySubject
from astrometricslib.api.quality import QualityMeasurements
from astrometricslib.drivers import get_target_data_version
from astrometricslib.reports import MeasurementStatus, QualityMeasurement, SubjectLevel
from wayfindinglib.analytics.star_shape_review import (
    StoredShapeRows,
    read_stack_roundness_low_limit,
    read_star_shape_rows,
    read_stored_shape_rows,
    update_stored_shape_rows,
)
from wayfindinglib.models.session.star_shape_review import FrameStarShape, StandoutFrame

TARGET_ID = "Target"
"""The id of the made-up target."""

START = datetime(2026, 11, 1, 6, tzinfo=UTC)
"""When the first made-up frame was taken (the night of 2026-10-31)."""


def _measurement(
    metric: str,
    subject: str,
    value: float | None,
    minutes: int,
    *,
    level: SubjectLevel = SubjectLevel.FRAME,
    target_id: str = TARGET_ID,
    version: int = 1,
    channel: str | None = None,
    variant: str | None = None,
    status: MeasurementStatus = MeasurementStatus.MEASURED,
    setting_id: int | None = None,
) -> QualityMeasurement:
    """Build one stored measurement.

    Parameters
    ----------
    metric : `str`
        The metric name.
    subject : `str`
        The subject id.
    value : `float` or `None`
        The value.
    minutes : `int`
        When it was measured, in minutes after `START`.
    level, target_id, version, channel, variant, status, setting_id
        The other fields of the row.

    Returns
    -------
    measurement : `QualityMeasurement`
        The row, ready to write.
    """
    return QualityMeasurement(
        metric=metric,
        definition_version=version,
        subject=QualitySubject(
            level=level,
            subject_id=subject,
            target_id=target_id,
            capture_setting_id=setting_id,
            channel=channel,
            variant=variant,
        ),
        status=status,
        value=value,
        reason="not measured" if status == MeasurementStatus.NOT_MEASURED else "",
        origin=MeasurementOrigin.MEASURED,
        run_id=f"run-{minutes}",
        measured_at=START + timedelta(minutes=minutes),
    )


def _frame_rows(setting_id: int) -> list[QualityMeasurement]:
    """Write the frame-level rows of the made-up library.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        The rows, in the order they are written.
    """
    a_subject, b_subject, c_subject = (f"{TARGET_ID}/{name}.fits" for name in ("a", "b", "c"))
    rows = [
        _measurement("frame.star_elongation", a_subject, 1.40, 1),
        _measurement("frame.star_elongation_angle_deg", a_subject, 40.0, 1, version=2),
        _measurement("frame.star_elongation_coherence", a_subject, 0.83, 1),
        _measurement("frame.star_width_center_px", a_subject, 2.5, 1),
        _measurement("frame.star_width_corner_xlow_ylow_px", a_subject, 2.7, 1),
        _measurement("frame.star_width_corner_xhigh_ylow_px", a_subject, 2.8, 1),
        _measurement("frame.star_width_corner_xlow_yhigh_px", a_subject, 2.9, 1),
        _measurement("frame.star_width_corner_xhigh_yhigh_px", a_subject, 3.0, 1),
        # Frame b: an older elongation that a newer one replaces, an angle at
        # a version the reader is not pinned to, and a color-channel row.
        _measurement("frame.star_elongation", b_subject, 1.90, 2),
        _measurement("frame.star_elongation", b_subject, 1.20, 3),
        _measurement("frame.star_elongation_angle_deg", b_subject, 75.0, 3, version=1),
        _measurement("frame.star_elongation_coherence", b_subject, 0.20, 3),
        _measurement("frame.star_elongation", b_subject, 5.0, 3, channel="R"),
        _measurement("frame.star_width_corner_xhigh_yhigh_px", b_subject, 3.3, 3),
        # Frame c was set aside as trailed; frame d has no usable elongation.
        _measurement("frame.star_elongation", c_subject, 2.10, 4),
        _measurement(
            "frame.star_elongation",
            f"{TARGET_ID}/d.fits",
            None,
            5,
            status=MeasurementStatus.NOT_MEASURED,
        ),
        _measurement("frame.star_elongation_coherence", f"{TARGET_ID}/d.fits", 0.5, 5),
        # Another target's frame, which must not be read.
        _measurement("frame.star_elongation", "Other/a.fits", 3.0, 6, target_id="Other"),
        # A frame metric the review does not read.
        _measurement("frame.sky_noise_e", a_subject, 12.0, 6),
    ]
    for row in rows:
        row.subject.capture_setting_id = setting_id
    return rows


def _stack_rows() -> list[QualityMeasurement]:
    """Write the stack-level rows of the made-up library.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        The rows, in the order they are written.
    """
    stack = f"{TARGET_ID}/stack.fits"
    rows = [
        _measurement(
            "stacking.star_elongation", f"{TARGET_ID}/old_stack.fits", 1.30, 10, level=SubjectLevel.STACK
        ),
        _measurement(
            "stacking.star_elongation_angle_deg",
            f"{TARGET_ID}/old_stack.fits",
            10.0,
            10,
            level=SubjectLevel.STACK,
        ),
        _measurement("stacking.star_elongation", stack, 1.08, 20, level=SubjectLevel.STACK),
        _measurement("stacking.star_elongation_angle_deg", stack, 120.0, 20, level=SubjectLevel.STACK),
        _measurement("stacking.star_elongation_coherence", stack, 0.61, 20, level=SubjectLevel.STACK),
        _measurement("stacking.star_width_center_px", stack, 2.2, 20, level=SubjectLevel.STACK),
        _measurement("stacking.star_width_corner_xlow_yhigh_px", stack, 2.6, 20, level=SubjectLevel.STACK),
        _measurement("stacking.star_elongation", stack, 1.5, 21, level=SubjectLevel.STACK, channel="G"),
        _measurement(
            "stacking.star_elongation",
            f"{TARGET_ID}/subset.fits",
            1.9,
            30,
            level=SubjectLevel.STACK,
            variant="half_a",
        ),
    ]
    rows.extend(
        _measurement(
            "stacking.star_elongation",
            f"T{index}/stack.fits",
            1.02 + 0.01 * index,
            40 + index,
            level=SubjectLevel.STACK,
            target_id=f"T{index}",
        )
        for index in range(6)
    )
    return rows


def _library(tmp_path: Path) -> tuple[Any, Any, Any]:
    """Write the made-up library and build the stand-ins that read it.

    Returns
    -------
    astrometrics : `types.SimpleNamespace`
        Has ``quality.records`` (the real reader over the made-up store) and
        ``targets.frames.get_set_aside``.
    target : `types.SimpleNamespace`
        The made-up target with its frames.
    config : `types.SimpleNamespace`
        Gives the library folder, as the configuration does.
    """
    library = tmp_path / "library"
    library.mkdir()
    store = QualityStore(str(library / "astrometrics.db"))
    setting_id = store.get_or_create_capture_setting(CaptureSetting(camera="CamA"))
    store.write_measurements([*_frame_rows(setting_id), *_stack_rows()])
    config = SimpleNamespace(get_library_path=lambda: str(library))
    set_aside = [
        SimpleNamespace(
            kind="trailed",
            path="_excluded/c.fits",
            original_path="c.fits",
            subject_id=f"{TARGET_ID}/c.fits",
            date="2026-11-01 06:04:00",
            reason="stars stretched 2.10",
        ),
        SimpleNamespace(
            kind="clouded",
            path="_excluded/e.fits",
            original_path="e.fits",
            subject_id=f"{TARGET_ID}/e.fits",
            date="2026-11-01 06:05:00",
            reason="few stars",
        ),
    ]
    astrometrics = SimpleNamespace(
        quality=SimpleNamespace(records=QualityMeasurements(config)),
        targets=SimpleNamespace(frames=SimpleNamespace(get_set_aside=lambda _target: list(set_aside))),
    )
    frames = [
        SimpleNamespace(
            path=f"{name}.fits", role="LIGHT", timestamp=(START + timedelta(minutes=minutes)).timestamp()
        )
        for name, minutes in (("b", 3), ("a", 1), ("d", 5))
    ]
    frames.append(SimpleNamespace(path="dark.fits", role="DARK", timestamp=START.timestamp()))
    target = SimpleNamespace(
        id=TARGET_ID, frames=frames, frame_subject_id=lambda frame: f"{TARGET_ID}/{frame.path}"
    )
    return astrometrics, target, config


EXPECTED_FRAMES = [
    FrameStarShape(
        path="a.fits",
        timestamp=(START + timedelta(minutes=1)).timestamp(),
        elongation=1.40,
        angle_deg=40.0,
        direction_coherence=0.83,
        center_width_px=2.5,
        corner_widths_px={"xlow_ylow": 2.7, "xhigh_ylow": 2.8, "xlow_yhigh": 2.9, "xhigh_yhigh": 3.0},
        elongation_row_id=1,
    ),
    FrameStarShape(
        path="b.fits",
        timestamp=(START + timedelta(minutes=3)).timestamp(),
        elongation=1.20,
        direction_coherence=0.20,
        corner_widths_px={"xhigh_yhigh": 3.3},
        elongation_row_id=10,
    ),
]
"""The frames the reader gave before the change, oldest first."""

EXPECTED_STACK = FrameStarShape(
    path=f"{TARGET_ID}/stack.fits",
    elongation=1.08,
    angle_deg=120.0,
    direction_coherence=0.61,
    center_width_px=2.2,
    corner_widths_px={"xlow_yhigh": 2.6},
    elongation_row_id=22,
)
"""The stack the reader gave before the change: the newest library stack."""

EXPECTED_SET_ASIDE = [
    StandoutFrame(
        path="c.fits",
        timestamp=datetime(2026, 11, 1, 6, 4, tzinfo=UTC).timestamp(),
        elongation=2.10,
        reason="stars stretched 2.10",
    )
]
"""The trailed frames the reader gave before the change."""

EXPECTED_STACK_LIMIT = 0.8757160287804062
"""The stack baseline before the change, leaving out the target's stack."""


def test_the_reader_gives_the_same_frames_stack_and_set_aside_frames(tmp_path: Path) -> None:
    """The whole-target read equals the answer the old reader gave."""
    astrometrics, target, _ = _library(tmp_path)

    frames, stack, set_aside = read_star_shape_rows(astrometrics, target)

    assert frames == EXPECTED_FRAMES
    assert stack == EXPECTED_STACK
    assert set_aside == EXPECTED_SET_ASIDE


def test_a_night_or_frame_list_gives_the_same_answer_as_before(tmp_path: Path) -> None:
    """Narrowing by night or by frames still leaves the stack out."""
    astrometrics, target, _ = _library(tmp_path)

    night_frames, night_stack, night_set_aside = read_star_shape_rows(
        astrometrics, target, night="2026-10-31"
    )
    listed_frames, listed_stack, listed_set_aside = read_star_shape_rows(
        astrometrics, target, frame_paths=["b.fits", "c.fits"]
    )

    assert night_frames == EXPECTED_FRAMES
    assert night_stack is None
    assert night_set_aside == EXPECTED_SET_ASIDE
    assert listed_frames == EXPECTED_FRAMES[1:]
    assert listed_stack is None
    assert listed_set_aside == EXPECTED_SET_ASIDE


def test_the_stack_baseline_is_the_same_as_before(tmp_path: Path) -> None:
    """The library's other stacks give the same stack baseline."""
    astrometrics, _, _ = _library(tmp_path)

    limit = read_stack_roundness_low_limit(astrometrics, f"{TARGET_ID}/stack.fits")

    assert limit == pytest.approx(EXPECTED_STACK_LIMIT)


def test_kept_rows_give_the_same_answer_and_change_with_the_target(tmp_path: Path) -> None:
    """Rows read once give the same answer; a new row changes the version."""
    astrometrics, target, config = _library(tmp_path)
    version = get_target_data_version(config, TARGET_ID)
    stored = read_stored_shape_rows(astrometrics, TARGET_ID)

    assert read_star_shape_rows(astrometrics, target, stored=stored) == (
        EXPECTED_FRAMES,
        EXPECTED_STACK,
        EXPECTED_SET_ASIDE,
    )
    store = QualityStore(str(tmp_path / "library" / "astrometrics.db"))
    store.write_measurements([_measurement("frame.star_elongation", f"{TARGET_ID}/a.fits", 1.1, 50)])
    assert get_target_data_version(config, TARGET_ID) != version
    assert read_stored_shape_rows(astrometrics, TARGET_ID).frames[f"{TARGET_ID}/a.fits"][
        "frame.star_elongation"
    ][0] == pytest.approx(1.1)


def _kept_and_store(tmp_path: Path) -> tuple[Any, Any, Any, StoredShapeRows, QualityStore]:
    """Write the made-up library and read its rows once, with their version.

    Returns
    -------
    astrometrics, target, read_version, kept, store
        The stand-in library, the target, a function that reads the target's
        data version, the kept rows, and the store to add rows to.
    """
    astrometrics, target, config = _library(tmp_path)

    def read_version() -> Any:
        """Read the made-up target's data version.

        Returns
        -------
        version : `Any`
            See `get_target_data_version`.
        """
        return get_target_data_version(config, TARGET_ID)

    kept = read_stored_shape_rows(astrometrics, TARGET_ID, read_version=read_version)
    store = QualityStore(str(tmp_path / "library" / "astrometrics.db"))
    return astrometrics, target, read_version, kept, store


def test_updating_kept_rows_with_added_rows_equals_a_full_read(tmp_path: Path) -> None:
    """Added rows merged into kept rows give exactly what a full read gives.

    The added rows cover a new frame, a newer measurement that replaces an
    older one, a newer unusable row that hides a usable one, a newer row at
    another definition version, a color-channel row, another target's row
    and a new stack row.
    """
    astrometrics, target, read_version, kept, store = _kept_and_store(tmp_path)
    assert kept.version == read_version()
    kept_copy = StoredShapeRows(
        frames={subject: dict(values) for subject, values in kept.frames.items()},
        stack=kept.stack,
        recency={subject: dict(values) for subject, values in kept.recency.items()},
        version=kept.version,
        mark=kept.mark,
    )
    a_subject, b_subject, e_subject = (f"{TARGET_ID}/{name}.fits" for name in ("a", "b", "e"))
    setting_id = store.get_or_create_capture_setting(CaptureSetting(camera="CamA"))
    added = [
        _measurement("frame.star_elongation", e_subject, 1.33, 60),
        _measurement("frame.star_elongation_coherence", e_subject, 0.4, 60),
        _measurement("frame.star_width_center_px", e_subject, 2.4, 60),
        _measurement("frame.star_elongation", a_subject, 1.25, 61),
        _measurement(
            "frame.star_elongation_coherence", b_subject, None, 61, status=MeasurementStatus.NOT_MEASURED
        ),
        _measurement("frame.star_elongation_angle_deg", a_subject, 41.0, 61, version=3),
        _measurement("frame.star_elongation", e_subject, 4.0, 61, channel="G"),
        _measurement("frame.star_elongation", "Other/e.fits", 3.0, 61, target_id="Other"),
        _measurement(
            "stacking.star_elongation", f"{TARGET_ID}/stack.fits", 1.04, 62, level=SubjectLevel.STACK
        ),
    ]
    for row in added:
        row.subject.capture_setting_id = setting_id
    store.write_measurements(added)
    target.frames.append(
        SimpleNamespace(path="e.fits", role="LIGHT", timestamp=(START + timedelta(minutes=60)).timestamp())
    )

    updated = update_stored_shape_rows(astrometrics, TARGET_ID, kept, read_version())
    fresh = read_stored_shape_rows(astrometrics, TARGET_ID, read_version=read_version)

    assert updated is not None
    assert updated == fresh
    assert read_star_shape_rows(astrometrics, target, stored=updated) == read_star_shape_rows(
        astrometrics, target
    )
    assert updated.frames[a_subject]["frame.star_elongation"][0] == pytest.approx(1.25)
    assert "frame.star_elongation_coherence" not in updated.frames[b_subject]
    assert "frame.star_elongation_angle_deg" not in updated.frames[a_subject]
    assert updated.stack is not None and updated.stack.elongation == pytest.approx(1.04)
    assert kept == kept_copy


def test_updating_again_after_an_update_still_equals_a_full_read(tmp_path: Path) -> None:
    """An updated answer can be brought up to date again, frame by frame."""
    astrometrics, _, read_version, kept, store = _kept_and_store(tmp_path)

    for frame_number in range(3):
        subject = f"{TARGET_ID}/n{frame_number}.fits"
        store.write_measurements([_measurement("frame.star_elongation", subject, 1.1, 70 + frame_number)])
        updated = update_stored_shape_rows(astrometrics, TARGET_ID, kept, read_version())
        assert updated is not None
        assert updated == read_stored_shape_rows(astrometrics, TARGET_ID, read_version=read_version)
        kept = updated


def test_no_new_rows_keeps_the_numbers(tmp_path: Path) -> None:
    """With no row added, the update gives the same numbers without reading."""
    astrometrics, _, read_version, kept, _ = _kept_and_store(tmp_path)

    assert update_stored_shape_rows(astrometrics, TARGET_ID, kept, read_version()) == kept


def test_a_removed_frame_makes_the_update_give_up(tmp_path: Path) -> None:
    """Rows removed (a deleted frame) as rows were added need a full read."""
    astrometrics, _, read_version, kept, store = _kept_and_store(tmp_path)

    store.delete_frame_measurements([f"{TARGET_ID}/d.fits"])
    store.write_measurements([
        _measurement("frame.star_elongation", f"{TARGET_ID}/e.fits", 1.3, 60),
        _measurement("frame.star_elongation_coherence", f"{TARGET_ID}/e.fits", 0.3, 60),
    ])

    assert update_stored_shape_rows(astrometrics, TARGET_ID, kept, read_version()) is None


def test_a_removed_newest_row_makes_the_update_give_up(tmp_path: Path) -> None:
    """When the kept newest row is gone, its row id may be used again."""
    astrometrics, _, read_version, _, store = _kept_and_store(tmp_path)
    store.write_measurements([_measurement("frame.star_elongation", f"{TARGET_ID}/e.fits", 1.3, 60)])
    kept = read_stored_shape_rows(astrometrics, TARGET_ID, read_version=read_version)

    store.delete_frame_measurements([f"{TARGET_ID}/e.fits"])
    store.write_measurements([_measurement("frame.star_elongation", f"{TARGET_ID}/f.fits", 1.2, 61)])

    assert update_stored_shape_rows(astrometrics, TARGET_ID, kept, read_version()) is None


def test_a_row_measured_before_the_kept_newest_time_makes_the_update_give_up(tmp_path: Path) -> None:
    """A row added late with an old time is missed, so the update gives up."""
    astrometrics, _, read_version, kept, store = _kept_and_store(tmp_path)

    store.write_measurements([_measurement("frame.star_elongation", f"{TARGET_ID}/a.fits", 1.6, 0)])

    assert update_stored_shape_rows(astrometrics, TARGET_ID, kept, read_version()) is None


def test_rows_read_while_the_data_changed_cannot_be_updated(tmp_path: Path) -> None:
    """When the version changed during the read, the rows record no version."""
    astrometrics, _, _, _, _ = _kept_and_store(tmp_path)
    versions = iter([("a",), ("b",)])

    def changing_version() -> Any:
        """Give a different version on each call.

        Returns
        -------
        version : `Any`
            A stand-in version.
        """
        return next(versions)

    kept = read_stored_shape_rows(astrometrics, TARGET_ID, read_version=changing_version)

    assert kept.version is None
    assert update_stored_shape_rows(astrometrics, TARGET_ID, kept, ("b",)) is None
