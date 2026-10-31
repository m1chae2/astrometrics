"""Purpose: Tests for the plain-wording helpers and the package exposure row.

Description: Checks the hedged spectral-type phrase, the exposure phrases, the
plain message of a rule that could not be checked, and
`exposure_request_from_statement`: which statements make a package row, how a
free-text filter is normalized, and where each field of the row comes from.
"""

import pytest

from astrometricslib import CaptureSetting, FilterType
from wayfindinglib.analytics.result_quality.all_rules import ALL_RULES
from wayfindinglib.analytics.result_quality.exposure_request import (
    exposure_request_from_statement,
    normalize_filter_name,
)
from wayfindinglib.analytics.result_quality.plain_text import (
    exposure_phrases,
    format_plain,
    plain_missing_data,
    spectral_type_phrase,
)
from wayfindinglib.models.planning.observation_package import FrameType
from wayfindinglib.models.quality.statements import (
    ActionClass,
    Confidence,
    ProposedAction,
    StatementKind,
    TargetStatement,
)
from wayfindinglib.test.plain_wording_support import assert_plain_message_is_sound


def _setting(**changes: object) -> CaptureSetting:
    """Build a capture setting for a mono camera with an Ha filter.

    Returns
    -------
    setting : `CaptureSetting`
        The setting, with the given fields changed.
    """
    fields: dict[str, object] = {
        "camera": "ASI533MM",
        "filter_name": "Ha",
        "exposure_seconds": 120.0,
        "gain_setting": 100,
        "offset": 50,
        "binning": 1,
    }
    fields.update(changes)
    return CaptureSetting(**fields)


def _statement(
    rule: str = "more_frames",
    action: ProposedAction | None = None,
    setting: CaptureSetting | None = None,
) -> TargetStatement:
    """Build a recommendation statement for a rule.

    Returns
    -------
    statement : `TargetStatement`
        A capture-settings recommendation.
    """
    return TargetStatement(
        rule=rule,
        kind=StatementKind.RECOMMENDATION,
        action_class=ActionClass.CAPTURE_SETTINGS,
        capture_setting=setting if setting is not None else _setting(),
        message="m",
        proposed_action=action
        if action is not None
        else ProposedAction(frame_count=20, exposure_seconds=90.0),
        confidence=Confidence.DESIGN_ESTIMATE,
    )


@pytest.mark.parametrize(
    ("spectral_type", "phrase"),
    [
        ("K3V", "an orange K-type star"),
        ("G2V", "a yellow G-type star"),
        ("A0", "a white A-type star"),
        ("M5", "a red M-type star"),
        ("B8", "a blue-white B-type star"),
        ("O9", "a blue O-type star"),
        ("F6", "a yellow-white F-type star"),
        ("DA", "a star of type DA"),
    ],
)
def test_a_spectral_type_is_named_by_color_and_class(spectral_type: str, phrase: str) -> None:
    """The phrase names the class letter and the color a reader can picture."""
    assert spectral_type_phrase(spectral_type) == phrase


def test_exposure_phrases_read_well_with_and_without_an_exposure() -> None:
    """A known exposure is named; an unknown one says 'these frames'."""
    assert exposure_phrases(90.0) == {"exposure_phrase": "the 90 s frames", "exposure_clause": "90 s "}
    assert exposure_phrases(None) == {"exposure_phrase": "these frames", "exposure_clause": ""}


def test_missing_data_has_a_plain_message_that_names_the_rule() -> None:
    """The plain note for a rule that could not be checked says so in words."""
    assert "calibration gap" in plain_missing_data("calibration_gap")


def test_format_plain_fails_loudly_when_a_field_is_missing() -> None:
    """A template that needs a field the caller did not pass is a bug."""
    rule = next(rule for rule in ALL_RULES if rule.name == "altitude_cost")
    with pytest.raises(KeyError):
        format_plain(rule, {})
    assert "30 degrees" in format_plain(
        rule, {"altitude_degrees": 30.0, "loss_percent": 38.0, "advice_altitude_degrees": 30.0}
    )


def test_a_plain_message_check_catches_a_number_the_technical_text_lacks() -> None:
    """The shared check refuses a plain number the technical text lacks."""
    bad = TargetStatement(
        rule="x",
        kind=StatementKind.CONSTRAINT,
        message="The sky is bright.",
        plain_message="The sky is 7 times brighter.",
        confidence=Confidence.MEASURED,
    )
    with pytest.raises(AssertionError):
        assert_plain_message_is_sound(bad)
    with pytest.raises(AssertionError):
        assert_plain_message_is_sound(bad.model_copy(update={"plain_message": "The RMS is bright."}))
    with pytest.raises(AssertionError):
        assert_plain_message_is_sound(bad.model_copy(update={"plain_message": ""}))


def test_more_frames_makes_a_light_row_from_the_proposal_and_the_setting() -> None:
    """The row is a LIGHT row: filter from the setting, rest from the plan."""
    request = exposure_request_from_statement(_statement())
    assert request is not None
    assert request.frame_type == FrameType.LIGHT
    assert request.filter == FilterType.Ha
    assert request.count == 20
    assert request.exposure_sec == pytest.approx(90.0)
    assert (request.gain_setting, request.offset, request.binning) == (100, 50, 1)


def test_the_exposure_falls_back_to_the_setting_when_the_proposal_has_none() -> None:
    """A proposal with only a count uses the setting's own exposure."""
    request = exposure_request_from_statement(_statement(action=ProposedAction(frame_count=5)))
    assert request is not None
    assert request.exposure_sec == pytest.approx(120.0)


def test_the_proposal_overrides_the_setting_for_gain_offset_and_binning() -> None:
    """A proposed gain, offset or binning wins over the capture setting's."""
    action = ProposedAction(frame_count=5, exposure_seconds=60.0, gain_setting=120, offset=30, binning=2)
    request = exposure_request_from_statement(_statement(action=action))
    assert request is not None
    assert (request.gain_setting, request.offset, request.binning) == (120, 30, 2)


@pytest.mark.parametrize(
    ("free_text", "expected"),
    [
        ("Luminance", FilterType.L),
        ("L", FilterType.L),
        ("lum", FilterType.L),
        ("Red", FilterType.R),
        ("h-alpha", FilterType.Ha),
        ("HA", FilterType.Ha),
        ("O3", FilterType.OIII),
        ("SII", FilterType.SII),
        ("Star Analyzer 200", FilterType.SPEC),
        (None, FilterType.NONE),
        ("", FilterType.NONE),
        ("  none ", FilterType.NONE),
        ("Dual Narrowband 7nm", None),
    ],
)
def test_filter_text_is_normalized(free_text: str | None, expected: FilterType | None) -> None:
    """Common spellings map to a filter; an unknown name maps to nothing."""
    assert normalize_filter_name(free_text) == expected


def test_an_unrecognized_filter_makes_no_row() -> None:
    """A wrong filter would waste a night, so no row is made."""
    statement = _statement(setting=_setting(filter_name="Dual Narrowband 7nm"))
    assert exposure_request_from_statement(statement) is None


def test_a_setting_with_no_filter_gives_the_none_filter() -> None:
    """A camera with no filter gets FilterType.NONE."""
    request = exposure_request_from_statement(_statement(setting=_setting(filter_name=None)))
    assert request is not None
    assert request.filter == FilterType.NONE


def test_calibration_actions_and_other_rules_make_no_row() -> None:
    """Only a light-frame count from a package-row rule makes a row."""
    flats = ProposedAction(calibration_frame_type="flat", frame_count=8)
    assert exposure_request_from_statement(_statement(rule="more_frames", action=flats)) is None
    assert exposure_request_from_statement(_statement(rule="master_flat_noisy", action=flats)) is None
    assert exposure_request_from_statement(_statement(rule="underexposed_setting")) is None
    assert exposure_request_from_statement(_statement(action=ProposedAction(exposure_seconds=60.0))) is None
    assert exposure_request_from_statement(_statement(action=ProposedAction(note="n"))) is None


def test_no_exposure_anywhere_makes_no_row() -> None:
    """With no exposure in the proposal or the setting there is no row."""
    statement = _statement(action=ProposedAction(frame_count=5), setting=_setting(exposure_seconds=None))
    assert exposure_request_from_statement(statement) is None


def test_a_diminishing_returns_statement_asks_for_the_doubling_frames() -> None:
    """The package row of a fixed-pattern-limited setting asks 44 frames."""
    from astrometricslib import MeasurementReference
    from wayfindinglib.analytics.result_quality import report_rules
    from wayfindinglib.analytics.result_quality.capture_overheads import CaptureOverheads
    from wayfindinglib.analytics.result_quality.noise_growth import TakeMoreResult

    result = TakeMoreResult(
        target_noise_ratio=0.707,
        added_exposure_seconds=44 * 30.0,
        new_exposure_seconds=30.0,
        fixed_pattern_noise=0.583e-3,
        fixed_pattern_variance_share=0.49,
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
    setting = CaptureSetting(camera="ASI533MM Pro", telescope="RC8", filter_name="L", exposure_seconds=30.0)
    evidence = [MeasurementReference(row_id=1)]
    statement = report_rules.more_frames_statement(result, setting, overheads, evidence)
    assert statement is not None
    request = exposure_request_from_statement(statement)
    assert request is not None
    assert request.count == 44
    assert request.exposure_sec == pytest.approx(30.0)
