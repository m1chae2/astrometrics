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
    RestoreReport,
    SetAsideFrame,
    SubjectLevel,
    Target,
    frame_subject_id,
)
from astrometricslib.models.catalog_queries import CalibrationQueryResult
from astrometricslib.models.target import FrameRecord
from wayfindinglib.analytics.result_quality.reply_budget import MAXIMUM_REPLY_CHARACTERS
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.quality.statements import ActionClass, Confidence, Severity, StatementKind
from wayfindinglib.models.session.observation_session import (
    ChecklistItem,
    ObservationSession,
    QueuedObservationPackage,
    StartTimeMode,
)
from wayfindinglib.tasks.planning_tasks import target_report_tasks
from wayfindinglib.tasks.planning_tasks.target_report_inputs import assign_stacks_to_settings
from wayfindinglib.tasks.planning_tasks.target_report_tasks import (
    build_target_dataset_report,
    fit_report_to_budget,
)

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
            restore_excluded_frames=lambda target, apply=False: RestoreReport(
                targetId="T", applied=False, frames=self.set_aside
            ),
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
                self.rows.append(self._row(metric, value, subject_id, night="2026-10-03"))
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
                ("stacking.expected_empty_aperture_noise_stack_unit", expected, 1),
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
    assert lost[0].message == "2 of 32 frames were set aside (clouded 2)."
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


def test_more_frames_proposes_a_count_and_says_it_is_tentative(isolated_butler: DiskButler) -> None:
    """With 30 usable frames the report proposes a count of more frames."""
    report = _report(_Library(), isolated_butler)
    more = _of_rule(report, "more_frames")
    assert len(more) == 1
    assert more[0].kind == StatementKind.RECOMMENDATION
    assert more[0].confidence == Confidence.DESIGN_ESTIMATE
    assert more[0].proposed_action.frame_count > 0
    assert "to cut the noise by about 30%" in more[0].message
    assert more[0].expected_effect.noise_change_fraction == pytest.approx(-0.29, abs=0.02)
    assert more[0].proposed_action.exposure_seconds > 60.0
    assert "limited by read noise" in more[0].reason
    assert more[0].expected_effect.wall_clock_seconds > 0.0


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
    from wayfindinglib.models.quality.efficiency import ScienceGoal
    from wayfindinglib.tasks.planning_tasks.target_report_inputs import science_goal_for

    spectral, _ = science_goal_for(_framed_target("Vega", ["SPEC", "SPEC", "Luminance"]))
    extended, _ = science_goal_for(_framed_target("M 81", ["Luminance", "Luminance"]))
    star, reason = science_goal_for(_framed_target("Vega", ["Luminance", "Luminance"]))
    assert spectral == ScienceGoal.SLITLESS_SPECTRAL_RESOLUTION
    assert extended == ScienceGoal.EXTENDED_OBJECT
    assert star == ScienceGoal.FAINT_POINT_SOURCE
    assert "faint point source" in reason


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


def test_a_busy_target_gives_a_report_that_fits_the_mcp_reply_budget(
    isolated_butler: DiskButler,
) -> None:
    """3 settings, 500 frames and every metric fit the MCP reply limit."""
    report = _report(_busy_library(), isolated_butler)
    text = json.dumps(report.model_dump(), separators=(",", ":"), default=str)
    assert len(text) <= MAXIMUM_REPLY_CHARACTERS
    assert len(report.capture_settings) == 3
    for reading in report.context:
        assert reading.capture_setting_id is None or reading.capture_setting_id in {1, 2, 3}


def test_the_context_section_goes_first_and_then_the_long_evidence_lists(
    isolated_butler: DiskButler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tight budget drops readings, then evidence, never statements."""
    monkeypatch.setattr(target_report_tasks, "fit_report_to_budget", lambda report: report)
    library = _busy_library()
    full = _report(library, isolated_butler)
    assert full.context
    assert len(json.dumps(full.model_dump(mode="json"), separators=(",", ":"))) > MAXIMUM_REPLY_CHARACTERS
    without_context = full.model_copy(update={"context": []})
    size_without_context = len(json.dumps(without_context.model_dump(mode="json"), separators=(",", ":")))
    only_context = fit_report_to_budget(full, budget=size_without_context + 500)
    assert only_context.context == []
    assert only_context.context_omitted_reason is not None
    assert only_context.statements == full.statements
    tight = fit_report_to_budget(full, budget=2_000)
    assert [statement.rule for statement in tight.statements] == [s.rule for s in full.statements]
    assert tight.needs_attention == full.needs_attention
    for kept, original in zip(tight.statements, full.statements, strict=True):
        assert len(kept.evidence) + kept.evidence_omitted_count == len(original.evidence)
        assert len(kept.evidence) <= 1
