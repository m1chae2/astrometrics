"""Tests for matching frames to the guiding records.

A frame's exposure window is cut out of the guide-log samples, and the
error inside it is summarised. These tests use fake frames and fake samples
and check the window edges, the statistics, the frames without guiding data
and the flag for a frame far above its neighbours.
"""

from types import SimpleNamespace

import pytest

from astrometricslib import FrameRecord, Target
from astrometricslib.arguments import Page
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from astrometricslib.pipelines.stacking.frame_queries import query_frames
from wayfindinglib.tasks.control_tasks.frame_guiding import link_frames_to_guiding


def _control(rows: list[dict[str, float]], library: object = None) -> SimpleNamespace:
    """Build a stand-in control with fixed guiding rows.

    Parameters
    ----------
    rows : `list` [`dict`]
        The guiding rows to hand back, whatever the query.
    library : `object`, optional
        The stand-in for the control's shared `Astrometrics` handle.

    Returns
    -------
    control : `types.SimpleNamespace`
        An object with the two attributes the module uses.
    """
    logger = SimpleNamespace(get_guiding_samples=lambda **_: rows)
    return SimpleNamespace(config=None, records=logger, astrometrics=library)


def _samples(start: float, count: int, error: float) -> list[dict[str, float]]:
    """Make one guide sample per 3 seconds with a fixed error.

    Parameters
    ----------
    start : `float`
        Time of the first sample.
    count : `int`
        How many samples.
    error : `float`
        The right-ascension error in arcseconds; declination error is zero.

    Returns
    -------
    rows : `list` [`dict`]
        Guiding rows.
    """
    return [{"timestamp": start + 3.0 * index, "dra": error, "ddec": 0.0} for index in range(count)]


def _link(frames: list[FrameRecord], rows: list[dict[str, float]], chosen: object = None) -> dict:
    """Run the link on a one-target fake library.

    Parameters
    ----------
    frames : `list` [`FrameRecord`]
        The target's frames.
    rows : `list` [`dict`]
        The guiding rows.
    chosen : `FrameChoice`, optional
        Which frames to cover. Every light frame when omitted.

    Returns
    -------
    report : `dict`
        The module's report, as plain data.
    """
    target = Target(id="T 1", frames=frames)
    return link_frames_to_guiding(_control(rows), target, chosen).model_dump()


def _frame(number: int, start: float) -> FrameRecord:
    """Make a 60 second luminance frame.

    Parameters
    ----------
    number : `int`
        The frame number, used in the file name.
    start : `float`
        When it began.

    Returns
    -------
    frame : `FrameRecord`
        The frame.
    """
    return FrameRecord(
        path=f"/lights/T_1_{number:03d}.fits",
        role="LIGHT",
        filter="Luminance",
        exposure_s=60,
        timestamp=start,
    )


def test_each_frame_gets_only_the_samples_inside_its_window() -> None:
    """Samples are cut at the frame edges and summarised."""
    rows = _samples(1000.0, 20, 1.0) + _samples(1060.0, 20, 4.0)
    report = _link([_frame(1, 1000.0), _frame(2, 1060.0)], rows)
    first, second = report["frames"]
    assert first["guide_samples"] in (20, 21)
    assert first["rms_ra_arcsec"] == pytest.approx(1.0)
    assert second["rms_ra_arcsec"] == pytest.approx(4.0)
    assert first["coverage_fraction"] > 0.9


def test_a_frame_with_no_guiding_is_reported_not_guessed() -> None:
    """A frame outside the guide logs has zero samples and no statistics."""
    report = _link([_frame(1, 5000.0)], _samples(1000.0, 20, 1.0))
    assert report["frames"][0]["guide_samples"] == 0
    assert "rms_total_arcsec" not in report["frames"][0]
    assert report["frames_without_guiding"] == 1


def test_a_frame_far_above_the_group_is_flagged() -> None:
    """A frame at several times the group's median error is listed as worse."""
    frames = [_frame(number, 1000.0 + 60.0 * number) for number in range(5)]
    rows = []
    for number in range(5):
        rows += _samples(1000.0 + 60.0 * number, 19, 10.0 if number == 3 else 1.0)
    report = _link(frames, rows)
    assert report["group"]["worse_than_group"] == ["T_1_003.fits"]


def test_a_path_that_is_not_a_frame_of_the_target_is_an_error() -> None:
    """A path the target does not hold raises `NotFoundError`."""
    with pytest.raises(NotFoundError, match="not a frame"):
        _link([_frame(1, 1000.0)], [], ["/elsewhere/x.fits"])


def test_image_quality_is_put_on_the_same_row_as_the_guide_error() -> None:
    """Each frame's measured stars join its guiding numbers by file name."""
    frames = [_frame(1, 1000.0), _frame(2, 1060.0)]
    target = Target(id="T 1", frames=frames)
    asked: dict[str, object] = {}

    def measure(frames: list[FrameRecord]) -> SimpleNamespace:
        """Answer like the frame check, with one batch-check row per frame.

        Returns
        -------
        report : `types.SimpleNamespace`
            Rows for both frames, the second one flagged.
        """
        asked["files"] = [frame.path.rsplit("/", 1)[-1] for frame in frames]
        return SimpleNamespace(
            checks=SimpleNamespace(
                frames=[
                    {"path": "/x/T_1_001.fits", "star_count": 900, "fwhm_px": 3.1, "flags": []},
                    {"path": "/x/T_1_002.fits", "star_count": 400, "fwhm_px": 4.5, "flags": ["soft"]},
                ]
            )
        )

    diagnostics = SimpleNamespace(frames=SimpleNamespace(measure=measure))
    library = SimpleNamespace(quality=diagnostics)
    rows = _samples(1000.0, 20, 1.0) + _samples(1060.0, 20, 4.0)
    report = link_frames_to_guiding(_control(rows, library), target, include=["quality"]).model_dump()
    first, second = report["frames"]
    assert asked["files"] == ["T_1_001.fits", "T_1_002.fits"]
    assert first["star_count"] == 900
    assert second["quality_flags"] == ["soft"]
    assert second["rms_ra_arcsec"] == pytest.approx(4.0)
    assert "2 frame(s)" in report["image_quality"]


def test_a_page_cut_short_or_an_unknown_part_is_refused() -> None:
    """A frame page cut short by `limit`, or an unknown part, raises."""
    target = Target(id="T 1", frames=[_frame(1, 1000.0)])
    with pytest.raises(InvalidArgumentError, match="cut short"):
        link_frames_to_guiding(_control([]), target, Page(items=(), total=2, next_offset=0))
    with pytest.raises(InvalidArgumentError, match="include"):
        link_frames_to_guiding(_control([]), target, include=["noise"])


def test_a_frame_choice_picks_the_frames() -> None:
    """Frames from the library's frame query are the ones reported."""
    from datetime import UTC, datetime

    frames = [_frame(1, 1000.0), _frame(2, 1060.0)]
    target = Target(id="T 1", frames=frames)
    chosen = query_frames(target, None, since=datetime.fromtimestamp(1030.0, UTC))
    report = link_frames_to_guiding(_control([]), target, chosen).model_dump()
    assert [row["file"] for row in report["frames"]] == ["T_1_002.fits"]
    assert report["frames_matching"] == 1
