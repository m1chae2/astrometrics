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

The user decided on 2026-10-24 (issue #95, decision U2) that the goal is chosen
for each capture setting, not for the whole target. One target can have a
grating setting and imaging settings, and each is judged for its own goal:

* a setting whose filter is the spectroscopy filter is a slitless spectroscopy
  setting;
* any other setting of a target with a photometry quality record is a
  bright-star photometry setting;
* any other setting of a Messier, NGC or IC object is an extended-object
  setting;
* every other setting is a faint-point-source setting.

This is the one place the goal is chosen. The target report, tonight's ranking
and the night review call `science_goal_for_setting` (or
`science_goal_for_target` for a target with no frames yet).
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from astrometricslib import CaptureSetting, Target, frame_is_spectral
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


GoalSource = ScienceGoal | Callable[[CaptureSetting], ScienceGoal]
"""One goal for every setting, or a function from a setting to its goal."""


def goal_of_setting(source: GoalSource, setting: CaptureSetting) -> ScienceGoal:
    """Read the goal of a setting from a `GoalSource`.

    Parameters
    ----------
    source : `GoalSource`
        A goal (the same for every setting) or a function of the setting.
    setting : `CaptureSetting`
        The setting.

    Returns
    -------
    goal : `ScienceGoal`
        The setting's goal.
    """
    return source(setting) if callable(source) else source


def group_by_goal(
    items: Iterable[Any], setting_of: Callable[[Any], CaptureSetting], goal_for: GoalSource
) -> dict[ScienceGoal, list[Any]]:
    """Split items into groups that share a science goal.

    Parameters
    ----------
    items : `Iterable` [`Any`]
        Frame observations, summaries or anything that has a setting.
    setting_of : `Callable`
        Gives the capture setting of an item.
    goal_for : `GoalSource`
        The goal source.

    Returns
    -------
    groups : `dict` [`ScienceGoal`, `list`]
        The items of each goal, in their first-seen order.
    """
    groups: dict[ScienceGoal, list[Any]] = defaultdict(list)
    for item in items:
        groups[goal_of_setting(goal_for, setting_of(item))].append(item)
    return dict(groups)


def setting_is_spectral(setting: CaptureSetting, frames: Sequence[Any] = ()) -> bool:
    """Say whether a capture setting is a spectroscopy setting.

    The filter name is checked the way the library checks a frame
    (`frame_is_spectral`). When the setting has no filter name, the frames
    of the setting decide: it is spectral if most of them are.

    Parameters
    ----------
    setting : `CaptureSetting`
        The setting.
    frames : `Sequence` [`Any`], optional
        The frames taken with the setting. Used only when the setting has no
        filter name.

    Returns
    -------
    is_spectral : `bool`
        `True` for a grating (spectroscopy) setting.
    """
    if setting.filter_name is not None:
        return frame_is_spectral({"filter": setting.filter_name})
    return bool(frames) and sum(frame_is_spectral(frame) for frame in frames) > len(frames) / 2


def science_goal_for_setting(
    target: Target, setting: CaptureSetting, frames: Sequence[Any] = ()
) -> ScienceGoal:
    """Read the science goal of one capture setting of a target.

    Parameters
    ----------
    target : `Target`
        The library target.
    setting : `CaptureSetting`
        The setting.
    frames : `Sequence` [`Any`], optional
        The frames taken with the setting, for a setting with no filter name.

    Returns
    -------
    goal : `ScienceGoal`
        The goal the efficiency of this setting is judged for.
    """
    if setting_is_spectral(setting, frames):
        return ScienceGoal.SLITLESS_SPECTRAL_RESOLUTION
    if target.quality.photometry is not None:
        return ScienceGoal.BRIGHT_STAR_PHOTOMETRY
    if target.object_type.value in EXTENDED_OBJECT_TYPES:
        return ScienceGoal.EXTENDED_OBJECT
    return ScienceGoal.FAINT_POINT_SOURCE


__all__ = [
    "EXTENDED_OBJECT_TYPES",
    "GoalSource",
    "goal_of_setting",
    "group_by_goal",
    "science_goal_for_setting",
    "science_goal_for_target",
    "setting_is_spectral",
]
