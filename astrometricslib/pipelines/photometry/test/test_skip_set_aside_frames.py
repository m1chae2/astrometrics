"""Purpose: Test that photometry skips frames the quarantine sets aside.

Description: On 2026-09-24 most M 27 frames were clouded, so no star was in
every frame and the night had no comparison stars. Photometry now asks the
frame quarantine's own judging function which frames it would set aside, and
measures the rest. These tests use stand-in frame records and a stand-in
measuring function, so no FITS data is read. They check that:

* a session with clouded frames keeps only its clear frames;
* each skipped frame and its reason is in the job's warnings;
* a frame already in an `_excluded` folder is skipped;
* a night where every frame fails is left out, with a warning.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.photometry import runner
from astrometricslib.pipelines.photometry.pre_processing import skip_set_aside_frames as skip_module
from astrometricslib.pipelines.photometry.pre_processing.skip_set_aside_frames import (
    ALREADY_SET_ASIDE_REASON,
    skip_set_aside_frames,
)
from astrometricslib.pipelines.pipeline_base import PipelineRequest
from astrometricslib.pipelines.stacking.pre_processing import frame_quarantine

NIGHT_START = datetime(2026, 9, 24, 3, 0, tzinfo=UTC).timestamp()
FRAME_COUNT = 12


def make_frames(count: int = FRAME_COUNT) -> list[FrameRecord]:
    """Build frames of one night, two minutes apart.

    Returns
    -------
    frames : `list` [`FrameRecord`]
        Frames named ``frame_000.fits`` and so on.
    """
    return [
        FrameRecord(path=f"/lights/M 99/frame_{index:03d}.fits", timestamp=NIGHT_START + 120.0 * index)
        for index in range(count)
    ]


def stand_in_measurer(cloudy_indexes: set[int]) -> Any:
    """Build a measuring function that sees the given frames as clouded.

    Returns
    -------
    measure : `Callable`
        Takes a path and returns a quarantine measurement.
    """

    def measure(path: str) -> dict[str, Any]:
        """Measure one stand-in frame.

        Returns
        -------
        measurement : `dict`
            A sound frame, or a clouded one: few stars under bright sky.
        """
        index = int(Path(path).stem.split("_")[1])
        clouded = index in cloudy_indexes
        return {
            "path": path,
            "star_count": 900 if clouded else 3400 - 10 * index,
            "star_elongation": 1.05,
            "sky_median_adu": 3500.0 if clouded else 1400.0,
        }

    return measure


@pytest.fixture
def no_frames_set_aside(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the library hold no `_excluded` frames."""
    monkeypatch.setattr(frame_quarantine, "list_set_aside_frames", lambda *_arguments: [])


def use_measurer(monkeypatch: pytest.MonkeyPatch, cloudy_indexes: set[int]) -> None:
    """Give the quarantine the stand-in measuring function."""
    monkeypatch.setattr(
        frame_quarantine,
        "make_quarantine_measurer",
        lambda _target_id: stand_in_measurer(cloudy_indexes),
    )


def test_clouded_frames_are_skipped_and_clear_frames_kept(
    monkeypatch: pytest.MonkeyPatch, no_frames_set_aside: None
) -> None:
    """The quarantine's judgement splits the night into clear and clouded."""
    use_measurer(monkeypatch, {2, 3, 4})
    frames = make_frames()

    kept, skipped = skip_set_aside_frames("M 99", frames, "/frames")

    assert [frame.path for frame in kept] == [
        frame.path for index, frame in enumerate(frames) if index > 4 or index < 2
    ]
    assert [frame.path for frame, _reason in skipped] == [frames[index].path for index in (2, 3, 4)]
    assert all(reason.startswith("clouded") for _frame, reason in skipped)


def test_a_frame_in_an_excluded_folder_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A frame the manifest lists, with no file in place, is skipped."""
    use_measurer(monkeypatch, set())
    frames = make_frames()
    monkeypatch.setattr(
        frame_quarantine,
        "list_set_aside_frames",
        lambda *_arguments: [SimpleNamespace(original_path=frames[5].path)],
    )

    kept, skipped = skip_set_aside_frames("M 99", frames, "/frames")

    assert [(frame.path, reason) for frame, reason in skipped] == [(frames[5].path, ALREADY_SET_ASIDE_REASON)]
    assert frames[5].path not in {frame.path for frame in kept}
    assert len(kept) == FRAME_COUNT - 1


def test_each_skipped_frame_and_its_reason_is_logged_for_the_job(
    monkeypatch: pytest.MonkeyPatch, no_frames_set_aside: None, caplog: pytest.LogCaptureFixture
) -> None:
    """One warning per night names every skipped frame with its reason."""
    use_measurer(monkeypatch, {2, 3})

    with caplog.at_level(logging.WARNING, logger=skip_module.logger.name):
        skip_set_aside_frames("M 99", make_frames(), "/frames")

    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1
    assert "skipped 2 frame(s) of the night 2026-09-24" in messages[0]
    assert "frame_002.fits (clouded" in messages[0]
    assert "frame_003.fits (clouded" in messages[0]
    assert "frame_004.fits" not in messages[0]


def test_process_input_builds_the_session_from_the_clear_frames_only(
    monkeypatch: pytest.MonkeyPatch, no_frames_set_aside: None
) -> None:
    """The session, image paths and warnings follow the clear frames."""
    use_measurer(monkeypatch, {2, 3, 4})
    frames = make_frames()
    request = PipelineRequest(target=Target(id="M 99", frames=frames), catalog_access=None, options={})

    result = runner.PhotometryPipelineAdapter().process_input(request)

    assert result.has_work
    clear_paths = [frame.path for index, frame in enumerate(frames) if index not in (2, 3, 4)]
    assert result.payload["image_paths"] == clear_paths
    assert [
        path for session in result.payload["photometry_sessions"] for path in session.frame_paths
    ] == clear_paths
    assert len(result.payload["quarantine_skipped_frames"]) == 3
    (warning,) = result.payload["input_logged_warnings"]
    assert "frame_002.fits" in warning.message


def test_a_night_where_every_frame_fails_is_left_out_with_a_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing is measured, and the reason and the warning say why."""
    use_measurer(monkeypatch, set())
    frames = make_frames()
    monkeypatch.setattr(
        frame_quarantine,
        "list_set_aside_frames",
        lambda *_arguments: [SimpleNamespace(original_path=frame.path) for frame in frames],
    )
    request = PipelineRequest(target=Target(id="M 99", frames=frames), catalog_access=None, options={})

    result = runner.PhotometryPipelineAdapter().process_input(request)

    assert not result.has_work
    assert "set aside by the frame quarantine" in result.payload["no_work_reason"]
    assert len(result.payload["quarantine_skipped_frames"]) == FRAME_COUNT
    assert len(result.payload["input_logged_warnings"]) == 1
