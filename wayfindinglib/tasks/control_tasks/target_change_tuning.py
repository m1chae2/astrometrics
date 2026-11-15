"""Purpose: Derive how far a slew must end from the last to be a new target.

Description: `find_target_changes` lists a slew only when it ends at least
`NEW_POSITION_DEGREES` from the last slew it listed. The slews of a night are
of two kinds: small ones (a centering step, a meridian flip) that end within
a fraction of a degree of where the mount was, and moves to a new target,
which end degrees away. This module reads where every recorded slew ended,
measures the distance from each slew end to the one before it, and puts the
limit in the middle of the widest gap, on a logarithmic scale, between the
small distances and the large ones. With no clear gap it gives no value.

The statistic is a pure function of a list of positions.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence

from astrometricslib.parameters import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    Tuner,
    TuningInputs,
    TuningResult,
    TuningSkippedError,
    resolve_parameter,
)
from wayfindinglib.analytics.tuning_support import largest_log_gap_threshold
from wayfindinglib.astronomy.coordinate_transforms import angular_separation_arcsec
from wayfindinglib.tasks.control_tasks.target_changes import NEW_POSITION_DEGREES, slew_end_positions

__all__ = [
    "MINIMUM_SEPARATION_GAP_RATIO",
    "NEW_POSITION_TUNER",
    "consecutive_separations_deg",
]

MINIMUM_SEPARATION_GAP_RATIO = TunedParameter(
    name="wayfinding.minimum_separation_gap_ratio",
    default=3.0,
    unit="ratio",
    description="The widest gap between slew distances must be at least this ratio to separate two kinds.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="WAY-D56",
)
"""The widest gap between slew distances must be at least this ratio.

Centering steps end within arcminutes and moves to a new target end degrees
away, a ratio of tens. A ratio of 3 is the smallest gap that is plainly wider
than the spread inside either kind. Register entry WAY-D56."""


def consecutive_separations_deg(positions: Sequence[tuple[float, float]]) -> list[float]:
    """Measure the distance from each slew end to the one before it.

    Parameters
    ----------
    positions : `~collections.abc.Sequence` [`tuple` [`float`, `float`]]
        Right ascension and declination in degrees of each slew end, in time
        order.

    Returns
    -------
    separations : `list` [`float`]
        The angular distances in degrees, one fewer than the positions.
    """
    return [
        angular_separation_arcsec(earlier[0], earlier[1], later[0], later[1]) / 3600.0
        for earlier, later in itertools.pairwise(positions)
    ]


def tune_new_position(inputs: TuningInputs) -> TuningResult:
    """Derive the new-position distance from the recorded slews.

    Parameters
    ----------
    inputs : `TuningInputs`
        The tuning run's inputs. Reads the ``"wayfinder"`` service.

    Returns
    -------
    result : `TuningResult`
        The distance that separates small slews from moves to a new target.

    Raises
    ------
    TuningSkippedError
        If too few slews were recorded or their distances show no two kinds.
    """
    context = inputs.service("wayfinder").control._context
    positions = slew_end_positions(context)
    separations = consecutive_separations_deg(positions)
    if not separations:
        raise TuningSkippedError("no recorded Ekos session has two slews with a known end position")
    threshold = largest_log_gap_threshold(separations, resolve_parameter(MINIMUM_SEPARATION_GAP_RATIO).value)
    if threshold is None:
        raise TuningSkippedError(
            f"the {len(separations)} slew distances show no gap between small slews and new targets"
        )
    return TuningResult(
        values={NEW_POSITION_DEGREES.name: threshold},
        sample=len(separations),
        source=(
            f"The middle, on a logarithmic scale, of the widest gap among {len(separations)} distances "
            "between the ends of consecutive recorded slews."
        ),
    )


NEW_POSITION_TUNER = Tuner(
    name="wayfinding.new_position",
    parameters=(NEW_POSITION_DEGREES,),
    method=(
        "Reads where every recorded Ekos slew ended and measures the distance between consecutive slew "
        "ends. Puts the limit in the middle of the widest gap, on a logarithmic scale, between the small "
        "distances (centering steps) and the large ones (new targets)."
    ),
    tune=tune_new_position,
)
