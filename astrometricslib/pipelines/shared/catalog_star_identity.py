"""Rules for telling whether two catalog names are one star.

The same star has a different name in every catalog: ``HD 151086`` in the
Henry Draper catalog, ``Gaia DR3 1328045433153485824`` in Gaia,
``2MASS J16...`` in 2MASS. A run that looks a star up in one catalog and a
later run that looks it up in another would save it twice, so both the
save step (`star_recording`) and the one-time cleanup script
(`scripts/merge_duplicate_catalog_stars.py`) need the same three answers:

* how close two positions must be to count as one star,
* whether two names come from the same catalog (then they are different
  stars, however close), and
* which name to keep.

They live here so the two can never disagree. The module also holds the
rule for merging one row's data into another, which the cleanup scripts
share, and `CATALOG_MATCH_RADIUS_ARCSEC`, the wider radius within which a
detected star is matched to a catalog entry. The astrometry pipeline's star
identifier and the save step both use that radius.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import numpy as np

from astrometricslib.foundation.run_bases import factor_for
from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    RunBasis,
    TunedParameter,
)
from astrometricslib.pipelines.shared.simbad_object_types import SIMBAD_NAME_PREFIX, without_name_prefix
from astrometricslib.utilities.gaia_record_ids import GAIA_DR3_PREFIX, gaia_record_id

# Two catalog rows closer than this on the sky are the same star. On the
# real catalog, pairs of non-position-only rows within 1 arcsecond number
# 678, within 2 arcseconds 754, and within 5 arcseconds 793: the count
# nearly stops growing after 2 arcseconds, so pairs inside it are one star
# under two names, and the few beyond are ordinary neighbors. 2 arcseconds
# is also well below the separation of real neighbors in these fields.
SAME_STAR_POSITION_TOLERANCE_ARCSEC = TunedParameter(
    name="shared.same_star_position_tolerance_arcsec",
    default=2.0,
    unit="arcsec",
    description="Largest position difference at which two records are the same star.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.PUBLISHED_CONSTANT,
    register_entry="AST-11",
)

# Two rows from different catalogs, one of them without a Gaia DR3 number,
# are the same star this close. A name-keyed row (for example "* bet01
# Cyg") holds a SIMBAD position, which for a bright star can differ from
# the Gaia DR3 position by a few arcseconds. On the real catalog (backup of
# 2026-11-24), pairs of one Gaia-keyed and one name-keyed row number 645
# within 2 arcseconds, 709 within 2.5, 750 within 3 and 772 within 3.5;
# past 3.5 arcseconds the count grows only by the 10 to 15 pairs per half
# arcsecond that real neighbors give. Albireo's pair sits 2.9 arcseconds
# apart. AST-11.
SAME_STAR_TOLERANCE_WITHOUT_GAIA_NUMBER_ARCSEC = TunedParameter(
    name="shared.same_star_tolerance_without_gaia_number_arcsec",
    default=3.0,
    unit="arcsec",
    description="Largest distance at which two records are one star when one has no Gaia number.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.PUBLISHED_CONSTANT,
    register_entry="AST-11",
)

# The maximum allowed distance between a detected star and a catalog entry
# for the two to count as the same physical star. This 10-arcsecond limit is
# used when matching detected stars to the SIMBAD and Gaia catalogs, and when
# reusing an existing position-only star's id (see `star_recording`).
# Stands for the sky distance within which two catalog positions are the
# same star. It follows the plate scale: the value is a factor times the
# plate scale the run measured (10.0 on the reference setup).
CATALOG_MATCH_RADIUS_ARCSEC = TunedParameter(
    name="shared.catalog_match_radius_arcsec",
    default=factor_for(10.0, RunBasis.PLATE_SCALE_ARCSEC_PER_PX),
    unit="× plate scale",
    description="Radius within which a detected star is matched to a catalog star.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.PLATE_SCALE_ARCSEC_PER_PX,
    reference_value=10.0,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="AST-D07",
)

# A star seen only by position, with no catalog identity, counts as the same
# star as a catalog star (or as another such star) this close to it. Each
# solve of a field puts a star a fraction of an arcsecond to a few arcseconds
# from where the last one did, and the star stays within this radius of its
# Gaia DR3 position for most stars; the counts are in AST-22.
POSITION_ONLY_SAME_STAR_RADIUS_ARCSEC = TunedParameter(
    name="shared.position_only_same_star_radius_arcsec",
    default=3.0,
    unit="arcsec",
    description="Largest distance at which a star known only by position is the same as another star.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.PUBLISHED_CONSTANT,
    register_entry="AST-22",
)

# Names that beat every other catalog, best first. A name matching the
# first pattern is preferred to one matching the second, and both to any
# other name.
_PREFERRED_NAME_PATTERNS = (r"^(HD|BD|CD|CPD)[\s+\-\d]", rf"^{re.escape(GAIA_DR3_PREFIX)}")


def catalog_family(star_id: str) -> str:
    """Name the catalog a star's id comes from.

    Parameters
    ----------
    star_id : `str`
        A catalog id such as ``"HD 151086"``, ``"Gaia DR3 132..."`` or
        ``"2MASS J0535..."``.

    Returns
    -------
    family : `str`
        The letters that start the id (``"HD"``, ``"Gaia DR"``,
        ``"2MASS J"``), enough to tell two catalogs apart.
    """
    match = re.match(r"^\s*(\d*[A-Za-z\[\]*.]+(?:\s+[A-Za-z]+)?)", star_id)
    return match.group(1).strip() if match else star_id


def name_preference_rank(star_id: str) -> int:
    """Rank how much a star's name is preferred; lower is better.

    Parameters
    ----------
    star_id : `str`
        A catalog id.

    Returns
    -------
    rank : `int`
        ``0`` for an HD/BD/CD/CPD name, ``1`` for a Gaia DR3 name, and
        ``2`` (after both) for any other catalog.
    """
    for rank, pattern in enumerate(_PREFERRED_NAME_PATTERNS):
        if re.match(pattern, star_id):
            return rank
    return len(_PREFERRED_NAME_PATTERNS)


def identity_rank(star_id: str) -> int:
    """Rank how good an id is as a star's saved identity; lower is better.

    A star that Gaia DR3 lists is saved under its Gaia number: every star
    in a field has one, it never changes, and the local Gaia cache can be
    checked against it. Names people know the star by are kept as its
    display name and aliases instead.

    Parameters
    ----------
    star_id : `str`
        A catalog id.

    Returns
    -------
    rank : `int`
        ``0`` for ``Gaia DR3 <digits>``, ``1`` for an HD/BD/CD/CPD name,
        ``2`` for any other catalog name and ``3`` for a position-only
        (``FIELD_J``) id.
    """
    if _GAIA_DR3_NAME_PATTERN.match(star_id):
        return 0
    if star_id.startswith("FIELD_J"):
        return 3
    return 1 if re.match(_PREFERRED_NAME_PATTERNS[0], star_id) else 2


def choose_survivor_id(ids: list[str]) -> str:
    """Pick which id of one star to keep.

    Parameters
    ----------
    ids : `list` [`str`]
        The ids of the rows for one star.

    Returns
    -------
    survivor_id : `str`
        A Gaia DR3 number if any, else an HD/BD/CD/CPD name, else the
        first other id alphabetically (see `identity_rank`).
    """
    return min(sorted(ids), key=identity_rank)


# Survey catalogs whose names are long numbers few people know a star by.
_SURVEY_NAME_PATTERN = re.compile(
    r"^(2MASS|TYC|UCAC\d?|GSC|SDSS|ZTF|ATO|GPM|LAMOST|WISE|AllWISE|PS1|USNO|Gaia DR\d|\[)"
)

# Bright-star catalogs a reader is likely to know: Henry Draper, the Bright
# Star (HR), Hipparcos, the Durchmusterungen and the SAO catalog.
_BRIGHT_STAR_NAME_PATTERN = re.compile(r"^(HD|HR|HIP|BD|CD|CPD|SAO)[\s+\-\d]")


def display_name_rank(name: str) -> int:
    """Rank how familiar a star's name is to a reader; lower is better.

    Parameters
    ----------
    name : `str`
        A name of the star.

    Returns
    -------
    rank : `int`
        ``0`` for a proper name (no digits), a Bayer or Flamsteed name
        (``* alf Lyr``) or a variable-star name (``V* RR Lyr``); ``1`` for
        a bright-star catalog name (HD, HR, HIP, BD, CD, CPD, SAO); ``2``
        for any other catalog name; ``3`` for a survey catalog name (2MASS,
        Tycho and the like); ``4`` for a Gaia name; ``5`` for a
        position-only id.
    """
    text = name.strip()
    if text.startswith("FIELD_J"):
        return 5
    if text.startswith(GAIA_DR3_PREFIX):
        return 4
    if _SURVEY_NAME_PATTERN.match(text):
        return 3
    if _BRIGHT_STAR_NAME_PATTERN.match(text):
        return 1
    if text.startswith(("* ", "V* ")) or not re.search(r"\d", text):
        return 0
    return 2


# Inside the two most familiar groups of `display_name_rank`, the order people
# know names in. Group 0: a proper name (``Vega``, or SIMBAD's ``NAME Vega``),
# then a Bayer name (a Greek letter and the constellation, ``* alf Lyr``),
# then a Flamsteed number (``* 3 Lyr``), then any other starred name such as
# a variable-star name (``V* RR Lyr``). Group 1: the Henry Draper (HD)
# number, then the Hipparcos (HIP) number, then the other bright-star
# catalogs. A name that matches none of its group's patterns comes last in
# the group.
_ORDER_WITHIN_RANK: dict[int, tuple[re.Pattern[str], ...]] = {
    0: (
        re.compile(r"^(?!\*\s|V\*\s)"),
        re.compile(r"^\*\s+[a-z]{2,3}\d{0,2}\s+[A-Z][a-z]{2}\b"),
        re.compile(r"^\*\s+\d+\s+[A-Z][a-z]{2}\b"),
    ),
    1: (re.compile(r"^HD[\s\d]"), re.compile(r"^HIP[\s\d]")),
}


def familiarity_key(name: str) -> tuple[int, int]:
    """Give the sort key that orders a star's names by familiarity.

    This is the library's one ranking of star names: `display_name_rank`
    gives the group, and inside the two most familiar groups the order
    people know names in breaks ties (see `_ORDER_WITHIN_RANK`).

    Parameters
    ----------
    name : `str`
        A name of the star, as SIMBAD or the library writes it.

    Returns
    -------
    key : `tuple` [`int`, `int`]
        The `display_name_rank` group, then the place inside the group;
        lower is more familiar.
    """
    text = name.strip()
    rank = display_name_rank(text)
    patterns = _ORDER_WITHIN_RANK.get(rank, ())
    for position, pattern in enumerate(patterns):
        if pattern.match(text):
            return rank, position
    return rank, len(patterns)


def order_names_by_familiarity(names: Iterable[str]) -> list[str]:
    """List a star's names, most familiar first, each once.

    Parameters
    ----------
    names : `Iterable` [`str`]
        Names of one star, in the order they were found. Empty names are
        left out.

    Returns
    -------
    ordered : `list` [`str`]
        The names, sorted by `familiarity_key`; names with equal keys keep
        their order.
    """
    unique: list[str] = []
    for name in names:
        text = str(name or "").strip()
        if text and text not in unique:
            unique.append(text)
    return sorted(unique, key=familiarity_key)


def most_familiar_name(identifiers: Iterable[str]) -> str | None:
    """Pick the name a person knows a star by best.

    Parameters
    ----------
    identifiers : `Iterable` [`str`]
        The star's names or SIMBAD identifiers, in any order.

    Returns
    -------
    name : `str` or `None`
        The most familiar one (see `familiarity_key`), with single spaces
        and without SIMBAD's ``NAME`` mark, or `None` for an empty list.
        Among names with equal keys the first in alphabetical order wins, so
        the answer does not depend on the order the names were listed in.
    """
    cleaned = [" ".join(str(item).split()) for item in identifiers if item and str(item).strip()]
    if not cleaned:
        return None
    return without_name_prefix(min(cleaned, key=lambda text: (familiarity_key(text), text)))


# The SIMBAD names kept as a star's aliases: proper, Bayer, Flamsteed and
# variable-star names, and the bright-star catalogs (groups 0 and 1 of
# `display_name_rank`). At most this many are kept, so the record stays
# small. Not tuning: a choice of what to show, not a measurement.
_ALIAS_NAME_RANK_LIMIT = 1
_MAXIMUM_ALIASES = 6


@dataclass(frozen=True)
class SimbadStarNames:
    """The id, name and aliases the library gives a star SIMBAD lists.

    Attributes
    ----------
    record_id : `str`
        The id the library saves the star under: ``Gaia DR3 <number>`` when
        SIMBAD lists a Gaia DR3 number, else SIMBAD's main identifier.
    name : `str`
        The most familiar name (see `most_familiar_name`).
    gaia_source_id : `int` or `None`
        The Gaia DR3 source number SIMBAD lists, if any.
    aliases : `list` [`str`]
        The other familiar names, most familiar first, without the id and
        the name, at most `_MAXIMUM_ALIASES`.
    """

    record_id: str
    name: str
    gaia_source_id: int | None
    aliases: list[str]


def name_simbad_star(main_id: str, identifiers: Iterable[str]) -> SimbadStarNames:
    """Name a star from SIMBAD's main identifier and list of identifiers.

    Both star identification paths (an astrometry run and a stack's own
    star names) name a SIMBAD star here, so the image and the library show
    the same name. A star Gaia DR3 lists is saved under its Gaia number,
    which every star in a field has and which never changes; a star Gaia
    does not list (the brightest ones) keeps its SIMBAD main identifier.

    Parameters
    ----------
    main_id : `str`
        SIMBAD's main identifier, or ``""`` when unknown.
    identifiers : `Iterable` [`str`]
        Every identifier SIMBAD lists for the star.

    Returns
    -------
    names : `SimbadStarNames`
        The id, name, Gaia number and aliases.
    """
    listed = [" ".join(str(item).split()) for item in identifiers if item and str(item).strip()]
    main = " ".join(str(main_id or "").split())
    gaia_source_id = gaia_dr3_source_id_from_names(listed)
    name = most_familiar_name([*listed, main]) or main or "Unknown"
    record_id = gaia_record_id(gaia_source_id) if gaia_source_id is not None else (main or name)
    # Only the names a reader may know the star by are kept as aliases, not
    # every survey number SIMBAD lists.
    familiar = [
        item
        for item in listed
        if not item.startswith(SIMBAD_NAME_PREFIX) and display_name_rank(item) <= _ALIAS_NAME_RANK_LIMIT
    ]
    aliases = [
        item for item in order_names_by_familiarity([main, *familiar]) if item not in (record_id, name)
    ][:_MAXIMUM_ALIASES]
    return SimbadStarNames(record_id=record_id, name=name, gaia_source_id=gaia_source_id, aliases=aliases)


# A Gaia DR3 name with a source number, such as "Gaia DR3 1328045433153485824".
# A made-up name for a Gaia star with no number ("Gaia DR3 J1234.5+12.3")
# does not match, because it is not digits only.
_GAIA_DR3_NAME_PATTERN = re.compile(rf"^\s*{re.escape(GAIA_DR3_PREFIX.strip())}\s+(\d+)\s*$")


def gaia_dr3_source_id_from_names(names: Iterable[str]) -> int | None:
    """Find a star's Gaia DR3 source number among its names.

    Parameters
    ----------
    names : `Iterable` [`str`]
        Names of one star, for example the ``|``-separated parts of SIMBAD's
        list of identifiers, or the star's own id and name.

    Returns
    -------
    source_id : `int` or `None`
        The number of the first name of the form ``Gaia DR3 <digits>``, or
        `None` when no name has that form.
    """
    for name in names:
        match = _GAIA_DR3_NAME_PATTERN.match(name)
        if match:
            return int(match.group(1))
    return None


def gaia_dr3_source_id_of(star: StellarObject) -> int | None:
    """Find a star's Gaia DR3 source number from everything it carries.

    This is the one place that answers "which Gaia DR3 source is this star?"
    for the spectroscopy steps that ask Gaia about a star (the reddening
    lookup and the XP spectrum comparison).

    Parameters
    ----------
    star : `StellarObject`
        The star. Its stored ``gaia_dr3_source_id`` (set from SIMBAD's list of
        names, or from a Gaia match) is used first. Otherwise its ``id``,
        ``name`` and ``target_ids`` are searched, in that order, for a name of
        the form ``Gaia DR3 <digits>``.

    Returns
    -------
    source_id : `int` or `None`
        The source number, or `None` when none is known. The brightest stars
        have none because Gaia DR3 does not list them.
    """
    if star.gaia_dr3_source_id:
        return int(star.gaia_dr3_source_id)
    return gaia_dr3_source_id_from_names(str(text or "") for text in (star.id, star.name, *star.target_ids))


def settle_names(survivor: StellarObject, others: Iterable[StellarObject]) -> None:
    """Give a merged star its most familiar name and keep the rest as aliases.

    Every id, name and alias of the survivor and of the rows merged into it
    is a name of the one star. The most familiar (see `display_name_rank`)
    becomes the survivor's name, unless it is a position-only id, and the
    others, except the survivor's id and position-only ids, become its
    aliases.

    Parameters
    ----------
    survivor : `StellarObject`
        The row that is kept, with its final id already set. Changed in
        place.
    others : `Iterable` [`StellarObject`]
        The rows merged into it.
    """
    names = [survivor.name, *survivor.aliases]
    for other in others:
        names.extend([other.name, other.id, *other.aliases])
    ordered = [name for name in order_names_by_familiarity(names) if not name.startswith("FIELD_J")]
    best = ordered[0] if ordered else survivor.id
    if display_name_rank(best) >= display_name_rank(survivor.id):
        # No name is more familiar than the id itself (a Gaia number).
        best = survivor.id
    survivor.name = best
    survivor.aliases = [name for name in ordered if name not in (survivor.id, survivor.name)]


# Fields that identify the row itself or are recomputed fresh by every
# pipeline run regardless of what is already on disk -- never gap-filled
# from a duplicate, either because overwriting them from an arbitrary
# cluster member would be wrong (id/name) or because a value here says
# nothing about which duplicate is "more complete" (is_catalog_identified
# is always False for a FIELD_J row by construction).
MERGE_EXCLUDED_FIELDS = frozenset({"id", "name", "aliases", "target_ids", "is_catalog_identified"})


def is_empty_value(value: Any) -> bool:
    """Report whether a StellarObject field value counts as "not yet set".

    Parameters
    ----------
    value : `Any`
        A field value read off a `StellarObject`.

    Returns
    -------
    is_empty : `bool`
        `True` if this value carries no real information yet, so a
        duplicate's own value for the same field is worth copying in.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, list | dict):
        return len(value) == 0
    if hasattr(value, "fluxes"):  # PhotometryResult
        return len(value.fluxes) == 0
    return False


def merge_duplicate_into_survivor(survivor: StellarObject, duplicate: StellarObject) -> None:
    """Copy a duplicate's non-empty fields onto the survivor, in place.

    Every declared `StellarObject` field is covered generically rather
    than hand-listed, so a field added to the model later is merged
    correctly without this function needing to be updated to match --
    the alternative (an explicit per-field list) is exactly the kind
    of thing that quietly drifts out of sync with the model it mirrors.
    Only fills a gap; a survivor's own non-empty value is never
    overwritten, so merging can only add data, never lose it.

    Parameters
    ----------
    survivor : `StellarObject`
        The row that will be kept, mutated in place.
    duplicate : `StellarObject`
        The row about to be deleted; nothing it uniquely holds is lost.
    """
    for field_name in duplicate.target_ids:
        if field_name not in survivor.target_ids:
            survivor.target_ids.append(field_name)
    # The duplicate's names stay known as the survivor's aliases. A
    # position-only id is no name, and the survivor's own id and name are
    # not repeated.
    names = order_names_by_familiarity([*survivor.aliases, duplicate.name, duplicate.id, *duplicate.aliases])
    survivor.aliases = [
        name for name in names if name not in (survivor.id, survivor.name) and not name.startswith("FIELD_J")
    ]

    for field_name in type(survivor).model_fields:
        if field_name in MERGE_EXCLUDED_FIELDS:
            continue
        if is_empty_value(getattr(survivor, field_name)):
            duplicate_value = getattr(duplicate, field_name)
            if not is_empty_value(duplicate_value):
                setattr(survivor, field_name, duplicate_value)


# Per-measurement lists of a light curve. Each is either empty or has one
# entry per timestamp.
_PER_MEASUREMENT_FIELDS = (
    "fluxes",
    "fluxes_normalized",
    "fluxes_detrended",
    "airmasses",
    "magnitudes",
    "is_saturated",
    "flux_errors",
    "fluxes_normalized_errors",
    "fluxes_detrended_errors",
    "time_bjd_tdb",
)


def merge_light_curves(first: PhotometryResult, second: PhotometryResult) -> PhotometryResult | None:
    """Join two light curves of the same star by measurement time.

    Where both have a measurement at the same time, the first is kept.
    A per-measurement list is kept only when both light curves have it.

    Parameters
    ----------
    first : `PhotometryResult`
        The survivor's light curve.
    second : `PhotometryResult`
        The other row's light curve.

    Returns
    -------
    merged : `PhotometryResult` or `None`
        The joined light curve, or `None` when a list is out of step with
        its timestamps in either curve (pairing values with times would
        then be a guess).
    """
    if not second.timestamps:
        return first
    if not first.timestamps:
        return second
    for light_curve in (first, second):
        for name in _PER_MEASUREMENT_FIELDS:
            values = getattr(light_curve, name)
            if values and len(values) != len(light_curve.timestamps):
                return None

    rows: dict = {}
    for light_curve in (second, first):  # the first is added last, so it wins ties
        for index, timestamp in enumerate(light_curve.timestamps):
            rows[timestamp] = {
                name: (getattr(light_curve, name)[index] if getattr(light_curve, name) else None)
                for name in _PER_MEASUREMENT_FIELDS
            }
    merged = PhotometryResult(timestamps=sorted(rows))
    for name in _PER_MEASUREMENT_FIELDS:
        if getattr(first, name) and getattr(second, name):
            setattr(merged, name, [rows[timestamp][name] for timestamp in merged.timestamps])
    series = merged.fluxes_detrended or merged.fluxes_normalized
    if len(series) >= 2 and float(np.mean(series)) > 0:
        merged.mean_flux = float(np.mean(series))
        merged.coefficient_of_variation = float(np.std(series) / np.mean(series))
    return merged


def merge_cluster(stars: dict[str, StellarObject], survivor_id: str) -> tuple[StellarObject, list[str]] | str:
    """Fold every other row of a cluster into the survivor.

    Parameters
    ----------
    stars : `dict` [`str`, `StellarObject`]
        The cluster's rows, by id.
    survivor_id : `str`
        The id of the row to keep.

    Returns
    -------
    result : `tuple` or `str`
        ``(survivor, removed_ids)`` when the merge is safe, otherwise a
        sentence saying why the cluster was left alone.
    """
    survivor = stars[survivor_id]
    others = [star for star_id, star in stars.items() if star_id != survivor_id]

    # A catalog gives every object its own id, so two ids from the same
    # catalog are two different objects, however close together (a close
    # binary, or neighboring sources in a cluster).
    # A position-only id carries no catalog identity, so it is left out.
    families = [catalog_family(star_id) for star_id in stars if not star_id.startswith("FIELD_J")]
    if len(set(families)) < len(families):
        return "two rows come from the same catalog, so they are different objects"

    # Keep the spectrum with the most measured samples, wherever it is held
    # (the survivor may have none at all, and the fill rule below cannot
    # move it: an empty spectrum result still counts as "set"). The
    # survivor is listed first, so it wins a tie.
    spectra_holders = [star for star in [survivor, *others] if star.has_spectra]
    if spectra_holders:
        best_holder = max(spectra_holders, key=lambda star: len(star.spectroscopy.wavelengths_angstrom))
        if best_holder is not survivor and len(best_holder.spectroscopy.wavelengths_angstrom) > len(
            survivor.spectroscopy.wavelengths_angstrom
        ):
            survivor.spectroscopy = best_holder.spectroscopy

    merged_curve = survivor.photometry
    for other in others:
        if merged_curve is None or other.photometry is None:
            continue
        merged_curve = merge_light_curves(merged_curve, other.photometry)
        if merged_curve is None:
            return "the light curves cannot be joined safely"

    for other in others:
        merge_duplicate_into_survivor(survivor, other)
    if merged_curve is not None:
        survivor.photometry = merged_curve
    settle_names(survivor, others)
    return survivor, [star.id for star in others]
