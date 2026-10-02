"""Purpose: Unit tests for guiding pre-processing.

Description: Verifies that the night's data-gathering problems are found and
judged against equipment-derived limits: lost frames, excursions, weak guide
signal, impossible calibrations and too little data.
"""

from typing import Any

import pytest

from wayfindinglib.analytics.performance_envelope import MINIMUM_SAMPLES_PER_SESSION
from wayfindinglib.session_analysis.guiding.pre_processing.assess_guiding_input_quality import (
    assess_guiding_input_quality,
)


def _assess(samples, runs, envelope, match="exact", scale_matches=True):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Run pre-processing with this module's usual arguments.

    Returns
    -------
    input_quality : `GuidingInputQuality`
        The assessment.
    """
    return assess_guiding_input_quality(samples, runs, envelope, match, scale_matches)


def test_a_clean_night_passes_every_check(make_samples: Any, make_run: Any, make_envelope: Any) -> None:
    """Verify a normal night has no problem flagged."""
    quality = _assess(make_samples(), [make_run()], make_envelope())

    assert quality.has_enough_samples is True
    assert quality.has_high_loss is False
    assert quality.has_frequent_excursions is False
    assert quality.has_low_signal is False
    assert quality.calibration_problems == []


def test_lost_frames_come_from_the_run_records(make_samples: Any, make_run: Any, make_envelope: Any) -> None:
    """Verify lost frames are counted from runs, since they are not samples."""
    runs = [make_run(0, frames_total=400, frames_lost=8), make_run(1, frames_total=200, frames_lost=12)]

    quality = _assess(make_samples(), runs, make_envelope())

    assert quality.frames_total == 600
    assert quality.frames_lost == 20
    assert quality.lost_fraction == pytest.approx(20 / 600)


def test_lost_frames_above_the_equipments_own_limit_are_flagged(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify a night worse than 90% of earlier nights is flagged."""
    quality = _assess(make_samples(), [make_run(frames_total=400, frames_lost=100)], make_envelope())

    assert quality.has_high_loss is True
    assert quality.lost_fraction_limit == pytest.approx(0.002)


def test_a_few_lost_frames_are_not_flagged_when_earlier_nights_lost_some(
    make_samples: Any, make_run: Any, make_envelope: Any, good_baseline: Any
) -> None:
    """Verify the limit is the equipment's own history, not zero."""
    good_baseline["guide_lost_fraction"] = [0.0, 0.01, 0.02, 0.0, 0.015, 0.03]

    quality = _assess(
        make_samples(), [make_run(frames_total=400, frames_lost=4)], make_envelope(baseline=good_baseline)
    )

    assert quality.has_high_loss is False


def test_excursions_are_samples_beyond_the_equipment_derived_limit(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify samples over the excursion limit are counted."""
    envelope = make_envelope()
    limit = envelope.value("guide_excursion_limit")
    samples = make_samples(count=400)
    for sample in samples[:40]:
        sample["dra"] = 3.0 * limit

    quality = _assess(samples, [make_run()], envelope)

    assert quality.excursion_count >= 40
    assert quality.excursion_fraction >= 0.1
    assert quality.has_frequent_excursions is True


def test_a_weak_guide_star_is_flagged_against_the_equipments_own_history(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify a signal well below earlier nights is low."""
    quality = _assess(make_samples(snr=40.0), [make_run()], make_envelope())

    assert quality.median_snr == pytest.approx(40.0)
    assert quality.has_low_signal is True


def test_an_impossible_calibrated_speed_is_reported(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify a mount speed above the sidereal rate is a calibration problem.

    Real case: the guider calibrations of 2026-09-21 and 2026-09-23 measured
    1086 and 446 arcsec/s against a sidereal rate of 15 arcsec/s.
    """
    quality = _assess(make_samples(), [make_run(ra_rate=446.0, dec_rate=908.0)], make_envelope())

    assert len(quality.calibration_problems) == 2
    assert "446.0" in quality.calibration_problems[0]
    assert "sidereal" in quality.calibration_problems[0]


def test_a_non_positive_calibrated_speed_is_reported(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify a zero or negative speed is also a calibration problem."""
    quality = _assess(make_samples(), [make_run(ra_rate=0.0)], make_envelope())

    assert any("not positive" in problem for problem in quality.calibration_problems)


def test_a_run_with_no_samples_does_not_count_for_calibration(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify a calibration that guided nothing is not counted."""
    quality = _assess(make_samples(), [make_run(ra_rate=446.0, samples_stored=0)], make_envelope())

    assert quality.calibration_problems == []


def test_calibration_is_checked_against_the_sidereal_rate_even_without_limits(
    make_samples: Any, make_run: Any
) -> None:
    """Verify the sidereal-rate check needs no equipment limits."""
    quality = _assess(make_samples(), [make_run(ra_rate=446.0)], None, match="none")

    assert len(quality.calibration_problems) == 1


def test_a_night_needs_enough_samples(make_samples: Any, make_run: Any, make_envelope: Any) -> None:
    """Verify the boundary at the minimum sample count."""
    envelope = make_envelope()

    assert (
        _assess(make_samples(MINIMUM_SAMPLES_PER_SESSION), [make_run()], envelope).has_enough_samples is True
    )
    assert (
        _assess(make_samples(MINIMUM_SAMPLES_PER_SESSION - 1), [make_run()], envelope).has_enough_samples
        is False
    )


def test_the_guide_cadence_ignores_pauses_between_runs(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify a long gap between runs is not the cadence."""
    samples = make_samples(count=200, cadence=3.2) + make_samples(
        count=200, start=1.79e9 + 4000.0, cadence=3.2
    )

    quality = _assess(samples, [make_run()], make_envelope())

    assert quality.cadence_seconds == pytest.approx(3.2)


def test_no_limits_apply_to_equipment_that_does_not_match(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify a night on other equipment is neither passed nor failed."""
    quality = _assess(make_samples(snr=40.0), [make_run(frames_lost=200)], make_envelope(), match="none")

    assert quality.has_low_signal is None
    assert quality.has_high_loss is None
    assert quality.has_frequent_excursions is None
    assert quality.limits_equipment_match == "none"


def test_a_limit_with_too_little_history_gives_no_verdict(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify new equipment gets the number but no verdict."""
    quality = _assess(make_samples(snr=40.0), [make_run(frames_lost=50)], make_envelope(baseline=None))

    assert quality.median_snr == pytest.approx(40.0)
    assert quality.has_low_signal is None
    assert quality.has_high_loss is None
    assert quality.lost_fraction == pytest.approx(50 / 400)


def test_the_scale_check_is_passed_through(make_samples: Any, make_run: Any, make_envelope: Any) -> None:
    """Verify the guide-scale agreement is recorded."""
    quality = _assess(make_samples(), [make_run()], make_envelope(), scale_matches=False)

    assert quality.guide_scale_matches_configuration is False


def test_guided_time_adds_up_the_runs(make_samples: Any, make_run: Any, make_envelope: Any) -> None:
    """Verify guided time is the total of the runs' lengths."""
    runs = [make_run(0, duration=1000.0), make_run(1, start=1.79e9 + 5000.0, duration=500.0)]

    quality = _assess(make_samples(), runs, make_envelope())

    assert quality.runs == 2
    assert quality.guided_seconds == pytest.approx(1500.0)


def test_a_dim_guide_star_is_judged_against_the_equipments_earlier_nights(
    make_samples: Any, make_envelope: Any
) -> None:
    """Verify brightness far below the usual is flagged, the usual is not."""
    usual = assess_guiding_input_quality(make_samples(), [], make_envelope(), "exact", True)
    dim = assess_guiding_input_quality(make_samples(star_mass=4000.0), [], make_envelope(), "exact", True)

    assert usual.has_dim_star is False
    assert dim.has_dim_star is True
    assert dim.median_star_mass == pytest.approx(4000.0)
    assert dim.star_mass_limit < usual.median_star_mass
    assert dim.typical_star_mass == pytest.approx(295000.0, rel=0.05)
    assert dim.typical_cadence_seconds == pytest.approx(3.2, rel=0.05)


def test_star_brightness_is_not_judged_without_earlier_nights(make_samples: Any, make_envelope: Any) -> None:
    """Verify new equipment is neither passed nor failed on brightness."""
    quality = assess_guiding_input_quality(
        make_samples(star_mass=4000.0), [], make_envelope(baseline=None), "exact", True
    )

    assert quality.median_star_mass == pytest.approx(4000.0)
    assert quality.has_dim_star is None


def test_samples_without_a_star_mass_give_no_brightness(make_samples: Any, make_envelope: Any) -> None:
    """Verify older samples with no brightness are not treated as zero."""
    samples = make_samples()
    for sample in samples:
        sample["star_mass"] = None

    quality = assess_guiding_input_quality(samples, [], make_envelope(), "exact", True)

    assert quality.median_star_mass is None
    assert quality.has_dim_star is None
