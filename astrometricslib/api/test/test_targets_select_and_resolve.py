"""Purpose: Test `TargetFrames.query` and `TargetCatalog.get`.

Description: Builds a catalog holding one target with a few timed light
frames, a dark and a spectroscopy frame, then checks that the frame choice
follows the filter, file range, time range and limit rules, and that `get`
turns ids into targets and, with ``required``, refuses unknown ones.
"""

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from astrometricslib.api.targets import TargetCatalog
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.target import FrameRecord, Target

_START = 1_760_000_000.0


def _frame(
    number: int, *, role: str = "LIGHT", filter_name: str = "Luminance", timestamp: float | None = None
) -> FrameRecord:
    """Build a frame whose name ends in ``number``.

    Parameters
    ----------
    number : `int`
        The frame number used in the file name and to space the times.
    role : `str`, optional
        The frame role.
    filter_name : `str`, optional
        The filter text.
    timestamp : `float`, optional
        The start time. Defaults to one minute per frame number.

    Returns
    -------
    frame : `FrameRecord`
        The frame.
    """
    return FrameRecord(
        path=f"/data/M13/Light_{number:03d}.fits",
        role=role,
        filter=filter_name,
        timestamp=_START + 60.0 * number if timestamp is None else timestamp,
    )


def _catalog() -> tuple[TargetCatalog, Target]:
    """Build a catalog with one target.

    Returns
    -------
    catalog, target : `TargetCatalog`, `Target`
        The catalog and its only target.
    """
    target = Target(id="M13")
    target.frames = [
        _frame(1),
        _frame(2, filter_name="R"),
        _frame(3),
        _frame(4, role="DARK"),
        _frame(5),
        FrameRecord(path="/data/M13/Light_006.fits", filter="Luminance"),
        _frame(7),
    ]
    storage = MagicMock()
    storage.get.return_value = [target]
    catalog = TargetCatalog(MagicMock(), storage)
    return catalog, target


def _names(frames: list[FrameRecord]) -> list[str]:
    """Return the file names of ``frames``.

    Parameters
    ----------
    frames : `list` [`FrameRecord`]
        The frames.

    Returns
    -------
    names : `list` [`str`]
        The last four characters before ``.fits`` of each path.
    """
    return [frame.path[-8:-5] for frame in frames]


def test_frame_query_keeps_timed_lights_oldest_first() -> None:
    """Darks and frames with no time are left out."""
    catalog, target = _catalog()
    assert _names(catalog.frames.query(target)) == ["001", "002", "003", "005", "007"]


def test_frame_query_filter_and_file_range() -> None:
    """The filter and the file range both narrow the choice."""
    catalog, target = _catalog()
    assert _names(catalog.frames.query(target, filter_name="r")) == ["002"]
    assert _names(catalog.frames.query(target, first_file="003", last_file="005")) == ["003", "005"]


def test_frame_query_time_range_with_offset_and_utc() -> None:
    """ISO times with an offset or none bound the start time."""
    catalog, target = _catalog()
    start = datetime.fromtimestamp(_START + 120.0, tz=UTC)
    until_without_offset = (start + timedelta(seconds=180)).replace(tzinfo=None).isoformat()
    chosen = catalog.frames.query(target, since=start.isoformat(), until=until_without_offset)
    assert _names(chosen) == ["002", "003", "005"]
    offset_text = start.astimezone(timezone(timedelta(hours=-6))).isoformat()
    assert _names(catalog.frames.query(target, since=offset_text, until=offset_text)) == ["002"]


def test_frame_query_takes_a_datetime_and_an_astropy_time() -> None:
    """A `datetime` and an astropy `Time` bound the start time as text does."""
    from astropy.time import Time

    catalog, target = _catalog()
    start = datetime.fromtimestamp(_START + 120.0, tz=UTC)
    chosen = catalog.frames.query(target, since=start, until=Time(start + timedelta(seconds=180)))
    assert _names(chosen) == ["002", "003", "005"]


def test_frame_query_limit_pages_the_frames_in_order() -> None:
    """A limit cuts the page; ``order_by="newest"`` puts the newest first."""
    catalog, target = _catalog()
    assert _names(catalog.frames.query(target, limit=2)) == ["001", "002"]
    assert _names(catalog.frames.query(target, order_by="newest", limit=2)) == ["007", "005"]
    assert _names(catalog.frames.query(target, first_file="002", limit=2)) == ["002", "003"]
    assert catalog.frames.query(target, limit=2).next_offset == 2


def test_frame_query_checks_arguments() -> None:
    """Bad times, bad limits and an id in place of a `Target` raise."""
    catalog, target = _catalog()
    assert len(catalog.frames.query(target)) == 5
    with pytest.raises(InvalidArgumentError):
        catalog.frames.query(target, since="yesterday-ish")
    with pytest.raises(InvalidArgumentError):
        catalog.frames.query(target, limit=0)
    with pytest.raises(InvalidArgumentError):
        catalog.frames.query("M13")  # type: ignore[arg-type]


def test_get_returns_target_for_id_and_can_require_one() -> None:
    """An id is found; ``required`` refuses an unknown id."""
    catalog, target = _catalog()
    assert catalog.get("M13", required=True) is target
    assert catalog.get("m13") is target
    assert catalog.get("M99") is None
    with pytest.raises(NotFoundError):
        catalog.get("M99", required=True)
    with pytest.raises(InvalidArgumentError):
        catalog.get("  ")
