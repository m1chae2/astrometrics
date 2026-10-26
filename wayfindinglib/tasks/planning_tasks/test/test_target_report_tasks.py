"""Purpose: Tests for the target dataset report built from stored measurements.

Description: Builds a small made-up library (a target with 32 frames over three
nights, two of them set aside, one stack, an empty calibration library) behind
fakes of the science library, and checks the report as a whole: the summary of
the capture setting, the plain-rule statement for the stack (which needs the
stack tied to its setting through the frame weights), the frames-lost warning
with its quarantine evidence, the cost of low altitude, the missing calibration
frames, "take N more", the explicit missing-data statements, the checklist
return, the starting point for a target with no frames, and that nothing is
stored.
"""

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import (
    AppConfiguration,
    Astrometrics,
    CaptureSetting,
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SetAsideFrame,
    SubjectLevel,
    Target,
    frame_subject_id,
)
from astrometricslib.models.catalog_queries import CalibrationQueryResult
from astrometricslib.models.target import FrameRecord
from wayfindinglib.analytics.result_quality.reply_budget import MAXIMUM_REPLY_CHARACTERS, fit_reply_to_budget
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.planning.target_report import (
    ForecastBasis,
    ForecastCheck,
    ForecastedChange,
    ForecastOutcome,
)
from wayfindinglib.models.quality.efficiency import ScienceGoal
from wayfindinglib.models.quality.statements import ActionClass, Confidence, Severity, StatementKind
from wayfindinglib.models.session.observation_session import (
    ChecklistItem,
    ObservationSession,
    QueuedObservationPackage,
    StartTimeMode,
)
from wayfindinglib.tasks.planning_tasks import target_report_tasks
from wayfindinglib.tasks.planning_tasks.target_report_inputs import assign_stacks_to_settings
from wayfindinglib.tasks.planning_tasks.target_report_tasks import build_target_dataset_report

NOW = datetime(2026, 10, 20, 22, 0, tzinfo=UTC)
SETTING = CaptureSetting(
    camera="ASI533MM Pro",
    telescope="RC8",
    pixel_scale_arcsec=1.0,
    filter_name="L",
    exposure_seconds=60.0,
    gain_setting=100,
    offset=50,
    binning=1,
)
NIGHT_STARTS = [datetime(2026, 10, day, 4, 0, tzinfo=UTC) for day in (1, 2, 3)]
STACK_ID = "T/T_stack.fits"
MOVED = "frame moved to _excluded/"


class _FakeQuality:
    """Answers `query` and `capture_settings` from a list of rows."""

    def __init__(self, rows: list[QualityMeasurement], settings: dict[int, CaptureSetting]) -> None:
        """Hold the rows and the settings.

        Parameters
        ----------
        rows : `list` [`QualityMeasurement`]
            Every stored row.
        settings : `dict` [`int`, `CaptureSetting`]
            The capture settings by id.
        """
        self._rows = rows
        self._settings = settings

    def set_aside_frames(self, target_id: str) -> list[Any]:
        """List the frames in the ``_excluded`` folders (none in this fake).

        Parameters
        ----------
        target_id : `str`
            The target.

        Returns
        -------
        frames : `list`
            Always empty.
        """
        return []

    def query(self, **filters: Any) -> list[QualityMeasurement]:
        """Filter the rows the way the store does.

        Parameters
        ----------
        **filters
            The query arguments. ``metric``, ``level``, ``target_id`` and
            ``camera`` are applied; the others are accepted and ignored.

        Returns
        -------
        rows : `list` [`QualityMeasurement`]
            The matching rows.
        """
        rows = self._rows
        if filters.get("metric"):
            rows = [row for row in rows if row.metric == filters["metric"]]
        if filters.get("level"):
            rows = [row for row in rows if row.subject.level.value == filters["level"]]
        if filters.get("target_id"):
            rows = [row for row in rows if row.subject.target_id == filters["target_id"]]
        if filters.get("camera"):
            rows = [
                row
                for row in rows
                if self._settings.get(row.subject.capture_setting_id or -1, SETTING).camera
                == filters["camera"]
            ]
        return list(rows)

    def calibration_identities(
        self, run_ids: list[str] | None = None, target_id: str | None = None
    ) -> list[Any]:
        """Give the stored calibration records (none in this fake).

        Parameters
        ----------
        run_ids : `list` [`str`], optional
            Only these runs.
        target_id : `str`, optional
            Only this target's stacks.

        Returns
        -------
        records : `list`
            Always empty, so every run counts as confounded.
        """
        return []

    def metrics(self, **_arguments: Any) -> list[Any]:
        """Give the registered metrics, from the real registry.

        Returns
        -------
        metrics : `list` [`MetricSummary`]
            The summaries.
        """
        return Astrometrics().quality.metrics()

    def capture_settings(self, ids: list[int] | None = None) -> dict[int, CaptureSetting]:
        """Give the settings, or the ones asked for.

        Parameters
        ----------
        ids : `list` [`int`], optional
            Only these ids.

        Returns
        -------
        settings : `dict` [`int`, `CaptureSetting`]
            The settings.
        """
        return {key: value for key, value in self._settings.items() if ids is None or key in ids}


class _Library:
    """A made-up library: one target, its rows and its calibration rows."""

    def __init__(
        self,
        darks: list[dict] | None = None,
        flats: list[dict] | None = None,
        runs: list[tuple[str, datetime, float, float]] | None = None,
    ) -> None:
        """Build the library.

        Parameters
        ----------
        darks, flats : `list` [`dict`], optional
            The calibration library's rows. Empty by default.
        runs : `list` [`tuple`], optional
            The stack runs, each as run id, time, measured noise and expected
            noise. One run by default.
        """
        self.rows: list[QualityMeasurement] = []
        self.runs = runs or [("r1", NOW, 0.02, 0.018)]
        self._next_id = 0
        self.target = self._build_target()
        self.set_aside = self._set_aside_frames()
        self.rows.extend(self._stack_rows())
        self.quality = _FakeQuality(self.rows, {1: SETTING})
        self.targets = SimpleNamespace(get=lambda target_id: self.target if target_id == "T" else None)
        self.processing = SimpleNamespace(
            calibration=SimpleNamespace(
                query=lambda **_arguments: CalibrationQueryResult(
                    detail="counts", darks=darks or [], biases=[], flats=flats or []
                )
            ),
        )

    def _row(
        self,
        metric: str,
        value: float | None,
        subject_id: str,
        *,
        level: SubjectLevel = SubjectLevel.FRAME,
        version: int = 1,
        night: str | None = None,
        setting_id: int | None = 1,
        run_id: str = "frame-run",
        sample_count: int | None = None,
        at: datetime = NOW,
        action_taken: str | None = None,
    ) -> QualityMeasurement:
        """Build one stored row with a fresh row id.

        Returns
        -------
        row : `QualityMeasurement`
            The row.
        """
        self._next_id += 1
        return QualityMeasurement(
            metric=metric,
            definition_version=version,
            subject=QualitySubject(
                level=level,
                subject_id=subject_id,
                target_id="T",
                observing_night_id=night,
                capture_setting_id=setting_id,
            ),
            status=MeasurementStatus.MEASURED,
            value=value,
            sample_count=sample_count,
            action_taken=action_taken,
            origin=MeasurementOrigin.MEASURED,
            run_id=run_id,
            measured_at=at,
            row_id=self._next_id,
        )

    def _path(self, index: int) -> str:
        """Name the file of a frame.

        Parameters
        ----------
        index : `int`
            The frame's number.

        Returns
        -------
        path : `str`
            A path in the library's layout.
        """
        return f"/data/lights/T/RC8/ASI533MM Pro/f{index:03d}.fits"

    def _build_target(self) -> Target:
        """Make the target, its frame records and the frames' rows.

        Frames 0 to 9 are from the first night, taken low (25 degrees) with
        wide stars; the others are higher and sharper. Frames 30 and 31 are
        later set aside.

        Returns
        -------
        target : `Target`
            The target.
        """
        frames = []
        for index in range(32):
            night_index = 0 if index < 10 else 1 if index < 20 else 2
            start = NIGHT_STARTS[night_index] + timedelta(minutes=2 * index)
            if index >= 30:
                night_index = 2
            subject_id = frame_subject_id(self._path(index), "T")
            night = start.strftime("%Y-%m-%d")
            frames.append(
                FrameRecord(
                    path=self._path(index),
                    exposure="60",
                    timestamp=start.timestamp(),
                    camera="ASI533MM Pro",
                    telescope="RC8",
                    altitudeDegrees=25.0 if night_index == 0 else 55.0,
                    sensorTemperatureC=-10.0,
                    binning=1,
                )
            )
            width = 4.5 if night_index == 0 else 3.0
            for metric, value, version in (
                ("frame.star_width_effective_arcsec", width, 1),
                ("frame.star_width_moffat_arcsec", width - 0.1, 1),
                ("frame.sky_rate_e_per_s_per_px", 0.5, 2),
                ("frame.sky_noise_e", (0.5 * 60.0) ** 0.5, 2),
                ("frame.read_noise_e", 3.0, 1),
                ("frame.star_elongation", 1.05, 1),
            ):
                self.rows.append(self._row(metric, value, subject_id, version=version, night=night))
        return Target(id="T", ra="09:55:33", dec="+69:03:55", frames=frames)

    def _set_aside_frames(self) -> list[SetAsideFrame]:
        """Make the two set-aside frames and their quarantine rows.

        Returns
        -------
        frames : `list` [`SetAsideFrame`]
            The frames the stacker moved.
        """
        moved = []
        for index in (30, 31):
            subject_id = frame_subject_id(self._path(index), "T")
            for metric, value in (
                ("frame.quarantine_roundness", 0.9),
                ("frame.quarantine_sky_median_adu", 900.0),
                ("frame.detected_region_count", 3.0),
            ):
                self.rows.append(
                    self._row(
                        metric,
                        value,
                        subject_id,
                        night="2026-10-03",
                        action_taken=MOVED if metric == "frame.quarantine_roundness" else None,
                    )
                )
            moved.append(
                SetAsideFrame(
                    file=f"f{index:03d}.fits",
                    originalPath=self._path(index),
                    kind="clouded",
                    reason="few stars",
                    starCount=3,
                    typicalStarCount=120.0,
                    skyMedianAdu=900.0,
                )
            )
        return moved

    def _stack_rows(self) -> list[QualityMeasurement]:
        """Make the stack rows of each run.

        Returns
        -------
        rows : `list` [`QualityMeasurement`]
            The stack-level rows and the 30 frame weights of each run. A run
            is a tuple of run id, time, measured noise and expected noise.
        """
        rows = []
        for run_id, at, noise, expected in self.runs:
            for metric, value, version in (
                ("stacking.group_brightest_star_peak_fraction", 2.0, 1),
                ("stacking.empty_aperture_noise_stack_unit", noise, 3),
                ("stacking.expected_empty_aperture_noise_stack_unit", expected, 2),
                ("stacking.effective_time_s", 1800.0, 1),
            ):
                rows.append(
                    self._row(
                        metric,
                        value,
                        STACK_ID,
                        level=SubjectLevel.STACK,
                        version=version,
                        setting_id=None,
                        run_id=run_id,
                        sample_count=2000
                        if "noise_stack_unit" in metric and "expected" not in metric
                        else None,
                        at=at,
                    )
                )
            for index in range(30):
                rows.append(
                    self._row(
                        "stacking.frame_weight",
                        1.0,
                        frame_subject_id(self._path(index), "T"),
                        setting_id=None,
                        run_id=run_id,
                        at=at,
                    )
                )
        return rows


@pytest.fixture
def isolated_butler(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> DiskButler:
    """Build a DiskButler backed by a fully isolated temporary database.

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


def _report(library: _Library, butler: DiskButler) -> Any:
    """Build the report for the library's target.

    Returns
    -------
    report : `TargetDatasetReport`
        The report.
    """
    return build_target_dataset_report(
        library.target, astrometrics=library, butler=butler, config=butler.config, at=NOW
    )


def _report_with_forecast_outcome(
    library: _Library, butler: DiskButler, monkeypatch: pytest.MonkeyPatch, outcome: ForecastOutcome | None
) -> Any:
    """Build the report with a made-up forecast check of a given outcome.

    The made-up library cannot produce a real agreeing check, so the check
    function is replaced.

    Parameters
    ----------
    library : `_Library`
        The library (two stack runs, so a check is attempted).
    butler : `DiskButler`
        The storage.
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the check function.
    outcome : `ForecastOutcome` or `None`
        The outcome of the one check, or `None` for no check at all.

    Returns
    -------
    report : `TargetDatasetReport`
        The report.
    """
    from wayfindinglib.analytics.result_quality import forecast

    change = ForecastedChange(noiseChangeFraction=-0.2, tolerance=0.05)
    checks = (
        []
        if outcome is None
        else [
            ForecastCheck(
                stackRunId="r2",
                forecastWhenPlanned=change,
                forecastGivenFramesTaken=change,
                measured=change,
                weightingMode="equal",
                paired=True,
                outcome=outcome,
                reason="" if outcome == ForecastOutcome.AS_FORECAST else "made up for the test",
            )
        ]
    )
    monkeypatch.setattr(forecast, "forecast_check", lambda *args, **keywords: checks)
    return _report(library, butler)


def _report_with_checks(
    library: _Library,
    butler: DiskButler,
    monkeypatch: pytest.MonkeyPatch,
    sequence: list[tuple[ForecastOutcome, ForecastBasis]],
) -> Any:
    """Build the report with made-up forecast checks, oldest first.

    Parameters
    ----------
    library : `_Library`
        The library (two stack runs, so a check is attempted).
    butler : `DiskButler`
        The storage.
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the check function.
    sequence : `list` [`tuple`]
        The outcome and basis of each check.

    Returns
    -------
    report : `TargetDatasetReport`
        The report.
    """
    from wayfindinglib.analytics.result_quality import forecast

    change = ForecastedChange(noiseChangeFraction=-0.2, tolerance=0.05)
    checks = [
        ForecastCheck(
            stackRunId="r2",
            forecastWhenPlanned=change,
            forecastGivenFramesTaken=change,
            measured=change,
            weightingMode="equal",
            paired=True,
            outcome=outcome,
            reason="" if outcome == ForecastOutcome.AS_FORECAST else "made up for the test",
            basis=basis,
        )
        for outcome, basis in sequence
    ]
    monkeypatch.setattr(forecast, "forecast_check", lambda *args, **keywords: checks)
    return _report(library, butler)


TWO_RUNS = [("r1", NOW - timedelta(days=1), 0.0361, 0.0361), ("r2", NOW, 0.0283, 0.0283)]
"""Two stack runs, so the report attempts a forecast check. Each stack's
expected noise equals its measured noise: no noise floor, so "take N more"
gives a count."""

CAMERA_GAIN = 0.783
"""Electrons per ADU of the ZWO ASI533MM Pro at gain 0 (the camera profile's
measured value). The made-up configuration holds no camera profile, so tests
that need a gain patch it in."""


@pytest.fixture
def camera_gain(monkeypatch: pytest.MonkeyPatch) -> float:
    """Give the report a camera gain, as the real camera profile does.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the profile lookup.

    Returns
    -------
    gain : `float`
        The gain, in electrons per ADU.
    """
    monkeypatch.setattr(target_report_tasks, "camera_gain_for_setting", lambda _config, _setting: CAMERA_GAIN)
    return CAMERA_GAIN


def _of_rule(report: Any, rule: str) -> list[Any]:
    """Pick the statements of one rule.

    Returns
    -------
    statements : `list`
        The statements whose rule is `rule`.
    """
    return [statement for statement in report.statements if statement.rule == rule]


def test_the_report_summarizes_the_one_capture_setting(isolated_butler: DiskButler) -> None:
    """32 frames, 2 set aside, 3 nights, with a loss account."""
    report = _report(_Library(), isolated_butler)
    assert len(report.capture_settings) == 1
    summary = report.capture_settings[0]
    assert summary.capture_setting == SETTING
    assert summary.frames_taken == 32
    assert summary.frames_set_aside == 2
    assert summary.nights == 3
    assert summary.captured_seconds == pytest.approx(32 * 60.0)
    assert summary.weight_effective_seconds == pytest.approx(1800.0)
    assert summary.loss_account is not None
    assert summary.loss_account.reference_name.startswith("baseline:")
    assert report.starting_point is None
    assert report.target_id == "T"


def test_the_stack_is_tied_to_its_setting_through_its_frame_weights(isolated_butler: DiskButler) -> None:
    """The overexposure rule reads a stack row, so the map must work."""
    report = _report(_Library(), isolated_butler)
    overexposed = _of_rule(report, "overexposed_setting")
    assert len(overexposed) == 1
    assert overexposed[0].kind == StatementKind.WARNING
    assert overexposed[0].capture_setting == SETTING
    assert report.needs_attention.warning_count >= 1
    assert report.needs_attention.worst_severity == Severity.WARNING


def test_frames_lost_cites_the_quarantine_rows(isolated_butler: DiskButler) -> None:
    """Two of 32 frames is 6%: the warning cites the quarantine rows."""
    library = _Library()
    report = _report(library, isolated_butler)
    lost = _of_rule(report, "frames_lost")
    assert len(lost) == 1
    assert lost[0].message == "2 of 32 frames were set aside (frame moved to _excluded/ 2)."
    quarantine_ids = {
        row.row_id
        for row in library.rows
        if row.metric.startswith("frame.quarantine") or row.metric == "frame.detected_region_count"
    }
    assert {reference.row_id for reference in lost[0].evidence} == quarantine_ids


def test_low_frames_make_an_altitude_cost_statement(isolated_butler: DiskButler) -> None:
    """The first night was low and blurry; it lost more than 15%."""
    report = _report(_Library(), isolated_butler)
    cost = _of_rule(report, "altitude_cost")
    assert len(cost) == 1
    assert cost[0].action_class == ActionClass.SCHEDULING
    assert "Frames below 30 degrees" in cost[0].message


def test_an_empty_calibration_library_gives_a_dark_and_a_flat_gap(isolated_butler: DiskButler) -> None:
    """With no darks or flats, both are asked for."""
    report = _report(_Library(), isolated_butler)
    gaps = _of_rule(report, "calibration_gap")
    assert sorted(statement.proposed_action.calibration_frame_type for statement in gaps) == ["dark", "flat"]
    dark = next(s for s in gaps if s.proposed_action.calibration_frame_type == "dark")
    assert (
        dark.message == "Take darks at gain 100, offset 50, bin 1, 60 s, -10 C: none in the library matches."
    )


def test_a_matching_calibration_library_gives_no_gap(isolated_butler: DiskButler) -> None:
    """A dark and a flat that match leave no calibration statement."""
    dark = {
        "camera": "ASI533MM Pro",
        "iso": "100",
        "offset": 50.0,
        "binning": "1x1",
        "temperature_c": -10.0,
        "exposure": 60.0,
        "count": 20,
    }
    flat = {
        "camera": "ASI533MM Pro",
        "filter": "L",
        "iso": "100",
        "offset": 50.0,
        "binning": "1x1",
        "count": 30,
    }
    report = _report(_Library([dark], [flat]), isolated_butler)
    assert _of_rule(report, "calibration_gap") == []


def test_more_frames_proposes_a_count_and_says_it_is_tentative(
    isolated_butler: DiskButler, monkeypatch: pytest.MonkeyPatch, camera_gain: float
) -> None:
    """With 30 frames and an agreeing forecast check, a count is proposed."""
    report = _report_with_forecast_outcome(
        _Library(runs=TWO_RUNS), isolated_butler, monkeypatch, ForecastOutcome.AS_FORECAST
    )
    more = _of_rule(report, "more_frames")
    assert len(more) == 1
    assert more[0].kind == StatementKind.RECOMMENDATION
    assert more[0].confidence == Confidence.DESIGN_ESTIMATE
    assert more[0].proposed_action.frame_count > 0
    assert "to cut the noise by about 31%" in more[0].message
    assert more[0].expected_effect.noise_change_fraction == pytest.approx(-0.29, abs=0.02)
    assert more[0].proposed_action.exposure_seconds > 60.0
    assert "limited by read noise" in more[0].reason
    assert more[0].expected_effect.wall_clock_seconds > 0.0
    # A new exposure length makes a new group: at least 5 frames are asked for.
    assert more[0].proposed_action.frame_count >= 5
    assert "new exposure group" in more[0].reason


@pytest.mark.parametrize(
    "outcome",
    [None, ForecastOutcome.SHORT_UNKNOWN_REASON, ForecastOutcome.NOT_CHECKABLE],
)
def test_take_more_is_hidden_until_a_forecast_check_agreed(
    isolated_butler: DiskButler, monkeypatch: pytest.MonkeyPatch, outcome: ForecastOutcome | None
) -> None:
    """Decision U1: no agreeing check of this target, no count of frames."""
    report = _report_with_forecast_outcome(_Library(runs=TWO_RUNS), isolated_butler, monkeypatch, outcome)

    (more,) = _of_rule(report, "more_frames")

    assert more.kind == StatementKind.INSUFFICIENT_DATA
    assert more.proposed_action is None
    assert "one check agrees" in more.reason
    assert "Take more frames and stack them" in more.reason


def test_a_floor_that_most_of_the_noise_is_made_of_replaces_the_count(
    isolated_butler: DiskButler, monkeypatch: pytest.MonkeyPatch, camera_gain: float
) -> None:
    """A floor above the random noise means more frames cannot reach the goal.

    Measured 0.01483 against expected 0.0100 is a floor that is 55% of the
    noise variance. Doubling the data still cuts the noise by 13%, above the
    stop limit, so the stop warning is not raised; the report says so in place
    of a count and names the floor.
    """
    runs = [("r1", NOW - timedelta(days=1), 0.01483, 0.0100), ("r2", NOW, 0.01483, 0.0100)]
    report = _report_with_forecast_outcome(
        _Library(runs=runs), isolated_butler, monkeypatch, ForecastOutcome.AS_FORECAST
    )

    assert _of_rule(report, "more_frames") == []
    (floor,) = _of_rule(report, "more_frames_will_not_help")

    assert floor.kind == StatementKind.WARNING
    assert "noise floor" in floor.message
    assert "55% of the noise" in floor.message
    assert floor.proposed_action is None
    assert floor.evidence


def test_a_pair_with_no_aperture_identity_keeps_take_more_hidden(isolated_butler: DiskButler) -> None:
    """Runs stored before the identity row give no checkable pair.

    The report's own check runs on the two made-up stacks. They have no
    aperture identity and no calibration record, so the check says it could
    not be made, the gate stays shut, and the hidden statement says why.
    """
    library = _Library(runs=TWO_RUNS)
    for run_id, at, noise, _expected in TWO_RUNS:
        library.rows.append(
            library._row(
                "stacking.empty_aperture_noise_normalized",
                noise / 1000.0,
                STACK_ID,
                level=SubjectLevel.STACK,
                version=3,
                setting_id=None,
                run_id=run_id,
                sample_count=2000,
                at=at,
            )
        )
    report = _report(library, isolated_butler)

    (check,) = report.forecast_checks
    (more,) = _of_rule(report, "more_frames")

    assert check.outcome == ForecastOutcome.NOT_CHECKABLE
    assert "aperture-set identity" in check.reason
    assert more.kind == StatementKind.INSUFFICIENT_DATA
    assert "aperture-set identity" in more.reason


def test_take_more_is_hidden_for_a_target_with_one_stack(isolated_butler: DiskButler) -> None:
    """A target with a single stack has no check, so the gate is shut."""
    report = _report(_Library(), isolated_butler)

    (more,) = _of_rule(report, "more_frames")

    assert more.kind == StatementKind.INSUFFICIENT_DATA
    assert "No stack has followed a forecast yet" in more.reason


def test_each_setting_states_its_own_science_goal(isolated_butler: DiskButler) -> None:
    """Decision U2: a grating setting and an imaging setting have two goals."""
    library = _Library()
    grating = SETTING.model_copy(update={"filter_name": "Star Analyzer 200"})
    for row in library.rows:
        if row.subject.level == SubjectLevel.FRAME and row.subject.subject_id.endswith((
            "f020.fits",
            "f021.fits",
        )):
            row.subject.capture_setting_id = 2
    library.quality = _FakeQuality(library.rows, {1: SETTING, 2: grating})

    report = _report(library, isolated_butler)

    goals = {summary.capture_setting.filter_name: summary.science_goal for summary in report.capture_settings}
    assert goals["L"] == ScienceGoal.FAINT_POINT_SOURCE
    assert goals["Star Analyzer 200"] == ScienceGoal.SLITLESS_SPECTRAL_RESOLUTION
    stated = [s for s in report.statements if s.capture_setting == SETTING and "science_goal" in s.values]
    assert stated
    assert all(s.values["science_goal"] == "faint_point_source" for s in stated)


def _store_guide_samples(library: _Library, butler: DiskButler, error_arcsec: float) -> None:
    """Store a guide log that covers every night of the library's frames.

    Parameters
    ----------
    library : `_Library`
        The library whose frames set the nights.
    butler : `DiskButler`
        The storage; the samples go into its control records.
    error_arcsec : `float`
        The right-ascension error of every sample.
    """
    by_night: dict[str, list[float]] = {}
    for frame in library.target.frames:
        night = datetime.fromtimestamp(frame.timestamp, UTC).strftime("%Y-%m-%d")
        by_night.setdefault(night, []).append(frame.timestamp)
    samples = []
    for night, starts in by_night.items():
        for second in range(int(min(starts)) - 10, int(max(starts)) + 70, 2):
            samples.append({
                "timestamp": float(second),
                "dra": error_arcsec,
                "ddec": 0.0,
                "session_id": night,
                "source": "ekos_guide_log",
            })
    butler.control_records.record_guiding_samples(samples)


def test_loose_guiding_makes_the_guiding_limits_exposure_statement(
    isolated_butler: DiskButler,
) -> None:
    """Decision U3: a guide error too big for the star width is reported."""
    library = _Library()
    _store_guide_samples(library, isolated_butler, error_arcsec=2.0)

    report = _report(library, isolated_butler)

    (statement,) = _of_rule(report, "guiding_limits_exposure")
    assert statement.kind == StatementKind.RECOMMENDATION
    assert statement.message.startswith("At 60 s only 0% of frames stay sharp")
    assert statement.values["allowed_guide_rms_arcsec"] > 0.0


def test_good_guiding_makes_no_guiding_statement(isolated_butler: DiskButler) -> None:
    """A quiet guide log keeps the rule silent."""
    library = _Library()
    _store_guide_samples(library, isolated_butler, error_arcsec=0.05)

    report = _report(library, isolated_butler)

    assert _of_rule(report, "guiding_limits_exposure") == []


def test_no_guide_log_says_what_is_missing(isolated_butler: DiskButler) -> None:
    """Without guide samples the rule is an insufficient-data statement."""
    report = _report(_Library(), isolated_butler)

    (statement,) = _of_rule(report, "guiding_limits_exposure")
    assert statement.kind == StatementKind.INSUFFICIENT_DATA
    assert "guide samples" in statement.reason


def test_missing_data_is_said_not_skipped(isolated_butler: DiskButler) -> None:
    """The flat rules have no rows here, so they say what is missing."""
    report = _report(_Library(), isolated_butler)
    missing = [s for s in report.statements if s.kind == StatementKind.INSUFFICIENT_DATA]
    assert missing
    assert all(statement.reason.strip() for statement in missing)
    assert any(statement.rule == "flat_noise_limits_stack" for statement in missing)


def test_statements_come_in_priority_order(isolated_butler: DiskButler) -> None:
    """Warnings first, then recommendations, then missing data."""
    report = _report(_Library(), isolated_butler)
    order = {StatementKind.WARNING: 0, StatementKind.RECOMMENDATION: 1, StatementKind.CONSTRAINT: 2}
    ranks = [order.get(statement.kind, 3) for statement in report.statements]
    assert ranks == sorted(ranks)


def _plan_with_ticked_item(ticked_at: datetime) -> ObservationSession:
    """Build a stored plan for the target with one ticked checklist item.

    Parameters
    ----------
    ticked_at : `datetime`
        When the item was ticked.

    Returns
    -------
    session : `ObservationSession`
        The plan.
    """
    return ObservationSession(
        id="plan-1",
        night_date=date(2026, 10, 20),
        site_profile_id="default",
        telescope_id="t",
        camera_id="c",
        queue=[
            QueuedObservationPackage(
                id="q1",
                observation_package_id="p1",
                target_id="T",
                start_time_mode=StartTimeMode.SOONEST,
            )
        ],
        checklist=[ChecklistItem(rule="overexposed_setting", capture_setting=SETTING, ticked_at=ticked_at)],
    )


def test_a_ticked_item_comes_back_when_newer_measurements_still_trip_the_rule(
    isolated_butler: DiskButler,
) -> None:
    """A row measured after the tick still trips the rule: it comes back."""
    isolated_butler.put(
        _plan_with_ticked_item(NOW - timedelta(days=2)), "observation_session", {"session_id": "plan-1"}
    )
    report = _report(_Library(), isolated_butler)
    returned = _of_rule(report, "overexposed_setting")[0]
    assert returned.checklist_return is not None
    assert returned.checklist_return.item_id
    assert "still fires" in returned.checklist_return.reason


def test_a_ticked_item_does_not_come_back_on_measurements_older_than_the_tick(
    isolated_butler: DiskButler,
) -> None:
    """A row measured before the tick says nothing about the fix."""
    isolated_butler.put(
        _plan_with_ticked_item(NOW + timedelta(days=1)), "observation_session", {"session_id": "plan-1"}
    )
    report = _report(_Library(), isolated_butler)
    assert _of_rule(report, "overexposed_setting")[0].checklist_return is None


def test_a_report_stores_nothing(isolated_butler: DiskButler) -> None:
    """Reading the report writes no session, package or site."""
    before = len(isolated_butler.get_all("observation_session"))
    _report(_Library(), isolated_butler)
    assert len(isolated_butler.get_all("observation_session")) == before
    assert isolated_butler.get("site_profile", {"id": "default"}) is None


def test_a_target_with_no_frames_gets_a_starting_point_that_says_why_it_is_empty(
    isolated_butler: DiskButler,
) -> None:
    """With no camera in the catalog, the starting point gives a reason."""
    library = _Library()
    library.rows.clear()
    library.set_aside = []
    library.target = Target(id="T", ra="09:55:33", dec="+69:03:55")
    report = _report(library, isolated_butler)
    assert report.capture_settings == []
    assert report.starting_point is not None
    assert report.starting_point.exposure_seconds is None
    assert "camera" in report.starting_point.exposure_reason
    assert report.starting_point.confidence == Confidence.DESIGN_ESTIMATE


def test_a_stack_of_two_settings_goes_to_the_setting_with_most_frames() -> None:
    """The assignment follows the majority of member frames."""
    library = _Library()
    # Give the last 10 frames a second setting.
    second = SETTING.model_copy(update={"exposure_seconds": 120.0})
    for row in library.rows:
        if row.subject.level == SubjectLevel.FRAME and row.subject.subject_id.endswith(("f020.fits",)):
            row.subject.capture_setting_id = 2
    members = {STACK_ID: [frame_subject_id(library._path(index), "T") for index in range(30)]}
    assignment, notes = assign_stacks_to_settings(library.rows, {1: SETTING, 2: second}, members)
    assert assignment == {STACK_ID: 1}
    assert any("2 capture settings" in note for note in notes)


def test_a_flat_noise_floor_makes_the_stop_warning_and_drops_take_more(
    isolated_butler: DiskButler,
) -> None:
    """Noise that levels off stops "take N more" and warns instead."""
    # Variance = expected variance + 4e-4: the floor is 0.02.
    runs = [
        ("r1", NOW - timedelta(days=2), 0.0361, 0.030),
        ("r2", NOW - timedelta(days=1), 0.0283, 0.020),
        ("r3", NOW, 0.0250, 0.015),
    ]
    report = _report(_Library(runs=runs), isolated_butler)
    stop = _of_rule(report, "more_frames_will_not_help")
    assert len(stop) == 1
    assert stop[0].kind == StatementKind.WARNING
    assert "Retake flats or dither" in stop[0].message
    assert stop[0].evidence
    assert _of_rule(report, "more_frames") == []


def test_each_run_after_the_first_gets_a_forecast_check(isolated_butler: DiskButler) -> None:
    """Three runs give two checks, in time order."""
    runs = [
        ("r1", NOW - timedelta(days=2), 0.0361, 0.030),
        ("r2", NOW - timedelta(days=1), 0.0283, 0.020),
        ("r3", NOW, 0.0250, 0.015),
    ]
    report = _report(_Library(runs=runs), isolated_butler)
    assert len(report.forecast_checks) == 2
    assert [check.stack_run_id for check in report.forecast_checks] == ["r2", "r3"]


def test_the_starting_point_follows_the_equipments_own_sky_and_read_noise(
    isolated_butler: DiskButler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new target gets the sky-dominated exposure from the library's frames.

    The library frames have a sky of 0.5 e/s/px (Moon included, since five
    nights for a sky model do not exist) and a read noise of 3 e, so the
    exposure is ceil(9 * 3**2 / 0.5) = 162 s. The cap is not computed, and
    the reason names the missing zero point.
    """
    import wayfindinglib.data_access.equipment_catalog_reader as reader

    monkeypatch.setattr(
        reader,
        "get_equipment_catalog",
        lambda config: SimpleNamespace(active_camera=lambda: SimpleNamespace(name="ASI533MM Pro")),
    )
    library = _Library()
    library.set_aside = []
    new_target = Target(id="NEW", ra="09:55:33", dec="+69:03:55")
    report = build_target_dataset_report(
        new_target, astrometrics=library, butler=isolated_butler, config=isolated_butler.config, at=NOW
    )
    point = report.starting_point
    assert point is not None
    assert point.exposure_seconds == pytest.approx(162.0)
    assert point.capture_setting == SETTING
    assert point.clipping_cap_seconds is None
    assert "zero point" in point.clipping_cap_reason
    assert "Moon included" in point.exposure_reason
    assert point.confidence == Confidence.DESIGN_ESTIMATE


def _framed_target(target_id: str, filters: list[str]) -> Target:
    """Build a target whose light frames use the given filters.

    Parameters
    ----------
    target_id : `str`
        The target's id.
    filters : `list` [`str`]
        One filter name for each light frame.

    Returns
    -------
    target : `Target`
        The target.
    """
    frames = [
        FrameRecord(path=f"/data/lights/{target_id}/RC8/Cam/f{index}.fits", filter=name, exposure="30")
        for index, name in enumerate(filters)
    ]
    return Target(id=target_id, frames=frames)


def test_the_science_goal_follows_the_filters_and_the_catalog_type() -> None:
    """Spectral frames, a Messier id and a plain star give three goals."""
    from wayfindinglib.analytics.result_quality.science_goal import science_goal_for_target
    from wayfindinglib.models.quality.efficiency import ScienceGoal

    spectral = science_goal_for_target(_framed_target("Vega", ["SPEC", "SPEC", "Luminance"]))
    extended = science_goal_for_target(_framed_target("M 81", ["Luminance", "Luminance"]))
    star = science_goal_for_target(_framed_target("Vega", ["Luminance", "Luminance"]))
    assert spectral == ScienceGoal.SLITLESS_SPECTRAL_RESOLUTION
    assert extended == ScienceGoal.EXTENDED_OBJECT
    assert star == ScienceGoal.FAINT_POINT_SOURCE


def test_the_natural_goals_say_what_is_missing_when_no_calibration_exists() -> None:
    """Spectroscopy and photometry give insufficient_data with the reason."""
    from wayfindinglib.models.quality.efficiency import ScienceGoal
    from wayfindinglib.tasks.planning_tasks.target_report_tasks import _natural_goal_statements

    spectral = _natural_goal_statements(ScienceGoal.SLITLESS_SPECTRAL_RESOLUTION, [], False)
    assert [statement.rule for statement in spectral] == ["spectrum_snr"]
    assert spectral[0].kind == StatementKind.INSUFFICIENT_DATA
    assert "class gap" in spectral[0].reason
    photometry = _natural_goal_statements(ScienceGoal.FAINT_POINT_SOURCE, [], True)
    assert [statement.rule for statement in photometry] == ["photometry_precision"]
    assert "catalog amplitude" in photometry[0].reason
    assert _natural_goal_statements(ScienceGoal.FAINT_POINT_SOURCE, [], False) == []


def _busy_library() -> _Library:
    """Build a library with 3 capture settings, 500 frames and every metric.

    The made-up target has 500 frames over three exposures. Each frame has a
    row of every registered frame-level metric, and the stack has a row of
    every registered metric of the other levels, so the context section has
    its largest possible size.

    Returns
    -------
    library : `_Library`
        The busy library.
    """
    library = _Library()
    settings = {
        1: SETTING,
        2: SETTING.model_copy(update={"exposure_seconds": 30.0}),
        3: SETTING.model_copy(update={"exposure_seconds": 120.0}),
    }
    library.quality = _FakeQuality(library.rows, settings)
    registered = Astrometrics().quality.metrics(detail="full")
    frames = []
    for index in range(32, 532):
        setting_id = 1 + index % 3
        start = NIGHT_STARTS[index % 3] + timedelta(minutes=index)
        path = library._path(index)
        subject_id = frame_subject_id(path, "T")
        frames.append(
            FrameRecord(
                path=path,
                exposure=str(int(settings[setting_id].exposure_seconds)),
                timestamp=start.timestamp(),
                camera="ASI533MM Pro",
                telescope="RC8",
                altitudeDegrees=45.0,
                sensorTemperatureC=-10.0,
                binning=1,
            )
        )
        for summary in registered:
            if summary.levels[0] != SubjectLevel.FRAME:
                continue
            library.rows.append(
                library._row(
                    summary.name,
                    1.5,
                    subject_id,
                    version=summary.definition_version,
                    night=start.strftime("%Y-%m-%d"),
                    setting_id=setting_id,
                )
            )
    for summary in registered:
        if summary.levels[0] != SubjectLevel.FRAME:
            library.rows.append(
                library._row(
                    summary.name,
                    2.5,
                    STACK_ID,
                    level=summary.levels[0],
                    version=summary.definition_version,
                    setting_id=None,
                )
            )
    library.target = Target(id="T", ra="09:55:33", dec="+69:03:55", frames=[*library.target.frames, *frames])
    return library


def test_a_busy_target_gives_a_full_report_and_the_mcp_reply_fits_the_budget(
    isolated_butler: DiskButler,
) -> None:
    """The library keeps everything; the MCP shortening fits the limit."""
    full = _report(_busy_library(), isolated_butler)
    assert full.context_omitted_reason is None
    assert all(statement.evidence_omitted_count == 0 for statement in full.statements)
    report = fit_reply_to_budget(full)
    text = json.dumps(report.model_dump(), separators=(",", ":"), default=str)
    assert len(text) <= MAXIMUM_REPLY_CHARACTERS
    assert len(report.statements) == len(full.statements)
    assert len(report.capture_settings) == 3
    for reading in report.context:
        assert reading.capture_setting_id is None or reading.capture_setting_id in {1, 2, 3}


def test_the_context_section_goes_first_and_then_the_long_evidence_lists(
    isolated_butler: DiskButler,
) -> None:
    """A tight budget drops readings, then evidence, never statements."""
    library = _busy_library()
    full = _report(library, isolated_butler)
    assert full.context
    assert len(json.dumps(full.model_dump(mode="json"), separators=(",", ":"))) > MAXIMUM_REPLY_CHARACTERS
    without_context = full.model_copy(update={"context": []})
    size_without_context = len(json.dumps(without_context.model_dump(mode="json"), separators=(",", ":")))
    only_context = fit_reply_to_budget(full, budget=size_without_context + 500)
    assert only_context.context == []
    assert only_context.context_omitted_reason is not None
    assert only_context.statements == full.statements
    tight = fit_reply_to_budget(full, budget=2_000)
    assert [statement.rule for statement in tight.statements] == [s.rule for s in full.statements]
    assert tight.needs_attention == full.needs_attention
    for kept, original in zip(tight.statements, full.statements, strict=True):
        assert len(kept.evidence) + kept.evidence_omitted_count == len(original.evidence)
        assert len(kept.evidence) <= 1


def test_take_more_prices_dithers_and_refocusing_like_the_ranking(
    isolated_butler: DiskButler, monkeypatch: pytest.MonkeyPatch, camera_gain: float
) -> None:
    """The time of "take N more" counts dithers and autofocus runs."""
    from wayfindinglib.analytics.result_quality.capture_overheads import (
        AUTOFOCUS_RUNS_PER_HOUR,
        DITHER_EVERY_FRAMES,
        estimate_capture_overheads,
        wall_clock_estimate,
    )

    report = _report_with_forecast_outcome(
        _Library(runs=TWO_RUNS), isolated_butler, monkeypatch, ForecastOutcome.AS_FORECAST
    )
    (more,) = _of_rule(report, "more_frames")
    action, effect = more.proposed_action, more.expected_effect
    overheads = estimate_capture_overheads([], action.exposure_seconds, SETTING.filter_name)
    downloads_only = wall_clock_estimate(action.frame_count, action.exposure_seconds, overheads)
    assert effect.wall_clock_seconds > downloads_only.total_seconds
    without_focus = wall_clock_estimate(
        action.frame_count, action.exposure_seconds, overheads, dither_every_frames=DITHER_EVERY_FRAMES
    )
    assert effect.wall_clock_seconds >= without_focus.total_seconds
    assert AUTOFOCUS_RUNS_PER_HOUR >= 1
    assert "dithers and refocusing" in more.message


def test_the_report_states_both_references_and_never_stores_the_site(isolated_butler: DiskButler) -> None:
    """Notes name the reference in use and the unstored site."""
    report = _report(_Library(), isolated_butler)

    assert any("measured against the baseline" in note for note in report.notes)
    assert any(
        "physical reference is not available" in note or "physical reference" in note for note in report.notes
    )
    assert any("No observing site is stored" in note for note in report.notes)
    assert isolated_butler.get("site_profile", {"id": "default"}) is None


def test_set_aside_frames_come_from_stored_rows_not_the_frames_folder(isolated_butler: DiskButler) -> None:
    """The report never calls the folder-walking restore listing."""
    library = _Library()

    def forbidden(*_arguments: Any, **_keywords: Any) -> None:
        """Fail if the report walks the frames tree.

        Raises
        ------
        AssertionError
            Always.
        """
        raise AssertionError("restore_excluded_frames must not be called")

    library.processing.restore_excluded_frames = forbidden
    report = _report(library, isolated_butler)
    assert report.capture_settings[0].frames_set_aside == 2
    assert any("not why" in note for note in report.notes)


def _provenanced(value: float, kind: str, source: str) -> SimpleNamespace:
    """Build a stand-in profile value with its provenance.

    Parameters
    ----------
    value : `float`
        The value in ADU.
    kind : `str`
        Where it came from, such as ``"measured"``.
    source : `str`
        The source text.

    Returns
    -------
    provenanced : `SimpleNamespace`
        An object shaped like a `ProvenancedValue`.
    """
    return SimpleNamespace(value=value, provenance=SimpleNamespace(kind=kind, source=source))


def test_the_clipping_note_uses_the_ceiling_when_the_threshold_cannot_be_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A threshold above the clip ceiling reports the ceiling instead."""
    from wayfindinglib.tasks.planning_tasks import target_report_tasks

    ceiling = _provenanced(16383, "measured", "raw maximum")
    profile = SimpleNamespace(
        clip_ceiling_adu=ceiling,
        pixel_saturation_level_adu=ceiling,
        saturation_threshold_can_be_reached=False,
        photometric_linearity_limit_adu=None,
    )
    monkeypatch.setattr(target_report_tasks, "resolve_camera_profile", lambda name, config: profile)
    note = target_report_tasks._clipping_note("Nikon D5300", None, ScienceGoal.FAINT_POINT_SOURCE)
    assert "at or above 16383 ADU (measured: raw maximum)" in note
    assert "the ceiling is used instead" in note
    assert "proportional" not in note


def test_the_clipping_note_names_the_linearity_limit_when_there_is_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ZWO-like profile keeps its threshold and adds the linearity limit."""
    from wayfindinglib.tasks.planning_tasks import target_report_tasks

    profile = SimpleNamespace(
        clip_ceiling_adu=_provenanced(65532, "measured", "raw maximum"),
        pixel_saturation_level_adu=_provenanced(65000, "assumed", "default"),
        saturation_threshold_can_be_reached=True,
        photometric_linearity_limit_adu=_provenanced(60000, "measured", "Antonov 2026"),
    )
    monkeypatch.setattr(target_report_tasks, "resolve_camera_profile", lambda name, config: profile)
    note = target_report_tasks._clipping_note("ZWO ASI 533MM Pro", None, ScienceGoal.FAINT_POINT_SOURCE)
    assert "at or above 65000 ADU (assumed: default)" in note
    assert "the ceiling is used instead" not in note
    assert "above 60000 ADU (measured: Antonov 2026)" in note


def test_an_agreeing_earlier_nights_check_shows_an_exploratory_count(
    isolated_butler: DiskButler, monkeypatch: pytest.MonkeyPatch, camera_gain: float
) -> None:
    """Decision D11: a subset check opens the gate as exploratory."""
    report = _report_with_checks(
        _Library(runs=TWO_RUNS),
        isolated_butler,
        monkeypatch,
        [(ForecastOutcome.AS_FORECAST, ForecastBasis.EARLIER_NIGHTS_SUBSET)],
    )

    (more,) = _of_rule(report, "more_frames")

    assert more.kind == StatementKind.RECOMMENDATION
    assert more.proposed_action.frame_count > 0
    assert more.confidence == Confidence.DESIGN_ESTIMATE
    assert more.values["count_basis"] == "exploratory"
    for text in (more.message, more.reason):
        assert "exploratory" in text
        assert (
            "frames already taken (the stack of all nights but the last against the stack of all nights)"
            in text
        )
        assert "next real stack will confirm it or close it again" in text
    assert report.forecast_checks[0].basis == ForecastBasis.EARLIER_NIGHTS_SUBSET


def test_a_later_real_check_that_agrees_removes_the_exploratory_mark(
    isolated_butler: DiskButler, monkeypatch: pytest.MonkeyPatch, camera_gain: float
) -> None:
    """A real later stack that agrees confirms the count: no mark."""
    report = _report_with_checks(
        _Library(runs=TWO_RUNS),
        isolated_butler,
        monkeypatch,
        [
            (ForecastOutcome.AS_FORECAST, ForecastBasis.EARLIER_NIGHTS_SUBSET),
            (ForecastOutcome.AS_FORECAST, ForecastBasis.LATER_STACK),
        ],
    )

    (more,) = _of_rule(report, "more_frames")

    assert more.kind == StatementKind.RECOMMENDATION
    assert "exploratory" not in more.message
    assert "count_basis" not in more.values


def test_a_later_real_check_that_disagrees_closes_an_exploratory_count(
    isolated_butler: DiskButler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real check that falls short hides the count again."""
    report = _report_with_checks(
        _Library(runs=TWO_RUNS),
        isolated_butler,
        monkeypatch,
        [
            (ForecastOutcome.AS_FORECAST, ForecastBasis.EARLIER_NIGHTS_SUBSET),
            (ForecastOutcome.SHORT_UNKNOWN_REASON, ForecastBasis.LATER_STACK),
        ],
    )

    (more,) = _of_rule(report, "more_frames")

    assert more.kind == StatementKind.INSUFFICIENT_DATA
    assert more.proposed_action is None
    assert "1 of them were made from frames already taken" in more.reason


def test_a_disagreeing_earlier_nights_check_keeps_the_count_hidden(
    isolated_butler: DiskButler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A subset check that falls short opens nothing and is named."""
    report = _report_with_checks(
        _Library(runs=TWO_RUNS),
        isolated_butler,
        monkeypatch,
        [(ForecastOutcome.SHORT_UNKNOWN_REASON, ForecastBasis.EARLIER_NIGHTS_SUBSET)],
    )

    (more,) = _of_rule(report, "more_frames")

    assert more.kind == StatementKind.INSUFFICIENT_DATA
    assert "made from frames already taken" in more.reason


def _floor_statement(setting: CaptureSetting = SETTING) -> Any:
    """Make the M 13 diminishing-returns "take N more" statement.

    Parameters
    ----------
    setting : `CaptureSetting`, optional
        The setting it is about.

    Returns
    -------
    statement : `TargetStatement`
        The recommendation for 44 more frames, with the floor sentences.
    """
    from wayfindinglib.analytics.result_quality.capture_overheads import CaptureOverheads
    from wayfindinglib.analytics.result_quality.noise_growth import TakeMoreResult
    from wayfindinglib.analytics.result_quality.report_rules import more_frames_statement

    result = TakeMoreResult(
        target_noise_ratio=0.707,
        added_exposure_seconds=44 * 30.0,
        new_exposure_seconds=30.0,
        floor_noise=0.583e-3,
        floor_variance_share=0.49,
        practical_limit_applied=True,
        doubling_frames=44,
        doubling_noise_cut=0.137,
        most_reachable_cut=0.296,
    )
    overheads = CaptureOverheads(
        download_seconds=5.0,
        dither_settle_seconds=20.0,
        autofocus_seconds=120.0,
        download_from_records=False,
        dither_from_records=False,
        autofocus_from_records=False,
        gaps_used=0,
    )
    return more_frames_statement(result, setting, overheads, [])


def test_the_floor_sentence_points_at_calibration_advice_in_the_same_report() -> None:
    """A calibration_gap statement for the setting swaps the last sentence."""
    from wayfindinglib.analytics.result_quality.report_rules import (
        FLOOR_CALIBRATION_TEXT,
        FLOOR_NOT_REMOVED_TEXT,
    )
    from wayfindinglib.models.quality.statements import TargetStatement

    floor = _floor_statement()
    gap = TargetStatement(
        rule="calibration_gap",
        kind=StatementKind.RECOMMENDATION,
        severity=None,
        action_class=ActionClass.CALIBRATION,
        capture_setting=SETTING,
        message="Take flats.",
        confidence=Confidence.DESIGN_ESTIMATE,
    )

    [with_gap, _] = target_report_tasks._point_floor_at_calibration([floor, gap])
    [without_gap] = target_report_tasks._point_floor_at_calibration([floor])

    assert with_gap.message.endswith(FLOOR_CALIBRATION_TEXT)
    assert FLOOR_NOT_REMOVED_TEXT not in with_gap.message
    assert "calibration recommendations" in with_gap.plain_message
    assert without_gap.message.endswith(FLOOR_NOT_REMOVED_TEXT)


def test_an_exploratory_gate_keeps_its_sentence_on_the_doubling_statement() -> None:
    """The D11 sentence and count basis go on the doubling statement too."""
    [marked] = target_report_tasks._mark_exploratory([_floor_statement()])

    assert target_report_tasks.EXPLORATORY_COUNT_SENTENCE in marked.message
    assert marked.values["count_basis"] == "exploratory"
    assert marked.proposed_action.frame_count == 44
