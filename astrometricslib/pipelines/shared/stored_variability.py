"""Fills in what the catalogs say about each star from the stored answers.

A star the catalogs list as variable must not serve as a comparison star in
photometry. SIMBAD, the AAVSO Variable Star Index (VSX) and Gaia DR3's
variability classes each say so in their own way (see
`models/known_variability.py`). A saved star may lack one of the three
answers, for example because SIMBAD could not be reached the day the star was
identified. This module fills the blank answers on the in-memory stars from
the library's stored catalog answers (see ``drivers/catalog_cache.py``):

- VSX and Gaia answers come through the same stored sky tiles the backfill
  script uses (`variable_star_catalogs`). The catalog is asked live only for a
  tile that was never stored or is past its lifetime.
- SIMBAD types come from the stored SIMBAD tiles of the field, matched by
  position. They are read from the store only, because the astrometry step
  that names the stars is what asks SIMBAD.

A catalog that cannot be reached and has nothing stored leaves the star's
answer blank, which stays "unverified" and never counts as "not listed". The
stars are changed in memory only; saving is the job of the backfill script and
the astrometry step.
"""

import logging
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.table import vstack

from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.known_variability import (
    KnownVariability,
    classify_gaia_variable_flag,
    classify_simbad_object_types,
    classify_vsx_type,
)
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.shared.catalog_star_identity import gaia_dr3_source_id_of
from astrometricslib.pipelines.shared.simbad_object_types import (
    STAR_REGION_VOTABLE_FIELDS,
    read_simbad_object_types,
)
from astrometricslib.pipelines.shared.star_kinds import stored_position_degrees
from astrometricslib.pipelines.shared.variable_star_catalogs import (
    build_position_table,
    gaia_flags_from_classes,
    group_into_fields,
    look_up_gaia_classes,
    look_up_vsx,
    vsx_types_from_matches,
)

logger = logging.getLogger(__name__)

# How far a stored SIMBAD row may lie from a star and still be that star, in
# arcseconds. The same 3 arcseconds as the VSX match (`MATCH_RADIUS_ARCSEC`
# is 3 pixel scales at the reference setup); a fixed value is used here
# because no plate scale is at hand.
SIMBAD_STORED_MATCH_ARCSEC = 3.0


@dataclass
class VariabilityFillReport:
    """What `fill_known_variability` did.

    Attributes
    ----------
    filled : `collections.Counter` [`str`]
        For each catalog (``"SIMBAD"``, ``"VSX"``, ``"Gaia DR3"``), how many
        stars got an answer they lacked.
    unreached : `list` [`str`]
        Plain sentences for each catalog that could not be read at all, for the
        run's warning list.
    """

    filled: Counter = field(default_factory=Counter)
    unreached: list[str] = field(default_factory=list)


def count_listed_by_source(stars: Iterable[StellarObject]) -> dict[str, int]:
    """Count the stars each catalog lists as variable or suspected variable.

    Parameters
    ----------
    stars : `Iterable` [`StellarObject`]
        The stars to count.

    Returns
    -------
    counts : `dict` [`str`, `int`]
        For ``"SIMBAD"``, ``"VSX"``, ``"Gaia DR3"`` and ``"any"``, the number
        of stars listed. A star listed by two catalogs counts once under
        ``"any"``.
    """
    listed = (KnownVariability.KNOWN_VARIABLE, KnownVariability.SUSPECTED_VARIABLE)
    counts = {"SIMBAD": 0, "VSX": 0, "Gaia DR3": 0, "any": 0}
    for star in stars:
        by_source = {
            "SIMBAD": classify_simbad_object_types(star.simbad_object_types) in listed,
            "VSX": classify_vsx_type(star.vsx_variability_type) in listed,
            "Gaia DR3": classify_gaia_variable_flag(star.gaia_variable_flag) in listed,
        }
        for name, is_listed in by_source.items():
            counts[name] += int(is_listed)
        counts["any"] += int(any(by_source.values()))
    return counts


def _fill_vsx_and_gaia(stars: Sequence[StellarObject], report: VariabilityFillReport) -> None:
    """Fill the blank VSX and Gaia answers from the stored tiles.

    Parameters
    ----------
    stars : `Sequence` [`StellarObject`]
        The stars; changed in place.
    report : `VariabilityFillReport`
        Receives the counts and any catalog that could not be read.
    """
    need_vsx = [s for s in stars if not s.vsx_variability_type and stored_position_degrees(s) is not None]
    need_gaia = [
        s
        for s in stars
        if not s.gaia_variable_flag and stored_position_degrees(s) is not None and gaia_dr3_source_id_of(s)
    ]
    for name, needing in (("VSX", need_vsx), ("Gaia DR3", need_gaia)):
        if not needing:
            continue
        table = build_position_table((s.id, *stored_position_degrees(s)) for s in needing)  # type: ignore[misc]
        matched = []
        classes: dict[int, str] = {}
        try:
            for group in group_into_fields(table):
                if name == "VSX":
                    matches = look_up_vsx(table[group])
                    if matches is not None:
                        matched.append(matches)
                else:
                    classes.update(look_up_gaia_classes(table[group]))
        except ExternalServiceError as unreached:
            logger.warning("%s", unreached)
            report.unreached.append(f"{name} listings could not be read: {unreached}")
            continue
        if name == "VSX":
            types = vsx_types_from_matches(
                [s.id for s in needing], vstack(matched, metadata_conflicts="silent") if matched else None
            )
            for star in needing:
                star.vsx_variability_type = types[star.id]
        else:
            flags = gaia_flags_from_classes({s.id: gaia_dr3_source_id_of(s) for s in needing}, classes)
            for star in needing:
                star.gaia_variable_flag = flags.get(star.id, "")
        report.filled[name] += len(needing)


def _fill_simbad(stars: Sequence[StellarObject], simbad: Any, report: VariabilityFillReport) -> None:
    """Fill the blank SIMBAD object types from the stored SIMBAD tiles.

    Parameters
    ----------
    stars : `Sequence` [`StellarObject`]
        The stars; changed in place.
    simbad : `Any`
        A SIMBAD driver. Only one with a ``stored_region`` method (the cached
        driver) can be read without a network call; any other is skipped.
    report : `VariabilityFillReport`
        Receives the counts and a note when the field is not stored.
    """
    reader = getattr(simbad, "stored_region", None)
    needing = [s for s in stars if not s.simbad_object_types and stored_position_degrees(s) is not None]
    if reader is None or not needing:
        return
    positions = np.array([stored_position_degrees(s) for s in needing], dtype=float)
    sky = SkyCoord(positions[:, 0] * u.deg, positions[:, 1] * u.deg)
    center = SkyCoord(float(np.mean(positions[:, 0])) * u.deg, float(np.mean(positions[:, 1])) * u.deg)
    radius = float(center.separation(sky).deg.max()) + 0.05
    try:
        table = reader(center, f"{radius:.4f}d", votable_fields=STAR_REGION_VOTABLE_FIELDS, row_limit=5000)
    except ExternalServiceError as unreached:
        logger.warning("%s", unreached)
        report.unreached.append(f"SIMBAD types could not be read: {unreached}")
        return
    if table is None or len(table) == 0:
        return
    ra_column = next((c for c in ("ra", "RA_d", "RA") if c in table.colnames), None)
    dec_column = next((c for c in ("dec", "DEC_d", "DEC") if c in table.colnames), None)
    if ra_column is None or dec_column is None:
        return
    entries = SkyCoord(
        np.ma.filled(np.ma.asarray(table[ra_column], dtype=float), np.nan) * u.deg,
        np.ma.filled(np.ma.asarray(table[dec_column], dtype=float), np.nan) * u.deg,
    )
    index, separation, _ = sky.match_to_catalog_sky(entries)
    for star, entry_index, distance in zip(needing, index, separation.arcsec, strict=True):
        if distance <= SIMBAD_STORED_MATCH_ARCSEC:
            types = read_simbad_object_types(table[int(entry_index)])
            if types:
                star.simbad_object_types = types
                report.filled["SIMBAD"] += 1


def fill_known_variability(stars: Sequence[StellarObject], simbad: Any = None) -> VariabilityFillReport:
    """Fill each star's blank catalog answers from the stored catalog answers.

    Parameters
    ----------
    stars : `Sequence` [`StellarObject`]
        The stars to fill, in place. An answer a star already has is kept.
    simbad : `SimbadDriver`, optional
        The SIMBAD driver whose stored tiles are read (see `_fill_simbad`).

    Returns
    -------
    report : `VariabilityFillReport`
        How many answers each catalog added, and which catalogs could not be
        read (each is also logged at warning level).
    """
    report = VariabilityFillReport()
    stars = list(stars)
    _fill_simbad(stars, simbad, report)
    _fill_vsx_and_gaia(stars, report)
    return report
