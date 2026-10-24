"""Tests for the raw-frame quality tool.

`QualityRecords.measure_frames` gives an AI client statistics on a
target's frames, a check of a folder, or a quarantine preview. These tests
use small fake frames and a fake measuring function, and check the choice of
kind, the newest-frames limit, the summary, and above all that the saved
target is never changed.
"""

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest

from astrometricslib.api.quality import QualityRecords
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.target import FrameRecord, Target


def _target(frame_count: int = 6) -> Target:
    """Build a target with light frames, a dark, and a spectral-free layout.

    Returns
    -------
    target : `Target`
        The target. Frames are numbered by time.
    """
    frames = [
        FrameRecord(
            path=f"/lights/f{number}.fits", role="LIGHT", camera="ZWO ASI533", timestamp=float(number)
        )
        for number in range(frame_count)
    ]
    frames.append(FrameRecord(path="/darks/d.fits", role="DARK", camera="ZWO ASI533", timestamp=99.0))
    return Target(id="T 1", frames=frames)


def _fake_measure(
    working: Target,
    measure_fwhm: Callable[[Any], float | None] | None,
    remeasure: bool,
    camera_id: str | None,
) -> dict[str, int]:
    """Fill in measurements on the frames it is given, like the real function.

    Returns
    -------
    counts : `dict` [`str`, `int`]
        The measured count.
    """
    for number, frame in enumerate(working.frames):
        frame.measurements.background_level = 300.0 + number
        frame.measurements.saturated_pixel_fraction = 0.001 * (number + 1)
    return {"measured": len(working.frames), "skipped": 0, "failed": 0}


@pytest.fixture
def diagnostics() -> QualityRecords:
    """Make the diagnostics object under test.

    Returns
    -------
    diagnostics : `QualityRecords`
        An instance with no configuration, since these kinds need none.
    """
    return QualityRecords(None, None)


def test_input_quality_summarizes_the_newest_frames(diagnostics: QualityRecords) -> None:
    """Only the newest light frames are measured, with a summary per number."""
    with patch(
        "astrometricslib.pipelines.shared.quality.frame_statistics.measure_frame_input_quality", _fake_measure
    ):
        report = diagnostics.measure_frames(_target(), kind="input_quality", limit=3)
    assert report.frames_measured == 3
    assert report.light_frames_in_target == 6
    assert [row["file"] for row in report.frames] == ["f3.fits", "f4.fits", "f5.fits"]
    background = report.summary["background_level"]
    assert (background["frames"], background["minimum"], background["maximum"]) == (3, 300.0, 302.0)
    assert report.summary["measured_fwhm_px"] == {"frames": 0}


def test_the_saved_target_is_never_changed(diagnostics: QualityRecords) -> None:
    """Measuring works on a copy, so the original frames keep no new values."""
    target = _target()
    with patch(
        "astrometricslib.pipelines.shared.quality.frame_statistics.measure_frame_input_quality", _fake_measure
    ):
        diagnostics.measure_frames(target, kind="input_quality")
    assert all(frame.measurements.background_level is None for frame in target.frames)


def test_camera_filter_and_dark_frames(diagnostics: QualityRecords) -> None:
    """Dark frames are never measured, and a camera filter can leave none."""
    with patch(
        "astrometricslib.pipelines.shared.quality.frame_statistics.measure_frame_input_quality", _fake_measure
    ):
        assert diagnostics.measure_frames(_target(), camera_id="asi533").frames_measured == 6
        assert diagnostics.measure_frames(_target(), camera_id="nikon").frames_measured == 0


def test_limit_is_clamped(diagnostics: QualityRecords) -> None:
    """A limit below 1 is raised to 1."""
    with patch(
        "astrometricslib.pipelines.shared.quality.frame_statistics.measure_frame_input_quality", _fake_measure
    ):
        assert diagnostics.measure_frames(_target(), limit=0).frames_measured == 1


def test_raw_check_needs_a_folder_and_passes_the_limit(diagnostics: QualityRecords) -> None:
    """The folder check needs a folder and measures only the newest frames."""
    with pytest.raises(InvalidArgumentError, match="needs a target or a folder_path"):
        diagnostics.measure_frames(kind="raw_check")
    selected = ["/staging/a.fits", "/staging/b.fits", "/staging/c.fits"]
    with (
        patch(
            "astrometricslib.pipelines.shared.quality.frame_selection.select_folder_paths",
            return_value=selected,
        ),
        patch(
            "astrometricslib.pipelines.shared.quality.raw_frame_check.check_raw_frames",
            return_value={"frames": [], "batch": {}},
        ) as check,
    ):
        report = diagnostics.measure_frames(kind="raw_check", folder_path="/staging", limit=2)
    check.assert_called_once_with(paths=selected[-2:])
    assert (report.frames_matching, report.frames_checked) == (3, 2)
    assert report.kind == "raw_check"
    assert report.folder_path == "/staging"


def test_bad_requests_are_errors(diagnostics: QualityRecords) -> None:
    """An unknown kind, a missing target, or an unused argument is refused."""
    with pytest.raises(InvalidArgumentError, match="kind must be one of"):
        diagnostics.measure_frames(kind="everything")
    with pytest.raises(InvalidArgumentError, match="needs a target"):
        diagnostics.measure_frames(kind="input_quality")
    with pytest.raises(InvalidArgumentError, match="needs a target"):
        diagnostics.measure_frames(kind="quarantine_preview")
    with pytest.raises(InvalidArgumentError, match="does not use: camera_id"):
        diagnostics.measure_frames(_target(), kind="quarantine_preview", camera_id="asi533")
    with pytest.raises(InvalidArgumentError, match="does not use: fwhm"):
        diagnostics.measure_frames(_target(), kind="raw_check", include=["fwhm"])


def test_a_file_range_takes_the_first_frames_inside_it(diagnostics: QualityRecords) -> None:
    """With a range, the limit keeps the first frames in it, not the newest."""
    with patch(
        "astrometricslib.pipelines.shared.quality.frame_statistics.measure_frame_input_quality", _fake_measure
    ):
        report = diagnostics.measure_frames(
            _target(), kind="input_quality", first_file="1", last_file="4", limit=2
        )
    assert [row["file"] for row in report.frames] == ["f1.fits", "f2.fits"]
    assert report.frames_matching == 4


def test_a_bad_time_is_refused(diagnostics: QualityRecords) -> None:
    """An unreadable time raises InvalidArgumentError."""
    with pytest.raises(InvalidArgumentError, match="ISO 8601"):
        diagnostics.measure_frames(_target(), since="last night")
