"""Purpose: `ObservationReview`, how the nights went and what the results say.

Description: `Wayfinder.review` is an `ObservationReview`. It holds five
objects, each reached by name:

* ``nights`` (`ReviewNights`): the recorded nights, one night's review,
  its capture analysis and target changes, the sky coverage, the findings
  that recur, the Ekos session records and the session in progress.
* ``targets`` (`ReviewTargets`): judgments of a target's data that exist
  already: its dataset report, one star's result, its star shapes and the
  guide error during each frame.
* ``guiding`` (`ReviewGuiding`): one night's guiding analysis, the
  guiding runs, the guiding spectrum and the mount's unguided error.
* ``performance`` (`ReviewPerformance`): the limits of the active
  equipment, the predicted sky rate and the focus-temperature slope.
* ``alignment`` (`ReviewAlignment`): the plate-solve attempts that checked
  the mount's pointing, the nights that recorded them, the pointing model
  and the polar alignment.

Review reads the same records and context as `control`, so it is built
from the `Wayfinder`'s `ObservatoryControl` and shares its context. Each
method is a thin wrapper of one owner function, which its docstring names.
The advice that looks ahead is in `planning`.
"""

from wayfindinglib.api.review.alignment import ReviewAlignment
from wayfindinglib.api.review.guiding import ReviewGuiding
from wayfindinglib.api.review.nights import ReviewNights
from wayfindinglib.api.review.observation_review import ObservationReview
from wayfindinglib.api.review.performance import ReviewPerformance
from wayfindinglib.api.review.targets import ReviewTargets

__all__ = [
    "ObservationReview",
    "ReviewAlignment",
    "ReviewGuiding",
    "ReviewNights",
    "ReviewPerformance",
    "ReviewTargets",
]
