"""Purpose: Tests for `control.history.query(kind="night_quality")`.

Description: Checks that the history query serves the typed night review,
refuses a review without a night, and refuses arguments the kind does not use.
The science library is a made-up stand-in injected into the Control context.
"""

import pytest

from astrometricslib import InvalidArgumentError
from wayfindinglib import ObservatoryControl
from wayfindinglib.analytics.test.night_quality_helpers import (
    REVIEWED,
    STAR,
    FakeLibrary,
    make_rows,
    make_target,
)
from wayfindinglib.models.session.night_quality import NightQuality
from wayfindinglib.tasks.control_tasks import night_analysis


@pytest.fixture(autouse=True)
def _no_guiding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the guiding analysis, which has its own tests, by no data."""
    monkeypatch.setattr(night_analysis, "guiding_night_analysis", lambda context, night_id: None)


def test_the_history_query_serves_the_night_review(control: ObservatoryControl) -> None:
    """The query returns the typed review of the night."""
    target = make_target(STAR, 5)
    control._context._astrometrics = FakeLibrary([target], make_rows(target, REVIEWED))

    quality = control.history.query(kind="night_quality", session_id=REVIEWED, register_job=False)

    assert isinstance(quality, NightQuality)
    assert quality.night_id == REVIEWED
    assert quality.targets[0].target_id == "HD 1"


def test_the_history_query_needs_a_night(control: ObservatoryControl) -> None:
    """A night review without a night is refused."""
    with pytest.raises(InvalidArgumentError):
        control.history.query(kind="night_quality", register_job=False)


def test_the_history_query_refuses_arguments_it_does_not_use(control: ObservatoryControl) -> None:
    """An Ekos file id means nothing to a night review, so it is refused."""
    with pytest.raises(InvalidArgumentError):
        control.history.query(kind="night_quality", session_id=REVIEWED, ekos_file_id="x", register_job=False)
