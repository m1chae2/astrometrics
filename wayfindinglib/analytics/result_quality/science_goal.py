"""Purpose: Choose the science goal that a target's efficiency is judged for.

Description: Efficiency depends on what the data is for. A galaxy gains depth
from a softer night, a faint star does not (`efficiency`). The goal is read
from the target itself, never asked of the user (decision S6):

* spectral frames make a slitless spectroscopy target;
* otherwise a target with a photometry quality record is a bright-star
  photometry target;
* otherwise a Messier, NGC or IC object is an extended object;
* every other name (a star or a star field) is a faint point source.

The rule is a design estimate (register row WAY-D74): a catalog number does not
say whether the user imaged the whole galaxy or one star in it.

This is the one place the goal is chosen. The target report, tonight's ranking
and the night review all call `science_goal_for_target`.
"""

from astrometricslib import Target, frame_is_spectral
from wayfindinglib.models.quality.efficiency import ScienceGoal

EXTENDED_OBJECT_TYPES = frozenset({"messier", "ngc", "ic"})
"""The `TargetObjectType` values that count as extended objects."""


def science_goal_for_target(target: Target) -> ScienceGoal:
    """Read the science goal from a target.

    Parameters
    ----------
    target : `Target`
        The library target.

    Returns
    -------
    goal : `ScienceGoal`
        The goal its efficiency is judged for.
    """
    lights = [frame for frame in target.frames if str(frame.role).upper().endswith("LIGHT")]
    if lights and sum(frame_is_spectral(frame) for frame in lights) > len(lights) / 2:
        return ScienceGoal.SLITLESS_SPECTRAL_RESOLUTION
    if target.quality.photometry is not None:
        return ScienceGoal.BRIGHT_STAR_PHOTOMETRY
    if target.object_type.value in EXTENDED_OBJECT_TYPES:
        return ScienceGoal.EXTENDED_OBJECT
    return ScienceGoal.FAINT_POINT_SOURCE


__all__ = ["EXTENDED_OBJECT_TYPES", "science_goal_for_target"]
