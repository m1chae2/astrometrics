"""Purpose: Unit tests for the guide-star loss event domain model.

Description: Verifies GuideStarLossEvent's default unrecovered state.
"""

from wayfindinglib.models.session.guide_star_loss import GuideStarLossEvent


def test_guide_star_loss_event_defaults_unrecovered():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a freshly opened event defaults to unrecovered, zero attempts."""
    event = GuideStarLossEvent(id="loss-1", observation_session_id="session-1", comparison_input_id="frame-1")

    assert event.recovered is False
    assert event.reacquire_attempts == 0
