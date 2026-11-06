"""Purpose: Tests that the night review is kept until its data change.

Description: Asks the review for a night through the public front door, with
a made-up science library, and counts how often the night is really built.
A second request with nothing changed must not build again. A new guiding
sample, a write to the science database, a new guiding run or an equipment
change must. `invalidate_night` forgets a night. The guiding baselines that
the envelope uses are built once and shared by every night.
"""

import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from astrometricslib.drivers import VersionedCache
from astrometricslib.foundation.storage.data_versions import bump_data_version
from wayfindinglib import ObservatoryControl
from wayfindinglib.analytics.test.night_quality_helpers import (
    REVIEWED,
    STAR,
    FakeLibrary,
    make_rows,
    make_target,
)
from wayfindinglib.api.review import ObservationReview
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.tasks.control_tasks import night_analysis, night_history, night_quality_task


@pytest.fixture(autouse=True)
def _no_guiding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the guiding analysis, which has its own tests, by no data."""
    monkeypatch.setattr(night_analysis, "guiding_night_analysis", lambda context, night_id, **_: None)
    monkeypatch.setattr(night_analysis, "capture_library", lambda *args, **kwargs: [])


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count the real builds of a night review and of the nights list.

    Returns
    -------
    counts : `dict` [`str`, `int`]
        ``night_quality`` and ``nights`` build counts.
    """
    counts = {"night_quality": 0, "nights": 0}
    real_review, real_list = night_quality_task.review_night_quality, night_history._build_night_list

    def counted_review(*args: Any, **kwargs: Any) -> Any:
        """Count, then review the night.

        Returns
        -------
        quality : `NightQuality`
            The review.
        """
        counts["night_quality"] += 1
        return real_review(*args, **kwargs)

    def counted_list(*args: Any, **kwargs: Any) -> Any:
        """Count, then list the nights.

        Returns
        -------
        nights : `list`
            The listed nights.
        """
        counts["nights"] += 1
        return real_list(*args, **kwargs)

    monkeypatch.setattr(night_quality_task, "review_night_quality", counted_review)
    monkeypatch.setattr(night_history, "_build_night_list", counted_list)
    return counts


@pytest.fixture
def review(control: ObservatoryControl) -> ObservationReview:
    """Give a review over a made-up library with no science database yet.

    Returns
    -------
    review : `ObservationReview`
        The review. The library's write counters read as all zero, so a data
        version can be worked out.
    """
    target = make_target(STAR, 5)
    control._context._astrometrics = FakeLibrary([target], make_rows(target, REVIEWED))
    return ObservationReview(control)


def _ask(review: ObservationReview) -> Any:
    """Ask for the review of the reviewed night.

    Returns
    -------
    quality : `NightQuality`
        The review.
    """
    return review.query(kind="night_quality", session_id=REVIEWED, register_job=False).night_quality


def _science_database(review: ObservationReview) -> Path:
    """Give the path of the test library's science database file.

    Returns
    -------
    path : `Path`
        The path.
    """
    return Path(str(review._context.config.get_library_path())) / "astrometrics.db"


def test_a_second_request_with_unchanged_data_does_not_build(
    review: ObservationReview, builds: dict[str, int]
) -> None:
    """The night is built once and served from memory the second time."""
    first = _ask(review)
    second = _ask(review)

    assert builds["night_quality"] == 1
    assert first == second
    assert first is not second


def test_a_new_guiding_sample_builds_again(review: ObservationReview, builds: dict[str, int]) -> None:
    """A guiding sample added to the records changes the version."""
    _ask(review)
    review._context.records.record_guiding_samples([
        {"session_id": REVIEWED, "timestamp": 1.0, "dra": 0.1, "ddec": 0.1, "source": "phd2_log"}
    ])
    _ask(review)

    assert builds["night_quality"] == 2


def test_a_write_to_the_science_database_builds_again(
    review: ObservationReview, builds: dict[str, int]
) -> None:
    """A new frame or measurement counts a write in the science database."""
    _ask(review)
    connection = sqlite3.connect(_science_database(review))
    bump_data_version(connection, "quality")
    connection.commit()
    connection.close()
    _ask(review)

    assert builds["night_quality"] == 2


def test_a_new_guiding_run_builds_again(review: ObservationReview, builds: dict[str, int]) -> None:
    """A guiding run stored in the butler changes the version."""
    _ask(review)
    review._context.butler.put(
        GuidingRunSummary(
            id="guide_log-new.txt#0",
            session_id=REVIEWED,
            source_file_name="guide_log-new.txt",
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
        {"id": "guide_log-new.txt#0"},
    )
    _ask(review)

    assert builds["night_quality"] == 2


def test_a_change_of_active_equipment_builds_again(
    review: ObservationReview, builds: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """New active equipment is new data, because the review judges for it."""
    _ask(review)
    monkeypatch.setattr("wayfindinglib.analytics.data_versions.equipment_signs", lambda config: ("other",))
    _ask(review)

    assert builds["night_quality"] == 2


def test_invalidate_night_forgets_that_night(review: ObservationReview, builds: dict[str, int]) -> None:
    """After `invalidate_night` the night is built again, others are kept."""
    _ask(review)
    review.query(kind="nights", register_job=False)
    other = review._context.review_cache
    other.get_or_build((night_quality_task.NIGHT_QUALITY_CACHE_KEY, "2020-01-01"), "v", lambda: "kept")

    forgotten = review.invalidate_night(REVIEWED)
    _ask(review)

    assert forgotten == 2  # the night and the list of nights
    assert builds["night_quality"] == 2
    assert (
        other.get_or_build((night_quality_task.NIGHT_QUALITY_CACHE_KEY, "2020-01-01"), "v", lambda: "x")
        == "kept"
    )


def test_invalidate_without_a_night_forgets_everything(
    review: ObservationReview, builds: dict[str, int]
) -> None:
    """With no night, every kept result goes."""
    _ask(review)
    review.invalidate_night()
    _ask(review)

    assert builds["night_quality"] == 2


def test_the_list_of_nights_is_kept_too(review: ObservationReview, builds: dict[str, int]) -> None:
    """The nights list is built once while the data are unchanged."""
    review.query(kind="nights", register_job=False)
    review.query(kind="nights", register_job=False)

    assert builds["nights"] == 1


def test_threads_asking_for_the_same_night_build_it_once(
    review: ObservationReview, builds: dict[str, int]
) -> None:
    """Concurrent first requests share one build."""
    answers: list[Any] = []
    threads = [threading.Thread(target=lambda: answers.append(_ask(review))) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert builds["night_quality"] == 1
    assert len(answers) == 6


def test_a_passing_database_lock_still_builds_the_night_once(
    review: ObservationReview, builds: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A version read that fails while the database is busy is tried again."""
    real_versions = type(review._context.records).data_versions
    seen = threading.local()

    def busy_then_fine(self: Any, *args: Any, **kwargs: Any) -> Any:
        """Fail like a locked database on a thread's first read.

        Returns
        -------
        versions : `dict` or `None`
            `None` on a thread's first read, then the real versions.
        """
        if not getattr(seen, "failed", False):
            seen.failed = True
            return None
        return real_versions(self, *args, **kwargs)

    monkeypatch.setattr(type(review._context.records), "data_versions", busy_then_fine)
    answers: list[Any] = []
    threads = [threading.Thread(target=lambda: answers.append(_ask(review))) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(answers) == 4
    assert builds["night_quality"] == 1


def test_a_version_that_stays_unknown_is_not_kept(
    review: ObservationReview, builds: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuine failure still builds each time and keeps nothing."""
    monkeypatch.setattr(type(review._context.records), "data_versions", lambda self, *a, **k: None)
    _ask(review)
    _ask(review)

    assert builds["night_quality"] == 2


def test_a_night_with_no_data_is_not_kept(review: ObservationReview, builds: dict[str, int]) -> None:
    """A night that cannot be reviewed raises each time and leaves nothing."""
    from astrometricslib.exceptions import NotFoundError

    for _ in range(2):
        with pytest.raises(NotFoundError):
            review.query(kind="night_quality", session_id="1999-01-01", register_job=False)

    assert builds["night_quality"] == 2


def test_the_guiding_baselines_are_built_once_for_every_night(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Judging two different nights reads the earlier nights' guiding once."""
    cache = VersionedCache()
    context = type("Context", (), {"review_cache": cache, "records": object()})()
    calls: list[str] = []
    monkeypatch.setattr(night_analysis, "review_data_version", lambda context: "v1")
    monkeypatch.setattr(
        night_analysis,
        "collect_night_baselines",
        lambda records, contexts, fingerprint, runs: calls.append(fingerprint) or [],
    )
    provide = night_analysis._night_baseline_provider(context, [], [])

    provide("rig")
    night_analysis._night_baseline_provider(context, [], [])("rig")  # another night, same data
    assert calls == ["rig"]

    monkeypatch.setattr(night_analysis, "review_data_version", lambda context: "v2")
    provide("rig")
    assert calls == ["rig", "rig"]
