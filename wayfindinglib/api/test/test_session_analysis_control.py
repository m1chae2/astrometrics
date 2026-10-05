"""Purpose: End-to-end tests for the session-quality analysis on the facade.

Description: Runs the guiding analysis behind `control.history.query`
(`night_analysis.guiding_night_analysis` and `guiding_night_summaries`) against
a real isolated configuration, a real Butler and a real log database filled
with synthetic nights. The main thing verified is that a night is judged only
against the equipment's earlier nights, never against itself.
"""

import pytest

from wayfindinglib import ObservatoryControl
from wayfindinglib.api.test.guiding_night_helpers import (
    FIRST_NIGHT,
    record_good_history,
    record_guiding_night,
)
from wayfindinglib.tasks.control_tasks import night_analysis


def test_an_unknown_night_gives_none(control: ObservatoryControl) -> None:
    """Verify a night with nothing recorded is not invented."""
    assert night_analysis.guiding_night_analysis(control._context, "1999-01-01") is None


def test_a_good_night_after_arecord_good_history_is_not_flagged(control: ObservatoryControl) -> None:
    """Verify an ordinary night passes every check."""
    record_good_history(control)
    night = record_guiding_night(control, 6, snr=300.0, sigma=1.0)

    analysis = night_analysis.guiding_night_analysis(control._context, night)

    assert analysis.input_quality.limits_equipment_match == "exact"
    assert analysis.flagged is False
    assert analysis.input_quality.has_low_signal is False


def test_a_weak_lossy_night_is_flagged_against_the_earlier_nights(control: ObservatoryControl) -> None:
    """Verify the equipment's own history decides what is unusual."""
    record_good_history(control)
    night = record_guiding_night(control, 6, snr=30.0, lost=200)

    analysis = night_analysis.guiding_night_analysis(control._context, night)

    assert analysis.flagged is True
    assert "check_guide_signal" in analysis.flag_reasons
    assert analysis.input_quality.has_low_signal is True
    assert analysis.input_quality.has_high_loss is True


def test_a_bad_night_is_not_judged_against_itself(control: ObservatoryControl) -> None:
    """Verify the history behind a night's limits stops before that night.

    Regression test, found on real data: the baseline used every recorded
    night, including the one being judged and the bad nights after it. A bad
    night therefore raised the limit it was compared with, and several nights
    that were plainly poor (9% of frames lost, weak guide star) passed.
    """
    record_good_history(control)
    night = record_guiding_night(control, 6, snr=30.0, lost=200)
    record_guiding_night(control, 7, snr=25.0, lost=180)
    record_guiding_night(control, 8, snr=28.0, lost=220)

    analysis = night_analysis.guiding_night_analysis(control._context, night)
    envelope_before = control.history.get_performance_envelope(before_night=night)
    envelope_with_everything = control.history.get_performance_envelope()

    assert analysis.flagged is True
    assert envelope_before.thresholds["guide_snr_low_limit"].sample_count == 6
    assert envelope_with_everything.thresholds["guide_snr_low_limit"].sample_count == 9
    assert envelope_with_everything.value("guide_snr_low_limit") < envelope_before.value(
        "guide_snr_low_limit"
    )


def test_a_night_with_too_little_history_gets_numbers_but_no_baseline_verdict(
    control: ObservatoryControl,
) -> None:
    """Verify new equipment is not judged by a history it lacks."""
    record_guiding_night(control, 0)
    night = record_guiding_night(control, 1, snr=30.0, lost=200)

    analysis = night_analysis.guiding_night_analysis(control._context, night)

    assert analysis.input_quality.median_snr == pytest.approx(30.0)
    assert analysis.input_quality.has_low_signal is None
    assert analysis.input_quality.has_high_loss is None


def test_a_night_on_other_equipment_gets_no_limits(control: ObservatoryControl) -> None:
    """Verify other equipment is not judged by this setup's limits."""
    other = "telescope=other|camera=other|guide_focal_mm=240|guide_scale=3.2"
    night = record_guiding_night(control, 0, fingerprint=other)
    runs = night_analysis.guiding_runs(control._context, night)
    control.guiding.save_run(
        runs[0].model_copy(update={"focal_length_mm": 240.0, "pixel_scale_arcsec_per_px": 3.2})
    )

    analysis = night_analysis.guiding_night_analysis(control._context, night)

    assert analysis.input_quality.limits_equipment_match == "none"
    assert analysis.input_quality.has_low_signal is None
    assert analysis.performance.rms_per_axis_arcsec is not None


def test_estimated_samples_never_reach_the_analysis(control: ObservatoryControl) -> None:
    """Verify fabricated pulse-derived samples never change an analysis."""
    night = record_guiding_night(control, 0)
    start = FIRST_NIGHT
    control._context.logger_interface.replace_guiding_samples([
        {
            "timestamp": start + 60.0 + index,
            "dra": 50.0,
            "ddec": 50.0,
            "snr": 22.0,
            "source": "indi_pulse_estimate",
        }
        for index in range(800)
    ])

    analysis = night_analysis.guiding_night_analysis(control._context, night)

    assert analysis.input_quality.samples_analyzed == 400


def test_the_summary_has_one_row_per_night_oldest_first(control: ObservatoryControl) -> None:
    """Verify the overview lists every night with its flag and kinds."""
    nights = record_good_history(control)
    bad = record_guiding_night(control, 6, snr=30.0, lost=200)

    rows = night_analysis.guiding_night_summaries(control._context)

    assert [row["sessionId"] for row in rows] == [*nights, bad]
    assert rows[-1]["flagged"] is True
    assert "check_guide_signal" in rows[-1]["recommendations"]
    assert all(row["flagged"] is False for row in rows[:-1])


def test_the_analysis_is_not_stored(control: ObservatoryControl) -> None:
    """Verify analysing a night writes nothing, so nothing can go stale."""
    night = record_guiding_night(control, 0)
    before = {
        name: len(control._context.butler.get_all(name)) for name in ("ekos_session_context", "guiding_run")
    }
    samples_before = len(control._context.logger_interface.get_guiding_logs(limit=100000))

    night_analysis.guiding_night_analysis(control._context, night)
    night_analysis.guiding_night_summaries(control._context)

    after = {
        name: len(control._context.butler.get_all(name)) for name in ("ekos_session_context", "guiding_run")
    }
    assert after == before
    assert len(control._context.logger_interface.get_guiding_logs(limit=100000)) == samples_before
