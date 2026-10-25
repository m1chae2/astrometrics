"""Purpose: Tests for the guiding-limits-exposure calculation.

Description: Builds guide samples by hand (a quiet log, a log with a bad
60-second stretch in every 300 seconds, and a noisy log) and checks the
allowed error, the share of sharp windows at each exposure, the exposure the
guiding supports, the reasons given when data is missing, and the
statement the report rule makes from the result. User decision U3,
2026-10-24 (issue #95).
"""

import pytest

from astrometricslib import CaptureSetting
from wayfindinglib.analytics.result_quality.guiding_exposure import (
    TARGET_SHARP_FRACTION,
    allowed_guide_rms_arcsec,
    assess_guiding_exposure,
    imaging_spans,
    sharpness_at_exposure,
)
from wayfindinglib.analytics.result_quality.guiding_join import FrameWindow
from wayfindinglib.analytics.result_quality.report_rules import guiding_limits_statements
from wayfindinglib.models.quality.statements import ActionClass, StatementKind
from wayfindinglib.test.plain_wording_support import assert_plain_message_is_sound

SETTING = CaptureSetting(camera="Cam", telescope="Scope", filter_name="L", exposure_seconds=120.0)
STAR_WIDTH_ARCSEC = 3.0
NIGHT_SECONDS = 3000.0


def _samples(
    bad_start: float | None,
    bad_length: float = 60.0,
    period: float = 300.0,
    good: float = 0.1,
    bad: float = 1.5,
) -> list[dict[str, float]]:
    """Build a guide log with a bad stretch in every period.

    Parameters
    ----------
    bad_start : `float` or `None`
        Seconds into each period where the bad stretch begins. `None` gives
        a log with no bad stretch.
    bad_length : `float`, optional
        The length of the bad stretch.
    period : `float`, optional
        The repeat period.
    good, bad : `float`, optional
        The error in arcseconds in the good and bad stretches.

    Returns
    -------
    samples : `list` [`dict` [`str`, `float`]]
        One sample every 2 seconds for 3000 seconds.
    """
    rows = []
    for second in range(0, int(NIGHT_SECONDS), 2):
        inside = bad_start is not None and bad_start <= second % period < bad_start + bad_length
        rows.append({"timestamp": float(second), "dra": bad if inside else good, "ddec": 0.0})
    return rows


def test_the_allowed_error_follows_the_star_width_budget() -> None:
    """A 3 arcsec star may take 10% more width: 0.58 arcsec RMS."""
    assert allowed_guide_rms_arcsec(3.0, 0.10) == pytest.approx(0.584, abs=0.001)


def test_imaging_spans_split_at_long_gaps() -> None:
    """Frames 5 minutes apart join; a 20-minute gap starts a new stretch."""
    spans = imaging_spans([(0.0, 60.0), (120.0, 180.0), (1500.0, 1560.0)])

    assert spans == [(0.0, 180.0), (1500.0, 1560.0)]


def test_a_bursty_log_is_sharp_in_short_windows_and_not_in_long_ones() -> None:
    """A bad minute in five spoils 20% of 30 s windows and all 300 s ones."""
    samples = _samples(150.0)
    allowed = allowed_guide_rms_arcsec(STAR_WIDTH_ARCSEC)

    short = sharpness_at_exposure([(0.0, NIGHT_SECONDS)], samples, 30.0, allowed)
    long = sharpness_at_exposure([(0.0, NIGHT_SECONDS)], samples, 300.0, allowed)

    assert short.windows_judged == 100
    assert short.sharp_fraction == pytest.approx(0.8)
    assert long.sharp_fraction == pytest.approx(0.0)


def test_the_supported_exposure_is_the_longest_that_reaches_the_target() -> None:
    """The ladder is judged at once; 30 s reaches 80%, 45 s and up do not."""
    spans = {"2026-10-01": [(0.0, NIGHT_SECONDS)]}
    widths = [STAR_WIDTH_ARCSEC] * 20

    outcome = assess_guiding_exposure(120.0, widths, spans, {"2026-10-01": _samples(150.0)})

    assert outcome.result is not None
    assert outcome.result.supported_exposure_seconds == pytest.approx(30.0)
    assert outcome.result.used.sharp_fraction < TARGET_SHARP_FRACTION
    assert outcome.result.nights == ["2026-10-01"]


def test_the_frames_own_share_is_found_from_their_windows() -> None:
    """Frames of 60 s from 0 to 3000 s: the share of them that stayed sharp."""
    spans = {"n": [(0.0, NIGHT_SECONDS)]}
    frames = [
        FrameWindow(frame_id=f"f{i}", start_epoch_seconds=i * 60.0, exposure_seconds=60.0) for i in range(50)
    ]

    outcome = assess_guiding_exposure(60.0, [STAR_WIDTH_ARCSEC] * 20, spans, {"n": _samples(150.0)}, frames)

    assert outcome.result is not None
    assert outcome.result.frames_sharp_fraction == pytest.approx(0.6)


def test_missing_guiding_is_a_reason_not_a_number() -> None:
    """No samples, or too few star widths, give a reason and no result."""
    spans = {"n": [(0.0, NIGHT_SECONDS)]}

    no_samples = assess_guiding_exposure(60.0, [3.0] * 20, spans, {"n": []})
    few_widths = assess_guiding_exposure(60.0, [3.0] * 3, spans, {"n": _samples(None)})

    assert no_samples.result is None
    assert "guide samples" in no_samples.reason
    assert few_widths.result is None
    assert "star width" in few_widths.reason


def test_the_statement_names_the_exposure_that_stays_sharp() -> None:
    """The recommendation gives the shares at the used and supported times."""
    spans = {"n": [(0.0, NIGHT_SECONDS)]}
    outcome = assess_guiding_exposure(120.0, [STAR_WIDTH_ARCSEC] * 20, spans, {"n": _samples(150.0)})

    (statement,) = guiding_limits_statements(SETTING, outcome)

    assert statement.kind == StatementKind.RECOMMENDATION
    assert statement.action_class == ActionClass.CAPTURE_SETTINGS
    assert statement.message.startswith("At 120 s only ")
    assert "; at 30 s, 80%" in statement.message
    assert_plain_message_is_sound(statement)
    assert "Try 30 s frames" in statement.plain_message
    assert statement.proposed_action.exposure_seconds == pytest.approx(30.0)
    assert statement.values["sharp_fraction_at_30s"] == pytest.approx(0.8)
    assert statement.values["allowed_guide_rms_arcsec"] == pytest.approx(0.584, abs=0.001)


def test_no_statement_when_the_guiding_is_good() -> None:
    """A quiet log keeps every window sharp, so the rule stays quiet."""
    spans = {"n": [(0.0, NIGHT_SECONDS)]}
    outcome = assess_guiding_exposure(120.0, [STAR_WIDTH_ARCSEC] * 20, spans, {"n": _samples(None)})

    assert guiding_limits_statements(SETTING, outcome) == []


def test_a_noisy_log_proposes_no_exposure_and_says_so() -> None:
    """With no exposure reaching the target, the action is a note only."""
    spans = {"n": [(0.0, NIGHT_SECONDS)]}
    outcome = assess_guiding_exposure(
        120.0, [STAR_WIDTH_ARCSEC] * 20, spans, {"n": _samples(0.0, bad_length=300.0)}
    )

    (statement,) = guiding_limits_statements(SETTING, outcome)

    assert statement.proposed_action.exposure_seconds is None
    assert statement.proposed_action.note
    assert "No tested exposure reaches" in statement.reason
    assert_plain_message_is_sound(statement)
    assert "Check the guiding first" in statement.plain_message


def test_missing_data_makes_an_insufficient_statement() -> None:
    """The report rule turns a reason into an insufficient-data statement."""
    spans = {"n": [(0.0, NIGHT_SECONDS)]}
    outcome = assess_guiding_exposure(120.0, [3.0] * 20, spans, {"n": []})

    (statement,) = guiding_limits_statements(SETTING, outcome)

    assert statement.kind == StatementKind.INSUFFICIENT_DATA
    assert statement.reason == outcome.reason
    assert_plain_message_is_sound(statement)
