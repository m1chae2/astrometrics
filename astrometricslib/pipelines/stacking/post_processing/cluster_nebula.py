"""Treats a star cluster that sits inside a nebula as a nebula.

SIMBAD gives NGC 2244 the type "open cluster", because the cluster is what
SIMBAD's entry for that name describes. In the sky it is the bright core of
the Rosette Nebula, and a picture of it is a picture of the nebula. The
preview's masked stretch (see `masked_stretch`) is for nebula and galaxy
targets only, so a cluster like this one was refused because of its catalog
type.

The rule is this. A target that SIMBAD calls a cluster counts as a nebula
when the stack's field holds a nebula that contains the target's position.
"Contains" means the target lies within half the nebula's major axis of the
nebula's position, so the target is inside the nebula's disc. The nebulae
come from `list_bright_object_choices`, the same list the user can choose
from in the image viewer, so no target name is written in the code and the
rule needs only data the library already keeps (the stack's plate solution
and SIMBAD's stored answers).

A cluster with no such nebula stays a cluster. That covers real open and
globular clusters (M 13) and a cluster that only has a nebula near it, in
the same frame but not around it (M 52 and the Bubble Nebula). A target that
is not a cluster is never changed. A stack with no plate solution, or a SIMBAD
that cannot be reached with nothing stored, leaves the cluster a cluster, and
the reason is recorded.
"""

import logging
import math
from dataclasses import dataclass

from astrometricslib._internal.drivers.interfaces.simbad_driver import SimbadDriver
from astrometricslib.models.processing_results import BrightObjectChoice
from astrometricslib.pipelines.stacking.post_processing.bright_object_choices import (
    list_bright_object_choices,
)
from astrometricslib.pipelines.stacking.post_processing.masked_stretch import TargetAnchor

logger = logging.getLogger(__name__)

__all__ = [
    "CategoryVerdict",
    "angular_separation_arcmin",
    "find_nebula_around_position",
    "refine_object_category",
]


@dataclass(frozen=True)
class CategoryVerdict:
    """The kind the preview treats a target as, and why.

    Attributes
    ----------
    category : `str` or `None`
        The kind to use: the catalog kind, or ``"nebula"`` for a cluster
        inside a nebula.
    catalog_category : `str` or `None`
        The kind from the target's SIMBAD type (`Target.object_category`).
    nebula : `str` or `None`
        SIMBAD's name of the nebula around the cluster, when there is one.
    reason : `str`
        A plain sentence for the preview record.
    """

    category: str | None
    catalog_category: str | None
    nebula: str | None
    reason: str

    def record(self) -> dict[str, object]:
        """Give the verdict as a plain dictionary for the preview record.

        Returns
        -------
        record : `dict` [`str`, `object`]
            The kind used, the catalog kind, the nebula and the reason.
        """
        return {
            "category": self.category,
            "catalog_category": self.catalog_category,
            "nebula": self.nebula,
            "reason": self.reason,
        }


def angular_separation_arcmin(
    ra_one_deg: float, dec_one_deg: float, ra_two_deg: float, dec_two_deg: float
) -> float:
    """Find the angle between two sky positions.

    Parameters
    ----------
    ra_one_deg, dec_one_deg : `float`
        The first position, in degrees.
    ra_two_deg, dec_two_deg : `float`
        The second position, in degrees.

    Returns
    -------
    separation_arcmin : `float`
        The angle between them in arcminutes (haversine formula, which stays
        accurate for small angles).
    """
    ra_one, dec_one, ra_two, dec_two = (
        math.radians(value) for value in (ra_one_deg, dec_one_deg, ra_two_deg, dec_two_deg)
    )
    half_chord = (
        math.sin((dec_two - dec_one) / 2.0) ** 2
        + math.cos(dec_one) * math.cos(dec_two) * math.sin((ra_two - ra_one) / 2.0) ** 2
    )
    return math.degrees(2.0 * math.asin(min(1.0, math.sqrt(half_chord)))) * 60.0


def find_nebula_around_position(
    ra_deg: float, dec_deg: float, objects: list[BrightObjectChoice]
) -> BrightObjectChoice | None:
    """Find the listed nebula whose disc contains a sky position.

    Parameters
    ----------
    ra_deg, dec_deg : `float`
        The position, in degrees (the target's catalog position).
    objects : `list` [`BrightObjectChoice`]
        The objects SIMBAD lists in the stack's field. Only nebulae count,
        and only a nebula with a known major axis: without a size there is
        no disc.

    Returns
    -------
    nebula : `BrightObjectChoice` or `None`
        The nebula whose disc (radius half the major axis) holds the
        position. If several do, the one whose center is nearest. `None`
        when none does.
    """
    best: tuple[float, BrightObjectChoice] | None = None
    for candidate in objects:
        if candidate.category != "nebula" or not candidate.major_axis_arcmin:
            continue
        separation = angular_separation_arcmin(ra_deg, dec_deg, candidate.ra_deg, candidate.dec_deg)
        if separation <= candidate.major_axis_arcmin / 2.0 and (best is None or separation < best[0]):
            best = (separation, candidate)
    return None if best is None else best[1]


def refine_object_category(
    category: str | None, anchor: TargetAnchor | None, stacked_path: str, simbad: SimbadDriver | None = None
) -> CategoryVerdict:
    """Decide whether a cluster target should be treated as a nebula.

    Only a target the catalog calls a cluster is looked at; any other kind is
    returned as it is, with no SIMBAD question. Never raises for a missing
    plate solution or SIMBAD: the cluster stays a cluster and the reason says
    so.

    Parameters
    ----------
    category : `str` or `None`
        The target's kind from its SIMBAD type (`Target.object_category`).
    anchor : `TargetAnchor` or `None`
        The target's catalog position. Without one the cluster stays a
        cluster. An anchor set to a chosen object (mode ``"object"``) or to
        off is not refined: the user's choice stands.
    stacked_path : `str`
        A FITS file with the stack's plate solution (the stack or the
        scratch copy of it the preview works on).
    simbad : `SimbadDriver`, optional
        The SIMBAD driver. The library's cached one if left out.

    Returns
    -------
    verdict : `CategoryVerdict`
        The kind to use and why.
    """
    if category != "cluster":
        return CategoryVerdict(category, category, None, "the target is not a cluster, so its kind is kept")
    if anchor is None or anchor.mode != "auto" or anchor.ra_deg is None or anchor.dec_deg is None:
        return CategoryVerdict(
            category, category, None, "the target has no catalog position to test against nebulae"
        )
    if simbad is None:
        from astrometricslib._internal.drivers.driver_set import Drivers

        simbad = Drivers().simbad_or_default()
    choices = list_bright_object_choices("", stacked_path, simbad)
    if not choices.objects:
        return CategoryVerdict(
            category,
            category,
            None,
            f"no nebula could be listed in the field ({choices.message}), so it stays a cluster",
        )
    nebula = find_nebula_around_position(anchor.ra_deg, anchor.dec_deg, choices.objects)
    if nebula is None:
        return CategoryVerdict(
            category,
            category,
            None,
            "no listed nebula contains the cluster's position, so it stays a cluster",
        )
    logger.info("The cluster lies inside %s, so the preview treats it as a nebula.", nebula.simbad_id)
    return CategoryVerdict(
        "nebula",
        category,
        nebula.simbad_id,
        f"the cluster lies inside {nebula.simbad_id}, so it counts as a nebula",
    )
