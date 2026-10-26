"""Purpose: Tests for the statements that need a join, a split or a fit.

Description: Checks the pure rules of the target report with made-up data:
frames lost, the cost of a bright sky, the cost of low altitude, the
sharpness trade-off, the calibration gap, "take N more" and the stop signal.
Each rule is checked at its limit (it fires just above and stays quiet just
below), takes its kind and class from the rule table, and cites its evidence.
The starting point (sky-dominated exposure and clipping cap) is checked
against hand-worked numbers.
"""

from collections import Counter

import pytest

from astrometricslib import CaptureSetting, MeasurementReference
from wayfindinglib.analytics.result_quality import report_rules as rr
from wayfindinglib.analytics.result_quality.capture_overheads import CaptureOverheads
from wayfindinglib.analytics.result_quality.efficiency import (
    CAUSE_SKY,
    CAUSE_SKY_MOON,
    STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC,
    STAR_WIDTH_NIGHTLY_SPREAD_KEY,
    FrameObservation,
)
from wayfindinglib.analytics.result_quality.noise_growth import (
    StackFrame,
    StackGroup,
    StopSignal,
    TakeMoreResult,
    WeightingMode,
)
from wayfindinglib.analytics.result_quality.starting_point import (
    StartingPointInputs,
    build_starting_point,
    clipping_cap_seconds,
    sky_dominated_exposure_seconds,
)
from wayfindinglib.models.quality.capture_summary import CaptureSettingSummary
from wayfindinglib.models.quality.efficiency import (
    EfficiencyReference,
    EfficiencyReferenceKind,
    LossAccount,
    LossShare,
    ScienceGoal,
)
from wayfindinglib.models.quality.statements import (
    ActionClass,
    Confidence,
    Severity,
    StatementKind,
)
from wayfindinglib.test.plain_wording_support import assert_plain_message_is_sound

SETTING = CaptureSetting(
    camera="ASI533MM Pro",
    telescope="RC8",
    filter_name="L",
    exposure_seconds=60.0,
    gain_setting=100,
    offset=50,
    binning=1,
)


def _summary(
    frames_taken: int = 100,
    frames_set_aside: int = 0,
    captured_seconds: float = 6000.0,
    account: LossAccount | None = None,
) -> CaptureSettingSummary:
    """Build the summary of one setting.

    Returns
    -------
    summary : `CaptureSettingSummary`
        The summary.
    """
    return CaptureSettingSummary(
        captureSetting=SETTING,
        framesTaken=frames_taken,
        nights=3,
        framesSetAside=frames_set_aside,
        capturedSeconds=captured_seconds,
        lossAccount=account,
    )


def _account(sky_seconds: float, moon_seconds: float | None) -> LossAccount:
    """Build a loss account with a sky loss and, optionally, its Moon share.

    Returns
    -------
    account : `LossAccount`
        The account.
    """
    shares = [LossShare(cause=CAUSE_SKY, seconds=sky_seconds, recoverableWithTime=True, modelDerived=False)]
    if moon_seconds is not None:
        shares.append(
            LossShare(cause=CAUSE_SKY_MOON, seconds=moon_seconds, recoverableWithTime=True, modelDerived=True)
        )
    return LossAccount(
        goal=ScienceGoal.FAINT_POINT_SOURCE,
        referenceName="baseline:x",
        setAsideSeconds=0.0,
        keptFrameDeficit=shares,
        causeUnknownReason="not measured",
        transparencyIncluded=False,
        transparencyReason="no zero point",
    )


# -- frames lost -------------------------------------------------------------


def test_frames_lost_fires_at_the_share_and_cites_the_quarantine_rows() -> None:
    """Five in a hundred frames set aside warns; four does not."""
    reasons = {SETTING: Counter({"clouded": 3, "trailed": 2})}
    evidence = {SETTING: [MeasurementReference(row_id=7)]}
    fired = rr.frames_lost_statements([_summary(frames_set_aside=5)], reasons, evidence, Counter(), 0)
    assert len(fired) == 1
    assert fired[0].kind == StatementKind.WARNING
    assert fired[0].severity == Severity.ADVICE
    assert fired[0].action_class == ActionClass.DATA_SELECTION
    assert fired[0].message == "5 of 100 frames were set aside (clouded 3, trailed 2)."
    assert [reference.row_id for reference in fired[0].evidence] == [7]
    quiet = rr.frames_lost_statements([_summary(frames_set_aside=4)], reasons, evidence, Counter(), 0)
    assert quiet == []


def test_frames_lost_reports_frames_no_measurement_could_place() -> None:
    """Frames with no stored setting get a statement with no setting."""
    statements = rr.frames_lost_statements([], {}, {}, Counter({"clouded": 2}), 8)
    assert len(statements) == 1
    assert statements[0].capture_setting is None
    assert "2 of 10" in statements[0].message
    assert "Measure the target's frames again" in statements[0].reason


# -- sky cost ----------------------------------------------------------------


def test_sky_cost_uses_the_moon_share_when_the_model_split_it() -> None:
    """600 s of 6000 s lost to the Moon is exactly 10%: the rule fires."""
    statements = rr.sky_cost_statements([_summary(account=_account(900.0, 600.0))])
    assert len(statements) == 1
    assert statements[0].action_class == ActionClass.SCHEDULING
    assert "10%" in statements[0].message
    assert "Moon" in statements[0].message
    assert "model" in statements[0].reason
    assert rr.sky_cost_statements([_summary(account=_account(900.0, 599.0))]) == []


def test_sky_cost_without_a_split_uses_the_whole_sky_loss_and_says_so() -> None:
    """With no Moon split, the whole sky loss is used."""
    statements = rr.sky_cost_statements([_summary(account=_account(700.0, None))])
    assert len(statements) == 1
    assert "Moon, twilight or light pollution" in statements[0].message
    assert "No sky model" in statements[0].reason


def test_sky_cost_needs_a_loss_account() -> None:
    """A summary with no loss account makes no statement."""
    assert rr.sky_cost_statements([_summary()]) == []


# -- altitude cost -----------------------------------------------------------


def _altitude_samples(low_eta: float, high_eta: float, count: int = 6) -> list[rr.AltitudeSample]:
    """Make frames at 25 and 55 degrees with given efficiencies.

    Returns
    -------
    samples : `list` [`AltitudeSample`]
        Half of them low and half high.
    """
    low = [rr.AltitudeSample(25.0, low_eta, 60.0) for _ in range(count)]
    high = [rr.AltitudeSample(55.0, high_eta, 60.0) for _ in range(count)]
    return low + high


def test_altitude_cost_finds_the_cut_with_the_largest_loss() -> None:
    """Low frames at 0.5 against 0.8 lose 37.5% at every cut."""
    cost = rr.find_altitude_cost(_altitude_samples(0.5, 0.8))
    assert cost is not None
    assert cost.relative_loss == pytest.approx(1.0 - 0.5 / 0.8)
    assert cost.cut_deg == pytest.approx(30.0)
    statement = rr.altitude_cost_statement(cost, SETTING, [MeasurementReference(row_id=1)])
    assert statement.action_class == ActionClass.SCHEDULING
    assert statement.message == "Frames below 30 degrees lost 38%. Image above 30 degrees."


def test_altitude_cost_is_quiet_below_the_loss_limit_or_with_few_frames() -> None:
    """A 10% loss is under the 15% limit; four frames on a side are too few."""
    assert rr.find_altitude_cost(_altitude_samples(0.72, 0.8)) is None
    assert rr.find_altitude_cost(_altitude_samples(0.5, 0.8, count=4)) is None


# -- sharpness trade-off -----------------------------------------------------


def _frames(count: int, sky: float = 1.0) -> list[StackFrame]:
    """Make frames of 60 s with the same sky and a 3 e read noise.

    Returns
    -------
    frames : `list` [`StackFrame`]
        The frames.
    """
    return [
        StackFrame(frame_id=f"f{index}", exposure_seconds=60.0, sky_rate_e_per_s_per_px=sky, read_noise_e=3.0)
        for index in range(count)
    ]


def test_sharpness_tradeoff_keeps_the_sharpest_share() -> None:
    """Keeping 70% of 20 equal frames raises noise by sqrt(20/14) - 1."""
    widths = [2.0 + 0.1 * index for index in range(20)]
    tradeoff = rr.find_sharpness_tradeoff(_frames(20), widths, WeightingMode.EQUAL)
    assert tradeoff is not None
    assert tradeoff.kept_count == 14
    # The median of all 20 widths is 2.95; of the sharpest 14, 2.65.
    assert tradeoff.star_width_change == pytest.approx(2.65 / 2.95 - 1.0)
    assert tradeoff.noise_change == pytest.approx((20 / 14) ** 0.5 - 1.0)
    statement = rr.sharpness_statement(tradeoff, SETTING, [])
    assert statement.action_class == ActionClass.DATA_SELECTION
    assert statement.kind == StatementKind.RECOMMENDATION
    assert (
        statement.message
        == "Keeping the sharpest 70% of frames sharpens stars by 10% and raises the noise by 20%."
    )


def test_sharpness_tradeoff_is_quiet_when_the_frames_are_alike_or_few() -> None:
    """Equal widths gain nothing; 19 frames are below the minimum."""
    assert rr.find_sharpness_tradeoff(_frames(20), [2.0] * 20, WeightingMode.EQUAL) is None
    assert (
        rr.find_sharpness_tradeoff(_frames(19), [2.0 + 0.1 * i for i in range(19)], WeightingMode.EQUAL)
        is None
    )


def test_sharpness_tradeoff_is_not_made_for_a_group_stack_on_one_setting() -> None:
    """The ``group`` mode is refused: use the group form instead."""
    widths = [2.0 + 0.1 * index for index in range(20)]

    assert rr.find_sharpness_tradeoff(_frames(20), widths, WeightingMode.GROUP) is None


def _stack_group(count: int, exposure: float, frame_noise: float) -> StackGroup:
    """Make one exposure group of frames with a spread of star widths.

    Returns
    -------
    group : `StackGroup`
        The group.
    """
    frames = tuple(
        StackFrame(
            frame_id=f"g{exposure:g}_{index}",
            exposure_seconds=exposure,
            sky_rate_e_per_s_per_px=1.0,
            read_noise_e=3.0,
            star_width=2.0 + 0.1 * index,
        )
        for index in range(count)
    )
    return StackGroup(frames=frames, frame_noise_adu=frame_noise)


def test_the_group_sharpness_tradeoff_is_diluted_by_the_other_groups() -> None:
    """Dropping frames of one group changes the stack less than the group."""
    groups = [_stack_group(10, 30.0, 20.0), _stack_group(20, 60.0, 30.0)]
    widths = [frame.star_width or 0.0 for frame in groups[1].frames]

    in_stack = rr.find_sharpness_tradeoff_in_groups(groups, 1, widths)
    alone = rr.find_sharpness_tradeoff(list(groups[1].frames), widths, WeightingMode.WFWHM)

    assert in_stack is not None and alone is not None
    assert in_stack.kept_count == 14
    assert in_stack.star_width_change == pytest.approx(alone.star_width_change)
    assert 0.0 < in_stack.noise_change < alone.noise_change


def test_the_group_sharpness_tradeoff_needs_enough_frames() -> None:
    """A group of 19 frames is below the minimum, as for one setting."""
    groups = [_stack_group(19, 60.0, 30.0), _stack_group(10, 30.0, 20.0)]

    assert rr.find_sharpness_tradeoff_in_groups(groups, 0, [2.0 + 0.1 * i for i in range(19)]) is None


def test_floor_limited_statement_says_the_floor_in_place_of_a_count() -> None:
    """The warning names the floor share of the noise and the best cut."""
    result = TakeMoreResult(
        target_noise_ratio=0.707,
        floor_limited=True,
        floor_noise=2.9e-3,
        floor_variance_share=0.88,
        lowest_reachable_ratio=0.95,
        reason="More frames of this kind will not reach the target.",
    )

    statement = rr.floor_limited_statement(result, SETTING, [MeasurementReference(row_id=5)])

    assert statement.rule == "more_frames_will_not_help"
    assert statement.kind == StatementKind.WARNING
    assert "noise floor (0.0029 in stack units, 88% of the noise)" in statement.message
    assert "only 5%" in statement.message
    assert statement.proposed_action is None
    assert [reference.row_id for reference in statement.evidence] == [5]
    assert_plain_message_is_sound(statement)


# -- calibration gap ---------------------------------------------------------

DARK_OK = {
    "camera": "ASI533MM Pro",
    "iso": "100",
    "offset": 50.0,
    "binning": "1x1",
    "temperature_c": -10.0,
    "exposure": 60.0,
    "count": 20,
}
FLAT_OK = {
    "camera": "ASI533MM Pro",
    "filter": "L",
    "iso": "100",
    "offset": 50.0,
    "binning": "1x1",
    "count": 30,
}


def _group(temperature: float | None = -10.0) -> rr.LightFrameGroup:
    """Make a group of light frames.

    Returns
    -------
    group : `LightFrameGroup`
        The group.
    """
    return rr.LightFrameGroup(capture_setting=SETTING, sensor_temperature_c=temperature, frame_count=40)


def test_calibration_gap_is_quiet_when_darks_and_flats_match() -> None:
    """A dark and a flat that match in every setting leave no gap."""
    assert rr.find_calibration_gaps([_group()], [DARK_OK], [FLAT_OK]) == []


@pytest.mark.parametrize(
    ("change", "differs"),
    [
        ({"exposure": 120.0}, "exposure"),
        ({"iso": "200"}, "gain"),
        ({"offset": 10.0}, "offset"),
        ({"binning": "2x2"}, "binning"),
        ({"temperature_c": 5.0}, "temperature"),
    ],
)
def test_calibration_gap_names_the_setting_that_differs(change: dict, differs: str) -> None:
    """A dark that differs in one setting does not match."""
    findings = rr.find_calibration_gaps([_group()], [{**DARK_OK, **change}], [FLAT_OK])
    assert [finding.frame_type for finding in findings] == ["dark"]
    statement = rr.calibration_gap_statement(findings[0])
    assert statement.kind == StatementKind.RECOMMENDATION
    assert statement.action_class == ActionClass.CALIBRATION
    assert statement.proposed_action.calibration_frame_type == "dark"
    assert f"differs in {differs}" in statement.reason
    assert "library dark row" in statement.reason
    assert (
        statement.message
        == "Take darks at gain 100, offset 50, bin 1, 60 s, -10 C: none in the library matches."
    )


def test_calibration_gap_accepts_a_temperature_within_the_tolerance() -> None:
    """A dark 2 degrees from the lights still matches (the tolerance is 3)."""
    assert rr.find_calibration_gaps([_group()], [{**DARK_OK, "temperature_c": -8.0}], [FLAT_OK]) == []


def test_calibration_gap_flat_needs_the_filter_and_says_when_a_camera_has_none() -> None:
    """A flat for another filter is a gap; a camera with no flats says so."""
    findings = rr.find_calibration_gaps([_group()], [DARK_OK], [{**FLAT_OK, "filter": "Ha"}])
    assert [finding.frame_type for finding in findings] == ["flat"]
    assert "differs in filter" in findings[0].nearest_row_text
    none = rr.find_calibration_gaps([_group()], [DARK_OK], [])
    assert "no flats for the camera ASI533MM Pro" in none[0].nearest_row_text


# -- more frames and the stop signal -----------------------------------------


def test_more_frames_statement_gives_the_count_the_time_and_the_noise() -> None:
    """The statement has the count, exposure, clock time and noise."""
    result = TakeMoreResult(
        frames_needed=20,
        predicted_noise_ratio=0.71,
        target_noise_ratio=0.707,
        added_exposure_seconds=1800.0,
        new_exposure_seconds=90.0,
        reason="A design estimate.",
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
    statement = rr.more_frames_statement(result, SETTING, overheads, [MeasurementReference(row_id=3)])
    assert statement is not None
    assert statement.kind == StatementKind.RECOMMENDATION
    assert statement.confidence == Confidence.DESIGN_ESTIMATE
    assert statement.proposed_action.frame_count == 20
    assert statement.proposed_action.exposure_seconds == pytest.approx(90.0)
    assert (
        statement.message
        == "Take 20 more at 90 s (about 32 min with downloads) to cut the noise by about 29%."
    )
    assert statement.expected_effect.wall_clock_seconds == pytest.approx(1900.0)
    assert rr.more_frames_statement(TakeMoreResult(target_noise_ratio=0.707), SETTING, overheads, []) is None


def test_stop_statement_is_a_warning_with_advice() -> None:
    """The stop warning names the effective time and what to fix first."""
    signal = StopSignal(
        stopped=True,
        predicted_gain_fraction=0.05,
        reason="Doubling the data would cut the noise by 5 percent.",
    )
    statement = rr.stop_statement(signal, SETTING, 7200.0, [MeasurementReference(row_id=9)])
    assert statement.kind == StatementKind.WARNING
    assert statement.severity == Severity.ADVICE
    assert (
        statement.message
        == "Noise stopped falling after about 2.0 h. Retake flats or dither before adding frames."
    )


# -- starting point ----------------------------------------------------------


def test_sky_dominated_exposure_is_nine_read_noise_variances_over_the_sky_rate() -> None:
    """Read noise 3 e and sky 0.5 e/s/px: 9 * 9 / 0.5 = 162 s."""
    assert sky_dominated_exposure_seconds(0.5, 3.0) == pytest.approx(162.0)
    with pytest.raises(ValueError, match="sky rate"):
        sky_dominated_exposure_seconds(0.0, 3.0)


def test_clipping_cap_aims_the_peak_at_the_target_fraction_of_the_room() -> None:
    """A peak rate of 100 ADU/s and 40000 ADU of room give 320 s."""
    assert clipping_cap_seconds(100.0, 40000.0) == pytest.approx(320.0)
    with pytest.raises(ValueError, match="above 0"):
        clipping_cap_seconds(0.0, 1.0)


def _inputs(**changes: object) -> StartingPointInputs:
    """Build starting point inputs with a sky and a read noise.

    Returns
    -------
    inputs : `StartingPointInputs`
        The inputs, with the given changes.
    """
    base = {
        "capture_setting": SETTING,
        "sky_rate_e_per_s_per_px": 0.5,
        "sky_note": "Sky: test.",
        "read_noise_e": 3.0,
        "read_noise_note": "Read noise: test.",
        "peak_rate_adu_per_s": None,
        "room_adu": None,
        "clipping_note": "No zero point exists.",
    }
    base.update(changes)
    return StartingPointInputs(**base)


def test_starting_point_has_an_exposure_and_no_cap_without_a_zero_point() -> None:
    """The exposure is computed; the cap is not computed and says why."""
    point = build_starting_point(_inputs())
    assert point.exposure_seconds == pytest.approx(162.0)
    assert point.clipping_cap_seconds is None
    assert "No zero point" in point.clipping_cap_reason
    assert point.confidence == Confidence.DESIGN_ESTIMATE


def test_starting_point_is_held_to_the_clipping_cap_when_it_is_shorter() -> None:
    """A cap of 80 s (0.8 * 10000 / 100) holds the 162 s exposure to 80 s."""
    point = build_starting_point(_inputs(peak_rate_adu_per_s=100.0, room_adu=10000.0))
    assert point.clipping_cap_seconds == pytest.approx(80.0)
    assert point.exposure_seconds == pytest.approx(80.0)
    assert "clipping cap" in point.exposure_reason


def test_starting_point_says_what_is_missing_instead_of_inventing_a_number() -> None:
    """With no read noise there is no exposure, and the reason names it."""
    point = build_starting_point(_inputs(read_noise_e=None, read_noise_note="No measured read noise exists."))
    assert point.exposure_seconds is None
    assert "No measured read noise" in point.exposure_reason


# -- seeing limits resolution ------------------------------------------------


def _width_observations(width_arcsec: float, count: int = 5) -> list[FrameObservation]:
    """Build kept frames of one setting that share a star width.

    Parameters
    ----------
    width_arcsec : `float`
        The effective star width of every frame, in arcseconds.
    count : `int`, optional
        How many frames.

    Returns
    -------
    observations : `list` [`FrameObservation`]
        The frames.
    """
    return [
        FrameObservation(
            frameId=f"frame-{index}",
            captureSetting=SETTING,
            exposureSeconds=60.0,
            starWidthEffectiveArcsec=width_arcsec,
        )
        for index in range(count)
    ]


def _reference(kind: EfficiencyReferenceKind, width_arcsec: float = 2.0) -> EfficiencyReference:
    """Build a reference with a star width.

    Returns
    -------
    reference : `EfficiencyReference`
        The reference.
    """
    return EfficiencyReference(
        name=kind.value,
        version=1,
        kind=kind,
        factors={STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC: width_arcsec},
        registerEntry="WAY-D21" if kind == EfficiencyReferenceKind.PHYSICAL else "WAY-D22",
    )


def _evidence(count: int = 5) -> dict[str, MeasurementReference]:
    """Cite one star-width row for each frame.

    Returns
    -------
    evidence : `dict` [`str`, `MeasurementReference`]
        The references by frame id.
    """
    return {f"frame-{index}": MeasurementReference(row_id=index + 1) for index in range(count)}


def test_seeing_limit_on_an_extended_object_is_a_constraint_with_no_action() -> None:
    """Wide stars on an extended object give a constraint."""
    reference = _reference(EfficiencyReferenceKind.PHYSICAL)
    statements = rr.seeing_limit_statements(
        _width_observations(3.0), ScienceGoal.EXTENDED_OBJECT, lambda _setting: reference, _evidence()
    )
    assert len(statements) == 1
    statement = statements[0]
    assert statement.kind == StatementKind.CONSTRAINT
    assert statement.proposed_action is None
    assert statement.action_class is None
    assert statement.capture_setting == SETTING
    assert len(statement.evidence) == 5
    assert statement.limits[0].value == pytest.approx(2.0)
    assert statement.limits[0].register_entry == "WAY-D21"


def test_seeing_limit_is_quiet_for_a_point_source_well_above_the_reference() -> None:
    """Stars far above the reference leave room to improve: no constraint."""
    reference = _reference(EfficiencyReferenceKind.PHYSICAL)
    statements = rr.seeing_limit_statements(
        _width_observations(3.0), ScienceGoal.FAINT_POINT_SOURCE, lambda _setting: reference, _evidence()
    )
    assert statements == []


def test_seeing_limit_fires_for_a_point_source_at_the_physical_reference() -> None:
    """A point source at the physical reference width is limited by seeing."""
    reference = _reference(EfficiencyReferenceKind.PHYSICAL)
    statements = rr.seeing_limit_statements(
        _width_observations(2.1), ScienceGoal.FAINT_POINT_SOURCE, lambda _setting: reference, _evidence()
    )
    assert [statement.kind for statement in statements] == [StatementKind.CONSTRAINT]


def test_seeing_limit_needs_a_width_beyond_the_baseline_spread() -> None:
    """A width inside the baseline nights' spread is ordinary, not a limit.

    The same width beyond the spread gives the constraint.
    """
    reference = _reference(EfficiencyReferenceKind.BASELINE).model_copy(
        update={"factors": {STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC: 2.0, STAR_WIDTH_NIGHTLY_SPREAD_KEY: 0.5}}
    )
    inside = rr.seeing_limit_statements(
        _width_observations(2.4), ScienceGoal.EXTENDED_OBJECT, lambda _setting: reference, _evidence()
    )
    beyond = rr.seeing_limit_statements(
        _width_observations(2.6), ScienceGoal.EXTENDED_OBJECT, lambda _setting: reference, _evidence()
    )
    assert inside == []
    assert [statement.kind for statement in beyond] == [StatementKind.CONSTRAINT]


def test_seeing_limit_says_nothing_for_a_physical_reference_without_site_seeing() -> None:
    """With no site seeing there is no reference width, so no statement."""
    from wayfindinglib.analytics.result_quality.efficiency import physical_reference

    reference = physical_reference(100.0, 1.5).reference
    assert reference is not None
    statements = rr.seeing_limit_statements(
        _width_observations(9.0), ScienceGoal.EXTENDED_OBJECT, lambda _setting: reference, _evidence()
    )
    assert statements == []


def test_seeing_limit_says_nothing_when_it_cannot_be_evaluated() -> None:
    """Nothing is said without a reference, widths or a fitting goal."""
    physical = _reference(EfficiencyReferenceKind.PHYSICAL)
    baseline = _reference(EfficiencyReferenceKind.BASELINE)
    kept = _width_observations(2.0)
    assert rr.seeing_limit_statements(kept, ScienceGoal.EXTENDED_OBJECT, lambda _s: None, _evidence()) == []
    assert (
        rr.seeing_limit_statements(
            _width_observations(2.0)[:0], ScienceGoal.EXTENDED_OBJECT, lambda _s: physical, _evidence()
        )
        == []
    )
    assert rr.seeing_limit_statements(kept, ScienceGoal.FAINT_POINT_SOURCE, lambda _s: baseline, {}) == []
    assert (
        rr.seeing_limit_statements(kept, ScienceGoal.PERIOD_DETECTION, lambda _s: physical, _evidence()) == []
    )


# -- plain messages ----------------------------------------------------------


def test_every_statement_this_module_builds_has_a_sound_plain_message() -> None:
    """Each builder gives a short, jargon-free plain message."""
    statements = []
    statements += rr.frames_lost_statements(
        [_summary(frames_set_aside=5)], {SETTING: Counter({"clouded": 3})}, {}, Counter({"clouded": 2}), 8
    )
    statements += rr.sky_cost_statements([_summary(account=_account(900.0, 600.0))])
    statements += rr.sky_cost_statements([_summary(account=_account(700.0, None))])
    cost = rr.find_altitude_cost(_altitude_samples(0.5, 0.8))
    assert cost is not None
    statements.append(rr.altitude_cost_statement(cost, SETTING, []))
    widths = [2.0 + 0.1 * index for index in range(20)]
    tradeoff = rr.find_sharpness_tradeoff(_frames(20), widths, WeightingMode.EQUAL)
    assert tradeoff is not None
    statements.append(rr.sharpness_statement(tradeoff, SETTING, []))
    for finding in rr.find_calibration_gaps([_group()], [{**DARK_OK, "binning": "2x2"}], [FLAT_OK]):
        statements.append(rr.calibration_gap_statement(finding))
    result = TakeMoreResult(
        frames_needed=20,
        predicted_noise_ratio=0.71,
        target_noise_ratio=0.707,
        added_exposure_seconds=1800.0,
        new_exposure_seconds=90.0,
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
    more = rr.more_frames_statement(result, SETTING, overheads, [])
    assert more is not None
    statements.append(more)
    signal = StopSignal(stopped=True, predicted_gain_fraction=0.05, reason="Doubling gives 5 percent.")
    statements.append(rr.stop_statement(signal, SETTING, 7200.0, []))
    statements += rr.seeing_limit_statements(
        _width_observations(3.0),
        ScienceGoal.EXTENDED_OBJECT,
        lambda _setting: _reference(EfficiencyReferenceKind.PHYSICAL),
        _evidence(),
    )
    statements.append(rr.insufficient("calibration_gap", "Library not read."))
    assert len(statements) >= 10
    for statement in statements:
        assert_plain_message_is_sound(statement)
    assert "roughly 29%" in next(s for s in statements if s.rule == "more_frames").plain_message


def _m13_result(*, doubling_cut: float = 0.137, worthwhile: bool = True) -> TakeMoreResult:
    """Make the answer for the M 13 case (a floor of 49 percent of the noise).

    Parameters
    ----------
    doubling_cut : `float`, optional
        The noise cut by doubling.
    worthwhile : `bool`, optional
        Whether doubling is worth asking for.

    Returns
    -------
    result : `TakeMoreResult`
        The diminishing-returns answer for 44 frames of 30 s.
    """
    return TakeMoreResult(
        target_noise_ratio=0.707,
        added_exposure_seconds=44 * 30.0,
        new_exposure_seconds=30.0,
        floor_noise=0.583e-3,
        floor_variance_share=0.49,
        lowest_reachable_ratio=0.704,
        practical_limit_applied=True,
        doubling_frames=44,
        doubling_noise_cut=doubling_cut,
        most_reachable_cut=0.296,
        doubling_worthwhile=worthwhile,
        reason="A design estimate.",
    )


_NO_EXTRA_OVERHEADS = CaptureOverheads(
    download_seconds=5.0,
    dither_settle_seconds=20.0,
    autofocus_seconds=120.0,
    download_from_records=False,
    dither_from_records=False,
    autofocus_from_records=False,
    gaps_used=0,
)


def test_beyond_the_practical_limit_the_statement_gives_one_doubling() -> None:
    """The M 13 numbers give the diminishing-returns shape, not a count."""
    statement = rr.more_frames_statement(
        _m13_result(), SETTING, _NO_EXTRA_OVERHEADS, [MeasurementReference(row_id=3)]
    )

    assert statement is not None
    assert statement.rule == "more_frames"
    assert statement.kind == StatementKind.RECOMMENDATION
    assert "as many frames again as the stack holds (44 more at 30 s" in statement.message
    assert "cuts the noise by about 14%" in statement.message
    assert "at most about 30% in all" in statement.message
    assert "noise floor of 0.000583 (stack units) makes up about 49% of the noise" in statement.message
    assert rr.FLOOR_CAUSE_TEXT in statement.message
    assert statement.message.endswith(rr.FLOOR_NOT_REMOVED_TEXT)
    assert statement.proposed_action.frame_count == 44
    assert statement.expected_effect.noise_change_fraction == pytest.approx(-0.137)
    assert statement.values["practical_limit_applied"] == pytest.approx(1.0)
    assert statement.values["doubling_frames"] == pytest.approx(44.0)
    assert_plain_message_is_sound(statement)


def test_a_worthless_doubling_gives_the_stop_warning_in_the_new_shape() -> None:
    """Under 5 percent: a warning, no proposed frames, same floor sentences."""
    result = _m13_result(doubling_cut=0.03, worthwhile=False)
    result = result.model_copy(update={"floor_variance_share": 0.88, "most_reachable_cut": 0.06})

    statement = rr.floor_limited_statement(result, SETTING, [MeasurementReference(row_id=5)])

    assert statement.rule == "more_frames_will_not_help"
    assert statement.proposed_action is None
    assert "only about 3%" in statement.message
    assert "at most about 6% in all" in statement.message
    assert rr.FLOOR_CAUSE_TEXT in statement.message
    assert_plain_message_is_sound(statement)
