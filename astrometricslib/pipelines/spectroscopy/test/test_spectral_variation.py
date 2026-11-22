"""Purpose: Unit tests for the spectral variation stage's runner.

Description: Checks `run_spectral_variation_analysis`: it follows only the
spectral frames, skips with a recorded reason when there are too few, passes
the follow-only mode to the batch, reports its counts, and stops when the run
was abandoned. The batch is replaced by a recorder, so no image is read.
"""

import logging
from types import SimpleNamespace

import pytest

from astrometricslib.foundation.run_abandonment import RunAbandonedError, RunAbandonment, bind_abandonment
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.spectroscopy import batch
from astrometricslib.pipelines.spectroscopy.spectral_variation import run_spectral_variation_analysis
from astrometricslib.utilities.parallel_batch import BatchRunSummary


def _spectral_frames(count: int) -> list[FrameRecord]:
    """Make spectral frame records.

    Returns
    -------
    frames : `list` [`FrameRecord`]
        The frames.
    """
    return [
        FrameRecord(path=f"/lib/spec_{index}.fits", role="LIGHT", filter="SPEC", timestamp=1000.0 + index)
        for index in range(count)
    ]


@pytest.fixture
def recorded_batch(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Replace the batch with a recorder that reports every frame followed.

    Returns
    -------
    calls : `list` [`dict`]
        One entry per call, with the frames and the keyword arguments.
    """
    calls: list[dict] = []

    def _fake(config, target, frame_records, **keywords):  # ruff: ignore[missing-return-type-private-function, missing-type-function-argument, missing-type-kwargs]
        calls.append({"frames": frame_records, **keywords})
        summary = BatchRunSummary()
        for frame in frame_records:
            summary.succeeded.append(frame.path)
            summary.results[frame.path] = {"status": "success", "stars_processed": 2}
        summary.notes["followed_stars"] = {"session": {"named": ["A"], "unnamed": ["FIELD_J1", "FIELD_J2"]}}
        return summary, [(SimpleNamespace(id="session"), None)]

    monkeypatch.setattr(batch, "process_spectroscopy_frames_by_session", _fake)
    return calls


def test_it_follows_the_spectral_frames_in_follow_only_mode(recorded_batch: list[dict]) -> None:
    """Verify only spectral frames go to the batch, in follow-only mode."""
    imaging = FrameRecord(path="/lib/light.fits", role="LIGHT", filter="Luminance")
    target = Target(id="VariationTarget", frames=[imaging, *_spectral_frames(3)])

    result = run_spectral_variation_analysis(target, None, None, None, None, measured_at=None)

    assert result["status"] == "finished"
    assert result["frames_followed"] == 3
    assert result["stars_processed"] == 6
    assert (result["stars_named"], result["stars_unnamed"]) == (1, 2)
    assert [frame.path for frame in recorded_batch[0]["frames"]] == [f"/lib/spec_{i}.fits" for i in range(3)]
    assert recorded_batch[0]["follow_only"] is True


def test_fewer_than_two_frames_is_skipped_with_a_logged_reason(
    recorded_batch: list[dict], caplog: pytest.LogCaptureFixture
) -> None:
    """Verify one spectral frame skips the stage and the reason is not lost."""
    target = Target(id="OneFrameTarget", frames=_spectral_frames(1))

    with caplog.at_level(logging.WARNING):
        result = run_spectral_variation_analysis(target, None, None, None, None)

    assert result["status"] == "skipped"
    assert "fewer than 2" in result["reason"]
    assert recorded_batch == []
    assert any("fewer than 2" in message for message in caplog.messages)


def test_a_run_where_every_session_is_too_short_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify skipped frames come back with reasons when no session can run."""
    skipped = [("/lib/spec_0.fits", "session s has fewer than 2 frames, too few to follow a change")]

    def _fake(config, target, frame_records, **keywords):  # ruff: ignore[missing-return-type-private-function, missing-type-function-argument, missing-type-kwargs]
        summary = BatchRunSummary()
        summary.skipped.extend(skipped)
        return summary, []

    monkeypatch.setattr(batch, "process_spectroscopy_frames_by_session", _fake)
    target = Target(id="ShortSessionsTarget", frames=_spectral_frames(2))

    result = run_spectral_variation_analysis(target, None, None, None, None)

    assert result["status"] == "skipped"
    assert result["skipped_frames"] == skipped


def test_an_abandoned_run_stops_before_any_extraction(recorded_batch: list[dict]) -> None:
    """Verify a run that was given up on does no further work."""
    target = Target(id="AbandonedTarget", frames=_spectral_frames(3))
    abandonment = RunAbandonment()
    abandonment.abandon()

    with bind_abandonment(abandonment), pytest.raises(RunAbandonedError):
        run_spectral_variation_analysis(target, None, None, None, None)

    assert recorded_batch == []


def test_a_run_where_no_frame_could_be_followed_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a run that followed nothing is skipped, not finished."""
    reason = "the session's stars could not be placed on this frame"

    def _fake(config, target, frame_records, **keywords):  # ruff: ignore[missing-return-type-private-function, missing-type-function-argument, missing-type-kwargs]
        summary = BatchRunSummary()
        summary.skipped.extend((frame.path, reason) for frame in frame_records)
        return summary, [(SimpleNamespace(id="session"), None)]

    monkeypatch.setattr(batch, "process_spectroscopy_frames_by_session", _fake)
    target = Target(id="NothingFollowedTarget", frames=_spectral_frames(2))

    result = run_spectral_variation_analysis(target, None, None, None, None)

    assert result["status"] == "skipped"
    assert reason in result["reason"]
