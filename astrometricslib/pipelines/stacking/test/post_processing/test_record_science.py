"""Tests for the measurements of the expected noise, flat and peaks.

Each builder must emit every metric it declares, mark estimated and unmeasured
inputs honestly, and give a reason for every value it cannot store. The
joining code (`compute_expected_noise`, `compute_stack_science`) is tested
with a fake store, camera profile and diagnostics, including its failure
paths: no failure may stop a stack or drop the other rows.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.models.camera_profile import ProvenancedValue, ProvenanceKind, ValueProvenance
from astrometricslib.models.quality.measurement import (
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.models.quality.quality_summary import ExposureGroupSummary
from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject_id
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.stacking.post_processing.expected_noise import (
    ExpectedNoise,
    ExpectedNoiseOutcome,
)
from astrometricslib.pipelines.stacking.post_processing.flat_noise_contribution import (
    FlatNoiseContribution,
    FlatNoiseOutcome,
)
from astrometricslib.pipelines.stacking.post_processing.record_science import (
    CLIPPED_EXPOSURE_GROUPS_REASON,
    SPECTRAL_STACK_REASON,
    StackScience,
    compute_expected_noise,
    compute_stack_science,
    expected_noise_measurements,
    flat_noise_measurements,
    group_peak_measurements,
    group_reference_exposure_measurements,
    science_measurements,
)
from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
    GroupReference,
    StackMember,
    StackMembership,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import StackNoiseOutcome
from astrometricslib.pipelines.stacking.test.conformance_fixture import (
    fixture_science,
    fixture_summary,
    stacking_emitted_measurements,
)

CONTEXT = PipelineRunContext(
    "M 99", "stacking", "1.3.0", "job-1", "job-1", datetime(2026, 10, 22, tzinfo=UTC)
)
SUBJECT = QualitySubject(level=SubjectLevel.STACK, subjectId="M 99/stack.fits", targetId="M 99")


def _expected(assumed_reason: str = "") -> ExpectedNoiseOutcome:
    """Build an expected-noise result.

    Parameters
    ----------
    assumed_reason : `str`, optional
        Why the numbers are assumed.

    Returns
    -------
    outcome : `ExpectedNoiseOutcome`
        A result for two frames.
    """
    return ExpectedNoiseOutcome(ExpectedNoise(1e-4, 5e-5, 1.1e-4, 59.4, 1 / 65535, 2, 0.006, assumed_reason))


def test_the_expected_noise_stores_its_parts_and_never_the_ratio() -> None:
    """Six rows: three noises, area, unit scale and predicted sky level."""
    rows = expected_noise_measurements(_expected(), CONTEXT, SUBJECT)
    assert [row.metric for row in rows] == [
        "stacking.expected_sky_noise_stack_unit",
        "stacking.expected_read_noise_stack_unit",
        "stacking.expected_empty_aperture_noise_stack_unit",
        "stacking.expected_noise_aperture_area_px",
        "stacking.expected_noise_scale_stack_unit_per_adu",
        "stacking.expected_sky_level_stack_unit",
    ]
    assert all(row.status == MeasurementStatus.MEASURED and row.sample_count == 2 for row in rows)
    assert all(row.reason == "" for row in rows)
    assert not any("ratio" in row.metric for row in rows)


def test_an_assumed_gain_or_sky_makes_every_expected_row_assumed() -> None:
    """The reason names what is assumed."""
    rows = expected_noise_measurements(_expected("the gain is assumed"), CONTEXT, SUBJECT)
    assert all(
        row.status == MeasurementStatus.ASSUMED and row.reason == "the gain is assumed" for row in rows
    )


def test_no_expected_noise_gives_not_measured_rows_with_the_reason() -> None:
    """Every row says why there is no number."""
    rows = expected_noise_measurements(
        ExpectedNoiseOutcome(None, "no stored sky measurement"), CONTEXT, SUBJECT
    )
    assert len(rows) == 6
    assert all(
        row.status == MeasurementStatus.NOT_MEASURED and row.reason == "no stored sky measurement"
        for row in rows
    )


def test_the_flat_rows_hold_the_sky_level_and_both_noise_values() -> None:
    """Three rows, in the units of the stack's noise."""
    rows = flat_noise_measurements(
        FlatNoiseOutcome(FlatNoiseContribution(0.004, 3e-5, 0.006)), 0.006, CONTEXT, SUBJECT
    )
    assert [row.metric for row in rows] == [
        "stacking.stack_sky_level_stack_unit",
        "stacking.flat_noise_after_smoothing_fraction",
        "stacking.flat_noise_in_aperture_stack_unit",
    ]
    assert [row.value for row in rows] == [0.006, 0.004, 3e-5]


def test_a_stack_without_flats_gives_not_measured_flat_rows_but_keeps_the_sky_level() -> None:
    """The reason names the missing flats; the sky level is still stored."""
    rows = flat_noise_measurements(
        FlatNoiseOutcome(None, "the stack used no flat frames"), 0.006, CONTEXT, SUBJECT
    )
    assert rows[0].value == pytest.approx(0.006)
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in rows[1:])
    assert rows[1].reason == "the stack used no flat frames"


CHECKED_RANGE = 80.0


def _group(**fields: Any) -> ExposureGroupSummary:
    """Build an exposure group entry with peak values.

    Parameters
    ----------
    **fields : `Any`
        Fields of `ExposureGroupSummary`, by their Python names. The base is
        a group of 60 s with three sampled frames, a peak of half the room
        and a measured profile.

    Returns
    -------
    group : `ExposureGroupSummary`
        The entry.
    """
    base: dict[str, Any] = {
        "exposure_seconds": 60.0,
        "frames_submitted": 10,
        "frames_stacked": 10,
        "dark_applied": True,
        "brightest_star_peak_fraction": 0.5,
        "brightest_star_peak_above_sky_adu": 32000.0,
        "peak_sky_adu": 500.0,
        "peak_clip_ceiling_adu": 65532.0,
        "peak_linearity_limit_adu": 60000.0,
        "peak_frames_with_peak": 3,
        "peak_frames_sampled": 3,
        "peak_frames_on_profile_ceiling": 3,
        "peak_clip_ceiling_provenance": "measured",
        "peak_linearity_limit_provenance": "measured",
        "peak_checked_range_room_multiples": CHECKED_RANGE,
    }
    base.update(fields)
    return ExposureGroupSummary(**base)


def _by_metric(rows: list[QualityMeasurement]) -> dict[str, QualityMeasurement]:
    """Index rows by metric name.

    Parameters
    ----------
    rows : `list` of `QualityMeasurement`
        One group's rows.

    Returns
    -------
    index : `dict`
        The rows by metric name.
    """
    return {row.metric: row for row in rows}


def test_a_measured_peak_stores_its_parts_and_the_frame_count() -> None:
    """The fraction, its parts, the limit and the two counts."""
    rows = group_peak_measurements(_group(), CONTEXT, SUBJECT)
    assert [row.value for row in rows] == [0.5, 32000.0, 500.0, 65532.0, 60000.0, 0.0, 0.0]
    assert all(row.status == MeasurementStatus.MEASURED for row in rows)
    assert rows[0].sample_count == 3
    assert rows[-1].metric == "stacking.group_clipped_frame_count"
    assert rows[-1].sample_count == 3


def test_an_estimate_inside_the_checked_range_is_assumed_with_the_checks_error() -> None:
    """A clipped star's peak and fraction are assumed; the rest are not."""
    group = _group(
        brightest_star_peak_fraction=1.3,
        brightest_star_peak_above_sky_adu=84000.0,
        peak_frames_estimated=3,
        peak_frames_clipped=3,
        peak_frames_on_profile_ceiling=0,
        peak_largest_estimate_to_room_ratio=1.4,
    )
    rows = group_peak_measurements(group, CONTEXT, SUBJECT)
    assert rows[0].value == pytest.approx(1.3)
    assert rows[0].status == MeasurementStatus.ASSUMED
    assert "3 of 3" in rows[0].reason
    assert "checked" in rows[0].reason
    assert rows[1].status == MeasurementStatus.ASSUMED
    assert [rows[index].status for index in (2, 3, 4, 5, 6)] == [MeasurementStatus.MEASURED] * 5
    assert rows[5].value == pytest.approx(3.0)
    assert rows[6].value == pytest.approx(3.0)


def test_an_estimate_beyond_the_checked_range_is_not_measured_with_its_size() -> None:
    """Beyond the range the fraction and the peak are not stored."""
    group = _group(
        brightest_star_peak_fraction=80.0,
        brightest_star_peak_above_sky_adu=5e6,
        peak_frames_estimated=3,
        peak_frames_clipped=3,
        peak_frames_on_profile_ceiling=0,
        peak_largest_estimate_to_room_ratio=CHECKED_RANGE + 40.0,
    )
    rows = _by_metric(group_peak_measurements(group, CONTEXT, SUBJECT))
    fraction = rows["stacking.group_brightest_star_peak_fraction"]
    assert fraction.status == MeasurementStatus.NOT_MEASURED
    assert "beyond the checked range" in fraction.reason
    assert f"{CHECKED_RANGE + 40.0:.0f} x" in fraction.reason
    assert rows["stacking.group_brightest_star_peak_above_sky_adu"].status == MeasurementStatus.NOT_MEASURED
    assert rows["stacking.group_clipped_frame_count"].value == pytest.approx(3.0)


def test_the_edge_of_the_checked_range_is_inside_it() -> None:
    """An estimate at the largest checked size is still assumed."""
    group = _group(
        peak_frames_estimated=1,
        peak_frames_clipped=1,
        peak_largest_estimate_to_room_ratio=CHECKED_RANGE,
    )
    rows = _by_metric(group_peak_measurements(group, CONTEXT, SUBJECT))
    assert rows["stacking.group_brightest_star_peak_fraction"].status == MeasurementStatus.ASSUMED


def test_a_datasheet_ceiling_or_limit_is_assumed_not_measured() -> None:
    """A datasheet or generic profile number is labeled assumed."""
    group = _group(peak_clip_ceiling_provenance="datasheet", peak_linearity_limit_provenance="assumed")
    rows = _by_metric(group_peak_measurements(group, CONTEXT, SUBJECT))
    ceiling = rows["stacking.group_clip_ceiling_adu"]
    limit = rows["stacking.group_linearity_limit_adu"]
    assert ceiling.status == MeasurementStatus.ASSUMED
    assert "datasheet" in ceiling.reason
    assert limit.status == MeasurementStatus.ASSUMED
    assert "assumed" in limit.reason


def test_a_ceiling_the_clipped_frames_reached_themselves_is_measured_whatever_the_profile_says() -> None:
    """If no frame used the profile's ceiling, its origin is irrelevant."""
    group = _group(peak_clip_ceiling_provenance="datasheet", peak_frames_on_profile_ceiling=0)
    rows = _by_metric(group_peak_measurements(group, CONTEXT, SUBJECT))
    assert rows["stacking.group_clip_ceiling_adu"].status == MeasurementStatus.MEASURED


def test_a_group_with_no_peak_and_no_limit_gives_reasons() -> None:
    """Missing numbers are not zero; the clipped count still counts."""
    group = _group(
        brightest_star_peak_fraction=None,
        brightest_star_peak_above_sky_adu=None,
        peak_sky_adu=None,
        peak_clip_ceiling_adu=None,
        peak_linearity_limit_adu=None,
        peak_linearity_limit_provenance=None,
        peak_frames_with_peak=0,
        peak_frames_clipped=2,
    )
    rows = group_peak_measurements(group, CONTEXT, SUBJECT)
    clipped = rows[-1]
    assert clipped.value == pytest.approx(2.0)
    assert all(row.status == MeasurementStatus.NOT_MEASURED and row.reason for row in rows[:-1])


def test_a_group_whose_frames_could_not_be_read_stores_nothing_but_reasons() -> None:
    """With no sampled frame even the clipped count is not measured."""
    group = _group(
        brightest_star_peak_fraction=None,
        brightest_star_peak_above_sky_adu=None,
        peak_sky_adu=None,
        peak_clip_ceiling_adu=None,
        peak_linearity_limit_adu=None,
        peak_linearity_limit_provenance=None,
        peak_frames_with_peak=0,
        peak_frames_sampled=0,
    )
    rows = group_peak_measurements(group, CONTEXT, SUBJECT)
    assert all(row.status == MeasurementStatus.NOT_MEASURED and row.reason for row in rows)


def test_without_science_every_value_is_not_measured() -> None:
    """A caller that gathered nothing still gets a row for each metric."""
    rows = science_measurements(None, CONTEXT, SUBJECT, [_group(), _group()], [SUBJECT, SUBJECT])
    # + 2: the measured-to-predicted ratio and the master dark's noise.
    assert len(rows) == 6 + 1 + 3 + 2 * 8 + 2
    assert [row.status for row in rows[:10]] == [MeasurementStatus.NOT_MEASURED] * 10


def test_group_values_follow_the_summarys_group_order_and_never_drift() -> None:
    """Each group's peak rows sit on its own subject, in order."""
    rows = stacking_emitted_measurements()
    fractions = [row for row in rows if row.metric == "stacking.group_brightest_star_peak_fraction"]
    assert [row.value for row in fractions] == [0.4, 1.3]
    assert len({row.subject.subject_id for row in fractions}) == 2
    with pytest.raises(ValueError, match="shorter"):
        science_measurements(fixture_science(), CONTEXT, SUBJECT, [_group(), _group()], [SUBJECT])


def test_a_group_left_out_of_the_stack_still_gets_its_own_peak_rows() -> None:
    """A group that could not be stacked gets its own not-measured rows."""
    from astrometricslib.pipelines.stacking.post_processing.record_quality import stacking_measurements

    summary = fixture_summary()
    summary.stacking_metrics.exposure_groups.append(
        ExposureGroupSummary(
            exposureSeconds=30.0,
            framesSubmitted=4,
            framesStacked=0,
            darkApplied=False,
            leftOutReason="could not be stacked",
        )
    )
    rows = stacking_measurements(
        summary,
        CONTEXT,
        "M99_Stacked.fits",
        StackNoiseOutcome(None, "none"),
        None,
        "no members",
        fixture_science(),
    )
    fractions = [row for row in rows if row.metric == "stacking.group_brightest_star_peak_fraction"]
    assert len(fractions) == 3
    assert fractions[2].status == MeasurementStatus.NOT_MEASURED
    assert len({row.subject.subject_id for row in fractions}) == 3


class _FakeStore:
    """A quality store that answers from a list and counts the questions."""

    def __init__(self, rows: list[QualityMeasurement], fail: bool = False) -> None:
        """Hold the rows to answer with.

        Parameters
        ----------
        rows : `list` of `QualityMeasurement`
            The rows `query` returns.
        fail : `bool`, optional
            Whether `query` raises.
        """
        self.rows = rows
        self.fail = fail
        self.queries = 0

    def query(self, **_keywords: Any) -> list[QualityMeasurement]:
        """Answer a query.

        Returns
        -------
        rows : `list` of `QualityMeasurement`
            The rows given at construction.

        Raises
        ------
        OSError
            When the store was made to fail.
        """
        self.queries += 1
        if self.fail:
            raise OSError("database is locked")
        return self.rows


def _sky_rows(paths: list[str], rate: float = 2.0) -> list[QualityMeasurement]:
    """Build a stored sky rate row for each frame path.

    Parameters
    ----------
    paths : `list` [`str`]
        The frame paths.
    rate : `float`, optional
        The sky rate in electrons per second per pixel.

    Returns
    -------
    rows : `list` of `QualityMeasurement`
        One measured row for each frame, as the measurement job stores it.
    """
    return [
        QualityMeasurement(
            metric="frame.sky_rate_e_per_s_per_px",
            definitionVersion=1,
            subject=QualitySubject(
                level=SubjectLevel.FRAME, subjectId=frame_subject_id(path, "M 99"), targetId="M 99"
            ),
            status=MeasurementStatus.MEASURED,
            value=rate,
            origin="measured",
            pipelineVersion="1",
            runId="r",
            measuredAt=datetime(2026, 10, 1, tzinfo=UTC),
        )
        for path in paths
    ]


def _membership(membership_reason: str = "") -> StackMembership:
    """Build a stack of two 60 s frames with equal weights.

    Parameters
    ----------
    membership_reason : `str`, optional
        Why the member list is uncertain.

    Returns
    -------
    membership : `StackMembership`
        The members.
    """
    frames = [SimpleNamespace(path=f"/frames/{name}.fits", exposure="60", camera="Fake Cam") for name in "ab"]
    return StackMembership(
        tuple(StackMember(frame, 1.0) for frame in frames), membership_reason=membership_reason
    )


def _measured(value: float) -> ProvenancedValue:
    """Build a measured camera profile number.

    Parameters
    ----------
    value : `float`
        The number.

    Returns
    -------
    number : `ProvenancedValue`
        The number, marked as measured.
    """
    return ProvenancedValue(value=value, provenance=ValueProvenance(kind=ProvenanceKind.MEASURED, source="t"))


@pytest.fixture
def fake_profiles(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Replace the camera profile lookup with a fake one.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        The patcher.

    Returns
    -------
    configurations : `list`
        The ``configuration`` argument of every profile lookup.
    """
    seen: list[object] = []

    def resolve(camera: object, configuration: object = None) -> str:
        """Record the configuration and return a stand-in profile.

        Returns
        -------
        profile : `str`
            A stand-in.
        """
        seen.append(configuration)
        return "profile"

    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.post_processing.record_science.resolve_camera_profile", resolve
    )
    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.post_processing.record_science.look_up_frame_sensor_noise",
        lambda _profile, _frame, **_keywords: (_measured(0.8), _measured(3.8), ""),
    )
    return seen


def test_the_expected_noise_joins_the_stored_sky_with_the_profile_and_uses_the_given_store(
    fake_profiles: list[object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two measured frames give a measured result from the given store."""
    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.post_processing.record_science.QualityStore.from_configuration",
        lambda _configuration: pytest.fail("a second store was opened"),
    )
    store = _FakeStore(_sky_rows(["/frames/a.fits", "/frames/b.fits"]))
    configuration = object()
    outcome = compute_expected_noise(_membership(), "", "M 99", configuration, store=store)
    assert outcome.result is not None
    assert outcome.result.assumed_reason == ""
    assert outcome.result.frame_count == 2
    # 2 e-/s/px x 60 s = 120 e-; gain 0.8 e-/ADU -> 150 ADU in every frame.
    assert outcome.result.predicted_sky_level == pytest.approx(150.0 / 65535.0)
    assert store.queries == 1
    assert fake_profiles == [configuration, configuration]


def test_a_spectral_stack_gets_no_expected_noise() -> None:
    """A spectral stack's measured noise is not stored, nor is this."""
    outcome = compute_expected_noise(_membership(), "", "M 99", is_spectral=True)
    assert outcome.result is None
    assert outcome.reason == SPECTRAL_STACK_REASON


def _group_entry(paths: list[str], **overrides: Any) -> dict[str, Any]:
    """Build one ``exposure_group_weights`` entry in the stacker's shape.

    Parameters
    ----------
    paths : `list` [`str`]
        The group's frame paths.
    **overrides : `Any`
        Entry values to replace.

    Returns
    -------
    entry : `dict`
        A 60 s group with weight 4, gain 1 and a sky of 2 per second.
    """
    entry: dict[str, Any] = {
        "paths": paths,
        "weight": 4.0,
        "frame_count": len(paths),
        "exposure_seconds": 60.0,
        "frame_noise_counts": 3.0,
        "gain": 1.0,
        "gain_measured": True,
        "sky_level_per_second": 2.0e-4,
        "stack_weight": None,
        "holds_folded_frames": False,
    }
    entry.update(overrides)
    return entry


def test_a_clipped_stack_gets_no_expected_noise_even_with_one_group() -> None:
    """A non-empty clipped list refuses the prediction, group or not."""
    for diagnostics in (
        {"clipped_exposure_groups": [60.0]},
        {"clipped_exposure_groups": [60.0], "exposure_group_weights": [_group_entry(["a"])]},
    ):
        outcome = compute_expected_noise(_membership(), "", "M 99", diagnostics=diagnostics)
        assert outcome.result is None
        assert outcome.reason == CLIPPED_EXPOSURE_GROUPS_REASON
    rows = expected_noise_measurements(outcome, CONTEXT, SUBJECT)
    assert all(row.status == MeasurementStatus.NOT_MEASURED and row.reason for row in rows)


def test_a_group_stack_with_known_weights_gets_the_group_prediction(fake_profiles: list[object]) -> None:
    """One group, gain 1: the noise equals the plain single-group result."""
    store = _FakeStore(_sky_rows(["/frames/a.fits", "/frames/b.fits"]))
    diagnostics = {"exposure_group_weights": [_group_entry(["/frames/a.fits", "/frames/b.fits"], weight=8.0)]}
    membership = StackMembership(tuple(StackMember(member.frame, 4.0) for member in _membership().members))
    group = compute_expected_noise(membership, "", "M 99", store=store, diagnostics=diagnostics)
    plain = compute_expected_noise(_membership(), "", "M 99", store=store)
    assert group.result is not None and plain.result is not None
    assert group.result.sky_noise == pytest.approx(plain.result.sky_noise)
    assert group.result.read_noise == pytest.approx(plain.result.read_noise)
    # The reference sky per second (2e-4) times the mean exposure (60 s).
    assert group.result.predicted_sky_level == pytest.approx(2.0e-4 * 60.0)


def test_group_stack_with_unknown_weights_gives_the_membership_reason(fake_profiles: list[object]) -> None:
    """Unknown weights are not measured, with the membership's reason."""
    frames = _membership().members
    membership = StackMembership(
        tuple(StackMember(member.frame, None) for member in frames),
        weights_reason="the 60 s exposure group holds folded frames of another exposure length",
    )
    diagnostics = {"exposure_group_weights": [_group_entry(["/frames/a.fits", "/frames/b.fits"])]}
    outcome = compute_expected_noise(
        membership,
        "",
        "M 99",
        store=_FakeStore(_sky_rows(["/frames/a.fits", "/frames/b.fits"])),
        diagnostics=diagnostics,
    )
    assert outcome.result is None
    assert outcome.reason == membership.weights_reason


def test_a_group_entry_without_its_facts_or_a_member_in_no_group_gives_a_reason(
    fake_profiles: list[object],
) -> None:
    """A missing fact or an unplaced member is never guessed."""
    store = _FakeStore(_sky_rows(["/frames/a.fits", "/frames/b.fits"]))
    membership = StackMembership(tuple(StackMember(member.frame, 1.0) for member in _membership().members))
    bare = {"exposure_group_weights": [{"paths": ["/frames/a.fits", "/frames/b.fits"], "weight": 1.0}]}
    assert "lacks" in compute_expected_noise(membership, "", "M 99", store=store, diagnostics=bare).reason
    unplaced = {"exposure_group_weights": [_group_entry(["/frames/a.fits"])]}
    outcome = compute_expected_noise(membership, "", "M 99", store=store, diagnostics=unplaced)
    assert outcome.result is None
    assert "no combined exposure group" in outcome.reason


def test_an_uncertain_member_list_makes_the_expected_noise_assumed(fake_profiles: list[object]) -> None:
    """Siril stacked fewer frames than candidates: the rows say so."""
    reason = "Siril stacked 1 of 2 candidate frames"
    store = _FakeStore(_sky_rows(["/frames/a.fits", "/frames/b.fits"]))
    outcome = compute_expected_noise(_membership(reason), "", "M 99", store=store)
    assert outcome.result is not None
    assert reason in outcome.result.assumed_reason
    rows = expected_noise_measurements(outcome, CONTEXT, SUBJECT)
    assert all(row.status == MeasurementStatus.ASSUMED and reason in row.reason for row in rows)


def test_missing_members_and_missing_sky_rows_give_a_reason(fake_profiles: list[object]) -> None:
    """No members or a missing sky rate gives a reason, not a default."""
    assert compute_expected_noise(None, "members unknown", "M 99").reason == "members unknown"
    store = _FakeStore(_sky_rows(["/frames/a.fits"]))
    outcome = compute_expected_noise(_membership(), "", "M 99", store=store)
    assert outcome.result is None
    assert "record_frame_measurements" in outcome.reason


def test_a_store_that_fails_gives_a_reason_and_does_not_raise(fake_profiles: list[object]) -> None:
    """A locked database must not stop a stack or a subset run."""
    outcome = compute_expected_noise(_membership(), "", "M 99", store=_FakeStore([], fail=True))
    assert outcome.result is None
    assert "could not be read" in outcome.reason


def test_a_profile_lookup_that_fails_gives_a_reason_and_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A camera profile error becomes a reason, never an exception."""

    def broken(*_arguments: object, **_keywords: object) -> None:
        """Fail like a profile that cannot be parsed.

        Raises
        ------
        ValueError
            Always.
        """
        raise ValueError("bad profile")

    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.post_processing.record_science.resolve_camera_profile", broken
    )
    store = _FakeStore(_sky_rows(["/frames/a.fits", "/frames/b.fits"]))
    outcome = compute_expected_noise(_membership(), "", "M 99", store=store)
    assert outcome.result is None
    assert "ValueError" in outcome.reason


def test_the_stack_science_takes_the_sky_level_from_the_noise_step_without_reading_the_file(
    fake_profiles: list[object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The noise step read the plane once; the stack is not read again."""
    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.post_processing.record_science.measure_stack_sky_level",
        lambda _path: pytest.fail("the stack was read a second time"),
    )
    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.post_processing.record_science.QualityStore.from_configuration",
        lambda _configuration: _FakeStore(_sky_rows(["/frames/a.fits", "/frames/b.fits"])),
    )
    science = compute_stack_science(
        "/nowhere/stack.fits",
        0.004,
        1.5,
        _membership(),
        "",
        "M 99",
        {},
        noise=StackNoiseOutcome(None, "no solution", stack_sky_level=0.006),
    )
    assert science.stack_sky_level == pytest.approx(0.006)
    assert science.expected_noise.result is not None
    assert science.flat_noise.result is not None
    assert science.flat_noise.result.sky_level == pytest.approx(0.006)


def test_a_stack_the_noise_step_did_not_read_has_its_sky_level_read_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A spectral stack skips the noise step, so the sampled reader is used."""
    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.post_processing.record_science.measure_stack_sky_level",
        lambda _path: 0.007,
    )
    science = compute_stack_science(
        "/nowhere/stack.fits", None, None, None, "no members", "M 99", {}, is_spectral=True
    )
    assert science.stack_sky_level == pytest.approx(0.007)
    assert science.expected_noise.reason == SPECTRAL_STACK_REASON


def test_a_failing_flat_step_does_not_drop_the_other_rows(
    fake_profiles: list[object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error in the flat's noise keeps the expected noise."""

    def broken(*_arguments: object) -> None:
        """Fail like a kernel size that cannot be worked out.

        Raises
        ------
        OverflowError
            Always.
        """
        raise OverflowError("cannot convert float infinity to integer")

    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.post_processing.record_science.flat_noise_contribution", broken
    )
    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.post_processing.record_science.QualityStore.from_configuration",
        lambda _configuration: _FakeStore(_sky_rows(["/frames/a.fits", "/frames/b.fits"])),
    )
    science = compute_stack_science(
        "/nowhere/stack.fits",
        0.004,
        1.5,
        _membership(),
        "",
        "M 99",
        {},
        noise=StackNoiseOutcome(None, "x", stack_sky_level=0.006),
    )
    assert science.flat_noise.result is None
    assert "OverflowError" in science.flat_noise.reason
    assert science.expected_noise.result is not None


def test_a_non_finite_blur_width_loses_no_row() -> None:
    """A NaN blur width (it used to raise) loses only the flat rows."""
    science = compute_stack_science(
        "/nowhere/stack.fits",
        0.004,
        float("nan"),
        None,
        "no members",
        "M 99",
        {},
        noise=StackNoiseOutcome(None, "x", stack_sky_level=0.006),
    )
    assert science.flat_noise.result is None
    assert "blur width" in science.flat_noise.reason
    rows = science_measurements(science, CONTEXT, SUBJECT, [_group()], [SUBJECT])
    assert len(rows) == 6 + 1 + 3 + 8 + 2


def test_the_group_reference_exposure_is_stored_on_the_groups_own_subject() -> None:
    """The row has the reference frame's exposure, found by summary index."""
    references = (GroupReference(0, None, 60.0), GroupReference(1, "/frames/long.fits", 300.0))
    science = StackScience(
        ExpectedNoiseOutcome(None, "x"),
        FlatNoiseOutcome(None, "x"),
        None,
        group_references=references,
    )
    other = QualitySubject(level=SubjectLevel.STACK, subjectId="M 99/groups/b.fits", targetId="M 99")
    rows = science_measurements(science, CONTEXT, SUBJECT, [_group(), _group()], [SUBJECT, other])
    reference_rows = [row for row in rows if row.metric == "stacking.group_reference_exposure_s"]
    assert [row.value for row in reference_rows] == [60.0, 300.0]
    assert [row.subject.subject_id for row in reference_rows] == [SUBJECT.subject_id, other.subject_id]
    assert all(row.status == MeasurementStatus.MEASURED for row in reference_rows)


def test_an_unknown_group_reference_is_not_measured_with_its_reason() -> None:
    """An unknown exposure and a group with no entry both say why."""
    unknown = GroupReference(0, None, None, "no single registration reference frame can be identified")
    (row,) = group_reference_exposure_measurements((unknown,), 0, CONTEXT, SUBJECT)
    assert row.status == MeasurementStatus.NOT_MEASURED
    assert row.value is None
    assert row.reason == "no single registration reference frame can be identified"
    (missing,) = group_reference_exposure_measurements((unknown,), 1, CONTEXT, SUBJECT)
    assert missing.status == MeasurementStatus.NOT_MEASURED
    assert "left out" in missing.reason


def test_a_folded_member_scale_raises_the_group_noise(fake_profiles: list[object]) -> None:
    """A member's exposure scale reaches the group prediction."""
    store = _FakeStore(_sky_rows(["/frames/a.fits", "/frames/b.fits"]))
    diagnostics = {"exposure_group_weights": [_group_entry(["/frames/a.fits", "/frames/b.fits"], weight=8.0)]}
    frames = _membership().members
    unscaled = StackMembership(tuple(StackMember(member.frame, 4.0) for member in frames))
    scaled = StackMembership(
        tuple(
            StackMember(member.frame, 4.0, exposure_scale=scale)
            for member, scale in zip(frames, (1.0, 2.0), strict=True)
        )
    )
    plain = compute_expected_noise(unscaled, "", "M 99", store=store, diagnostics=diagnostics)
    folded = compute_expected_noise(scaled, "", "M 99", store=store, diagnostics=diagnostics)
    assert plain.result is not None and folded.result is not None
    assert folded.result.read_noise > plain.result.read_noise
    # Two frames with weight 1/2 each: the variance share is (1 + 4) / 4
    # with k = 1 and 2, against (1 + 1) / 4 with k = 1 and 1.
    assert folded.result.read_noise / plain.result.read_noise == pytest.approx((5.0 / 2.0) ** 0.5)
