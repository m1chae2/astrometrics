"""Purpose: Test the star-shape verdicts built from stored measurements.

Description: Each verdict of `judge_star_shape` is checked on made-up metric
rows: round stars, elongated stars with a shared direction, elongated stars
with random directions, frames the quarantine set aside, corner widths
reported as not validated, a stack judged against the stack baseline, and no
rows at all (a plain "not measured yet", not an error). The reader
`read_star_shape_rows` is checked on a small stand-in library.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.arguments import observing_night_id
from astrometricslib.exceptions import InvalidArgumentError
from wayfindinglib.analytics.star_shape_review import (
    PINNED_FRAME_SHAPE_METRICS,
    STACK_ELONGATION_METRIC,
    judge_star_shape,
    read_stack_roundness_low_limit,
    read_star_shape_rows,
    stack_roundness_low_limit,
)
from wayfindinglib.models.session.star_shape_review import FrameStarShape, StandoutFrame

ROUNDNESS_LIMIT = 0.9
"""A made-up history limit: roundness below 0.9 is elongated."""


def _frames(
    elongations: list[float], angle: float | None = 30.0, coherence: float | None = 0.9
) -> list[FrameStarShape]:
    """Build one frame row for each elongation.

    Returns
    -------
    frames : `list` [`FrameStarShape`]
        Rows taken one minute apart.
    """
    return [
        FrameStarShape(
            path=f"/lib/Target/frame_{index:03d}.fits",
            timestamp=1.0e9 + 60.0 * index,
            elongation=value,
            angle_degrees=angle,
            direction_coherence=coherence,
        )
        for index, value in enumerate(elongations)
    ]


def _typical(count: int = 12, center: float = 1.30) -> list[float]:
    """Make slightly different elongations around a center value.

    Returns
    -------
    values : `list` [`float`]
        The elongations.
    """
    return [center + 0.01 * ((index % 5) - 2) for index in range(count)]


def test_round_stars_are_called_round() -> None:
    """Stars within the history limit are round; direction is moot."""
    review = judge_star_shape("Target", _frames(_typical(center=1.05)), roundness_low_limit=ROUNDNESS_LIMIT)

    assert review.roundness.state == "round"
    assert review.roundness.numbers["roundness_low_limit"] == ROUNDNESS_LIMIT
    assert review.direction.state == "not_needed"
    assert review.standout.state == "none_found"


def test_elongated_stars_with_a_shared_direction_point_at_tracking() -> None:
    """High coherence means a shared stretch (tracking and the like)."""
    review = judge_star_shape(
        "Target", _frames(_typical(), coherence=0.9), roundness_low_limit=ROUNDNESS_LIMIT
    )

    assert review.roundness.state == "elongated"
    assert review.direction.state == "shared"
    assert "tracking" in review.direction.sentence
    assert review.direction.numbers["angle_degrees"] == pytest.approx(30.0)


def test_elongated_stars_in_random_directions_point_at_seeing_or_focus() -> None:
    """Low coherence points at seeing or focus, not tracking."""
    review = judge_star_shape(
        "Target", _frames(_typical(), coherence=0.1), roundness_low_limit=ROUNDNESS_LIMIT
    )

    assert review.roundness.state == "elongated"
    assert review.direction.state == "mixed"
    assert "seeing or focus" in review.direction.sentence


def test_the_frames_the_quarantine_set_aside_are_listed_as_they_are() -> None:
    """The quarantine's trailed frames are listed and not judged again."""
    frames = _frames([*_typical(), 2.4])
    set_aside = [StandoutFrame(path=frames[-1].path, elongation=2.4, reason="stars stretched 2.40")]

    review = judge_star_shape("Target", frames, roundness_low_limit=ROUNDNESS_LIMIT, set_aside=set_aside)

    assert review.standout.state == "found"
    assert review.standout_frames == set_aside
    assert [frame.standout for frame in review.series].count(True) == 1


def test_a_frame_far_from_the_others_is_not_flagged_by_the_review_itself() -> None:
    """With nothing set aside, an odd frame is not flagged here."""
    review = judge_star_shape("Target", _frames([*_typical(), 2.4]), roundness_low_limit=ROUNDNESS_LIMIT)

    assert review.standout.state == "none_found"
    assert review.standout_frames == []


def test_the_review_gives_the_frames_typical_elongation() -> None:
    """The typical elongation is the frames' median, for the screen to use."""
    review = judge_star_shape("Target", _frames([1.1, 1.3, 1.2, 2.0]), roundness_low_limit=ROUNDNESS_LIMIT)

    assert review.typical_elongation == pytest.approx(1.25)


def test_corner_widths_are_reported_but_not_validated() -> None:
    """The corner over center ratio is given, with no tilt verdict."""
    frames = [
        frame.model_copy(
            update={
                "center_width_px": 3.0,
                "corner_widths_px": {
                    "xlow_ylow": 3.3,
                    "xhigh_ylow": 3.6,
                    "xlow_yhigh": 3.9,
                    "xhigh_yhigh": 3.6,
                },
            }
        )
        for frame in _frames(_typical())
    ]

    review = judge_star_shape("Target", frames, roundness_low_limit=ROUNDNESS_LIMIT)

    assert review.corners.state == "not_validated"
    assert review.corners.numbers["corner_over_center"] == pytest.approx(1.2)
    assert "not yet validated (AST-18)" in review.corners.sentence
    assert "tilt" in review.corners.sentence


def test_no_rows_give_a_not_measured_verdict_not_an_error() -> None:
    """No stored star shapes give plain "not measured yet" verdicts."""
    review = judge_star_shape("Target", [], roundness_low_limit=ROUNDNESS_LIMIT)

    assert review.frame_count == 0
    assert review.series == []
    assert review.typical_elongation is None
    for verdict in (review.roundness, review.direction, review.corners):
        assert verdict.state == "not_measured"
        assert "not measured yet" in verdict.sentence
    assert review.standout.state == "none_found"


def test_without_a_history_limit_the_stars_are_not_called_round_or_elongated() -> None:
    """With no history limit, the review says it cannot judge."""
    review = judge_star_shape("Target", _frames(_typical()), roundness_low_limit=None)

    assert review.roundness.state == "no_history"
    assert review.direction.state == "no_history"
    assert review.notes


def test_a_stack_with_its_own_numbers_is_judged_against_the_stack_baseline() -> None:
    """The stack's elongation is judged against other stacks, not frames."""
    stack = FrameStarShape(path="Target/stack", elongation=1.08, angle_degrees=100.0, direction_coherence=0.8)

    review = judge_star_shape(
        "Target", _frames(_typical()), stack, roundness_low_limit=0.5, stack_roundness_low_limit=0.95
    )

    assert review.scope == "stack"
    assert review.roundness.state == "elongated"
    assert review.roundness.numbers["roundness_low_limit"] == pytest.approx(0.95)
    assert "your stacks" in review.roundness.sentence
    assert review.typical_elongation == pytest.approx(1.30, abs=0.01)


def test_a_stack_without_a_stack_baseline_is_not_judged() -> None:
    """Without enough other stacks, a stack is neither round nor elongated."""
    stack = FrameStarShape(path="Target/stack", elongation=1.02)

    review = judge_star_shape("Target", _frames(_typical()), stack, ROUNDNESS_LIMIT)

    assert review.roundness.state == "no_history"
    assert any("stacks" in note for note in review.notes)


def test_the_stack_baseline_follows_the_history_rule() -> None:
    """The limit is the median roundness times exp(-3 x the log spread)."""
    assert stack_roundness_low_limit([1.05, 1.1]) is None
    elongations = [1.02, 1.04, 1.05, 1.06, 1.08]
    limit = stack_roundness_low_limit(elongations)
    assert limit is not None
    assert limit < 1.0 / 1.05
    assert limit > 0.8


def test_a_stack_without_numbers_falls_back_to_the_frames() -> None:
    """A stack with no stored star shape leaves the frames in charge."""
    stack = FrameStarShape(path="Target/stack")

    review = judge_star_shape("Target", _frames(_typical()), stack, ROUNDNESS_LIMIT)

    assert review.scope == "frames"
    assert review.roundness.state == "elongated"


def test_frame_angles_are_averaged_as_axes() -> None:
    """Angles of 5 and 175 degrees are one direction, not 90."""
    frames = _frames(_typical(), angle=5.0)
    frames[::2] = [frame.model_copy(update={"angle_degrees": 175.0}) for frame in frames[::2]]

    review = judge_star_shape("Target", frames, roundness_low_limit=ROUNDNESS_LIMIT)

    angle = review.direction.numbers["angle_degrees"]
    assert angle is not None
    assert min(angle, 180.0 - angle) < 6.0


def test_the_pinned_versions_match_the_registered_metrics() -> None:
    """The registry has not moved past a version this review is pinned to."""
    from astrometricslib.pipelines.shared.quality.metric_registry import get_metric_registry

    registry = get_metric_registry()
    for metric, version in PINNED_FRAME_SHAPE_METRICS.items():
        assert registry.get(metric).definition_version == version, metric


def _row(
    metric: str, subject: str, value: float, level: str = "frame", reason: str = "", version: int = 1
) -> Any:
    """Build a stored row as `astrometrics.quality.query` returns it.

    Returns
    -------
    row : `types.SimpleNamespace`
        A measured row.
    """
    return SimpleNamespace(
        metric=metric,
        definition_version=version,
        subject=SimpleNamespace(subject_id=subject, channel=None, variant=None, level=level),
        status="measured",
        value=value,
        reason=reason,
        row_id=hash((metric, subject)) % 1000,
        measured_at=datetime(2026, 11, 1, tzinfo=UTC),
    )


class _Quality:
    """A stand-in for `astrometrics.quality` that answers from rows."""

    def __init__(self, rows: list[Any], set_aside: list[Any] | None = None) -> None:
        """Keep the rows and the set-aside frames."""
        self.rows = rows
        self.set_aside = set_aside or []

    def set_aside_frames(self, target: Any) -> list[Any]:
        """Answer with the set-aside frames.

        Returns
        -------
        frames : `list`
            The stand-in records.
        """
        return list(self.set_aside)

    def query(self, metric: str, level: str, target_id: str | None = None, latest_only: bool = True) -> Any:
        """Answer with the rows of one metric, or refuse an unknown one.

        Returns
        -------
        result : `types.SimpleNamespace`
            Has ``rows``.

        Raises
        ------
        InvalidArgumentError
            If a stack metric has no rows, as an unregistered one would.
        """
        if metric == STACK_ELONGATION_METRIC and not any(row.metric == metric for row in self.rows):
            raise InvalidArgumentError("not registered")
        return SimpleNamespace(rows=[row for row in self.rows if row.metric == metric])


class _Target:
    """A stand-in target with two light frames and one dark."""

    id = "Target"

    def __init__(self) -> None:
        """Build the frames."""
        night_one = datetime(2026, 11, 1, 6, tzinfo=UTC).timestamp()
        night_two = datetime(2026, 11, 3, 6, tzinfo=UTC).timestamp()
        self.frames = [
            SimpleNamespace(path="a.fits", role="LIGHT", timestamp=night_one),
            SimpleNamespace(path="b.fits", role="LIGHT", timestamp=night_two),
            SimpleNamespace(path="dark.fits", role="DARK", timestamp=night_one),
        ]

    def frame_subject_id(self, frame: Any) -> str:
        """Name a frame the way the stored rows do.

        Returns
        -------
        subject_id : `str`
            The target id and the file name.
        """
        return f"Target/{frame.path}"


def _library() -> SimpleNamespace:
    """Build a science-library stand-in with rows for both light frames.

    Returns
    -------
    astrometrics : `types.SimpleNamespace`
        Has ``quality``.
    """
    rows = [
        _row("frame.star_elongation", "Target/a.fits", 1.4),
        _row("frame.star_elongation", "Target/b.fits", 1.2),
        _row("frame.star_elongation_angle_deg", "Target/a.fits", 40.0, version=2),
        _row("frame.star_elongation_coherence", "Target/a.fits", 0.83),
        _row("frame.star_elongation", "Target/c.fits", 2.1),
    ]
    set_aside = [
        SimpleNamespace(
            kind="trailed",
            path="_excluded/c.fits",
            original_path="c.fits",
            subject_id="Target/c.fits",
            date="2026-11-03 06:30:00",
            reason="stars stretched 2.10",
        ),
        SimpleNamespace(
            kind="clouded",
            path="_excluded/d.fits",
            original_path="d.fits",
            subject_id="Target/d.fits",
            date="2026-11-03 06:40:00",
            reason="few stars",
        ),
    ]
    quality = _Quality(rows, set_aside)
    return SimpleNamespace(
        quality=quality,
        targets=SimpleNamespace(frames=SimpleNamespace(get_set_aside=quality.set_aside_frames)),
    )


def test_the_reader_gives_frames_oldest_first_with_the_stored_coherence() -> None:
    """The coherence comes from its own stored row; no stack rows is `None`."""
    frames, stack, _ = read_star_shape_rows(_library(), _Target())

    assert [frame.path for frame in frames] == ["a.fits", "b.fits"]
    assert frames[0].direction_coherence == pytest.approx(0.83)
    assert frames[0].angle_degrees == pytest.approx(40.0)
    assert frames[1].angle_degrees is None
    assert stack is None


def test_the_reader_can_limit_to_a_night_or_a_list_of_frames() -> None:
    """A night or a frame list narrows the frames and leaves the stack out."""
    night_frames, night_stack, night_set_aside = read_star_shape_rows(
        _library(), _Target(), night=observing_night_id(_Target().frames[1].timestamp)
    )
    listed_frames, _, listed_set_aside = read_star_shape_rows(_library(), _Target(), frame_paths=["a.fits"])
    first_night = observing_night_id(_Target().frames[0].timestamp)
    _, _, other_night_set_aside = read_star_shape_rows(_library(), _Target(), night=first_night)

    assert len(night_frames) == 1
    assert night_stack is None
    assert [item.path for item in night_set_aside] == ["c.fits"]
    assert [frame.path for frame in listed_frames] == ["a.fits"]
    assert listed_set_aside == []
    assert other_night_set_aside == []


def test_the_reader_lists_only_the_frames_set_aside_as_trailed() -> None:
    """Clouded frames are left out; a trailed one keeps its elongation."""
    _, _, set_aside = read_star_shape_rows(_library(), _Target())

    assert [item.path for item in set_aside] == ["c.fits"]
    assert set_aside[0].elongation == pytest.approx(2.1)
    assert set_aside[0].reason == "stars stretched 2.10"
    assert set_aside[0].timestamp == datetime(2026, 11, 3, 6, 30, tzinfo=UTC).timestamp()


def test_the_stack_baseline_leaves_out_the_stack_being_judged() -> None:
    """The library's other stacks set the baseline; too few give none."""
    rows = [
        _row(STACK_ELONGATION_METRIC, f"T{index}/stack.fits", 1.02 + 0.01 * index, level="stack")
        for index in range(5)
    ]
    library = SimpleNamespace(quality=_Quality(rows))

    assert read_stack_roundness_low_limit(library) is not None
    assert read_stack_roundness_low_limit(library, exclude_subject="T0/stack.fits") is None


def test_the_reader_ignores_rows_of_another_definition_version() -> None:
    """A row at a version the code is not pinned to is not used."""
    library = _library()
    for row in library.quality.rows:
        row.definition_version = 99

    frames, _, _ = read_star_shape_rows(library, _Target())

    assert frames == []
