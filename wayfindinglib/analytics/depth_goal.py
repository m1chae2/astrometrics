"""Purpose: The depth levels a novice can choose, per kind of object.

Description: A person picks how deep a target's image should be in plain words:
Quick look, Good (the default) or Deep. Each word stands for a depth in
magnitudes, and which depth applies depends on the kind of object:

* A galaxy or a nebula (including planetary, emission and reflection nebulae
  and supernova remnants) is an extended object, so it is judged by the
  surface-brightness limit: the faintest surface brightness (magnitudes per
  square arcsecond) seen at 3 sigma in a 10 by 10 arcsecond box
  (`stacking.surface_brightness_limit_3sigma_10arcsec_mag_per_arcsec2`).
* A star cluster (open or globular) is a field of points, so it is judged by
  the point-source depth: the faintest star (Gaia G magnitude) seen at 5 sigma
  (`stacking.point_source_depth_5sigma_mag`).
* A star, a double star, a planet, the Moon, the Sun or a comet has no depth
  goal.
* A target whose object type is not known is judged by the point-source depth,
  the lowest-fidelity depth, because every field has stars.

The kind comes from the target's stored SIMBAD object type, which the library
records when it looks the target up in SIMBAD
(`astrometricslib.target_object_category` groups the codes into galaxy, nebula,
cluster and star; see `depth_kind_of_target`). A target id that names two
objects ("M 52 - Bubble Nebula") is typed from its first designation.

The depth of a stack never gets worse as frames are added, so a higher number
is deeper for both depths. The time to reach a level comes from the existing
"take N more" forecast, aimed at the noise ratio that gives the missing depth
(`noise_ratio_for_depth_gain`). Register row WAY-D94 holds the level values.
"""

import re
from collections.abc import Mapping

from astrometricslib import target_object_category
from wayfindinglib.analytics.result_quality.forecast import (
    POINT_SOURCE_DEPTH_METRIC,
    SURFACE_BRIGHTNESS_LIMIT_METRIC,
)
from wayfindinglib.models.planning.depth_goal import DepthKind, DepthLevel

DEPTH_GOAL_ENTRY = "WAY-D94"
"""Register row behind the level values."""

DEFAULT_DEPTH_LEVEL = DepthLevel.GOOD
"""The level of a target nobody has chosen a level for."""

LEVEL_ORDER: tuple[DepthLevel, ...] = (DepthLevel.QUICK_LOOK, DepthLevel.GOOD, DepthLevel.DEEP)
"""The levels from shallowest to deepest."""

LEVEL_LABELS: Mapping[DepthLevel, str] = {
    DepthLevel.QUICK_LOOK: "Quick look",
    DepthLevel.GOOD: "Good",
    DepthLevel.DEEP: "Deep",
}
"""The words shown for each level."""

LEVEL_DEPTHS: Mapping[DepthKind, Mapping[DepthLevel, float]] = {
    DepthKind.POINT_SOURCE: {
        DepthLevel.QUICK_LOOK: 18.6,
        DepthLevel.GOOD: 19.3,
        DepthLevel.DEEP: 20.1,
    },
    DepthKind.SURFACE_BRIGHTNESS: {
        DepthLevel.QUICK_LOOK: 24.0,
        DepthLevel.GOOD: 24.7,
        DepthLevel.DEEP: 25.5,
    },
}
"""The depth that reaches each level, for each depth kind.

Point source is the 5 sigma depth in Gaia G magnitudes. Surface brightness is
the 3 sigma limit over 10 by 10 arcseconds in magnitudes per square
arcsecond. Larger is deeper for both.

Derivation (user decision 2026-10-27, option C, time-based): NGC 7331 on the
copy, with this setup, reached 19.7 mag (5 sigma point source) and 25.1
mag/arcsec2 (3 sigma, 10 by 10 arcseconds) in 4.1 h. Each level is the depth
reached in a set time at that rate: Quick look 30 min, Good 2 h, Deep 8 h. The
depth changes by 1.25 times log10 of the time ratio. (Random noise alone would
give 1.25 = 2.5 / 2, since noise falls as the square root of the time; the
same factor is used for both kinds.) For example, 2 h: 19.7 + 1.25 *
log10(2 / 4.1) = 19.3. Status: Design estimate, register row WAY-D94; one
target and one setup, no uncertainty recorded, not held out.
"""

DEPTH_METRICS: Mapping[DepthKind, str] = {
    DepthKind.POINT_SOURCE: POINT_SOURCE_DEPTH_METRIC,
    DepthKind.SURFACE_BRIGHTNESS: SURFACE_BRIGHTNESS_LIMIT_METRIC,
}
"""The stored stack metric each depth kind reads (pinned at version 1)."""

DEPTH_UNITS: Mapping[DepthKind, str] = {
    DepthKind.POINT_SOURCE: "mag",
    DepthKind.SURFACE_BRIGHTNESS: "mag/arcsec2",
}
"""The unit of each depth kind."""

UNKNOWN_TYPE_REASON = "The object type is not known, so the point-source depth is used."
"""Why a target of unknown type is judged by the point-source depth."""

NO_GOAL_REASON = "Stars, planets, the Moon, the Sun and comets have no depth goal."
"""Why a star or a body of the solar system has no depth goal."""


_DEEP_SKY_DESIGNATION = re.compile(r"^(NGC|IC)[\s_]*\d", re.IGNORECASE)
"""An NGC or IC designation at the start of a target id, such as "NGC 6888"."""


def depth_kind_of_target(target_id: str, simbad_object_type: str | None) -> tuple[DepthKind | None, str]:
    """Say which depth a target is judged by.

    The kind comes from the target's stored SIMBAD object type, grouped by
    `target_object_category`: a galaxy or nebula is judged by surface
    brightness, a cluster by the point-source depth, and a star or a body of
    the solar system has no depth goal. A target whose type is not stored, or
    is a kind of object the grouping does not know, is judged by the
    point-source depth, the lowest-fidelity depth, because every field has
    stars (user decision 2026-10-27).

    Parameters
    ----------
    target_id : `str`
        The target id, such as "M 81" or "Moon". A planet, the Moon, the Sun
        or a comet has no depth goal even when no SIMBAD type is stored.
    simbad_object_type : `str` or `None`
        The target's stored SIMBAD main object type code, or ``None``.

    Returns
    -------
    kind, reason : `tuple` [`DepthKind` or `None`, `str`]
        The depth kind, or ``None`` when there is no depth goal. The reason is
        empty for a known type, `NO_GOAL_REASON` for no goal, and
        `UNKNOWN_TYPE_REASON` when the point-source depth is used by default.

    An NGC or IC object that SIMBAD types as a star keeps a goal and is
    judged like a target of unknown type.

    Notes
    -----
    A target id that names two objects (such as "M 52 - Bubble Nebula") is
    typed by SIMBAD from its first designation (M 52, an open cluster). That is
    accepted, and the cluster type gives the point-source depth.
    """
    category = target_object_category(simbad_object_type, target_id)
    if category in ("galaxy", "nebula"):
        return DepthKind.SURFACE_BRIGHTNESS, ""
    if category == "cluster":
        return DepthKind.POINT_SOURCE, ""
    if category == "solar_system":
        return None, NO_GOAL_REASON
    if category == "star":
        # An NGC or IC object is a deep-sky catalog entry, so it always gets a
        # goal: SIMBAD types NGC 6888, the Crescent Nebula, as its Wolf-Rayet
        # star. It is treated as of unknown type (user decision 2026-10-27).
        if _DEEP_SKY_DESIGNATION.match(target_id.strip()):
            return DepthKind.POINT_SOURCE, UNKNOWN_TYPE_REASON
        return None, NO_GOAL_REASON
    return DepthKind.POINT_SOURCE, UNKNOWN_TYPE_REASON


def goal_depth(kind: DepthKind, level: DepthLevel) -> float:
    """Give the depth that reaches a level.

    Parameters
    ----------
    kind : `DepthKind`
        The depth kind.
    level : `DepthLevel`
        The level.

    Returns
    -------
    depth : `float`
        The depth, in the kind's unit.
    """
    return LEVEL_DEPTHS[kind][level]


def level_reached(kind: DepthKind, current_depth: float) -> DepthLevel | None:
    """Find the deepest level a depth meets.

    Parameters
    ----------
    kind : `DepthKind`
        The depth kind.
    current_depth : `float`
        The stack's depth, in the kind's unit.

    Returns
    -------
    level : `DepthLevel` or `None`
        The deepest level whose depth is at most `current_depth`, or ``None``
        when even Quick look is not met.
    """
    reached = None
    for level in LEVEL_ORDER:
        if current_depth >= LEVEL_DEPTHS[kind][level]:
            reached = level
    return reached


def noise_ratio_for_depth_gain(goal_depth_mag: float, current_depth_mag: float) -> float:
    """Give the noise ratio that adds a depth gain.

    A gain of dm magnitudes needs the noise to fall to ``10 ** (-0.4 * dm)`` of
    its value now, since a magnitude is -2.5 log10 of a flux and the depth is
    set by the noise.

    Parameters
    ----------
    goal_depth_mag : `float`
        The depth wanted.
    current_depth_mag : `float`
        The depth now.

    Returns
    -------
    ratio : `float`
        The noise after over the noise now, below 1 when the goal is deeper.
    """
    return 10.0 ** (-0.4 * (goal_depth_mag - current_depth_mag))


def time_text(seconds: float) -> str:
    """Write a duration the way a novice reads it.

    Parameters
    ----------
    seconds : `float`
        The duration.

    Returns
    -------
    text : `str`
        ``"40 min"``, ``"2 h"`` or ``"1 h 20 min"``, rounded to the minute.
    """
    minutes = max(1, round(seconds / 60.0))
    hours, rest = divmod(minutes, 60)
    if hours == 0:
        return f"{rest} min"
    return f"{hours} h" if rest == 0 else f"{hours} h {rest} min"


def level_label(level: DepthLevel | None) -> str:
    """Write a level in words.

    Parameters
    ----------
    level : `DepthLevel` or `None`
        The level, or ``None`` for an image below Quick look.

    Returns
    -------
    label : `str`
        The words.
    """
    return "Below Quick look" if level is None else LEVEL_LABELS[level]
