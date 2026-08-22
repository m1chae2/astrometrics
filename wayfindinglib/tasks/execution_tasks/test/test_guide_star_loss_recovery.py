"""Purpose: Unit tests for attempt_guide_star_recovery.

Description: Verifies a recovered attempt stops the loop early and
records the attempt count it succeeded at, and an exhausted loop
reports unrecovered at the configured bound -- mirroring
`test_meridian_flip.py`'s coverage of the same bounded-loop shape.
"""

from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.tasks.execution_tasks.guide_star_loss_recovery import attempt_guide_star_recovery


def test_recovery_stops_at_the_first_successful_attempt():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the loop stops as soon as reacquisition succeeds."""
    attempts_made = []

    def _reacquire() -> bool:
        attempts_made.append(1)
        return len(attempts_made) == 2

    event = attempt_guide_star_recovery(
        "loss-1", "session-1", "frame-1", _reacquire, CorrectionConfig(guide_reacquire_attempts=5)
    )

    assert event.recovered is True
    assert event.reacquire_attempts == 2
    assert len(attempts_made) == 2


def test_recovery_reports_unrecovered_after_exhausting_attempts():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an always-failing reacquisition exhausts the configured bound."""
    event = attempt_guide_star_recovery(
        "loss-2", "session-1", "frame-2", lambda: False, CorrectionConfig(guide_reacquire_attempts=3)
    )

    assert event.recovered is False
    assert event.reacquire_attempts == 3


def test_recovery_succeeds_on_the_first_attempt():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a first-attempt success does not run any further attempts."""
    attempts_made = []

    def _reacquire() -> bool:
        attempts_made.append(1)
        return True

    event = attempt_guide_star_recovery(
        "loss-3", "session-1", "frame-3", _reacquire, CorrectionConfig(guide_reacquire_attempts=5)
    )

    assert event.recovered is True
    assert event.reacquire_attempts == 1
    assert len(attempts_made) == 1
