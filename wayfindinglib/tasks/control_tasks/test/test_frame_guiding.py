"""Tests for matching frames to the guiding records.

A frame's exposure window is cut out of the guide-log samples, and the
error inside it is summarised. These tests use fake frames and fake samples
and check the window edges, the statistics, the frames without guiding data
and the flag for a frame far above its neighbours.
"""

from types import SimpleNamespace

import pytest

from astrometricslib import FrameRecord, FrameSelection, Target
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
    logger = SimpleNamespace(get_guiding_logs=lambda **_: rows)
    return SimpleNamespace(config=None, logger_interface=logger, astrometrics=library)


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


def _link(frames: list[FrameRecord], rows: list[dict[str, float]], selection: FrameSelection) -> dict:
    """Run the link on a one-target fake library.

    Parameters
    ----------
    frames : `list` [`FrameRecord`]
        The target's frames.
    rows : `list` [`dict`]
        The guiding rows.
    selection : `FrameSelection`
        Which frames to cover.

    Returns
    -------
    report : `dict`
        The module's report.
    """
    target = Target(id="T 1", frames=frames)
    library = SimpleNamespace(targets=SimpleNamespace(get=lambda _id, refresh=False: target))
    return link_frames_to_guiding(_control(rows, library), "T 1", selection, 50)


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
        exposure="60",
        timestamp=start,
    )


def test_each_frame_gets_only_the_samples_inside_its_window() -> None:
    """Samples are cut at the frame edges and summarised."""
    rows = _samples(1000.0, 20, 1.0) + _samples(1060.0, 20, 4.0)
    report = _link([_frame(1, 1000.0), _frame(2, 1060.0)], rows, FrameSelection())
    first, second = report["frames"]
    assert first["guide_samples"] in (20, 21)
    assert first["rms_ra_arcsec"] == pytest.approx(1.0)
    assert second["rms_ra_arcsec"] == pytest.approx(4.0)
    assert first["coverage_fraction"] > 0.9


def test_a_frame_with_no_guiding_is_reported_not_guessed() -> None:
    """A frame outside the guide logs has zero samples and no statistics."""
    report = _link([_frame(1, 5000.0)], _samples(1000.0, 20, 1.0), FrameSelection())
    assert report["frames"][0]["guide_samples"] == 0
    assert "rms_total_arcsec" not in report["frames"][0]
    assert report["frames_without_guiding"] == 1


def test_a_frame_far_above_the_group_is_flagged() -> None:
    """A frame at several times the group's median error is listed as worse."""
    frames = [_frame(number, 1000.0 + 60.0 * number) for number in range(5)]
    rows = []
    for number in range(5):
        rows += _samples(1000.0 + 60.0 * number, 19, 10.0 if number == 3 else 1.0)
    report = _link(frames, rows, FrameSelection())
    assert report["group"]["worse_than_group"] == ["T_1_003.fits"]


def test_an_unknown_target_is_an_error() -> None:
    """A target that is not in the library gives an error, not a crash."""
    library = SimpleNamespace(targets=SimpleNamespace(get=lambda _id, refresh=False: None))
    report = link_frames_to_guiding(_control([], library), "Nope", FrameSelection(), 10)
    assert "No target" in report["error"]


def test_image_quality_is_put_on_the_same_row_as_the_guide_error() -> None:
    """Each frame's measured stars join its guiding numbers by file name."""
    frames = [_frame(1, 1000.0), _frame(2, 1060.0)]
    target = Target(id="T 1", frames=frames)
    asked: dict[str, object] = {}

    def frame_quality(**arguments: object) -> dict:
        """Answer like the raw frame check, with one row per frame.

        Returns
        -------
        report : `dict`
            Rows for both frames, the second one flagged.
        """
        asked.update(arguments)
        return {
            "frames": [
                {"path": "/x/T_1_001.fits", "star_count": 900, "fwhm_px": 3.1, "flags": []},
                {"path": "/x/T_1_002.fits", "star_count": 400, "fwhm_px": 4.5, "flags": ["soft"]},
            ]
        }

    diagnostics = SimpleNamespace(frame_quality=frame_quality)
    library = SimpleNamespace(
        targets=SimpleNamespace(get=lambda _id, refresh=False: target),
        processing=SimpleNamespace(diagnostics=diagnostics),
    )
    rows = _samples(1000.0, 20, 1.0) + _samples(1060.0, 20, 4.0)
    report = link_frames_to_guiding(
        _control(rows, library), "T 1", FrameSelection(), 50, include_quality=True
    )
    first, second = report["frames"]
    assert (asked["first_file"], asked["last_file"]) == ("T_1_001.fits", "T_1_002.fits")
    assert first["star_count"] == 900
    assert second["quality_flags"] == ["soft"]
    assert second["rms_ra_arcsec"] == pytest.approx(4.0)
    assert "2 frame(s)" in report["image_quality"]
