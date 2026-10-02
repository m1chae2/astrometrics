"""Purpose: Unit tests for the exposure-length view.

Description: Builds nights of guide samples with a known fault (a lost star, a
jump, steady wobble, a pause between runs) and checks that each is blamed on
the right cause, that it spoils only the exposures it overlaps, and that longer
exposures are spoiled more often.
"""

from typing import Any

import pytest

from wayfindinglib.session_analysis.guiding.processing.measure_exposure_feasibility import (
    RELIABLE_CLEAN_FRACTION,
    measure_exposure_feasibility,
)

_CADENCE = 3.2
"""Seconds between guide samples, this observatory's guide cycle."""


def _by_length(feasibility: list) -> dict[float, Any]:
    """Index the results by exposure length.

    Returns
    -------
    by_length : `dict` [`float`, `ExposureFeasibility`]
        Each result, keyed by its exposure length.
    """
    return {result.exposure_seconds: result for result in feasibility}


def test_a_steady_night_is_clean_at_every_length(make_samples: Any, make_envelope: Any) -> None:
    """Verify guiding that holds gives clean exposures of any length."""
    samples = make_samples(count=1500, sigma=0.5)

    feasibility, reliable = measure_exposure_feasibility(samples, [30, 120, 300], make_envelope(), "exact")

    assert all(result.clean_fraction == pytest.approx(1.0) for result in feasibility)
    assert reliable == pytest.approx(300.0)


def test_a_lost_star_spoils_only_the_exposures_that_overlap_it(
    make_samples: Any, make_envelope: Any, make_run: Any
) -> None:
    """Verify a 60 s gap is a lost star that longer exposures overlap more."""
    samples = make_samples(count=1500, sigma=0.5)
    start = samples[0]["timestamp"]
    samples = [s for s in samples if not (start + 2000 <= s["timestamp"] < start + 2060)]

    run = make_run(start=start, duration=samples[-1]["timestamp"] - start)

    feasibility, _ = measure_exposure_feasibility(samples, [30, 300], make_envelope(), "exact", [run])
    short, long = _by_length(feasibility)[30], _by_length(feasibility)[300]

    assert 0.0 < short.lost_fraction < long.lost_fraction
    assert long.clean_fraction < short.clean_fraction < 1.0
    assert long.jump_fraction == pytest.approx(0.0)


def test_one_missed_frame_is_not_a_lost_star(make_samples: Any, make_envelope: Any) -> None:
    """Verify a single dropped frame does not spoil an exposure."""
    samples = make_samples(count=1500, sigma=0.5)
    del samples[700]

    feasibility, _ = measure_exposure_feasibility(samples, [60], make_envelope(), "exact")

    assert feasibility[0].lost_fraction == pytest.approx(0.0)
    assert feasibility[0].clean_fraction == pytest.approx(1.0)


def test_a_jump_is_blamed_on_the_jump(make_samples: Any, make_envelope: Any) -> None:
    """Verify a sample beyond the excursion limit spoils its windows."""
    samples = make_samples(count=1500, sigma=0.5)
    samples[700]["dra"] = 50.0

    feasibility, _ = measure_exposure_feasibility(samples, [30, 300], make_envelope(), "exact")
    by_length = _by_length(feasibility)

    assert by_length[30].jump_fraction > 0.0
    assert by_length[300].jump_fraction > by_length[30].jump_fraction
    assert by_length[300].lost_fraction == pytest.approx(0.0)


def test_steady_wobble_spoils_every_exposure(make_samples: Any, make_envelope: Any) -> None:
    """Verify an error scatter above the limit fails every window."""
    samples = make_samples(count=1500, sigma=2.5)
    for sample in samples:
        sample["dra"] = max(min(sample["dra"], 5.0), -5.0)
        sample["ddec"] = max(min(sample["ddec"], 5.0), -5.0)

    feasibility, reliable = measure_exposure_feasibility(samples, [60], make_envelope(), "exact")

    assert feasibility[0].wobble_fraction == pytest.approx(1.0)
    assert feasibility[0].clean_fraction == pytest.approx(0.0)
    assert reliable is None


def test_a_steady_offset_from_the_lock_position_is_not_wobble(make_samples: Any, make_envelope: Any) -> None:
    """Verify an offset that does not change does not blur a star."""
    samples = make_samples(count=1500, sigma=0.4)
    for sample in samples:
        sample["dra"] += 4.0

    feasibility, _ = measure_exposure_feasibility(samples, [60], make_envelope(), "exact")

    assert feasibility[0].clean_fraction == pytest.approx(1.0)


def test_a_pause_between_guiding_runs_is_not_blamed_on_the_guider(
    make_samples: Any, make_envelope: Any
) -> None:
    """Verify windows with no guiding running are not counted."""
    first = make_samples(count=600, sigma=0.5)
    second = make_samples(count=600, sigma=0.5, start=first[-1]["timestamp"] + 3000.0, seed=2)

    feasibility, _ = measure_exposure_feasibility(first + second, [60], make_envelope(), "exact")

    assert feasibility[0].clean_fraction == pytest.approx(1.0)
    assert feasibility[0].lost_fraction == pytest.approx(0.0)


def test_a_length_longer_than_the_night_has_too_few_windows_to_report(
    make_samples: Any, make_envelope: Any
) -> None:
    """Verify a length the data cannot test is left out, not guessed."""
    samples = make_samples(count=300, sigma=0.5)

    feasibility, _ = measure_exposure_feasibility(samples, [30, 600], make_envelope(), "exact")

    assert [result.exposure_seconds for result in feasibility] == [30.0]


def test_the_longest_reliable_length_needs_half_the_exposures_clean(
    make_samples: Any, make_envelope: Any
) -> None:
    """Verify the reliable length is the longest that reaches the threshold."""
    samples = make_samples(count=1500, sigma=0.5)
    for index in range(100, 1500, 100):
        samples[index]["dra"] = 50.0

    feasibility, reliable = measure_exposure_feasibility(samples, [30, 120, 300], make_envelope(), "exact")
    by_length = _by_length(feasibility)

    assert by_length[30].clean_fraction >= RELIABLE_CLEAN_FRACTION
    assert by_length[300].clean_fraction < RELIABLE_CLEAN_FRACTION
    assert reliable in (30.0, 120.0)


def test_nothing_is_measured_without_limits(make_samples: Any, make_envelope: Any) -> None:
    """Verify no envelope, or other equipment, gives no view."""
    samples = make_samples(count=1500, sigma=0.5)

    assert measure_exposure_feasibility(samples, [60], None, "exact") == ([], None)
    assert measure_exposure_feasibility(samples, [60], make_envelope(), "none") == ([], None)
    assert measure_exposure_feasibility([], [60], make_envelope(), "exact") == ([], None)
    assert measure_exposure_feasibility(samples, [], make_envelope(), "exact") == ([], None)
