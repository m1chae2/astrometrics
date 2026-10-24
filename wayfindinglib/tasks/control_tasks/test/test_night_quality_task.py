"""Purpose: Tests for the night review, `control.history` kind night_quality.

Description: Builds a made-up science library of several nights, then reviews
one night. Checks the loss account against the user's own baseline, the
set-aside time recorded by stored rows, the warnings and the shared
`needs_attention` count, the order of targets, the guiding summary, and the
honest notes. The library is a small stand-in with the same ``targets`` and
``quality`` calls the real one has. The guiding analysis is replaced by a
fixed answer, because it has its own tests.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import AppConfiguration, FrameRecord, NotFoundError
from wayfindinglib.analytics.result_quality.efficiency import STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC
from wayfindinglib.analytics.result_quality.judge_results import needs_attention
from wayfindinglib.analytics.result_quality.science_goal import science_goal_for_setting
from wayfindinglib.analytics.test.night_quality_helpers import (
    FIRST_NIGHT,
    OTHER,
    REVIEWED,
    STAR,
    FakeLibrary,
    make_row,
    make_rows,
    make_target,
)
from wayfindinglib.models.session.night_quality import GuidingNightSummary, NightQuality
from wayfindinglib.models.session.session_quality import (
    GuidingInputQuality,
    GuidingPerformance,
    GuidingRunPerformance,
    GuidingSessionAnalysis,
)
from wayfindinglib.tasks.control_tasks import night_analysis, night_quality_task

_CONFIG: dict[str, AppConfiguration] = {}
"""The isolated configuration of the running test."""


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the configuration at an empty file in a temporary folder."""
    config_path = tmp_path / "astrometrics.config.toml"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    _CONFIG["config"] = AppConfiguration()


def _context(library: FakeLibrary, stored: dict[str, list[Any]] | None = None) -> Any:
    """Build a stand-in for the Control context.

    Parameters
    ----------
    library : `FakeLibrary`
        The science library stand-in.
    stored : `dict` [`str`, `list`], optional
        Stored records by kind (``ekos_session_context``, ``guiding_run``).

    Returns
    -------
    context : `SimpleNamespace`
        Has what the night review reads.
    """
    records = stored or {}
    return SimpleNamespace(
        astrometrics=library,
        config=_CONFIG["config"],
        active_telescope=lambda: None,
        butler=SimpleNamespace(get_all=lambda kind: records.get(kind, []), get=lambda kind, key=None: None),
    )


@pytest.fixture(autouse=True)
def _no_guiding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the guiding analysis, which has its own tests, by no data."""
    monkeypatch.setattr(night_analysis, "guiding_night_analysis", lambda context, night_id: None)


def _review(library: FakeLibrary) -> NightQuality:
    """Review the bad night of a library.

    Returns
    -------
    quality : `NightQuality`
        The review.
    """
    return night_quality_task.build_night_quality(_context(library), REVIEWED)


def test_the_loss_account_is_stated_against_the_users_own_baseline() -> None:
    """A bad night loses time to seeing and sky against the best nights."""
    target = make_target(STAR, 5)
    library = FakeLibrary([target], make_rows(target, REVIEWED))

    quality = _review(library)

    (entry,) = quality.targets
    assert entry.target_id == "HD 1"
    assert entry.goal.value == "faint_point_source"
    assert entry.frames_taken == 4
    assert entry.captured_seconds == pytest.approx(240.0)
    account = entry.loss_account
    assert account is not None
    assert account.reference_name.startswith("baseline:")
    causes = {share.cause for share in account.kept_frame_deficit}
    assert "seeing" in causes
    assert any(cause.startswith("sky_brightness") for cause in causes)
    assert entry.efficiency_effective_seconds is not None
    assert entry.efficiency_effective_seconds < entry.captured_seconds
    assert account.transparency_included is False


def test_set_aside_frames_recorded_by_rows_are_counted_apart() -> None:
    """Frames a stored row says were moved to _excluded are lost time."""
    target = make_target(STAR, 5)
    rows = make_rows(target, REVIEWED)
    stamp = next(
        frame.timestamp
        for frame in target.frames
        if datetime.fromtimestamp(frame.timestamp or 0.0, tz=UTC).date().isoformat() == REVIEWED
    )
    moved = "frame moved to _excluded/"
    # Two moved frames that were measured, and one that was not (only a
    # quarantine row exists), so both paths of the account are used.
    for index in range(3):
        extra = FrameRecord(
            path=f"/lib/lights/HD 1/Scope/CamA/moved_{index}.fits", timestamp=stamp, exposure="60"
        )
        metric = STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC if index < 2 else "frame.quarantine_roundness"
        rows.append(make_row(metric, 1, 0.5, target, extra, moved))

    (entry,) = _review(FakeLibrary([target], rows)).targets

    account = entry.loss_account
    assert account is not None
    assert account.set_aside_seconds == pytest.approx(180.0)
    assert "_excluded" in account.set_aside[0].cause
    assert entry.frames_set_aside == 3


def test_a_night_with_no_set_aside_rows_says_the_time_may_be_higher() -> None:
    """The review does not pretend that zero set-aside frames were seen."""
    target = make_target(STAR, 5)

    quality = _review(FakeLibrary([target], make_rows(target, REVIEWED)))

    assert any("set aside" in note for note in quality.notes)


def test_warnings_are_counted_by_the_shared_function_and_listed_first() -> None:
    """A target with trailed stars warns and leads the list."""
    clean = make_target(OTHER, 5)
    trailed = make_target(STAR, 5)
    rows = make_rows(clean, REVIEWED) + make_rows(trailed, REVIEWED, elongation=1.6)

    quality = _review(FakeLibrary([clean, trailed], rows))

    assert [entry.target_id for entry in quality.targets] == ["HD 1", "HD 2"]
    first, second = quality.targets
    assert first.needs_attention.warning_count >= 1
    assert any(statement.rule == "trailed_stars" for statement in first.statements)
    assert second.needs_attention.warning_count == 0
    expected = needs_attention([*first.statements, *second.statements])
    assert quality.needs_attention == expected


def test_too_few_nights_fall_back_to_no_reference_and_say_so() -> None:
    """With two nights there is no baseline and no telescope reference."""
    target = make_target(STAR, 4)
    library = FakeLibrary(
        [target], [row for row in make_rows(target, REVIEWED) if row.subject.observing_night_id]
    )

    # Only the reviewed night and one earlier night keep their rows.
    keep = {REVIEWED, (FIRST_NIGHT).date().isoformat()}
    library.rows = [row for row in library.rows if row.subject.observing_night_id in keep]

    (entry,) = _review(library).targets

    assert entry.loss_account is None
    assert any("No reference" in note for note in entry.notes)


def test_a_missing_stored_site_is_said_and_never_stored() -> None:
    """With no stored site the review names where its site came from."""
    target = make_target(STAR, 5)

    quality = _review(FakeLibrary([target], make_rows(target, REVIEWED)))

    assert any("No observing site is stored" in note for note in quality.notes)


def test_an_empty_night_is_not_found() -> None:
    """A night with no measurements and no guiding raises `NotFoundError`."""
    target = make_target(STAR, 5)

    with pytest.raises(NotFoundError):
        night_quality_task.build_night_quality(
            _context(FakeLibrary([target], make_rows(target, REVIEWED))), "2020-01-01"
        )


def test_a_night_with_only_guiding_still_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guiding data alone gives a review with a guiding summary."""
    analysis = GuidingSessionAnalysis(
        session_id="2020-01-01",
        equipment_fingerprint="x",
        flagged=True,
        flag_reasons=["guiding_above_limit"],
        input_quality=GuidingInputQuality(
            runs=1,
            guided_seconds=3600.0,
            frames_total=100,
            frames_lost=20,
            lost_fraction=0.2,
            samples_analyzed=100,
            excursion_count=3,
            has_enough_samples=True,
            limits_equipment_match="exact",
        ),
        performance=GuidingPerformance(
            rms_per_axis_arcsec=1.4,
            expected_star_widening_fraction=0.25,
            runs=[GuidingRunPerformance(run_id="r1", started_at=1.0, rms_per_axis_arcsec=1.4)],
        ),
    )
    monkeypatch.setattr(night_analysis, "guiding_night_analysis", lambda context, night_id: analysis)

    quality = night_quality_task.build_night_quality(_context(FakeLibrary([], [])), "2020-01-01")

    assert quality.targets == []
    guiding = quality.guiding
    assert isinstance(guiding, GuidingNightSummary)
    assert guiding.available and guiding.flagged
    assert guiding.rms_per_axis_arcsec == pytest.approx(1.4)
    assert guiding.lost_fraction == pytest.approx(0.2)
    assert guiding.excursion_count == 3
    assert guiding.runs[0].run_id == "r1"


def test_a_review_with_long_evidence_lists_is_cut_for_the_mcp_reply_budget() -> None:
    """Statements keep fewer references, count the lost ones, and all stay."""
    from astrometricslib import MeasurementReference
    from wayfindinglib.analytics.result_quality.reply_budget import (
        MAXIMUM_REPLY_CHARACTERS,
        fit_reply_to_budget,
        reply_characters,
    )
    from wayfindinglib.models.quality.statements import (
        ActionClass,
        Confidence,
        NeedsAttention,
        Severity,
        StatementKind,
        TargetStatement,
    )
    from wayfindinglib.models.session.night_quality import NightTargetQuality

    statement = TargetStatement(
        rule="trailed_stars",
        kind=StatementKind.WARNING,
        severity=Severity.ADVICE,
        action_class=ActionClass.SESSION_PROCEDURE,
        message="Stars are trailed.",
        confidence=Confidence.DESIGN_ESTIMATE,
        evidence=[MeasurementReference(row_id=row) for row in range(1, 701)],
    )
    entries = [
        NightTargetQuality(
            target_id=f"T{index}",
            goal="faint_point_source",
            frames_taken=700,
            frames_set_aside=0,
            captured_seconds=1.0,
            statements=[statement, statement],
            needs_attention=NeedsAttention(warning_count=2, worst_severity=Severity.ADVICE),
        )
        for index in range(6)
    ]
    review = NightQuality(
        night_id="2026-10-02",
        targets=entries,
        needs_attention=NeedsAttention(warning_count=12, worst_severity=Severity.ADVICE),
        guiding=GuidingNightSummary(available=False, reason="No guiding."),
    )
    assert reply_characters(review) > MAXIMUM_REPLY_CHARACTERS
    fitted = fit_reply_to_budget(review)
    assert reply_characters(fitted) <= MAXIMUM_REPLY_CHARACTERS
    for entry in fitted.targets:
        assert len(entry.statements) == 2
        assert len(entry.statements[0].evidence) + entry.statements[0].evidence_omitted_count == 700
    assert fitted.needs_attention == review.needs_attention
    assert any("cut to fit" in note for note in fitted.notes)


def test_the_night_review_fits_focus_drift_and_estimates_polar_alignment() -> None:
    """Focus drift and polar alignment are worked out for the night."""
    from astrometricslib import frame_subject_id
    from wayfindinglib.models.session.ekos_session import EkosAutofocusRun, EkosSessionContext
    from wayfindinglib.models.session.guiding_run import GuidingRunSummary

    target = make_target(STAR, 4, frames_per_night=40)
    rows = make_rows(target, REVIEWED)
    night_frames = [
        frame
        for frame in target.frames
        if datetime.fromtimestamp(frame.timestamp or 0.0, tz=UTC).date().isoformat() == REVIEWED
    ]
    for index, frame in enumerate(night_frames):
        drop = 0.15 * index
        rows.append(make_row("frame.focuser_temperature_degc", 1, 10.0 - drop, target, frame))
        rows.append(
            make_row("frame.star_width_moffat_px", 1, (2.5**2 + (0.5 * drop) ** 2) ** 0.5, target, frame)
        )
        rows.append(make_row("stacking.frame_registration_rotation_deg", 1, 0.01 * index, target, frame))
    start = night_frames[0].timestamp - 600.0
    contexts = [
        EkosSessionContext(
            id="ctx",
            session_id=REVIEWED,
            started_at=start,
            ended_at=start + 7200.0,
            autofocus_runs=[EkosAutofocusRun(timestamp=start, succeeded=True, filter_name="L")],
        )
    ]
    runs = [
        GuidingRunSummary(
            id="g#1", session_id=REVIEWED, source_file_name="g", written_by_ekos=True, started_at=start
        )
    ]
    context = _context(FakeLibrary([target], rows), {"ekos_session_context": contexts, "guiding_run": runs})

    quality = night_quality_task.build_night_quality(context, REVIEWED)

    assert quality.focus_drift is not None
    assert quality.focus_drift.frames_used > 10
    assert quality.polar_alignment is not None
    assert quality.polar_alignment.exploratory is True
    assert quality.polar_alignment.sample_count == len(night_frames)
    assert frame_subject_id(night_frames[0].path, target.id)


def test_a_night_without_rotation_or_focus_rows_says_so() -> None:
    """Missing inputs give an explicit reason, never a default."""
    target = make_target(STAR, 5)

    quality = _review(FakeLibrary([target], make_rows(target, REVIEWED)))

    assert quality.polar_alignment is None
    assert any("polar-alignment" in note for note in quality.notes)
    assert quality.focus_drift is not None
    assert quality.focus_drift.status == "insufficient_data"


def test_a_target_with_two_goals_states_each_setting_goal() -> None:
    """Decision U2: a grating and an imaging setting are judged apart."""
    from astrometricslib import CaptureSetting, Target
    from wayfindinglib.analytics.result_quality.efficiency import FrameObservation
    from wayfindinglib.models.quality.efficiency import ScienceGoal

    imaging = CaptureSetting(camera="Cam", filter_name="L", exposure_seconds=60.0)
    grating = CaptureSetting(camera="Cam", filter_name="Star Analyzer 200", exposure_seconds=20.0)
    observations = [
        FrameObservation(
            frame_id=f"{name}{index}", capture_setting=setting, night_id="n", exposure_seconds=seconds
        )
        for name, setting, seconds in (("i", imaging, 60.0), ("g", grating, 20.0))
        for index in range(3)
    ]
    target = Target(id="NGC 7331")

    summary, goal = night_quality_task._summary_by_goal(
        "n",
        observations,
        [],
        lambda setting: science_goal_for_setting(target, setting),
        ScienceGoal.FAINT_POINT_SOURCE,
        lambda _setting: None,
        None,
    )

    goals = {item.capture_setting.filter_name: item.science_goal for item in summary.setting_summaries}
    assert goals == {
        "L": ScienceGoal.EXTENDED_OBJECT,
        "Star Analyzer 200": ScienceGoal.SLITLESS_SPECTRAL_RESOLUTION,
    }
    assert goal == ScienceGoal.EXTENDED_OBJECT
    assert summary.loss_account is None
    assert any("different science goals" in note for note in summary.notes)
