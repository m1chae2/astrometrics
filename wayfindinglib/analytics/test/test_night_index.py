"""Purpose: Unit tests for the index of the nights behind the night list.

Description: Checks that the digest of the finished nights ignores tonight
and sees every change to an old night, that the signature of tonight sees
only tonight, and that the memo of each target's frames reads again only the
targets whose stored document changed (and every target when the equipment
or configuration changed).
"""

from types import SimpleNamespace
from typing import Any

import pytest

from wayfindinglib.analytics import night_index
from wayfindinglib.analytics.night_index import NightFrame, NightIndex, TargetFrameMemo

TONIGHT = "2026-11-07"


def _index(
    frames: list[NightFrame], ekos: set[str] | None = None, guiding: set[str] | None = None
) -> NightIndex:
    """Build an index with some frames and sessions.

    Returns
    -------
    index : `NightIndex`
        The index, with tonight at `TONIGHT`.
    """
    return NightIndex(TONIGHT, tuple(frames), frozenset(ekos or ()), frozenset(guiding or ()))


OLD = NightFrame("2026-11-05", "M 31", "M 31/a.fits")
NEW = NightFrame(TONIGHT, "M 31", "M 31/b.fits")


def test_the_finished_digest_ignores_tonights_frames_and_sessions() -> None:
    """A frame or session of tonight leaves it as it is."""
    base = _index([OLD])
    assert _index([OLD, NEW], {TONIGHT}, {TONIGHT}).finished_digest == base.finished_digest


@pytest.mark.parametrize(
    "other",
    [
        _index([OLD, NightFrame("2026-11-05", "M 13", "M 13/c.fits")]),
        _index([]),
        _index([NightFrame("2026-11-04", "M 31", "M 31/a.fits")]),
        _index([OLD], ekos={"2026-11-05"}),
        _index([OLD], guiding={"2026-11-05"}),
    ],
)
def test_the_finished_digest_sees_a_change_to_a_finished_night(other: NightIndex) -> None:
    """A frame or session of an old night changes it."""
    assert other.finished_digest != _index([OLD]).finished_digest


def test_the_tonight_signature_sees_only_tonight() -> None:
    """Tonight's frames and sessions change it; an old night's do not."""
    base = _index([OLD, NEW])
    assert _index([NEW]).tonight_signature == base.tonight_signature
    assert _index([OLD, NEW], ekos={"2026-11-05"}).tonight_signature == base.tonight_signature
    assert _index([OLD]).tonight_signature != base.tonight_signature
    assert _index([OLD, NEW], guiding={TONIGHT}).tonight_signature != base.tonight_signature
    assert base.tonight_subject_ids == {"M 31/b.fits"}
    assert base.finished_subject_ids == {"M 31/a.fits"}


class _Library:
    """A stand-in science library that counts how its targets are read."""

    def __init__(self, frames: dict[str, list[float]]) -> None:
        """Hold the timestamps of each target's frames."""
        self.frames = frames
        self.calls: list[str] = []
        self.targets = SimpleNamespace(list=self._list, get=self._get)

    def _target(self, target_id: str) -> SimpleNamespace:
        """Make the target object for an id.

        Returns
        -------
        target : `SimpleNamespace`
            The target, with its frames and the subject-id rule.
        """
        return SimpleNamespace(
            id=target_id,
            frames=[SimpleNamespace(timestamp=stamp, name=f"{stamp}") for stamp in self.frames[target_id]],
            frame_subject_id=lambda frame: f"{target_id}/{frame.name}",
        )

    def _list(self) -> list[SimpleNamespace]:
        """Read every target.

        Returns
        -------
        targets : `list`
            All the targets.
        """
        self.calls.append("list")
        return [self._target(target_id) for target_id in self.frames]

    def _get(self, target_id: str, refresh: bool = False) -> SimpleNamespace:
        """Read one target.

        Returns
        -------
        target : `SimpleNamespace`
            The target.
        """
        assert refresh
        self.calls.append(target_id)
        return self._target(target_id)


@pytest.fixture
def reading(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Set up a context whose targets are a counting stand-in.

    Returns
    -------
    reading : `SimpleNamespace`
        ``context``, ``library``, ``signs`` (the stored documents' signs,
        change an entry to change a target) and ``context_signs``.
    """
    library = _Library({"M 31": [1.8e9, 1.8e9 + 60], "M 13": [1.8e9 + 120]})
    context = SimpleNamespace(
        config=SimpleNamespace(),
        astrometrics=library,
        night_frame_memo=TargetFrameMemo(),
    )
    state = SimpleNamespace(
        context=context,
        library=library,
        signs={"M 31": (10, "a"), "M 13": (5, "b")},
        context_signs="rig-1",
    )
    catalog = SimpleNamespace(
        active_telescope=lambda: SimpleNamespace(name="T"), active_camera=lambda: SimpleNamespace(name="C")
    )
    monkeypatch.setattr(night_index, "get_equipment_catalog", lambda config: catalog)
    monkeypatch.setattr(night_index, "get_target_document_signs", lambda config: state.signs)
    monkeypatch.setattr(night_index, "nights_context_signs", lambda config: state.context_signs)
    monkeypatch.setattr(
        night_index.capture_history,
        "equipment_light_frames",
        lambda astrometrics, telescope, camera, targets: ((t, f) for t in targets for f in t.frames),
    )
    return state


def _read(reading: SimpleNamespace) -> list[NightFrame]:
    """Read the nights' frames.

    Returns
    -------
    frames : `list` [`NightFrame`]
        The frames, sorted.
    """
    return sorted(night_index.read_night_frames(reading.context), key=lambda frame: frame.subject_id)


def test_the_first_read_lists_every_target_and_the_second_reads_nothing(reading: SimpleNamespace) -> None:
    """Nothing changed: the remembered frames are used."""
    first = _read(reading)
    assert reading.library.calls == ["list"]
    assert [frame.subject_id for frame in first] == [
        "M 13/1800000120.0",
        "M 31/1800000000.0",
        "M 31/1800000060.0",
    ]

    assert _read(reading) == first
    assert reading.library.calls == ["list"]


def test_only_the_target_whose_document_changed_is_read_again(reading: SimpleNamespace) -> None:
    """A new frame in one target reads that target alone."""
    _read(reading)
    reading.library.frames["M 31"].append(1.8e9 + 3600)
    reading.signs["M 31"] = (11, "c")

    frames = _read(reading)

    assert reading.library.calls == ["list", "M 31"]
    assert len(frames) == 4


def test_a_target_removed_from_the_library_is_forgotten(reading: SimpleNamespace) -> None:
    """The frames of a deleted target are no longer listed."""
    _read(reading)
    del reading.signs["M 13"]

    assert {frame.target_id for frame in _read(reading)} == {"M 31"}
    assert reading.library.calls == ["list"]


def test_a_new_target_is_read(reading: SimpleNamespace) -> None:
    """A target added by another program is found."""
    _read(reading)
    reading.library.frames["M 51"] = [1.8e9]
    reading.signs["M 51"] = (3, "d")

    assert "M 51" in {frame.target_id for frame in _read(reading)}


def test_a_change_of_equipment_or_configuration_reads_every_target(reading: SimpleNamespace) -> None:
    """The frames that count depend on the rig, so all are found again."""
    _read(reading)
    reading.context_signs = "rig-2"
    _read(reading)

    assert reading.library.calls == ["list", "list"]


def test_unreadable_document_signs_read_everything_each_time(reading: SimpleNamespace) -> None:
    """With no signs, nothing is remembered."""
    reading.signs = None
    first: Any = _read(reading)
    assert _read(reading) == first
    assert reading.library.calls == ["list", "list"]
    assert not reading.context.night_frame_memo.frames
