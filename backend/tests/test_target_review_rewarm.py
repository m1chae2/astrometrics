"""Purpose: Tests for the night-time re-reads of the target reviews.

Description: `TargetReviewRewarmer` reads a changed target's Image Processing
reviews again in the background. These tests check that a burst of changes
gives one re-read per target after a quiet spell, that each re-read goes
through the heavy lane and waits while the lane is busy, that the targets the
app asked about last are read again too, that nothing is read before the
start-up warm-up has finished, and that stopping drops what is waiting. They
also check that the services that change a target's data ask for a re-read.
"""

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services.data.target_service import TargetService
from backend.services.infrastructure import heavy_lane, target_review_rewarm
from backend.services.infrastructure.sync_service import SyncService
from backend.services.infrastructure.target_review_rewarm import TargetReviewRewarmer
from backend.services.observatory.advisory_service import AdvisoryService
from backend.services.processing.image_processing_service import ImageProcessingService
from backend.services.processing.quality_measurement_service import QualityMeasurementService

QUIET = 0.1
"""The quiet spell the tests use, in seconds."""


def _ready() -> threading.Event:
    """Give a start-up warm-up flag that is already set.

    Returns
    -------
    ready : `threading.Event`
        A set event.
    """
    ready = threading.Event()
    ready.set()
    return ready


class _Recorder:
    """A stand-in warm function that records the targets it read."""

    def __init__(self, rewarmer: TargetReviewRewarmer | None = None) -> None:
        """Start with no reads.

        Parameters
        ----------
        rewarmer : `TargetReviewRewarmer`, optional
            When given, each read also asks it to note the target, as the
            real service methods do.
        """
        self.targets: list[str] = []
        self._rewarmer = rewarmer

    def __call__(self, target_id: str) -> None:
        """Record one read."""
        self.targets.append(target_id)
        if self._rewarmer is not None:
            self._rewarmer.note_requested(target_id)


async def _run_for(rewarmer: TargetReviewRewarmer, warm: Callable[[str], object], seconds: float) -> None:
    """Run the rewarmer's loop for a while, then cancel it.

    Parameters
    ----------
    rewarmer : `TargetReviewRewarmer`
        The rewarmer to run.
    warm : callable
        The warm function.
    seconds : `float`
        How long to let it run.
    """
    task = asyncio.create_task(rewarmer.run(warm, _ready()))
    await asyncio.sleep(seconds)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_a_burst_of_changes_gives_one_re_read_per_target_after_the_quiet_spell() -> None:
    """Many changes in a burst read each target once, after the burst."""
    rewarmer = TargetReviewRewarmer(quiet_seconds=QUIET)
    warm = _Recorder()

    async def scenario() -> None:
        """Send a burst, check nothing ran during it, then let it settle."""
        task = asyncio.create_task(rewarmer.run(warm, _ready()))
        for _ in range(10):
            rewarmer.request(["M 31"])
            rewarmer.request(["M 81", "M 31"])
            await asyncio.sleep(QUIET / 4)
        assert warm.targets == []
        await asyncio.sleep(QUIET * 4)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())

    assert sorted(warm.targets) == ["M 31", "M 81"]
    assert rewarmer.waiting_targets() == []


def test_a_target_that_keeps_changing_is_read_after_the_longest_wait() -> None:
    """Steady changes cannot put a re-read off forever."""
    rewarmer = TargetReviewRewarmer(quiet_seconds=QUIET, longest_wait_seconds=QUIET * 2)
    warm = _Recorder()

    async def scenario() -> None:
        """Change the target more often than the quiet spell for a while."""
        task = asyncio.create_task(rewarmer.run(warm, _ready()))
        for _ in range(12):
            rewarmer.request(["M 13"])
            await asyncio.sleep(QUIET / 3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())

    assert "M 13" in warm.targets


def test_re_reads_go_through_the_heavy_lane_one_target_per_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every re-read is its own `run_heavy` step."""
    lane_steps: list[tuple[str, ...]] = []
    real_run_heavy = heavy_lane.run_heavy

    async def recording_run_heavy(function: Callable[..., Any], *args: Any) -> Any:
        """Note the step and run it in the real lane.

        Returns
        -------
        result : `~typing.Any`
            What the step returned.
        """
        lane_steps.append(tuple(str(argument) for argument in args[1:]))
        return await real_run_heavy(function, *args)

    monkeypatch.setattr(target_review_rewarm, "run_heavy", recording_run_heavy)
    rewarmer = TargetReviewRewarmer(quiet_seconds=QUIET)
    warm = _Recorder()

    async def scenario() -> None:
        """Change two targets and let the re-reads run."""
        rewarmer.request(["M 31", "M 81"])
        await _run_for(rewarmer, warm, QUIET * 5)

    asyncio.run(scenario())

    assert sorted(lane_steps) == [("M 31",), ("M 81",)]
    assert sorted(warm.targets) == ["M 31", "M 81"]


def test_a_busy_lane_puts_the_re_read_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """While a user's heavy request waits, nothing is read; then it is."""
    lane = {"has_room": False}
    monkeypatch.setattr(target_review_rewarm, "heavy_lane_has_room", lambda: lane["has_room"])
    rewarmer = TargetReviewRewarmer(quiet_seconds=QUIET, busy_retry_seconds=QUIET / 2)
    warm = _Recorder()

    async def scenario() -> None:
        """Keep the lane busy past the quiet spell, then free it."""
        task = asyncio.create_task(rewarmer.run(warm, _ready()))
        rewarmer.request(["M 57"])
        await asyncio.sleep(QUIET * 4)
        assert warm.targets == []
        assert rewarmer.waiting_targets() == ["M 57"]
        lane["has_room"] = True
        await asyncio.sleep(QUIET * 3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())

    assert warm.targets == ["M 57"]


def test_the_lane_has_room_only_with_a_free_place_and_nobody_waiting() -> None:
    """`heavy_lane_has_room` is false when the places are all taken."""

    async def scenario() -> list[bool]:
        """Look at the lane empty, full and empty again.

        Returns
        -------
        answers : `list` [`bool`]
            The three answers.
        """
        answers = [heavy_lane.heavy_lane_has_room()]
        release = threading.Event()
        holders = [
            asyncio.create_task(heavy_lane.run_heavy(release.wait, 5.0))
            for _ in range(heavy_lane.HEAVY_LANE_SLOTS + 1)
        ]
        await asyncio.sleep(0.1)
        answers.append(heavy_lane.heavy_lane_has_room())
        release.set()
        await asyncio.gather(*holders)
        answers.append(heavy_lane.heavy_lane_has_room())
        return answers

    assert asyncio.run(scenario()) == [True, False, True]


def test_the_recently_asked_about_targets_are_read_again_after_a_change() -> None:
    """A change to one target also re-reads the targets the app asked about."""
    rewarmer = TargetReviewRewarmer(quiet_seconds=QUIET, recent_count=2)
    for target_id in ("M 1", "M 2", "M 3", "M 2"):
        rewarmer.note_requested(target_id)
    warm = _Recorder(rewarmer)

    async def scenario() -> None:
        """Change a target the app never asked about."""
        rewarmer.request(["M 31"])
        await _run_for(rewarmer, warm, QUIET * 5)

    asyncio.run(scenario())

    assert rewarmer.recent_targets() == ["M 2", "M 3"]
    assert sorted(warm.targets) == ["M 2", "M 3", "M 31"]


def test_a_change_that_names_no_target_re_reads_only_the_recent_targets() -> None:
    """New calibration frames name no target: only the recent ones are read."""
    rewarmer = TargetReviewRewarmer(quiet_seconds=QUIET)
    rewarmer.note_requested("M 27")
    rewarmer.request([])

    assert rewarmer.waiting_targets() == ["M 27"]


def test_background_reads_are_not_counted_as_the_app_asking() -> None:
    """The warm-ups' own reads leave the recent targets alone."""
    rewarmer = TargetReviewRewarmer()

    with rewarmer.background_reads():
        rewarmer.note_requested("M 31")
    rewarmer.note_requested("M 81")

    assert rewarmer.recent_targets() == ["M 81"]


def test_nothing_is_read_before_the_start_up_warm_up_has_finished(monkeypatch: pytest.MonkeyPatch) -> None:
    """The loop waits for the start-up flag before it reads anything."""
    monkeypatch.setattr(target_review_rewarm, "READY_POLL_SECONDS", QUIET / 4)
    rewarmer = TargetReviewRewarmer(quiet_seconds=QUIET / 4)
    warm = _Recorder()
    ready = threading.Event()

    async def scenario() -> None:
        """Change a target, wait past the quiet spell, then finish start-up."""
        task = asyncio.create_task(rewarmer.run(warm, ready))
        await asyncio.sleep(0)
        rewarmer.request(["M 42"])
        await asyncio.sleep(QUIET * 3)
        assert warm.targets == []
        ready.set()
        await asyncio.sleep(QUIET * 3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())

    assert warm.targets == ["M 42"]


def test_stopping_drops_the_waiting_re_reads_and_ends_the_loop() -> None:
    """At shutdown the waiting targets are dropped and the loop returns."""
    rewarmer = TargetReviewRewarmer(quiet_seconds=QUIET * 3)
    warm = _Recorder()

    async def scenario() -> bool:
        """Change a target, stop before it is due, and wait for the loop.

        Returns
        -------
        ended : `bool`
            Whether the loop ended on its own after `stop`.
        """
        task = asyncio.create_task(rewarmer.run(warm, _ready()))
        await asyncio.sleep(0)
        rewarmer.request(["M 31"])
        rewarmer.stop()
        rewarmer.request(["M 81"])
        await asyncio.sleep(QUIET * 5)
        ended = task.done()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return ended

    assert asyncio.run(scenario()) is True
    assert warm.targets == []
    assert rewarmer.waiting_targets() == []


def test_cancelling_the_loop_leaves_the_waiting_targets_unread() -> None:
    """Cancelling the task (the lifespan's shutdown) reads nothing more."""
    rewarmer = TargetReviewRewarmer(quiet_seconds=QUIET * 2)
    warm = _Recorder()

    async def scenario() -> None:
        """Change a target and cancel the loop before it is due."""
        task = asyncio.create_task(rewarmer.run(warm, _ready()))
        await asyncio.sleep(0)
        rewarmer.request(["M 31"])
        await asyncio.sleep(QUIET / 2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()
        await asyncio.sleep(QUIET * 3)

    asyncio.run(scenario())

    assert warm.targets == []


def test_a_failed_re_read_is_logged_at_debug_level_and_the_loop_goes_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing read is a debug line; the next target is still read."""
    rewarmer = TargetReviewRewarmer(quiet_seconds=QUIET)
    read: list[str] = []

    def warm(target_id: str) -> None:
        """Fail for one target, record the other.

        Raises
        ------
        RuntimeError
            For ``M 31``.
        """
        if target_id == "M 31":
            raise RuntimeError("drive not mounted")
        read.append(target_id)

    async def scenario() -> None:
        """Change both targets and let the loop run."""
        rewarmer.request(["M 31", "M 81"])
        await _run_for(rewarmer, warm, QUIET * 5)

    with caplog.at_level(logging.DEBUG, logger=target_review_rewarm.__name__):
        asyncio.run(scenario())

    assert read == ["M 81"]
    failures = [record for record in caplog.records if "re-read failed" in record.getMessage()]
    assert [record.levelno for record in failures] == [logging.DEBUG]


def test_a_request_from_another_thread_wakes_the_loop() -> None:
    """A service thread's request is read without waiting for anything else."""
    rewarmer = TargetReviewRewarmer(quiet_seconds=QUIET)
    warm = _Recorder()

    async def scenario() -> None:
        """Send the request from a worker thread while the loop idles."""
        task = asyncio.create_task(rewarmer.run(warm, _ready()))
        await asyncio.sleep(QUIET)
        await asyncio.to_thread(rewarmer.request, ["M 13"])
        await asyncio.sleep(QUIET * 4)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    started = time.monotonic()
    asyncio.run(scenario())

    assert warm.targets == ["M 13"]
    assert time.monotonic() - started < 5.0


# --- The services that change a target's data ask for a re-read. ---


def test_putting_frames_back_asks_for_a_re_read() -> None:
    """A put-back that moved frames asks for the target to be read again."""
    rewarmer = MagicMock()
    astrometrics = MagicMock()
    astrometrics.targets.frames.restore.return_value = [SimpleNamespace(path="/frames/a.fits")]
    service = TargetService(config=MagicMock(), astrometrics=astrometrics, review_rewarmer=rewarmer)

    service.put_back_frames("M 31", ["/frames/_excluded/a.fits"])

    rewarmer.request.assert_called_once_with(["M 31"])


def test_the_image_processing_reads_note_the_target_the_app_asked_about() -> None:
    """The three Image Processing reads note the target the app asked about."""
    rewarmer = MagicMock()
    astrometrics = MagicMock()
    astrometrics.targets.get.return_value = None
    service = TargetService(config=MagicMock(), astrometrics=astrometrics, review_rewarmer=rewarmer)
    service.subjects = MagicMock()
    advisory = AdvisoryService(wayfinder=MagicMock(), review_rewarmer=rewarmer)

    service.get_frame_statuses("M 31")
    service.get_frame_stats_grouped("M 81")
    advisory.get_star_shape_review("M 13")
    advisory.get_star_shape_review("M 57", night="2026-11-26")

    noted = [call.args[0] for call in rewarmer.note_requested.call_args_list]
    assert noted == ["M 31", "M 81", "M 13"]


def test_announced_quality_rows_ask_for_a_re_read() -> None:
    """Stored quality rows ask for the measured target to be read again."""
    asked: list[list[str]] = []
    service = QualityMeasurementService(
        SimpleNamespace(quality=MagicMock(), targets=MagicMock()), review_trigger=asked.append
    )

    service.announce_summaries([
        {"target_id": "M 13", "frames_measured": 3, "rows_written": 12, "observing_night_ids": []},
        {"target_id": "M 27", "frames_measured": 0, "rows_written": 0, "observing_night_ids": []},
    ])

    assert asked == [["M 13"]]


def test_a_sync_that_added_frames_asks_for_a_re_read() -> None:
    """A target sync names its target; a calibration sync names none."""
    asked: list[list[str]] = []

    class _Remote:
        """A stand-in `control.remote` that copies two frames at once."""

        def sync_frames(
            self, target: object = None, kind: str | None = None, dry_run: bool = False
        ) -> SimpleNamespace:
            """Report two copied frames.

            Returns
            -------
            result : `types.SimpleNamespace`
                A successful sync with no measurements.
            """
            return SimpleNamespace(success=True, transferred=2, measurements=None)

    from backend.services.infrastructure import thread_management

    service = SyncService(observatory_api=SimpleNamespace(remote=_Remote()), review_trigger=asked.append)
    for object_id in ("M 31", "Dark"):
        service.start_sync(object_id)
        thread_management._syncing[object_id].join(timeout=5.0)

    assert asked == [["M 31"], []]


def test_a_finished_stack_job_asks_for_a_re_read() -> None:
    """The stacking job's end asks for the stacked target to be read again."""
    asked: list[list[str]] = []
    service = ImageProcessingService.__new__(ImageProcessingService)
    service._review_trigger = asked.append
    service._lock = threading.Lock()
    future: Future = Future()
    service._jobs = {"job-1": {"future": future}}

    service._rewarm_reviews_when_done("job-1", "M 31")
    assert asked == []
    future.set_result("done")

    assert asked == [["M 31"]]
