"""Purpose: Unit tests for the suggestion refresh and package edits.

Description: Verifies `ObservationPlanning.refresh_suggestions` (rule
suggestions created, updated and deleted by source key and the noise goal,
skipped when a planned package or a queue entry holds the key, never deleted
for missing data, dismissals that hold until the evidence changes, ranking
suggestions that expire, calibration items merged into the checklist, and the
automatic Complete that can revert), the carry-over of missing frames into a
later session, the card progress include of `get_plan`, the suggestion log,
and the new `edit_package` options (framing, dismiss, reopen baseline, row
counting times). Uses a temporary database, a fixed clock and an in-memory
science library, never real data.
"""

from datetime import UTC, datetime, timedelta

import pytest

from astrometricslib import (
    AppConfiguration,
    CaptureSetting,
    FilterType,
    InvalidArgumentError,
    MeasurementOrigin,
    MeasurementReference,
    MeasurementStatus,
    NotFoundError,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from wayfindinglib.analytics.frame_counting import TargetFrames, judgments_by_path
from wayfindinglib.analytics.package_suggestions import DEFAULT_NOISE_GOAL
from wayfindinglib.analytics.session_progress import current_night
from wayfindinglib.analytics.test.frame_counting_helpers import FakeLibrary, judgment_row, make_frame
from wayfindinglib.api.planning import ObservationPlanning
from wayfindinglib.api.test.test_planning import _site, _telescope
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.planning.observation_package import (
    ExposureRequest,
    FrameType,
    ObservationPackage,
    PackageStatus,
)
from wayfindinglib.models.planning.suggestion_log import SuggestionEvent
from wayfindinglib.models.planning.target_report import RecommendedAdditionalFramesStatus
from wayfindinglib.models.planning.tonight_ranking import (
    CandidateStatus,
    RankedCandidate,
    RankedList,
    RankedUse,
    TonightRanking,
    TonightSkyModel,
)
from wayfindinglib.models.quality.statements import (
    ActionClass,
    Confidence,
    ProposedAction,
    Severity,
    StatementKind,
    TargetStatement,
)
from wayfindinglib.models.session.observation_session import ObservationSession, RunState
from wayfindinglib.models.session.session_progress import ObservationSessionWithProgress

NOW = datetime(2026, 10, 24, 20, 0, tzinfo=UTC)
"""The fixed clock: two hours before the first test frame (22:00)."""

NOISE_METRIC = "stacking.empty_aperture_noise_normalized"


@pytest.fixture
def isolated_butler(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> DiskButler:
    """Build a DiskButler backed by a fully isolated temporary database.

    Returns
    -------
    butler : `DiskButler`
        The constructed, isolated butler.
    """
    config_path = tmp_path / "astrometrics.config.toml"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return DiskButler(app_config=config)


class Clock:
    """A clock the tests can move."""

    def __init__(self, now: datetime) -> None:
        """Start at a time.

        Parameters
        ----------
        now : `datetime`
            The first time.
        """
        self.now = now

    def __call__(self) -> datetime:
        """Give the current fake time.

        Returns
        -------
        now : `datetime`
            The time.
        """
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """Replace planning's clock with one the test controls.

    Returns
    -------
    clock : `Clock`
        The clock, set to `NOW`.
    """
    fake = Clock(NOW)
    monkeypatch.setattr("wayfindinglib.api.planning._utc_now", fake)
    return fake


def noise_row(value: float, *, measured_at: datetime = NOW, row_id: int = 900) -> QualityMeasurement:
    """Build the stack noise row of M 81.

    Parameters
    ----------
    value : `float`
        The normalized noise.
    measured_at : `datetime`, optional
        When it was measured.
    row_id : `int`, optional
        The row id.

    Returns
    -------
    row : `QualityMeasurement`
        The row.
    """
    return QualityMeasurement(
        metric=NOISE_METRIC,
        definition_version=3,
        subject=QualitySubject(level=SubjectLevel.STACK, subject_id="M 81/stack", target_id="M 81"),
        status=MeasurementStatus.MEASURED,
        value=value,
        origin=MeasurementOrigin.MEASURED,
        run_id="stack-run",
        measured_at=measured_at,
        row_id=row_id,
    )


def more_frames(frame_count: int = 40, *, filter_name: str = "L", exposure: float = 60.0) -> TargetStatement:
    """Build the "take more frames" recommendation.

    Parameters
    ----------
    frame_count : `int`, optional
        The frames asked for.
    filter_name : `str`, optional
        The capture setting's filter.
    exposure : `float`, optional
        The exposure in seconds.

    Returns
    -------
    statement : `TargetStatement`
        The statement.
    """
    return TargetStatement(
        rule="more_frames",
        kind=StatementKind.RECOMMENDATION,
        action_class=ActionClass.CAPTURE_SETTINGS,
        capture_setting=CaptureSetting(
            camera="Test Camera", telescope="Test Scope", filter_name=filter_name, exposure_seconds=exposure
        ),
        message=f"Take {frame_count} more frames.",
        plain_message=f"Take {frame_count} more frames: we expect less noise.",
        proposed_action=ProposedAction(exposure_seconds=exposure, frame_count=frame_count),
        evidence=[MeasurementReference(row_id=5)],
        confidence=Confidence.PARTLY_MEASURED,
    )


def flats_noisy() -> TargetStatement:
    """Build the warning of a rule that makes a checklist item.

    Returns
    -------
    statement : `TargetStatement`
        The statement.
    """
    return TargetStatement(
        rule="master_flat_noisy",
        kind=StatementKind.WARNING,
        severity=Severity.ADVICE,
        action_class=ActionClass.CALIBRATION,
        capture_setting=CaptureSetting(camera="Test Camera", filter_name="L"),
        message="The master flat is noisy.",
        confidence=Confidence.DESIGN_ESTIMATE,
    )


def stop_warning() -> TargetStatement:
    """Build the warning that more frames will not help.

    Returns
    -------
    statement : `TargetStatement`
        The statement.
    """
    return TargetStatement(
        rule="more_frames_will_not_help",
        kind=StatementKind.WARNING,
        severity=Severity.ADVICE,
        action_class=ActionClass.CAPTURE_SETTINGS,
        message="More frames will not help.",
        confidence=Confidence.PARTLY_MEASURED,
    )


def missing_data() -> TargetStatement:
    """Build the note that the "more frames" rule had no data.

    Returns
    -------
    statement : `TargetStatement`
        The statement.
    """
    return TargetStatement(
        rule="more_frames",
        kind=StatementKind.INSUFFICIENT_DATA,
        message="No forecast yet.",
        reason="Measure the frames first.",
        confidence=Confidence.PARTLY_MEASURED,
    )


class Report:
    """A stand-in for the target dataset report: only the statements."""

    def __init__(self, statements: list[TargetStatement]) -> None:
        """Hold the statements.

        Parameters
        ----------
        statements : `list` [`TargetStatement`]
            The statements.
        """
        self.statements = statements


def ranking(*targets: str, use: RankedUse = RankedUse.IMAGING) -> TonightRanking:
    """Build a ranking in which each target is ranked, in the order given.

    Parameters
    ----------
    *targets : `str`
        The targets, best first.
    use : `RankedUse`, optional
        The list.

    Returns
    -------
    ranking : `TonightRanking`
        The ranking.
    """
    return TonightRanking(
        at=NOW,
        lists=[
            RankedList(
                use=use,
                gain_description="noise cut in one hour",
                candidates=[
                    RankedCandidate(
                        target_id=target,
                        use=use,
                        status=CandidateStatus.RANKED,
                        rank=index + 1,
                        gain_per_hour_fraction=0.2 - index * 0.01,
                    )
                    for index, target in enumerate(targets)
                ],
            )
        ],
        sky=TonightSkyModel(at=NOW),
    )


class Fixture:
    """The planning object under test with the stand-ins the tests change."""

    def __init__(self, planning: ObservationPlanning, library: FakeLibrary) -> None:
        """Hold the planning object and the library.

        Parameters
        ----------
        planning : `ObservationPlanning`
            The object under test.
        library : `FakeLibrary`
            Its science library stand-in.
        """
        self.planning = planning
        self.library = library
        self.statements: dict[str, list[TargetStatement]] = {}
        self.ranking: TonightRanking | None = ranking()
        self.report_errors: set[str] = set()


@pytest.fixture
def fixture(isolated_butler: DiskButler, clock: Clock, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    """Build a planning object whose reports and ranking the test sets.

    Returns
    -------
    fixture : `Fixture`
        The planning object and its stand-ins. M 81 starts with a noise of
        3e-4, above the goal.
    """
    library = FakeLibrary({"M 81": [], "M 13": []}, [noise_row(3e-4)])
    planning = ObservationPlanning(isolated_butler.config, isolated_butler, astrometrics=library)
    made = Fixture(planning, library)

    def fake_advisory(
        kind: str, target: str | None = None, at: object = None, **_arguments: object
    ) -> object:
        """Answer the two advisory kinds the refresh reads.

        Parameters
        ----------
        kind : `str`
            ``"quality"`` or ``"tonight"``.
        target : `str`, optional
            The target of a quality report.
        at : `object`, optional
            Ignored.
        **_arguments : `object`
            Ignored.

        Returns
        -------
        advice : `object`
            A `Report` or the ranking.

        Raises
        ------
        NotFoundError
            For a target in `report_errors`, or when no ranking is set.
        """
        if kind == "tonight":
            if made.ranking is None:
                raise NotFoundError("no ranking")
            return made.ranking
        if target in made.report_errors:
            raise NotFoundError(f"no report for {target}")
        return Report(made.statements.get(target, []))

    monkeypatch.setattr(planning, "get_advisory", fake_advisory)
    return made


def suggested(fixture: Fixture, target: str = "M 81") -> list[ObservationPackage]:
    """List a target's suggested packages, dismissed ones too.

    Parameters
    ----------
    fixture : `Fixture`
        The fixture.
    target : `str`, optional
        The target.

    Returns
    -------
    packages : `list` [`ObservationPackage`]
        The suggested packages of the target.
    """
    return fixture.planning.get_plan(
        kind="packages", status="SUGGESTED", target=target, include_dismissed=True
    )


def night_session(fixture: Fixture, offset_days: int = 0) -> ObservationSession:
    """Make the session of tonight (or another night).

    Parameters
    ----------
    fixture : `Fixture`
        The fixture.
    offset_days : `int`, optional
        Days from tonight.

    Returns
    -------
    session : `ObservationSession`
        The session.
    """
    night = (current_night(NOW) + timedelta(days=offset_days)).isoformat()
    return fixture.planning.create_plan(
        "empty_session", site_profile=_site(), telescope=_telescope(), camera_id="cam1", night_id=night
    )


def frames(count: int, start: int = 0, target: str = "M 81") -> list:
    """Build light frames the test session's equipment took.

    Parameters
    ----------
    count : `int`
        How many frames.
    start : `int`, optional
        The number of the first frame.
    target : `str`, optional
        The target.

    Returns
    -------
    frames : `list` [`FrameRecord`]
        The frames, one minute apart from 22:00.
    """
    return [
        make_frame(start + index, target_id=target, camera="cam1", telescope="t1") for index in range(count)
    ]


def judge(fixture: Fixture, target_frames: list, target: str = "M 81") -> None:
    """Store the frames in the library with a passing judgment each.

    Parameters
    ----------
    fixture : `Fixture`
        The fixture.
    target_frames : `list` [`FrameRecord`]
        The frames.
    target : `str`, optional
        The target.
    """
    fixture.library.frames_by_target[target] = target_frames
    fixture.library.rows = [
        row for row in fixture.library.rows if row.subject.level != SubjectLevel.FRAME
    ] + [judgment_row(frame, target, row_id=index) for index, frame in enumerate(target_frames)]


def log_events(fixture: Fixture) -> list[tuple[str, str]]:
    """Read the suggestion log as event and cause pairs.

    Lines written at the same fake instant have no fixed order, so tests
    check membership, not position.

    Parameters
    ----------
    fixture : `Fixture`
        The fixture.

    Returns
    -------
    events : `list` [`tuple`]
        The event and cause of each line.
    """
    entries = fixture.planning.get_plan(kind="suggestion_log")
    return [(entry.event.value, entry.cause) for entry in reversed(entries)]


# -- Rule suggestions -------------------------------------------------------


def test_a_rule_suggestion_becomes_a_suggested_package_with_a_keyed_row(fixture: Fixture) -> None:
    """A recommendation above the noise goal makes a suggested package."""
    fixture.statements["M 81"] = [more_frames(40)]
    change = fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    [package] = suggested(fixture)
    assert change.added == [package.id]
    assert package.status == PackageStatus.SUGGESTED
    [row] = package.exposure_requests
    assert (row.frame_type, row.count, row.exposure_sec) == (FrameType.LIGHT, 40, 60.0)
    assert row.source_key.startswith("more_frames|M 81|")
    assert row.reason == "Take 40 more frames: we expect less noise."
    assert row.evidence_fingerprint
    assert package.suggestion.confidence == "partly_measured"
    entry = fixture.planning.get_plan(kind="suggestion_log")[0]
    assert entry.event == SuggestionEvent.CREATED
    assert sorted(entry.evidence_row_ids) == [5, 900]
    assert entry.values["noise"] == pytest.approx(3e-4)
    assert entry.values["noise_goal"] == pytest.approx(DEFAULT_NOISE_GOAL)


def test_a_second_refresh_with_the_same_evidence_changes_nothing(fixture: Fixture) -> None:
    """Nothing is written when the evidence is the same."""
    fixture.statements["M 81"] = [more_frames(40)]
    fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    revision = suggested(fixture)[0].revision
    change = fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    assert change.is_empty
    assert suggested(fixture)[0].revision == revision


def test_a_changed_proposal_updates_the_row_and_the_package(fixture: Fixture) -> None:
    """A new frame count updates the row (and the log says so)."""
    fixture.statements["M 81"] = [more_frames(40)]
    fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    fixture.statements["M 81"] = [more_frames(120)]
    change = fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    [package] = suggested(fixture)
    assert change.updated == [package.id]
    assert package.exposure_requests[0].count == 120
    assert package.revision == 1
    assert "updated" in [event for event, _cause in log_events(fixture)]


def test_a_target_below_the_noise_goal_gets_no_suggestion_and_loses_its_old_one(fixture: Fixture) -> None:
    """A noise at or below the goal deletes the suggestion."""
    fixture.statements["M 81"] = [more_frames(40)]
    fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    fixture.library.rows = [noise_row(DEFAULT_NOISE_GOAL * 0.9)]
    change = fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    assert len(change.deleted) == 1 and suggested(fixture) == []
    assert ("deleted", "the noise goal is met") in log_events(fixture)
    assert fixture.planning.refresh_suggestions(["M 81"], register_job=False).added == []


def test_a_stop_rule_deletes_the_suggestion(fixture: Fixture) -> None:
    """A rule that says more frames will not help removes the suggestion."""
    fixture.statements["M 81"] = [more_frames(40)]
    fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    fixture.statements["M 81"] = [more_frames(40), stop_warning()]
    change = fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    assert len(change.deleted) == 1
    assert ("deleted", "a rule says more frames will not help") in log_events(fixture)


def test_missing_data_never_deletes_a_suggestion(fixture: Fixture) -> None:
    """Missing data leaves the suggestion alone."""
    fixture.statements["M 81"] = [more_frames(40)]
    fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    [package] = suggested(fixture)
    # The rules had no data this time.
    fixture.statements["M 81"] = [missing_data()]
    assert fixture.planning.refresh_suggestions(["M 81"], register_job=False).is_empty
    # The report could not be built.
    fixture.report_errors.add("M 81")
    change = fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    assert change.is_empty and any("no report" in note for note in change.notes)
    fixture.report_errors.clear()
    # The noise is unknown.
    fixture.statements["M 81"] = [more_frames(40)]
    fixture.library.rows = []
    assert fixture.planning.refresh_suggestions(["M 81"], register_job=False).is_empty
    assert [item.id for item in suggested(fixture)] == [package.id]


def test_no_noise_reading_creates_no_suggestion(fixture: Fixture) -> None:
    """Without a noise reading nothing is suggested."""
    fixture.library.rows = []
    fixture.statements["M 81"] = [more_frames(40)]
    assert fixture.planning.refresh_suggestions(["M 81"], register_job=False).added == []


def test_a_key_held_by_a_planned_package_is_not_suggested(fixture: Fixture) -> None:
    """A planned package holding the source key blocks a suggestion."""
    fixture.statements["M 81"] = [more_frames(40)]
    fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    [package] = suggested(fixture)
    fixture.planning.edit_queue(night_session(fixture).id, add=[{"package_id": package.id}])
    assert fixture.planning.get_plan(kind="packages", status="PLANNED")[0].id == package.id
    change = fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    assert change.added == [] and suggested(fixture) == []
    assert ("promoted", "added to a session") in log_events(fixture)


def test_a_key_held_by_a_queue_entry_is_not_suggested(fixture: Fixture) -> None:
    """A queue entry holding the source key blocks a suggestion."""
    fixture.statements["M 81"] = [more_frames(40)]
    fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    [package] = suggested(fixture)
    session = night_session(fixture)
    fixture.planning.edit_queue(session.id, add=[{"package_id": package.id}])
    fixture.planning.edit_package(package.id, delete=True)
    change = fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    assert change.added == [] and suggested(fixture) == []


def test_a_complete_package_holds_its_key_until_a_newer_stack(fixture: Fixture) -> None:
    """A complete package blocks its key only until the stack is made again."""
    fixture.statements["M 81"] = [more_frames(2)]
    fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    [package] = suggested(fixture)
    fixture.planning.edit_queue(night_session(fixture).id, add=[{"package_id": package.id}])
    judge(fixture, frames(2))
    change = fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    assert change.completed == [package.id]
    fixture.library.rows.append(noise_row(3e-4, measured_at=NOW))
    assert fixture.planning.refresh_suggestions(["M 81"], register_job=False).added == []
    fixture.library.rows = [row for row in fixture.library.rows if row.subject.level != SubjectLevel.STACK]
    fixture.library.rows.append(noise_row(2.5e-4, measured_at=NOW + timedelta(days=1)))
    assert len(fixture.planning.refresh_suggestions(["M 81"], register_job=False).added) == 1


# -- Dismissal ---------------------------------------------------------------


def test_a_dismissed_suggestion_stays_hidden_until_the_evidence_changes(fixture: Fixture) -> None:
    """A dismissal holds until the evidence changes."""
    fixture.statements["M 81"] = [more_frames(118)]
    fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    [package] = suggested(fixture)
    fixture.planning.edit_package(package.id, dismiss=True)
    assert fixture.planning.get_plan(kind="packages", status="SUGGESTED") == []
    # The same evidence: still dismissed, nothing changes.
    assert fixture.planning.refresh_suggestions(["M 81"], register_job=False).is_empty
    assert fixture.planning.get_plan(kind="packages", status="SUGGESTED") == []
    # A small change in the count (118 to 121) rounds to the same evidence.
    fixture.statements["M 81"] = [more_frames(121)]
    fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    assert fixture.planning.get_plan(kind="packages", status="SUGGESTED") == []
    # A different proposal is new evidence: the suggestion comes back.
    fixture.statements["M 81"] = [more_frames(200)]
    change = fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    assert change.updated == [package.id]
    [shown] = fixture.planning.get_plan(kind="packages", status="SUGGESTED")
    assert shown.exposure_requests[0].count == 200 and not shown.suggestion.dismissed_fingerprint


def test_undismiss_brings_a_suggestion_back(fixture: Fixture) -> None:
    """Undismiss shows the package again with no new evidence."""
    fixture.statements["M 81"] = [more_frames(40)]
    fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    [package] = suggested(fixture)
    fixture.planning.edit_package(package.id, dismiss=True)
    fixture.planning.edit_package(package.id, undismiss=True)
    assert [item.id for item in fixture.planning.get_plan(kind="packages", status="SUGGESTED")] == [
        package.id
    ]
    events = [event for event, _cause in log_events(fixture)]
    assert "dismissed" in events and "undismissed" in events


def test_one_row_can_be_dismissed_and_restored(fixture: Fixture) -> None:
    """A dismissed row leaves, stays out, and returns on undismiss."""
    fixture.statements["M 81"] = [more_frames(40), more_frames(30, filter_name="R")]
    fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    [package] = suggested(fixture)
    key = package.exposure_requests[1].source_key
    edited = fixture.planning.edit_package(package.id, dismiss=True, source_key=key)
    assert [row.source_key for row in edited.exposure_requests] == [package.exposure_requests[0].source_key]
    assert fixture.planning.refresh_suggestions(["M 81"], register_job=False).is_empty
    assert len(suggested(fixture)[0].exposure_requests) == 1
    restored = fixture.planning.edit_package(package.id, undismiss=True, source_key=key)
    assert len(restored.exposure_requests) == 2


def test_dismiss_is_refused_for_a_planned_package_and_mixed_arguments(fixture: Fixture) -> None:
    """Only a suggestion can be dismissed, and not with a change."""
    from astrometricslib import ConflictError

    package = fixture.planning.create_plan("package", target="M 81")
    with pytest.raises(ConflictError):
        fixture.planning.edit_package(package.id, dismiss=True)
    with pytest.raises(InvalidArgumentError):
        fixture.planning.edit_package(package.id, dismiss=True, priority=2)
    with pytest.raises(InvalidArgumentError):
        fixture.planning.edit_package(package.id, source_key="x", priority=2)


# -- Ranking suggestions -----------------------------------------------------


def test_the_top_three_of_each_use_become_tentative_packages_for_tonight(fixture: Fixture) -> None:
    """Tonight's top three become Tentative packages with no rows."""
    fixture.ranking = ranking("M 81", "M 13", "NGC 1", "NGC 2")
    change = fixture.planning.refresh_suggestions(register_job=False)
    packages = fixture.planning.get_plan(kind="packages", status="SUGGESTED")
    assert sorted(package.target_id for package in packages) == ["M 13", "M 81", "NGC 1"]
    assert len(change.added) == 3
    night = current_night(NOW).isoformat()
    for package in packages:
        assert package.exposure_requests == []
        assert package.suggestion.source_kind == "ranking"
        assert package.suggestion.confidence == "design_estimate"
        assert package.suggestion.observing_night_id == night
        assert package.suggestion.source_key == f"ranking|{package.target_id}|imaging|{night}"
        assert "roughly" in package.suggestion.reason
    assert fixture.planning.refresh_suggestions(register_job=False).added == []
    assert len(fixture.planning.get_plan(kind="packages", status="SUGGESTED")) == 3


def test_an_provisional_count_is_said_in_the_ranking_suggestion(fixture: Fixture) -> None:
    """An provisional count is said in the suggestion's reason."""
    made = ranking("M 81", "M 13")
    candidates = made.lists[0].candidates
    candidates[1] = candidates[1].model_copy(
        update={"recommended_additional_frames_status": RecommendedAdditionalFramesStatus.PROVISIONAL}
    )
    fixture.ranking = made
    fixture.planning.refresh_suggestions(register_job=False)
    reasons = {
        package.target_id: package.suggestion.reason
        for package in fixture.planning.get_plan(kind="packages", status="SUGGESTED")
    }
    assert "provisional" not in reasons["M 81"]
    assert (
        "Its count of recommended additional frames is provisional, so it ranks below a confirmed one"
        in reasons["M 13"]
    )


def test_a_ranking_suggestion_is_removed_when_it_falls_out_of_the_top(fixture: Fixture) -> None:
    """A target that drops out of tonight's top three loses its suggestion."""
    fixture.ranking = ranking("M 81", "M 13", "NGC 1")
    fixture.planning.refresh_suggestions(register_job=False)
    fixture.ranking = ranking("M 81", "M 13", "NGC 2")
    change = fixture.planning.refresh_suggestions(register_job=False)
    remaining = {
        package.target_id for package in fixture.planning.get_plan(kind="packages", status="SUGGESTED")
    }
    assert remaining == {"M 81", "M 13", "NGC 2"} and len(change.deleted) == 1


def test_ranking_suggestions_expire_after_their_night(fixture: Fixture, clock: Clock) -> None:
    """A ranking suggestion is deleted once its night has ended."""
    fixture.ranking = ranking("M 81")
    fixture.planning.refresh_suggestions(register_job=False)
    clock.now = NOW + timedelta(days=1)
    change = fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    assert len(change.deleted) == 1
    assert fixture.planning.get_plan(kind="packages", status="SUGGESTED") == []
    assert ("deleted", "the night ended") in log_events(fixture)


def test_a_failed_ranking_leaves_ranking_suggestions_alone(fixture: Fixture) -> None:
    """A failed ranking deletes nothing (missing data)."""
    fixture.ranking = ranking("M 81")
    fixture.planning.refresh_suggestions(register_job=False)
    fixture.ranking = None
    change = fixture.planning.refresh_suggestions(register_job=False)
    assert change.deleted == [] and any("No ranking" in note for note in change.notes)
    assert len(fixture.planning.get_plan(kind="packages", status="SUGGESTED")) == 1


def test_ranking_skips_a_target_already_queued_tonight_and_a_held_key(fixture: Fixture) -> None:
    """A queued target or a held key is not suggested."""
    held = fixture.planning.create_plan("package", target="M 81")
    session = night_session(fixture)
    fixture.planning.edit_queue(session.id, add=[{"package_id": held.id}])
    fixture.ranking = ranking("M 81", "M 13")
    fixture.planning.refresh_suggestions(register_job=False)
    targets = {
        package.target_id for package in fixture.planning.get_plan(kind="packages", status="SUGGESTED")
    }
    assert targets == {"M 13"}


# -- Calibration suggestions -------------------------------------------------


def test_a_calibration_statement_adds_a_checklist_item_merged_by_key(fixture: Fixture) -> None:
    """A rule that makes a checklist item adds it to tonight's session once."""
    session = night_session(fixture)
    fixture.statements["M 81"] = [flats_noisy()]
    fixture.statements["M 13"] = [flats_noisy()]
    change = fixture.planning.refresh_suggestions(register_job=False)
    stored = fixture.planning.get_plan(session.id)
    assert [item.rule for item in stored.checklist] == ["master_flat_noisy"]
    assert stored.checklist[0].label == "The master flat is noisy."
    assert change.checklist_item_ids == [stored.checklist[0].item_id]
    again = fixture.planning.refresh_suggestions(register_job=False)
    assert again.checklist_item_ids == []
    assert len(fixture.planning.get_plan(session.id).checklist) == 1


def test_an_item_stored_without_a_label_reads_back_with_its_rules_purpose(fixture: Fixture) -> None:
    """An old item reads back with its rule's purpose, else the rule name."""
    from wayfindinglib.analytics.result_quality.all_rules import ALL_RULES
    from wayfindinglib.models.session.observation_session import ChecklistItem

    session = night_session(fixture)
    fixture.planning.edit_queue(
        session.id,
        add_checklist=[ChecklistItem(rule="master_flat_noisy"), ChecklistItem(rule="no_such_rule")],
    )
    labels = [item.label for item in fixture.planning.get_plan(session.id).checklist]
    purpose = next(rule.purpose for rule in ALL_RULES if rule.name == "master_flat_noisy")
    assert labels == [purpose, "no_such_rule"]


def test_a_calibration_statement_without_a_session_leaves_a_note(fixture: Fixture) -> None:
    """With no session tonight, nothing is added and the note says why."""
    fixture.statements["M 81"] = [flats_noisy()]
    change = fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    assert change.checklist_item_ids == []
    assert any("no session" in note for note in change.notes)


# -- Complete and carry-over -------------------------------------------------


def _planned_package_in_session(
    fixture: Fixture, count: int = 3
) -> tuple[ObservationPackage, ObservationSession]:
    """Make a planned package of one light row and queue it tonight.

    Parameters
    ----------
    fixture : `Fixture`
        The fixture.
    count : `int`, optional
        The frames the row asks for.

    Returns
    -------
    package, session : `ObservationPackage`, `ObservationSession`
        The package and its session.
    """
    package = fixture.planning.create_plan(
        "package",
        target="M 81",
        exposure_requests=[
            ExposureRequest(frame_type=FrameType.LIGHT, filter=FilterType.L, exposure_sec=60.0, count=count)
        ],
    )
    session = night_session(fixture)
    fixture.planning.edit_queue(session.id, add=[{"package_id": package.id}])
    return package, session


def test_a_package_completes_when_its_frames_are_judged(fixture: Fixture) -> None:
    """Judged frames that meet every light row complete a package."""
    package, _session = _planned_package_in_session(fixture, 3)
    judge(fixture, frames(3))
    change = fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    stored = fixture.planning.get_plan(kind="packages", status="COMPLETE")[0]
    assert change.completed == [package.id] and stored.completed_at is not None
    assert ("completed", "every light row is captured") in log_events(fixture)


def test_frames_not_yet_judged_do_not_complete_a_package(fixture: Fixture) -> None:
    """Captured frames with no judgment leave the package planned."""
    _planned_package_in_session(fixture, 3)
    fixture.library.frames_by_target["M 81"] = frames(3)
    assert fixture.planning.refresh_suggestions(["M 81"], register_job=False).completed == []


def test_complete_reverts_when_frames_are_set_aside(fixture: Fixture) -> None:
    """A complete package goes back to planned when a frame is set aside."""
    package, _session = _planned_package_in_session(fixture, 3)
    judge(fixture, frames(3))
    fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    judge(fixture, frames(3)[:2])
    change = fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    assert change.reverted == [package.id]
    stored = fixture.planning.get_plan(kind="packages")[0]
    assert stored.status == PackageStatus.PLANNED and stored.completed_at is None


def test_an_unreadable_target_does_not_revert_a_complete_package(fixture: Fixture) -> None:
    """Missing frame data leaves a complete package complete."""
    _package, _session = _planned_package_in_session(fixture, 3)
    judge(fixture, frames(3))
    fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    fixture.library.read_failures.add("M 81")
    change = fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    assert change.reverted == [] and any("could not be read" in note for note in change.notes)
    assert fixture.planning.get_plan(kind="packages")[0].status == PackageStatus.COMPLETE


def test_a_package_added_to_a_later_session_asks_only_for_missing_frames(fixture: Fixture) -> None:
    """Carry-over drops met rows and keeps the missing count."""
    package = fixture.planning.create_plan(
        "package",
        target="M 81",
        exposure_requests=[
            ExposureRequest(frame_type=FrameType.LIGHT, filter=FilterType.L, exposure_sec=60.0, count=10),
            ExposureRequest(frame_type=FrameType.LIGHT, filter=FilterType.R, exposure_sec=60.0, count=2),
        ],
    )
    judge(
        fixture,
        frames(4)
        + [make_frame(20 + n, filter_value=FilterType.R, camera="cam1", telescope="t1") for n in range(2)],
    )
    later = night_session(fixture, offset_days=1)
    change = fixture.planning.edit_queue(later.id, add=[{"package_id": package.id}])
    [entry] = change.session.queue
    assert [(row.filter, row.count) for row in entry.exposure_requests] == [(FilterType.L, 6)]
    stored = fixture.planning.get_plan(kind="packages")[0]
    assert [row.count for row in stored.exposure_requests] == [10, 2]


def test_a_package_with_nothing_captured_keeps_all_its_rows_in_a_session(fixture: Fixture) -> None:
    """With no frames captured, the entry copies the package's own rows."""
    _package, session = _planned_package_in_session(fixture, 10)
    [entry] = fixture.planning.get_plan(session.id).queue
    assert [row.count for row in entry.exposure_requests] == [10]


# -- Package edits -----------------------------------------------------------


def test_edit_package_sets_the_framing(fixture: Fixture) -> None:
    """Edit_package stores the framing the Planetarium sends."""
    package = fixture.planning.create_plan("package", target="M 81")
    edited = fixture.planning.edit_package(
        package.id, framing={"center_ra_deg": 148.9, "center_dec_deg": 69.1, "rotation_deg": 12.0}
    )
    assert edited.framing.center_ra_deg == pytest.approx(148.9) and edited.revision == 1
    assert fixture.planning.get_plan(kind="packages")[0].framing.rotation_deg == pytest.approx(12.0)
    with pytest.raises(InvalidArgumentError):
        fixture.planning.edit_package(package.id, framing={"center_ra_deg": 1.0})


def test_a_new_row_starts_counting_now_and_an_unchanged_row_keeps_its_time(
    fixture: Fixture, clock: Clock
) -> None:
    """A changed row restarts its counting time; a new count does not."""
    package = fixture.planning.create_plan(
        "package",
        target="M 81",
        exposure_requests=[
            ExposureRequest(frame_type=FrameType.LIGHT, filter=FilterType.L, exposure_sec=60.0, count=5)
        ],
    )
    created = package.exposure_requests[0].counted_from
    assert created == NOW
    clock.now = NOW + timedelta(hours=3)
    edited = fixture.planning.edit_package(
        package.id,
        exposure_requests=[
            ExposureRequest(frame_type=FrameType.LIGHT, filter=FilterType.L, exposure_sec=60.0, count=9),
            ExposureRequest(frame_type=FrameType.LIGHT, filter=FilterType.R, exposure_sec=60.0, count=3),
        ],
    )
    assert edited.exposure_requests[0].counted_from == NOW
    assert edited.exposure_requests[1].counted_from == NOW + timedelta(hours=3)


def test_reopen_stores_a_baseline_so_old_frames_do_not_complete_it_again(fixture: Fixture) -> None:
    """Reopening stores a baseline so old frames do not complete it."""
    package, _session = _planned_package_in_session(fixture, 3)
    judge(fixture, frames(3))
    fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    reopened = fixture.planning.edit_package(package.id, reopen=True)
    assert reopened.status == PackageStatus.PLANNED
    assert reopened.exposure_requests[0].baseline_captured == 3
    assert fixture.planning.refresh_suggestions(["M 81"], register_job=False).completed == []
    judge(fixture, frames(6))
    assert fixture.planning.refresh_suggestions(["M 81"], register_job=False).completed == [package.id]


def test_reopen_with_no_session_counts_from_now(fixture: Fixture, clock: Clock) -> None:
    """A package in no session reopens with rows counting from now."""
    package = fixture.planning.create_plan(
        "package",
        target="M 81",
        exposure_requests=[
            ExposureRequest(frame_type=FrameType.LIGHT, filter=FilterType.L, exposure_sec=60.0, count=3)
        ],
    )
    stored = fixture.planning._butler.get("observation_package", {"id": package.id})
    stored.status = PackageStatus.COMPLETE
    fixture.planning._butler.put(stored, "observation_package", {"id": package.id})
    clock.now = NOW + timedelta(hours=1)
    reopened = fixture.planning.edit_package(package.id, reopen=True)
    assert reopened.exposure_requests[0].counted_from == clock.now
    assert reopened.exposure_requests[0].baseline_captured == 0


# -- Reads --------------------------------------------------------------------


def test_get_plan_can_include_the_card_progress(fixture: Fixture) -> None:
    """`include=["progress"]` adds the entry counts and a run state."""
    _package, session = _planned_package_in_session(fixture, 4)
    judge(fixture, frames(2))
    plain = fixture.planning.get_plan(session.id)
    assert not isinstance(plain, ObservationSessionWithProgress)
    with_progress = fixture.planning.get_plan(session.id, include=["progress"])
    assert isinstance(with_progress, ObservationSessionWithProgress)
    [entry] = with_progress.progress.entries
    assert (entry.requested, entry.captured) == (4, 2)
    assert with_progress.progress.run_state in (RunState.UPCOMING, RunState.IN_PROGRESS, RunState.IMAGING)
    by_night = fixture.planning.get_plan(night_id=session.night_date.isoformat(), include=["progress"])
    assert by_night.progress.entries[0].entry_id == entry.entry_id


def test_the_session_list_can_include_the_run_state_including_not_run(fixture: Fixture) -> None:
    """A past night that never ran reads "not run" in the session list."""
    past = night_session(fixture, offset_days=-3)
    package = fixture.planning.create_plan(
        "package",
        target="M 81",
        exposure_requests=[
            ExposureRequest(frame_type=FrameType.LIGHT, filter=FilterType.L, exposure_sec=60.0, count=4)
        ],
    )
    fixture.planning.edit_queue(past.id, add=[{"package_id": package.id}])
    summaries = fixture.planning.get_plan(include=["progress"])
    assert [summary.run_state for summary in summaries] == [RunState.NOT_RUN]
    assert fixture.planning.get_plan()[0].run_state is None


def test_include_is_refused_where_it_does_not_apply(fixture: Fixture) -> None:
    """`include` is for session reads only, and only "progress" is known."""
    with pytest.raises(InvalidArgumentError):
        fixture.planning.get_plan(kind="packages", include=["progress"])
    with pytest.raises(InvalidArgumentError):
        fixture.planning.get_plan(include=["everything"])
    with pytest.raises(InvalidArgumentError):
        fixture.planning.get_plan(kind="sessions", include_dismissed=True)


def test_pick_one_gives_the_planned_package_else_the_suggested_one(fixture: Fixture) -> None:
    """`pick="one"` prefers planned, skips dismissed, matches names loosely."""

    def pick(name: str) -> ObservationPackage | None:
        """Pick the one package of a target.

        Returns
        -------
        package : `ObservationPackage` or `None`
            What the library picks.
        """
        return fixture.planning.get_plan(kind="packages", target=name, pick="one")

    assert pick("M 81") is None
    fixture.statements["M 81"] = [more_frames(40)]
    fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    [suggestion] = suggested(fixture)
    assert pick("m_81").id == suggestion.id
    fixture.planning.edit_package(suggestion.id, dismiss=True)
    assert pick("M 81") is None
    fixture.planning.edit_package(suggestion.id, undismiss=True)
    planned = fixture.planning.create_plan("package", target="M 81")
    assert pick("M 81").id == planned.id
    with pytest.raises(InvalidArgumentError):
        fixture.planning.get_plan(kind="packages", pick="one")
    with pytest.raises(InvalidArgumentError):
        fixture.planning.get_plan(kind="sessions", pick="one")
    with pytest.raises(InvalidArgumentError):
        fixture.planning.get_plan(kind="packages", target="M 81", pick="two")


def test_the_suggestion_log_can_be_read_for_one_target(fixture: Fixture) -> None:
    """The log lists lines newest first and can be narrowed to a target."""
    fixture.statements["M 81"] = [more_frames(40)]
    fixture.planning.refresh_suggestions(["M 81"], register_job=False)
    assert len(fixture.planning.get_plan(kind="suggestion_log", target="M 81")) == 1
    assert fixture.planning.get_plan(kind="suggestion_log", target="M 13") == []


def test_the_judgments_helper_is_used_by_the_reader() -> None:
    """The judgments of a target with no rows are empty (nothing is judged)."""
    assert judgments_by_path("M 81", [make_frame(0)], []) == {}
    assert TargetFrames().frames == []


def test_a_conditional_delete_checks_the_status_and_the_revision(fixture: Fixture) -> None:
    """A conditional delete checks the status and the revision."""
    butler = fixture.planning._butler
    package = fixture.planning.create_plan("package", target="M 81")
    assert not butler.delete_package(package.id, only_status="SUGGESTED")
    assert not butler.delete_package(package.id, expected_revision=4)
    assert butler.delete_package(package.id, expected_revision=0, only_status="PLANNED")
    assert not butler.delete_package(package.id)
