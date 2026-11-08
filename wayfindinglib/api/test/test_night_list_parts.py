"""Purpose: Tests that the list of nights is kept in two parts and stays exact.

Description: The list of nights keeps the entries of finished nights and
rebuilds only the night in progress when only tonight's data change. These
tests check two things. First, the list always equals a full build made the
old way, from every frame and session, after each kind of change: new frames
tonight, a frame added to or deleted from an old night, a deleted frame
tonight, a new guiding run, a new Ekos session, a stored sky measurement, a
change of equipment or configuration, and the night rolling over. Second,
each kind of change rebuilds only the parts it touches.

The frames come from a synthetic library (see the ``library`` fixture), and a
frame added or deleted counts a write to the target documents, as a real one
does. The stored sky measurements are rows in a small stand-in for the
science database's quality table.
"""

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from astrometricslib.drivers import bump_data_version
from astrometricslib.reports import observing_night_id
from wayfindinglib import ObservatoryControl
from wayfindinglib.analytics import capture_history
from wayfindinglib.api.test.capture_night_helpers import Library, add_frames, record_ekos_captures
from wayfindinglib.api.test.guiding_night_helpers import FIRST_NIGHT, record_guiding_night
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.models.session.night_list import NightListEntry
from wayfindinglib.tasks.control_tasks import night_analysis, night_history

TONIGHT_DAY = 5
"""The day, counted from the first test night, that is tonight."""

NOW = FIRST_NIGHT + TONIGHT_DAY * 86400.0 + 3600.0
"""A moment during the night in progress."""

LIMIT = 400
"""A limit large enough to list every night."""


class Rig:
    """The synthetic library, its sky measurements and the build counts.

    Parameters
    ----------
    control : `ObservatoryControl`
        The isolated control the nights are listed from.
    library : `Library`
        The synthetic frames.
    """

    def __init__(self, control: ObservatoryControl, library: Library) -> None:
        """Keep the pieces and start the counts at zero."""
        self.control = control
        self.library = library
        self.counts = {"nights": 0, "tonight": 0}
        self.serial = 0
        self.now = NOW
        self.database = Path(str(control._context.config.get_library_path())) / "astrometrics.db"
        connection = sqlite3.connect(self.database)
        connection.execute(
            "CREATE TABLE quality_measurements "
            "(row_id INTEGER PRIMARY KEY, metric TEXT, subject_level TEXT, subject_id TEXT)"
        )
        connection.commit()
        connection.close()

    def bump_targets(self) -> None:
        """Count a write to the target documents, as a frame change does."""
        connection = sqlite3.connect(self.database)
        bump_data_version(connection, "targets")
        connection.commit()
        connection.close()

    def store_background_row(self, path: str) -> None:
        """Store the sky background row of a frame, as a measurement does."""
        connection = sqlite3.connect(self.database)
        connection.execute(
            "INSERT INTO quality_measurements (metric, subject_level, subject_id) VALUES (?, 'frame', ?)",
            (capture_history.BACKGROUND_METRIC, path),
        )
        connection.commit()
        connection.close()

    def add_frames(self, day: int, count: int = 1, **overrides: Any) -> None:
        """Add frames of a day and count the write.

        A frame with a background value also gets its stored sky row.
        """
        for _ in range(count):
            self.serial += 1
            add_frames(
                self.library,
                day,
                count=1,
                path=f"/frames/frame_{self.serial:04d}.fits",
                timestamp=FIRST_NIGHT + day * 86400.0 + 60.0 * self.serial,
                **overrides,
            )
            frame = self.library.frames[-1]
            if frame.background_adu is not None:
                self.store_background_row(frame.path)
        self.bump_targets()

    def delete_last_frame_of(self, day: int) -> None:
        """Remove the newest frame of a day and count the write."""
        night = observing_night_id(FIRST_NIGHT + day * 86400.0)
        frame = [frame for frame in self.library.frames if capture_history.night_of(frame) == night][-1]
        self.library.frames.remove(frame)
        self.bump_targets()

    def listed(self, limit: int = LIMIT) -> list[NightListEntry]:
        """Give the list of nights as the task builds it now.

        Returns
        -------
        nights : `list` [`NightListEntry`]
            The list.
        """
        return night_history.build_night_list(self.control._context, limit, now=self.now)

    def reference(self, limit: int = LIMIT) -> list[NightListEntry]:
        """Build the list the old way: every frame and session, in one go.

        Returns
        -------
        nights : `list` [`NightListEntry`]
            The list a full, uncached build gives.
        """
        context = self.control._context
        ekos_records, guiding_runs = night_analysis.recorded_sessions(context)
        frames_by_night = capture_history.frames_by_night(self.library.frames)
        ekos_nights = {record.session_id for record in ekos_records}
        guiding_nights = {run.session_id for run in guiding_runs}
        nights = sorted(set(frames_by_night) | ekos_nights | guiding_nights)[-limit:]
        return [
            NightListEntry(
                night_id=night,
                night_date=date.fromisoformat(night),
                targets_imaged=sorted({frame.target_id for frame in frames_by_night.get(night, [])}),
                frame_count=len(frames_by_night.get(night, [])),
                has_frame_data=bool(frames_by_night.get(night)),
                has_guiding_data=night in guiding_nights,
                has_sky_data=any(
                    frame.background_adu is not None for frame in frames_by_night.get(night, [])
                ),
                has_ekos_record=night in ekos_nights,
            )
            for night in nights
        ]

    def builds_after(self, action: Any) -> tuple[int, int]:
        """Run an action and a request, and count the builds the request made.

        Returns
        -------
        counts : `tuple` [`int`, `int`]
            The builds of the finished nights, then of the night in progress.
        """
        action()
        before = dict(self.counts)
        assert self.listed() == self.reference()
        return self.counts["nights"] - before["nights"], self.counts["tonight"] - before["tonight"]


@pytest.fixture
def rig(control: ObservatoryControl, library: Library, monkeypatch: pytest.MonkeyPatch) -> Rig:
    """Give a rig with four finished nights and tonight, each with frames.

    Returns
    -------
    rig : `Rig`
        The rig, with the lists already built once.
    """
    rig = Rig(control, library)
    real = night_history._build_night_list

    def counted(*args: Any, **kwargs: Any) -> Any:
        """Count the build of a part, then build it.

        Returns
        -------
        entries : `list`
            The part's entries.
        """
        rig.counts["nights" if kwargs["finished"] else "tonight"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(night_history, "_build_night_list", counted)
    for day in range(TONIGHT_DAY + 1):
        rig.add_frames(day, count=3)
    record_ekos_captures(control, 1, 3)
    record_guiding_night(control, 2)
    assert rig.listed() == rig.reference()
    return rig


def test_the_first_request_builds_both_parts_and_the_second_builds_none(rig: Rig) -> None:
    """A request with nothing changed is served from the kept parts."""
    assert rig.counts == {"nights": 1, "tonight": 1}
    assert rig.builds_after(lambda: None) == (0, 0)


def test_new_frames_tonight_rebuild_only_tonight(rig: Rig) -> None:
    """Several new frames tonight leave the finished nights as they were."""
    for _ in range(3):
        assert rig.builds_after(lambda: rig.add_frames(TONIGHT_DAY, count=2)) == (0, 1)
    tonight = rig.listed()[-1]
    assert tonight.night_id == observing_night_id(NOW)
    assert tonight.frame_count == 3 + 3 * 2


def test_a_frame_added_to_an_old_night_rebuilds_the_finished_nights(rig: Rig) -> None:
    """A frame of an old night, ingested late, is counted."""
    assert rig.builds_after(lambda: rig.add_frames(0, count=1)) == (1, 0)
    assert rig.listed()[0].frame_count == 4


def test_a_frame_deleted_from_an_old_night_rebuilds_the_finished_nights(rig: Rig) -> None:
    """A frame removed from an old night is no longer counted."""
    assert rig.builds_after(lambda: rig.delete_last_frame_of(1)) == (1, 0)


def test_a_frame_deleted_tonight_rebuilds_only_tonight(rig: Rig) -> None:
    """A frame removed from tonight changes tonight's entry only."""
    assert rig.builds_after(lambda: rig.delete_last_frame_of(TONIGHT_DAY)) == (0, 1)


def test_the_last_frame_of_tonight_deleted_removes_the_entry(rig: Rig) -> None:
    """A night with no frame and no session is not listed."""
    for _ in range(3):
        rig.delete_last_frame_of(TONIGHT_DAY)
    assert observing_night_id(NOW) not in [night.night_id for night in rig.listed()]
    assert rig.listed() == rig.reference()


def test_a_frame_without_a_sky_value_gives_no_sky_data(rig: Rig) -> None:
    """A night whose only new frames have no background value, as before."""
    rig.add_frames(TONIGHT_DAY + 1, count=2, background_adu=None)
    rig.now += 86400.0
    assert not rig.listed()[-1].has_sky_data
    assert rig.listed() == rig.reference()


def test_a_new_guiding_run_tonight_rebuilds_only_tonight(rig: Rig) -> None:
    """A guiding run recorded for tonight changes tonight's entry only."""
    tonight = observing_night_id(NOW)

    def put_run() -> None:
        """Store a guiding run filed under tonight."""
        rig.control._context.butler.put(
            GuidingRunSummary(
                id="guide_log-tonight.txt#0",
                session_id=tonight,
                source_file_name="guide_log-tonight.txt",
                written_by_ekos=True,
                started_at=1.0,
                ended_at=2.0,
                pixel_scale_arcsec_per_px=6.445,
                focal_length_mm=120.0,
                ra_rate_arcsec_per_second=5.0,
                dec_rate_arcsec_per_second=7.5,
                frames_total=10,
                frames_lost=0,
                samples_stored=10,
            ),
            "guiding_run",
            {"id": "guide_log-tonight.txt#0"},
        )

    assert rig.builds_after(put_run) == (0, 1)
    assert rig.listed()[-1].has_guiding_data


def test_a_new_ekos_session_tonight_rebuilds_only_tonight(rig: Rig) -> None:
    """An Ekos session record for tonight changes tonight's entry only."""
    assert rig.builds_after(lambda: record_ekos_captures(rig.control, TONIGHT_DAY, 3)) == (0, 1)
    assert rig.listed()[-1].has_ekos_record


def test_a_new_session_of_a_finished_night_rebuilds_the_finished_nights(rig: Rig) -> None:
    """A guiding run or Ekos record for an old night is counted."""
    assert rig.builds_after(lambda: record_guiding_night(rig.control, 3)) == (1, 0)
    assert rig.builds_after(lambda: record_ekos_captures(rig.control, 0, 3)) == (1, 0)


def test_a_sky_measurement_of_tonights_frame_rebuilds_only_tonight(rig: Rig) -> None:
    """The sky value that makes tonight's entry say it has sky data."""
    rig.add_frames(TONIGHT_DAY + 1, count=1, background_adu=None)
    rig.now += 86400.0
    rig.listed()
    frame = rig.library.frames[-1]
    frame.background_adu = 100.0

    assert rig.builds_after(lambda: rig.store_background_row(frame.path)) == (0, 1)
    assert rig.listed()[-1].has_sky_data


def test_a_sky_measurement_of_an_old_frame_rebuilds_the_finished_nights(rig: Rig) -> None:
    """A measurement made later for an old frame changes the old nights."""
    rig.add_frames(0, count=1, background_adu=None)
    rig.listed()
    frame = rig.library.frames[-1]
    frame.background_adu = 100.0

    assert rig.builds_after(lambda: rig.store_background_row(frame.path)) == (1, 0)


def test_live_guiding_samples_rebuild_neither_part(rig: Rig) -> None:
    """The guiding samples are not read by the list."""
    assert rig.builds_after(
        lambda: rig.control._context.records.record_guiding_samples([
            {"session_id": "x", "timestamp": 1.0, "dra": 0.1, "ddec": 0.1, "source": "phd2_log"}
        ])
    ) == (0, 0)


def test_a_change_of_equipment_rebuilds_both_parts(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    """New active equipment changes which frames count."""
    assert rig.builds_after(
        lambda: monkeypatch.setattr(
            "wayfindinglib.analytics.data_versions.equipment_signs", lambda config: ("other",)
        )
    ) == (1, 1)


def test_a_change_of_configuration_rebuilds_both_parts(rig: Rig) -> None:
    """A changed camera profile rebuilds both parts."""
    assert rig.builds_after(
        lambda: rig.control._context.config.update_config({
            "Observatory.Camera.Main A": {"pixel_size_μm": "3.9"}
        })
    ) == (1, 1)


def test_the_night_rolling_over_builds_the_new_finished_night_once(rig: Rig) -> None:
    """The night that ends becomes finished: one build, then none."""

    def next_night() -> None:
        """Move the clock to the next night, and take its first frame."""
        rig.now += 86400.0
        rig.add_frames(TONIGHT_DAY + 1, count=1)

    assert rig.builds_after(next_night) == (1, 1)
    assert rig.listed()[-2].night_id == observing_night_id(NOW)
    assert rig.builds_after(lambda: rig.add_frames(TONIGHT_DAY + 1, count=1)) == (0, 1)
    assert rig.builds_after(lambda: None) == (0, 0)


def test_a_limit_keeps_the_newest_nights_from_the_same_parts(rig: Rig) -> None:
    """Different limits cut the same kept parts."""
    for limit in (1, 2, 4, LIMIT):
        assert rig.listed(limit) == rig.reference(limit)
    assert rig.counts == {"nights": 1, "tonight": 1}


def test_every_kind_of_change_in_turn_keeps_the_list_exact(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    """The list equals a full build after a long run of mixed changes."""
    steps = [
        lambda: rig.add_frames(TONIGHT_DAY, count=2),
        lambda: rig.add_frames(TONIGHT_DAY, count=1, background_adu=None),
        lambda: rig.add_frames(1, count=1),
        lambda: rig.delete_last_frame_of(2),
        lambda: rig.delete_last_frame_of(TONIGHT_DAY),
        lambda: record_guiding_night(rig.control, TONIGHT_DAY),
        lambda: record_ekos_captures(rig.control, 4, 3),
        lambda: rig.add_frames(TONIGHT_DAY, count=1),
        lambda: setattr(rig, "now", rig.now + 86400.0),
        lambda: rig.add_frames(TONIGHT_DAY + 1, count=2),
        lambda: setattr(rig, "now", rig.now + 3 * 86400.0),
        lambda: rig.add_frames(TONIGHT_DAY + 3, count=1),
    ]
    for step in steps:
        step()
        assert rig.listed() == rig.reference()
        assert rig.listed(3) == rig.reference(3)
