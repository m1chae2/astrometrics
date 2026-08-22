"""Purpose: Unit tests for execute_meridian_flip.

Description: Verifies the flip completes and resumes when every step
succeeds, and each of the sequence's bounds -- exposure handling,
guiding stop, slew, realign iteration limit, and guide reacquire
attempts -- produces a non-resumed outcome with a failure_detail when
exhausted, without proceeding past the failing step -- the cases
`Wayfinding_Library_Architecture.md` §2.4.11 calls out.
"""

import pytest

from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.tasks.execution_tasks.meridian_flip import MeridianFlipSteps, execute_meridian_flip


def _steps(**overrides) -> MeridianFlipSteps:  # ruff: ignore[missing-type-kwargs]
    defaults = {
        "complete_or_abandon_exposure": lambda: True,
        "stop_guiding": lambda: True,
        "slew_through_flip": lambda: True,
        "realign_iteration": lambda: (True, 5.0),
        "reacquire_guide_star": lambda: True,
        "resume_exposure": lambda: True,
    }
    defaults.update(overrides)
    return MeridianFlipSteps(**defaults)


def test_flip_completes_and_resumes_when_every_step_succeeds():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a fully successful flip is marked completed and resumed."""
    outcome = execute_meridian_flip("flip-1", "entry-1", 5.0, _steps(), CorrectionConfig())
    assert outcome.flip_completed is True
    assert outcome.resumed is True
    assert outcome.failure_detail is None


def test_exposure_handling_failure_stops_immediately():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a failed exposure-handling step stops before later steps."""
    later_steps_called = []
    outcome = execute_meridian_flip(
        "flip-2",
        "entry-1",
        5.0,
        _steps(
            complete_or_abandon_exposure=lambda: False,
            stop_guiding=lambda: later_steps_called.append("guiding") or True,
        ),
        CorrectionConfig(),
    )
    assert outcome.resumed is False
    assert outcome.failure_detail is not None
    assert later_steps_called == []


def test_guiding_stop_failure_stops_before_slew():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a failed guiding-stop step stops before the slew step runs."""
    later_steps_called = []
    outcome = execute_meridian_flip(
        "flip-3",
        "entry-1",
        5.0,
        _steps(
            stop_guiding=lambda: False,
            slew_through_flip=lambda: later_steps_called.append("slew") or True,
        ),
        CorrectionConfig(),
    )
    assert outcome.resumed is False
    assert later_steps_called == []


def test_slew_failure_stops_before_realign():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a failed slew step stops before the realign loop runs."""
    later_steps_called = []
    outcome = execute_meridian_flip(
        "flip-4",
        "entry-1",
        5.0,
        _steps(
            slew_through_flip=lambda: False,
            realign_iteration=lambda: later_steps_called.append("realign") or (True, 1.0),
        ),
        CorrectionConfig(),
    )
    assert outcome.resumed is False
    assert later_steps_called == []


def test_realign_never_converges_exhausts_iteration_limit():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify realign_iteration always failing exhausts the iteration limit."""
    config = CorrectionConfig(alignment_iteration_limit=3)
    call_count = {"value": 0}

    def realign_iteration():  # ruff: ignore[missing-return-type-private-function]
        call_count["value"] += 1
        return False, 60.0

    later_steps_called = []
    outcome = execute_meridian_flip(
        "flip-5",
        "entry-1",
        5.0,
        _steps(
            realign_iteration=realign_iteration,
            reacquire_guide_star=lambda: later_steps_called.append("reacquire") or True,
        ),
        config,
    )
    assert outcome.resumed is False
    assert outcome.realign_attempts == 3
    assert call_count["value"] == 3
    assert outcome.residual_pointing_error_arcsec == pytest.approx(60.0)
    assert later_steps_called == []


def test_realign_converges_partway_through_iteration_budget():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a later-attempt convergence records the correct attempt count."""
    config = CorrectionConfig(alignment_iteration_limit=5)
    attempts = iter([(False, 40.0), (False, 20.0), (True, 8.0)])

    outcome = execute_meridian_flip(
        "flip-6", "entry-1", 5.0, _steps(realign_iteration=lambda: next(attempts)), config
    )
    assert outcome.realign_attempts == 3
    assert outcome.residual_pointing_error_arcsec == pytest.approx(8.0)
    assert outcome.resumed is True


def test_guide_reacquire_exhausts_attempt_budget():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify reacquire_guide_star always failing exhausts its budget."""
    config = CorrectionConfig(guide_reacquire_attempts=2)
    call_count = {"value": 0}

    def reacquire_guide_star():  # ruff: ignore[missing-return-type-private-function]
        call_count["value"] += 1
        return False

    later_steps_called = []
    outcome = execute_meridian_flip(
        "flip-7",
        "entry-1",
        5.0,
        _steps(
            reacquire_guide_star=reacquire_guide_star,
            resume_exposure=lambda: later_steps_called.append("resume") or True,
        ),
        config,
    )
    assert outcome.resumed is False
    assert outcome.guide_reacquire_attempts == 2
    assert call_count["value"] == 2
    assert later_steps_called == []


def test_resume_exposure_failure_is_final_bound():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a failed resume step leaves flip_completed False."""
    outcome = execute_meridian_flip(
        "flip-8", "entry-1", 5.0, _steps(resume_exposure=lambda: False), CorrectionConfig()
    )
    assert outcome.resumed is False
    assert outcome.flip_completed is False
    assert outcome.failure_detail is not None
