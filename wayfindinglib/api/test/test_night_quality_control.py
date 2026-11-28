"""Purpose: Tests for `review.nights.get`, the review of one night.

Description: Checks that `get` serves the typed night review, and that a
night with nothing recorded gives `None` unless the night is required.
The science library is a made-up stand-in injected into the Control context.
"""

import pytest

from astrometricslib.exceptions import NotFoundError
from wayfindinglib import ObservatoryControl
from wayfindinglib.analytics.test.library_version_helpers import attach_library_versions
from wayfindinglib.analytics.test.night_quality_helpers import (
    REVIEWED,
    STAR,
    FakeLibrary,
    make_rows,
    make_target,
)
from wayfindinglib.api.review import ObservationReview
from wayfindinglib.models.session.night_quality import NightQuality
from wayfindinglib.tasks.control_tasks import night_analysis, night_quality_task


@pytest.fixture(autouse=True)
def _no_guiding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the guiding analysis, which has its own tests, by no data."""
    monkeypatch.setattr(night_analysis, "guiding_night_analysis", lambda context, night_id, **_: None)


def test_get_serves_the_night_review(control: ObservatoryControl) -> None:
    """`nights.get` returns the typed review of the night."""
    target = make_target(STAR, 5)
    control._context._astrometrics = attach_library_versions(
        FakeLibrary([target], make_rows(target, REVIEWED)), control._context.config
    )

    quality = ObservationReview(control).nights.get(REVIEWED)

    assert isinstance(quality, NightQuality)
    assert quality.night_id == REVIEWED
    assert quality.targets[0].target_id == "HD 1"


def test_a_night_with_nothing_recorded_is_none_unless_required(
    control: ObservatoryControl, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A night with nothing recorded gives `None`, or raises if required."""

    def missing(context: object, night: str) -> NightQuality:
        """Fail as a night with nothing recorded does.

        Raises
        ------
        NotFoundError
            Always.
        """
        raise NotFoundError(f"Nothing recorded for {night}.")

    monkeypatch.setattr(night_quality_task, "build_night_quality", missing)
    assert ObservationReview(control).nights.get("2030-01-01") is None
    with pytest.raises(NotFoundError):
        ObservationReview(control).nights.get("2030-01-01", required=True)
