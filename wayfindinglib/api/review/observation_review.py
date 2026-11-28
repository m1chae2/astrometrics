"""Purpose: `ObservationReview`, the `review` area of the `Wayfinder`.

Description: `Wayfinder.review` is an `ObservationReview`. It holds the five
review objects (``nights``, ``targets``, ``guiding``, ``performance`` and
``alignment``) over the context it shares with `control`, and has no
methods of its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.api.review.alignment import ReviewAlignment
from wayfindinglib.api.review.guiding import ReviewGuiding
from wayfindinglib.api.review.nights import ReviewNights
from wayfindinglib.api.review.performance import ReviewPerformance
from wayfindinglib.api.review.targets import ReviewTargets

if TYPE_CHECKING:
    from wayfindinglib.api.control.control import ObservatoryControl

__all__ = ["ObservationReview"]


class ObservationReview(ControlChild):
    """Read and analyse past and current observing sessions.

    Parameters
    ----------
    control : `ObservatoryControl`
        The `Wayfinder`'s hardware sub-API. Review shares its context
        (configuration, storage and records) and holds no other state.
    """

    nights: ReviewNights
    targets: ReviewTargets
    guiding: ReviewGuiding
    performance: ReviewPerformance
    alignment: ReviewAlignment

    def __init__(self, control: ObservatoryControl) -> None:
        """Share the context of `control` with the five review objects.

        Parameters
        ----------
        control : `ObservatoryControl`
            The hardware sub-API whose context is shared.
        """
        super().__init__(control._context)
        self.nights = ReviewNights(self._context)
        self.targets = ReviewTargets(self._context)
        self.guiding = ReviewGuiding(self._context)
        self.performance = ReviewPerformance(self._context)
        self.alignment = ReviewAlignment(self._context)
