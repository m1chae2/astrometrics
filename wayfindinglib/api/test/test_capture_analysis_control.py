"""Purpose: End-to-end tests for the capture analysis on the facade.

Description: Runs the capture analysis behind `control.history.query`
(`night_analysis.capture_night_analysis` and `capture_night_summaries`) against
a real isolated configuration, a real Butler and a real log database. The
science library's frame records are replaced by synthetic light frames, so the
tests state only what makes a night different. The main thing verified is that
a night is judged only against the equipment's earlier nights, never against
itself, and that the science library's saturation verdicts reach the
recommendations.
"""

import pytest

from wayfindinglib import ObservatoryControl
from wayfindinglib.api.test.capture_night_helpers import (
    Library,
    add_frames,
    record_ekos_captures,
    record_good_history,
)
from wayfindinglib.api.test.guiding_night_helpers import record_guiding_night
from wayfindinglib.models.session.capture_frame import StackSaturationVerdict
from wayfindinglib.tasks.control_tasks import night_analysis


def test_an_unknown_night_gives_none(control: ObservatoryControl, library: Library) -> None:
    """Verify a night with nothing recorded is not invented."""
    assert night_analysis.capture_night_analysis(control._context, "1999-01-01") is None


def test_an_ordinary_night_after_arecord_good_history_is_not_flagged(
    control: ObservatoryControl, library: Library
) -> None:
    """Verify an ordinary night passes every check."""
    record_good_history(control, library)
    record_guiding_night(control, 6)
    night = add_frames(library, 6)
    record_ekos_captures(control, 6, 30)

    analysis = night_analysis.capture_night_analysis(control._context, night)

    assert analysis.input_quality.limits_equipment_match == "exact"
    assert analysis.input_quality.captures_without_frame == 0
    assert analysis.performance.star_quality.median_star_width_arcsec == pytest.approx(5.5)
    assert analysis.performance.star_quality.star_width_limit_arcsec is not None
    assert not analysis.flagged


def test_soft_stars_are_flagged_against_the_earlier_nights(
    control: ObservatoryControl, library: Library
) -> None:
    """Verify the equipment's own history decides what is unusual."""
    record_good_history(control, library)
    record_guiding_night(control, 6)
    night = add_frames(library, 6, star_width_arcsec=12.0)

    analysis = night_analysis.capture_night_analysis(control._context, night)

    assert analysis.flagged
    assert analysis.flag_reasons == ["star_width_above_baseline"]


def test_a_soft_night_is_not_judged_against_itself(control: ObservatoryControl, library: Library) -> None:
    """Verify the history behind a night's limits stops before that night.

    Later soft nights would otherwise raise the limit a soft night is
    compared with, and it would pass.
    """
    record_good_history(control, library)
    for day in (6, 7, 8):
        record_guiding_night(control, day)
    night = add_frames(library, 6, star_width_arcsec=12.0)
    add_frames(library, 7, star_width_arcsec=12.0)
    add_frames(library, 8, star_width_arcsec=12.0)

    analysis = night_analysis.capture_night_analysis(control._context, night)
    with_everything = control.history.get_performance_envelope()
    before = control.history.get_performance_envelope(before_night=night)

    assert analysis.flagged
    assert before.thresholds["night_star_width_high_limit"].sample_count == 6
    assert with_everything.thresholds["night_star_width_high_limit"].sample_count == 9
    assert with_everything.value("night_star_width_high_limit") > before.value("night_star_width_high_limit")


def test_a_night_with_too_little_history_gets_numbers_but_no_verdict(
    control: ObservatoryControl, library: Library
) -> None:
    """Verify new equipment is not judged by a history it lacks."""
    record_guiding_night(control, 0)
    night = add_frames(library, 0, star_width_arcsec=12.0)

    analysis = night_analysis.capture_night_analysis(control._context, night)

    assert analysis.performance.star_quality.median_star_width_arcsec == pytest.approx(12.0)
    assert analysis.performance.star_quality.star_width_limit_arcsec is None
    assert not analysis.flagged
    assert "insufficient_data" in [r.kind.value for r in analysis.recommendations]


def test_exposures_ekos_finished_that_are_not_in_the_library_are_reported(
    control: ObservatoryControl, library: Library
) -> None:
    """Verify a download gap shows when Ekos is compared with the library."""
    record_guiding_night(control, 0)
    night = add_frames(library, 0, count=30)
    record_ekos_captures(control, 0, 41)

    analysis = night_analysis.capture_night_analysis(control._context, night)

    assert analysis.input_quality.ekos_light_captures == 41
    assert analysis.input_quality.captures_without_frame == 11
    assert "captures_not_in_library" in [r.kind.value for r in analysis.recommendations]


def test_the_science_librarys_verdict_reaches_the_recommendation(
    control: ObservatoryControl, library: Library
) -> None:
    """Verify a stacked target's saturation verdict is used and quoted."""
    record_guiding_night(control, 0)
    night = add_frames(library, 0, is_spectral=True, exposure_seconds=2.0, filter_name="Star Analyzer 200")
    library.verdicts.append(
        StackSaturationVerdict(
            target_id="Target",
            is_spectral=True,
            exposure_seconds=2.0,
            saturated=True,
            recommended_exposure_seconds=0.55,
        )
    )

    analysis = night_analysis.capture_night_analysis(control._context, night)

    (recommendation,) = [r for r in analysis.recommendations if r.kind.value == "spectral_star_clipped"]
    assert recommendation.confidence == "high"
    assert "0.55 s" in recommendation.message


def test_the_summary_has_one_row_per_night_oldest_first(
    control: ObservatoryControl, library: Library
) -> None:
    """Verify the overview lists every night with its flag and kinds."""
    record_good_history(control, library, nights=3)

    rows = night_analysis.capture_night_summaries(control._context)

    assert [row["sessionId"] for row in rows] == sorted(row["sessionId"] for row in rows)
    assert len(rows) == 3
    assert {"flagged", "lightFrames", "capturesWithoutFrame", "recommendations"} <= set(rows[0])
