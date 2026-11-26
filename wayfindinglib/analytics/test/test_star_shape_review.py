"""Purpose: Test the star-shape verdicts built from stored measurements.

Description: Each verdict of `judge_star_shape` is checked on made-up metric
rows: round stars, elongated stars with a shared direction, elongated stars
with random directions, a standout frame, corner widths reported as not
validated, and no rows at all (a plain "not measured yet", not an error).
The reader `read_star_shape_rows` is checked on a small stand-in library.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.exceptions import InvalidArgumentError
from astrometricslib.reports import observing_night_id
from wayfindinglib.analytics.star_shape_review import (
    PINNED_FRAME_SHAPE_METRICS,
    STACK_ELONGATION_METRIC,
    judge_star_shape,
    read_star_shape_rows,
)
from wayfindinglib.models.session.star_shape_review import FrameStarShape

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


def test_a_frame_far_from_the_typical_elongation_stands_out() -> None:
    """One trailed frame is listed with its value; the others are not."""
    frames = _frames([*_typical(), 2.4])

    review = judge_star_shape("Target", frames, roundness_low_limit=ROUNDNESS_LIMIT)

    assert review.standout.state == "found"
    assert [item.elongation for item in review.standout_frames] == [2.4]
    assert review.standout_frames[0].path == frames[-1].path
    assert review.standout_frames[0].spreads_above_typical > 3.0
    assert [frame.standout for frame in review.series].count(True) == 1


def test_a_frame_below_the_typical_elongation_is_not_a_standout() -> None:
    """Only stretch above the typical value is a candidate to set aside."""
    review = judge_star_shape("Target", _frames([*_typical(), 1.0]), roundness_low_limit=ROUNDNESS_LIMIT)

    assert review.standout.state == "none_found"


def test_too_few_frames_give_no_standouts() -> None:
    """The spread of a handful of frames is not known, so none is flagged."""
    review = judge_star_shape("Target", _frames([1.3, 1.31, 1.29, 2.4]), roundness_low_limit=ROUNDNESS_LIMIT)

    assert review.standout.state == "none_found"


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
    for verdict in (review.roundness, review.direction, review.standout, review.corners):
        assert verdict.state == "not_measured"
        assert "not measured yet" in verdict.sentence


def test_without_a_history_limit_the_stars_are_not_called_round_or_elongated() -> None:
    """With no history limit, the review says it cannot judge."""
    review = judge_star_shape("Target", _frames(_typical()), roundness_low_limit=None)

    assert review.roundness.state == "no_history"
    assert review.direction.state == "no_history"
    assert review.notes


def test_a_stack_with_its_own_numbers_is_preferred_to_the_frames() -> None:
    """The stack's elongation and direction replace the frames' median."""
    stack = FrameStarShape(path="Target/stack", elongation=1.02, angle_degrees=100.0, direction_coherence=0.8)

    review = judge_star_shape("Target", _frames(_typical()), stack, ROUNDNESS_LIMIT)

    assert review.scope == "stack"
    assert review.roundness.state == "round"


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

    def __init__(self, rows: list[Any]) -> None:
        """Keep the rows."""
        self.rows = rows

    def query(self, metric: str, level: str, target_id: str, latest_only: bool = True) -> Any:
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
    ]
    return SimpleNamespace(quality=_Quality(rows))


def test_the_reader_gives_frames_oldest_first_with_the_stored_coherence() -> None:
    """The coherence comes from its own stored row; no stack rows is `None`."""
    frames, stack = read_star_shape_rows(_library(), _Target())

    assert [frame.path for frame in frames] == ["a.fits", "b.fits"]
    assert frames[0].direction_coherence == pytest.approx(0.83)
    assert frames[0].angle_degrees == pytest.approx(40.0)
    assert frames[1].angle_degrees is None
    assert stack is None


def test_the_reader_can_limit_to_a_night_or_a_list_of_frames() -> None:
    """A night or a frame list narrows the frames and leaves the stack out."""
    night_frames, night_stack = read_star_shape_rows(
        _library(), _Target(), night=observing_night_id(_Target().frames[1].timestamp)
    )
    listed_frames, _ = read_star_shape_rows(_library(), _Target(), frame_paths=["a.fits"])

    assert len(night_frames) == 1
    assert night_stack is None
    assert [frame.path for frame in listed_frames] == ["a.fits"]


def test_the_reader_ignores_rows_of_another_definition_version() -> None:
    """A row at a version the code is not pinned to is not used."""
    library = _library()
    for row in library.quality.rows:
        row.definition_version = 99

    frames, _ = read_star_shape_rows(library, _Target())

    assert frames == []
