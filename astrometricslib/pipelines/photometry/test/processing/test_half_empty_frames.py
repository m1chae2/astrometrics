"""Purpose: Test that a half-empty frame does not shrink the comparison pool.

Description: In the NGC 7331 session of 2026-10-04, 27 of 123 frames measured
only about half of the typical number of stars (a large dither moved half the
field off the sensor). A comparison star must have a flux in every usable
frame, and the old rule (usable at one half) kept those frames, so the pool
fell to 2 stars. These tests check that:

* a frame with half the stars is kept out of choosing the comparison set, and
  the pool keeps every star that the other frames measured;
* that frame stays in the light curves, normalized with the stars measured in
  it, and the analyzer warns and names it;
* the analyzer logs the count left after each pool rule every time, also
  when the set is large enough.
"""

import logging

import pytest

from astrometricslib.pipelines.photometry.processing.comparison_ensemble import (
    MINIMUM_USABLE_FRAME_FRACTION,
    build_flux_table,
    describe_comparison_pool,
    select_comparison_set,
)
from astrometricslib.pipelines.photometry.test.test_comparison_ensemble_injection import (
    FRAME_COUNT,
    SimulatedSession,
    analyze,
    simulate_session,
)

STAR_COUNT = 60
HALF_EMPTY_FRAME = 5


def _session_with_half_empty_frame() -> SimulatedSession:
    """Build a session where every second star is unmeasured in one frame.

    Returns
    -------
    session : `SimulatedSession`
        Sixty stars; the even-numbered ones have no flux in frame 5.
    """
    return simulate_session(
        star_count=STAR_COUNT,
        missing_in_frame=dict.fromkeys(range(0, STAR_COUNT, 2), HALF_EMPTY_FRAME),
    )


def test_the_default_usable_frame_share_keeps_typical_frames() -> None:
    """A frame at the typical count takes part; one at half does not."""
    assert 0.5 < MINIMUM_USABLE_FRAME_FRACTION.default < 1.0


def test_a_half_empty_frame_is_kept_apart_and_the_pool_keeps_every_other_star() -> None:
    """The frame leaves the fit table and the other stars stay candidates."""
    session = _session_with_half_empty_frame()

    table = build_flux_table(session.stars)

    assert table is not None
    assert len(table.timestamps) == FRAME_COUNT - 1
    assert table.unusable_timestamps == (session.timestamps[HALF_EMPTY_FRAME],)
    assert table.unusable_fluxes.shape == (STAR_COUNT, 1)
    selection = select_comparison_set(table)
    assert len(selection.member_rows) >= 5


def test_a_half_empty_frame_stays_in_the_light_curves_and_is_named_in_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The frame is normalized with its own stars and a warning names it."""
    session = _session_with_half_empty_frame()

    with caplog.at_level(logging.INFO):
        analyzer = analyze(session)

    stamp = session.timestamps[HALF_EMPTY_FRAME]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("1 frame(s)" in message and stamp.isoformat() in message for message in warnings)
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("Comparison pool:" in message and "never saturated" in message for message in infos)
    assert analyzer.comparison_set is not None
    assert stamp in analyzer.frame_reference_flux
    measured_star = next(star for star in analyzer.stellar_objects if star.id == "Star_1")
    assert len(measured_star.photometry.timestamps) == FRAME_COUNT


def test_the_pool_description_counts_each_rule() -> None:
    """The counts run from the stars measured everywhere to the vetted set."""
    session = simulate_session(star_count=STAR_COUNT)
    table = build_flux_table(session.stars)
    assert table is not None

    text = describe_comparison_pool(table, select_comparison_set(table))

    assert text.startswith(
        f"{STAR_COUNT} of {STAR_COUNT} stars have a flux in at least 95% of the {FRAME_COUNT}"
    )
    assert "candidates passed the constancy check" in text
