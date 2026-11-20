"""Purpose: Tests for the tracking-risk jitter limits and score breakpoints.

Description: The safe and trailing jitter limits follow the star width and
the plate scale; with a plate scale given they are 0.75 and 1.25 pixels on the
reference setup, without one the setup's plate scale is used, and with no
setup at all the fixed fallbacks stand. A jitter at the safe limit scores the
caution breakpoint.
"""

import pytest

from astrometricslib.parameters import RunBasis, parameter_context, record_basis
from wayfindinglib.analytics import tracking_risk


def test_a_given_plate_scale_gives_the_reference_pixel_limits() -> None:
    """At the reference star width, the limits are 0.75 and 1.25 pixels."""
    with parameter_context():
        assert tracking_risk._rms_limits(2.0) == pytest.approx((1.5, 2.5))


def test_wider_stars_allow_more_jitter() -> None:
    """Doubling the star width doubles both limits."""
    with parameter_context():
        record_basis(RunBasis.STAR_WIDTH_PX, 2.0 * 2.33)
        assert tracking_risk._rms_limits(1.0) == pytest.approx((1.5, 2.5))


def test_with_no_plate_scale_at_all_the_fallbacks_stand() -> None:
    """A run with no setup and no plate scale uses the fixed fallbacks."""
    with parameter_context():
        assert tracking_risk._rms_limits(None) == pytest.approx((1.2, 2.0))


def test_a_jitter_at_the_safe_limit_scores_the_caution_breakpoint() -> None:
    """The score just above the safe limit is at the amber breakpoint."""
    with parameter_context():
        ideal, _ = tracking_risk._rms_limits(1.0)
        assert tracking_risk.rms_risk_score(ideal * 1.0001, 1.0) == pytest.approx(0.28, abs=0.001)
