"""Purpose: Tests for the depth goal status and the chosen level's storage.

Description: Builds the status for a target with made-up stack rows and a
stand-in report: below the goal with a time, at the goal, more frames will not
help, no depth kind, no stack, no depth, and a hidden or provisional time. A
round trip through a real isolated butler checks the stored level and the
default. Made-up numbers only.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import (
    AppConfiguration,
    CaptureSetting,
    InvalidArgumentError,
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from wayfindinglib.analytics.result_quality.fixed_pattern_location import FixedPatternCause
from wayfindinglib.analytics.test.frame_counting_helpers import FakeLibrary
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.planning.depth_goal import DepthGoalState, DepthLevel
from wayfindinglib.models.planning.target_report import RecommendedAdditionalFramesStatus
from wayfindinglib.models.quality.statements import (
    ActionClass,
    Confidence,
    ExpectedEffect,
    ProposedAction,
    Severity,
    StatementKind,
    TargetStatement,
)
from wayfindinglib.tasks.planning_tasks import depth_goal_tasks as tasks

SURFACE_METRIC = "stacking.surface_brightness_limit_3sigma_10arcsec_mag_per_arcsec2"
POINT_METRIC = "stacking.point_source_depth_5sigma_mag"
NOISE_METRIC = "stacking.empty_aperture_noise_normalized"
NOW = datetime(2026, 10, 27, 20, 0, tzinfo=UTC)


def row(
    metric: str,
    value: float | None,
    *,
    target: str = "NGC 7331",
    version: int = 1,
    subject_id: str | None = None,
    reason: str = "",
) -> QualityMeasurement:
    """Build one stack row.

    Parameters
    ----------
    metric : `str`
        The metric.
    value : `float` or `None`
        The value, or `None` for a row that was not measured.
    target : `str`, optional
        The target.
    version : `int`, optional
        The definition version.
    subject_id : `str`, optional
        The stack's subject id.
    reason : `str`, optional
        Why the row has no value.

    Returns
    -------
    row : `QualityMeasurement`
        The row.
    """
    return QualityMeasurement(
        metric=metric,
        definition_version=version,
        subject=QualitySubject(
            level=SubjectLevel.STACK, subject_id=subject_id or f"{target}/stack", target_id=target
        ),
        status=MeasurementStatus.MEASURED if value is not None else MeasurementStatus.NOT_MEASURED,
        value=value,
        reason=reason,
        origin=MeasurementOrigin.MEASURED,
        run_id="run",
        measured_at=NOW,
        row_id=1,
    )


def statement(
    rule: str, kind: StatementKind, *, seconds: float | None = None, frames: int = 30, values: Any = None
) -> TargetStatement:
    """Build a statement of the target report.

    Parameters
    ----------
    rule : `str`
        The rule name.
    kind : `StatementKind`
        The kind.
    seconds : `float`, optional
        The wall-clock time of a recommendation.
    frames : `int`, optional
        The frames of a recommendation.
    values : `dict`, optional
        The statement's values.

    Returns
    -------
    statement : `TargetStatement`
        The statement.
    """
    if kind == StatementKind.RECOMMENDATION:
        return TargetStatement(
            rule=rule,
            kind=kind,
            action_class=ActionClass.CAPTURE_SETTINGS,
            capture_setting=CaptureSetting(camera="Cam", telescope="Scope", filter_name="L"),
            message="Take more.",
            proposed_action=ProposedAction(exposure_seconds=120.0, frame_count=frames),
            expected_effect=ExpectedEffect(description="Less noise.", wall_clock_seconds=seconds),
            confidence=Confidence.DESIGN_ESTIMATE,
            values=values or {},
        )
    if kind == StatementKind.WARNING:
        return TargetStatement(
            rule=rule,
            kind=kind,
            severity=Severity.ADVICE,
            action_class=ActionClass.CAPTURE_SETTINGS,
            message="A warning.",
            confidence=Confidence.DESIGN_ESTIMATE,
            values=values or {},
        )
    return TargetStatement(
        rule=rule,
        kind=kind,
        message="Missing.",
        reason="No forecast yet.",
        confidence=Confidence.DESIGN_ESTIMATE,
    )


class Recorder:
    """A report builder that records the noise ratio it was asked for."""

    def __init__(self, statements: list[TargetStatement]) -> None:
        """Hold the statements of the report to give.

        Parameters
        ----------
        statements : `list` [`TargetStatement`]
            The statements.
        """
        self.statements = statements
        self.ratios: list[float] = []

    def __call__(self, ratio: float) -> Any:
        """Give the report and note the ratio.

        Parameters
        ----------
        ratio : `float`
            The noise ratio asked for.

        Returns
        -------
        report : `SimpleNamespace`
            Statements and no forecast checks.
        """
        self.ratios.append(ratio)
        return SimpleNamespace(statements=self.statements, forecast_checks=[])


@pytest.fixture
def butler(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> DiskButler:
    """Build a DiskButler backed by an isolated temporary database.

    Returns
    -------
    butler : `DiskButler`
        The butler.
    """
    config_path = tmp_path / "astrometrics.config.toml"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return DiskButler(app_config=config)


@pytest.fixture
def forecast_status(monkeypatch: pytest.MonkeyPatch) -> dict[str, RecommendedAdditionalFramesStatus]:
    """Let a test choose the forecast status the report's checks give.

    Returns
    -------
    holder : `dict`
        Set ``holder["status"]``; it starts as confirmed.
    """
    holder = {"status": RecommendedAdditionalFramesStatus.CONFIRMED}
    monkeypatch.setattr(
        tasks, "recommended_additional_frames_status_from_checks", lambda _checks: holder["status"]
    )
    return holder


STORED_TYPES = {"NGC 7331": "LIN", "M 13": "GlC", "Vega": "*"}
"""The SIMBAD types the fake library stores for the targets used here."""


def status_of(
    butler: DiskButler,
    rows: list[QualityMeasurement],
    recorder: Recorder,
    target: str = "NGC 7331",
    object_type: str | None = None,
) -> Any:
    """Build the status of a target.

    Parameters
    ----------
    butler : `DiskButler`
        The storage.
    rows : `list` [`QualityMeasurement`]
        The stored rows.
    recorder : `Recorder`
        The report builder.
    target : `str`, optional
        The target.
    object_type : `str`, optional
        The target's stored SIMBAD type. Defaults to the one in `STORED_TYPES`,
        or none for a target not listed there.

    Returns
    -------
    status : `DepthGoalStatus`
        The status.
    """
    stored = {target: object_type or STORED_TYPES[target]} if (object_type or target in STORED_TYPES) else {}
    return tasks.build_depth_goal_status(
        target,
        astrometrics=FakeLibrary({target: []}, rows, stored),
        butler=butler,
        report_for_noise_ratio=recorder,
    )


def test_below_goal_gives_the_forecast_time(butler: DiskButler, forecast_status: dict) -> None:
    """A quick-look image of a galaxy needs time to reach Good."""
    recorder = Recorder([statement("more_frames", StatementKind.RECOMMENDATION, seconds=4800.0, frames=40)])
    result = status_of(butler, [row(SURFACE_METRIC, 24.2)], recorder)
    assert result.state == DepthGoalState.BELOW_GOAL
    assert result.reached_level == DepthLevel.QUICK_LOOK
    assert result.level == DepthLevel.GOOD
    assert result.seconds_to_goal == pytest.approx(4800.0) and result.frames_to_goal == 40
    assert result.message == "About 1 h 20 min more to reach this depth."
    assert not result.provisional
    # The forecast is aimed at the noise ratio of the missing 0.5 magnitude.
    assert recorder.ratios == [pytest.approx(10 ** (-0.4 * 0.5))]


def test_a_provisional_forecast_is_marked(butler: DiskButler, forecast_status: dict) -> None:
    """A provisional count says so."""
    forecast_status["status"] = RecommendedAdditionalFramesStatus.PROVISIONAL
    recorder = Recorder([statement("more_frames", StatementKind.RECOMMENDATION, seconds=4800.0)])
    result = status_of(butler, [row(SURFACE_METRIC, 24.2)], recorder)
    assert result.provisional
    assert result.message.endswith("to reach this depth (provisional).")


def test_an_unchecked_forecast_hides_the_time(butler: DiskButler, forecast_status: dict) -> None:
    """With no checked forecast no time is shown."""
    forecast_status["status"] = RecommendedAdditionalFramesStatus.NOT_CHECKED
    recorder = Recorder([statement("more_frames", StatementKind.INSUFFICIENT_DATA)])
    result = status_of(butler, [row(SURFACE_METRIC, 24.2)], recorder)
    assert result.seconds_to_goal is None
    assert result.message.startswith("The time needed is not shown yet")


def test_an_image_below_every_level_names_no_level(butler: DiskButler, forecast_status: dict) -> None:
    """An image below the shallowest level gives the time and no level name."""
    recorder = Recorder([statement("more_frames", StatementKind.RECOMMENDATION, seconds=3600.0)])
    result = status_of(butler, [row(SURFACE_METRIC, 22.0)], recorder)
    assert result.reached_level is None
    assert result.message == "About 1 h more to reach this depth."


def test_at_the_goal_needs_no_forecast(butler: DiskButler) -> None:
    """A depth that meets the level is done, and no report is built."""
    recorder = Recorder([])
    result = status_of(butler, [row(SURFACE_METRIC, 24.7)], recorder)
    assert result.state == DepthGoalState.REACHED
    assert result.message == "Your image has reached this depth."
    assert recorder.ratios == []


def test_a_deeper_image_reports_its_own_level(butler: DiskButler) -> None:
    """A deep image of a Good goal is reached; its own level is kept."""
    result = status_of(butler, [row(SURFACE_METRIC, 25.6)], Recorder([]))
    assert result.state == DepthGoalState.REACHED
    assert result.message == "Your image has reached this depth."
    assert result.reached_level == DepthLevel.DEEP


def test_more_frames_will_not_help_names_what_to_fix(butler: DiskButler, forecast_status: dict) -> None:
    """A stop rule and a flats rule give the plain advice."""
    recorder = Recorder([
        statement("more_frames_will_not_help", StatementKind.WARNING),
        statement("retake_flats", StatementKind.WARNING),
    ])
    result = status_of(butler, [row(SURFACE_METRIC, 24.2)], recorder)
    assert result.state == DepthGoalState.WILL_NOT_HELP
    assert result.message == (
        "More frames won't help much here. The noise has a fixed part we can't place yet. Retake flats first."
    )
    assert result.seconds_to_goal is None


@pytest.mark.parametrize(
    ("cause", "sentence"),
    [
        (FixedPatternCause.FLATS, "The flats no longer match the optics; new flats should help."),
        (
            FixedPatternCause.NEAR_TARGET,
            "Faint light from the object itself fills the sky around it, "
            "so the noise there won't average away.",
        ),
        (FixedPatternCause.BANDING, "The frames carry a fixed stripe pattern."),
        (
            FixedPatternCause.GRADIENT,
            "The sky brightness has a slope across the frame that stays in every frame.",
        ),
        (
            FixedPatternCause.CALIBRATION_MASTERS,
            "Noise in the calibration frames is the same in every frame.",
        ),
        (
            FixedPatternCause.SKY_STRUCTURE,
            "The sky has fixed structure, likely faint light or a slope in the background.",
        ),
        (FixedPatternCause.UNPLACED, "The noise has a fixed part we can't place yet."),
    ],
)
def test_each_cause_gives_its_one_sentence(
    butler: DiskButler, forecast_status: dict, cause: FixedPatternCause, sentence: str
) -> None:
    """The cause code picks the sentence, the same every time."""
    statements = [
        statement(
            "more_frames_will_not_help", StatementKind.WARNING, values={"fixed_pattern_cause": cause.value}
        )
    ]
    first = status_of(butler, [row(SURFACE_METRIC, 24.2)], Recorder(statements))
    second = status_of(butler, [row(SURFACE_METRIC, 24.2)], Recorder(statements))
    assert first.message == f"More frames won't help much here. {sentence}"
    assert first.message == second.message


def test_the_practical_limit_statement_also_carries_the_cause(
    butler: DiskButler, forecast_status: dict
) -> None:
    """With no stop statement, the "take more" statement gives the cause."""
    recorder = Recorder([
        statement(
            "more_frames",
            StatementKind.RECOMMENDATION,
            seconds=900.0,
            values={"practical_limit_applied": 1.0, "fixed_pattern_cause": "banding"},
        )
    ])
    result = status_of(butler, [row(SURFACE_METRIC, 24.2)], recorder)
    assert result.message.endswith("The frames carry a fixed stripe pattern.")


def test_the_practical_limit_also_means_frames_will_not_help(
    butler: DiskButler, forecast_status: dict
) -> None:
    """A recommendation that fell back to one doubling is no way there."""
    recorder = Recorder([
        statement(
            "more_frames",
            StatementKind.RECOMMENDATION,
            seconds=900.0,
            values={"practical_limit_applied": 1.0},
        )
    ])
    result = status_of(butler, [row(SURFACE_METRIC, 24.2)], recorder)
    assert result.state == DepthGoalState.WILL_NOT_HELP


def test_a_stop_signal_that_could_not_be_read_is_not_a_stop(
    butler: DiskButler, forecast_status: dict
) -> None:
    """Missing data under the stop rule's name is not a stop."""
    recorder = Recorder([
        statement("more_frames_will_not_help", StatementKind.INSUFFICIENT_DATA),
        statement("more_frames", StatementKind.RECOMMENDATION, seconds=1800.0),
    ])
    result = status_of(butler, [row(SURFACE_METRIC, 24.2)], recorder)
    assert result.state == DepthGoalState.BELOW_GOAL
    assert result.seconds_to_goal == pytest.approx(1800.0)


def test_a_cluster_uses_the_point_source_depth(butler: DiskButler, forecast_status: dict) -> None:
    """M 13 is judged by the 5 sigma point-source depth."""
    rows = [row(POINT_METRIC, 19.0, target="M 13"), row(SURFACE_METRIC, 30.0, target="M 13")]
    recorder = Recorder(
        [statement("more_frames", StatementKind.RECOMMENDATION, seconds=1200.0)],
    )
    result = status_of(butler, rows, recorder, target="M 13")
    assert result.depth_kind.value == "point_source" and result.depth_unit == "mag"
    assert result.goal_depth == pytest.approx(19.3)
    assert result.current_depth == pytest.approx(19.0)


def test_a_star_has_no_depth_goal(butler: DiskButler) -> None:
    """A star gets no goal and says why."""
    result = status_of(butler, [], Recorder([]), target="Vega")
    assert result.state == DepthGoalState.NO_DEPTH_GOAL
    assert result.depth_kind is None and result.reason
    assert [option.goal_depth for option in result.options] == [None, None, None]


def test_an_unknown_type_uses_the_point_source_depth_and_says_so(butler: DiskButler) -> None:
    """A target with no stored type is judged by the point-source depth."""
    rows = [row(POINT_METRIC, 19.0, target="Mystery Field")]
    result = status_of(butler, rows, Recorder([]), target="Mystery Field")
    assert result.depth_kind.value == "point_source"
    assert "object type is not known" in result.reason
    assert result.current_depth == pytest.approx(19.0)


def test_no_stack_and_no_depth(butler: DiskButler) -> None:
    """Not stacked, and stacked without a depth, read differently."""
    not_stacked = status_of(butler, [], Recorder([]))
    assert not_stacked.state == DepthGoalState.NOT_STACKED
    assert not_stacked.message == "Stack the frames to see the depth."
    no_depth = status_of(butler, [row(NOISE_METRIC, 3e-4, version=3)], Recorder([]))
    assert no_depth.state == DepthGoalState.DEPTH_NOT_MEASURED
    old_version = status_of(
        butler, [row(NOISE_METRIC, 3e-4, version=3), row(SURFACE_METRIC, 24.9, version=2)], Recorder([])
    )
    assert old_version.state == DepthGoalState.DEPTH_NOT_MEASURED
    assert "older definition" in old_version.reason
    not_measured = status_of(butler, [row(SURFACE_METRIC, None, reason="No zero point.")], Recorder([]))
    assert not_measured.reason == "No zero point."


def test_a_group_stack_depth_is_not_the_library_depth(butler: DiskButler) -> None:
    """Only the library stack's row counts."""
    rows = [row(SURFACE_METRIC, 25.9, subject_id="NGC 7331/groups/g1")]
    assert status_of(butler, rows, Recorder([])).state == DepthGoalState.NOT_STACKED


def test_the_chosen_level_round_trips_and_defaults_to_good(butler: DiskButler) -> None:
    """A stored level is read back; an unset target is Good."""
    assert tasks.read_chosen_level(butler, "NGC 7331") == DepthLevel.GOOD
    tasks.store_chosen_level(butler, "NGC 7331", DepthLevel.DEEP)
    assert tasks.read_chosen_level(butler, "NGC 7331") == DepthLevel.DEEP
    assert tasks.read_chosen_level(butler, "M 81") == DepthLevel.GOOD
    tasks.store_chosen_level(butler, "NGC 7331", DepthLevel.QUICK_LOOK)
    assert tasks.read_chosen_level(butler, "NGC 7331") == DepthLevel.QUICK_LOOK
    result = status_of(butler, [row(SURFACE_METRIC, 24.1)], Recorder([]))
    assert result.state == DepthGoalState.REACHED and result.level == DepthLevel.QUICK_LOOK


def test_coerce_level_checks_the_value() -> None:
    """A text level is accepted; anything else is refused."""
    assert tasks.coerce_level("deep") == DepthLevel.DEEP
    with pytest.raises(InvalidArgumentError):
        tasks.coerce_level("excellent")


def test_depth_goal_met_for_the_suggestion_refresh(butler: DiskButler) -> None:
    """Met, not met, and unknown."""
    library = FakeLibrary({"NGC 7331": [], "Vega": []}, [row(SURFACE_METRIC, 24.8)], STORED_TYPES)
    assert tasks.depth_goal_met(library, butler, "NGC 7331") is True
    tasks.store_chosen_level(butler, "NGC 7331", DepthLevel.DEEP)
    assert tasks.depth_goal_met(library, butler, "NGC 7331") is False
    assert tasks.depth_goal_met(FakeLibrary({"NGC 7331": []}, [], STORED_TYPES), butler, "NGC 7331") is None
    assert tasks.depth_goal_met(library, butler, "Vega") is None
