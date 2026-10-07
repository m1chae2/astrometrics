"""Purpose: Find stars in the library's star catalog, and record new ones.

Description: `StellarCatalog` is the public door to the star catalog. Its
methods check their arguments and call the functions here, which do the
work against the catalog storage (`AbstractCatalogAccess`):

* `run_star_query` answers `StellarCatalog.query`: it picks stars by ids,
  name, target, a region or a position, filters them by magnitude, data,
  spectral class and a search text, hides single-frame detections, sorts
  and pages them, and returns ids, summary rows, analysis records, full
  records, class counts, per-target counts, overlay positions or library
  statistics.
* `find_star` finds one star by id, allowing small spelling differences.
* `find_or_create_star` finds the star at a sky position, or records a new
  one there.

The storage reads only what each question needs. Summary rows come from the
indexed columns, so no full star record is loaded to list or count stars.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.catalog_queries import StarQueryResult, TargetStarCount
from astrometricslib.models.stellar_source import (
    BRIGHTEST_CATALOG_MAGNITUDE,
    StellarObject,
    has_catalog_magnitude,
)
from astrometricslib.pipelines.shared.star_analysis import (
    SPECTRAL_CLASS_LABELS,
    spectral_class_letter,
    summarize_star,
)

__all__ = [
    "QUERY_DETAILS",
    "QUERY_LIMITS",
    "QUERY_MAXIMUM_RADIUS_DEGREES",
    "QUERY_ORDERS",
    "StarQuery",
    "find_or_create_star",
    "find_star",
    "is_unresolved_detection",
    "run_star_query",
]

QUERY_LIMITS = {"ids": 2000, "summary": 500, "analysis": 10, "objects": 10, "overlay": 200}
"""Most stars one answer holds, by detail level, when the caller gives a
limit. An analysis record is about two kilobytes, so ten fit well inside a
reply."""

MAXIMUM_MATCH_RANKING = 200
"""Most stars with spectra read when ranking a class by spectrum match."""

QUERY_MAXIMUM_RADIUS_DEGREES = 5.0
"""Widest region a query searches. A wider circle on a 274,000-star library
returns more than a client can use."""

QUERY_DETAILS = (
    "exists",
    "ids",
    "summary",
    "analysis",
    "objects",
    "overlay",
    "class_counts",
    "target_counts",
    "stats",
)
"""The detail levels `StellarCatalog.query` accepts."""

QUERY_ORDERS = ("id", "useful", "match")
"""The orders `StellarCatalog.query` can sort stars in."""

POSITION_ONLY_STAR_ID_PREFIX = "FIELD_J"
"""Start of the id given to a star found in an image but never matched to a
catalog. Must match `POSITION_ONLY_STAR_ID_PREFIX` in
`astrometricslib/drivers/catalog_access.py`."""

_UNRESOLVED_DETECTION_ID = re.compile(r":Star_\d+$")
"""The end of the id photometry gives each point source it finds in a single
frame: the session id (``"{target}:{night}:{gain}:{offset}"``) joined with
``":Star_<n>"``. These detections are kept in the star catalog, but they are
working records, not real catalog stars. One imaging session can leave
thousands of them."""


def is_unresolved_detection(star_id: str) -> bool:
    """Tell whether a star id belongs to a single-frame photometry detection.

    Parameters
    ----------
    star_id : `str`
        The star's id.

    Returns
    -------
    bool
        `True` when the id ends in ``":Star_<n>"``, the form photometry
        gives a point source it found in one frame. A curated catalog id
        such as ``"HD 1234"`` or ``"Star_12"`` gives `False`.
    """
    return bool(_UNRESOLVED_DETECTION_ID.search(star_id))


@dataclass
class StarQuery:
    """The selectors and filters of one star query.

    Attributes
    ----------
    ids : `list` [`str`] or `None`
        Star ids to look up.
    name : `str` or `None`
        A star id or name.
    target_id : `str` or `None`
        Stars that belong to this target.
    ra_deg, dec_deg : `float` or `None`
        Centre of a region or position search, in degrees.
    radius_deg : `float` or `None`
        Radius of a region search, in degrees.
    tolerance_arcsec : `float` or `None`
        Match radius of a position search, in arcseconds.
    magnitude_range : `tuple` [`float`, `float`] or `None`
        Lowest and highest magnitude to keep.
    has_spectra : `bool` or `None`
        Keep only stars with (or without) a spectrum.
    has_photometry : `bool` or `None`
        Keep only stars with (or without) a light curve.
    has_catalog_magnitude : `bool` or `None`
        Keep only stars whose magnitude is (or is not) a real catalog
        magnitude (see `has_catalog_magnitude`).
    spectral_class : `str` or `None`
        Keep only stars of this spectral class.
    search : `str` or `None`
        Keep only stars whose id or name contains this text, ignoring case.
    include_unresolved : `bool`
        Keep the single-frame photometry detections (see
        `is_unresolved_detection`). They are hidden by default.
    """

    ids: list[str] | None = None
    name: str | None = None
    target_id: str | None = None
    ra_deg: float | None = None
    dec_deg: float | None = None
    radius_deg: float | None = None
    tolerance_arcsec: float | None = None
    magnitude_range: tuple[float, float] | None = None
    has_spectra: bool | None = None
    has_photometry: bool | None = None
    has_catalog_magnitude: bool | None = None
    spectral_class: str | None = None
    search: str | None = None
    include_unresolved: bool = False

    def selector(self) -> str | None:
        """Name the one selector given.

        Returns
        -------
        selector : `str` or `None`
            ``"ids"``, ``"name"``, ``"target_id"``, ``"region"`` or
            ``"position"``, or `None` to browse the whole library.

        Raises
        ------
        InvalidArgumentError
            If several selectors are given, both a radius and a tolerance
            are given, or a region or position lacks its centre.
        """
        region_given = self.radius_deg is not None
        position_given = self.tolerance_arcsec is not None
        if region_given and position_given:
            raise InvalidArgumentError(
                "Give radius_deg (a region) or tolerance_arcsec (one position), not both."
            )
        if (region_given or position_given) and (self.ra_deg is None or self.dec_deg is None):
            raise InvalidArgumentError("A region or position search needs ra_deg and dec_deg.")
        chosen = [
            label
            for label, given in (
                ("ids", self.ids is not None),
                ("name", self.name is not None),
                ("target_id", self.target_id is not None),
                ("region", region_given),
                ("position", position_given),
            )
            if given
        ]
        if len(chosen) > 1:
            raise InvalidArgumentError(f"Give one selector, not several: {', '.join(chosen)}.")
        return chosen[0] if chosen else None


def summary_row(star: Any) -> dict[str, Any]:
    """Describe one star as a summary row.

    The keys are camelCase because the app's screens read the rows as they
    are.

    Parameters
    ----------
    star : `StellarObject` or `StarSummary`
        The star, as a full record or as an indexed summary.

    Returns
    -------
    row : `dict` [`str`, `Any`]
        ``id``, ``name``, ``ra``, ``dec``, ``targetIds``, ``hasSpectra``,
        ``hasPhotometry``, ``magnitude`` (`None` when unknown),
        ``hasCatalogMagnitude`` (whether ``magnitude`` is a real catalog
        magnitude, see `has_catalog_magnitude`) and ``spectralType`` (empty
        when unknown).
    """
    return {
        "id": star.id,
        "name": star.name,
        "ra": star.right_ascension,
        "dec": star.declination,
        "targetIds": star.target_ids,
        "hasSpectra": star.has_spectra,
        "hasPhotometry": star.has_photometry,
        "magnitude": star.magnitude,
        "hasCatalogMagnitude": has_catalog_magnitude(star.magnitude),
        "spectralType": star.spectral_type,
    }


def find_star(storage: Any, star_id: str) -> StellarObject | None:
    """Find one star by id, allowing small differences in spelling.

    An exact id is tried first, which is fast. If that fails, the ids are
    compared ignoring spaces, underscores and capital letters. Only the id
    column is read for that comparison, never the stars' full records.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    star_id : `str`
        The id to look for.

    Returns
    -------
    star : `StellarObject` or `None`
        The star, or `None` if there is none.
    """
    exact = storage.get_by_ids("stellar_catalog", [star_id])
    if exact:
        return exact[0]
    normalized = star_id.lower().replace(" ", "").replace("_", "")
    for candidate_id in storage.list_star_ids():
        if candidate_id.lower().replace(" ", "").replace("_", "") == normalized:
            matches = storage.get_by_ids("stellar_catalog", [candidate_id])
            return matches[0] if matches else None
    return None


def _find_all_by_name(storage: Any, name: str) -> list[StellarObject]:
    """Find every star whose id or name equals a name, ignoring case.

    Returns
    -------
    stars : `list` [`StellarObject`]
        The matches, an exact id match first.
    """
    matching_ids = storage.find_star_ids_by_name(name)
    if name in matching_ids:
        matching_ids.remove(name)
        matching_ids.insert(0, name)
    by_id = {star.id: star for star in storage.get_by_ids("stellar_catalog", matching_ids)}
    return [by_id[star_id] for star_id in matching_ids if star_id in by_id]


def _find_nearest(
    storage: Any, ra_deg: float, dec_deg: float, tolerance_arcsec: float
) -> StellarObject | None:
    """Find the star nearest a spot on the sky, within a tolerance.

    Only stars near the spot are read, through the declination index.

    Returns
    -------
    star : `StellarObject` or `None`
        The nearest star inside the tolerance, or `None`.
    """
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    spot = SkyCoord(ra=ra_deg, dec=dec_deg, unit=(u.deg, u.deg))
    nearest_id: str | None = None
    nearest_separation = tolerance_arcsec
    for candidate in storage.list_stars_in_region(ra_deg, dec_deg, tolerance_arcsec / 3600.0):
        # A stored 0.0 means "position never set", not the point (0, 0) on
        # the sky, so those stars are never matched.
        if not candidate.right_ascension or not candidate.declination:
            continue
        try:
            place = SkyCoord(
                ra=float(candidate.right_ascension), dec=float(candidate.declination), unit=(u.deg, u.deg)
            )
        except ValueError, TypeError:
            continue
        separation = spot.separation(place).arcsecond
        if separation < nearest_separation:
            nearest_id, nearest_separation = candidate.id, separation
    if nearest_id is None:
        return None
    matches = storage.get_by_ids("stellar_catalog", [nearest_id])
    return matches[0] if matches else None


def _within_magnitudes(
    rows: list[dict[str, Any]], magnitude_range: tuple[float, float] | None
) -> list[dict[str, Any]]:
    """Keep the rows inside a magnitude range.

    Returns
    -------
    rows : `list` [`dict`]
        The rows in range. A row with no magnitude is dropped when a range is
        given.
    """
    if magnitude_range is None:
        return rows
    low, high = magnitude_range
    return [row for row in rows if row["magnitude"] is not None and low <= row["magnitude"] <= high]


def _selected_rows(storage: Any, query: StarQuery, selector: str | None) -> list[dict[str, Any]]:
    """Find the stars a selector names, as summary rows.

    Returns
    -------
    rows : `list` [`dict`]
        One row per star, inside the magnitude range. Empty when a position
        search finds nothing.
    """
    if selector == "region":
        magnitude_range = query.magnitude_range
        if query.has_catalog_magnitude:
            # Only catalog magnitudes are wanted, so the database can skip
            # every star whose magnitude is missing or instrumental.
            low, high = magnitude_range or (BRIGHTEST_CATALOG_MAGNITUDE, 60.0)
            magnitude_range = (max(low, BRIGHTEST_CATALOG_MAGNITUDE), high)
        return [
            summary_row(star)
            for star in storage.list_stars_in_region(
                query.ra_deg, query.dec_deg, query.radius_deg, magnitude_range
            )
        ]
    if selector == "ids":
        stars = storage.get_by_ids("stellar_catalog", list(query.ids))
    elif selector == "name":
        stars = _find_all_by_name(storage, query.name)
    elif selector == "position":
        star = _find_nearest(storage, query.ra_deg, query.dec_deg, query.tolerance_arcsec)
        stars = [star] if star is not None else []
    else:
        summaries = storage.list_star_summaries(target_id=query.target_id)
        return _within_magnitudes([summary_row(star) for star in summaries], query.magnitude_range)
    return _within_magnitudes([summary_row(star) for star in stars], query.magnitude_range)


def _best_matched_first(
    storage: Any, rows: list[dict[str, Any]], ranking_cap: int | None
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Put stars whose own spectrum matched best first.

    Only stars that have a spectrum are measured; the rest keep their order
    at the end. Ranking stops after ``ranking_cap`` stars with spectra.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    rows : `list` [`dict`]
        Summary rows, already in id order.
    ranking_cap : `int` or `None`
        Most stars with spectra to read for ranking. `None` reads them all.

    Returns
    -------
    rows : `list` [`dict`]
        The same rows, matched stars first, lowest difference first.
    match_rms : `dict` [`str`, `float`]
        Each matched star's difference from its closest reference spectrum.
    """
    with_spectra = [row for row in rows if row["hasSpectra"]]
    if ranking_cap is not None:
        with_spectra = with_spectra[:ranking_cap]
    scored: dict[str, float] = {}
    for star in storage.get_by_ids("stellar_catalog", [row["id"] for row in with_spectra]):
        rms = star.spectroscopy.self_determined_spectral_type_rms if star.spectroscopy else None
        if rms is not None:
            scored[star.id] = rms
    ordered = sorted(rows, key=lambda row: (row["id"] not in scored, scored.get(row["id"], 0.0)))
    return ordered, scored


def _useful_order_key(row: dict[str, Any]) -> tuple:
    """Sort key that puts the most useful stars first.

    Parameters
    ----------
    row : `dict`
        One summary row.

    Returns
    -------
    key : `tuple`
        Stars with a spectrum come first, then stars with a real catalog
        name (not a ``FIELD_J`` position-only id), then stars with
        photometry, then by catalog magnitude with the brightest first. A
        star with no catalog magnitude sorts after every star that has one.
        The id comes last, so the order is the same on every request and
        page boundaries never repeat or skip a star.
    """
    known_magnitude = row["hasCatalogMagnitude"]
    return (
        not row["hasSpectra"],
        str(row["id"]).startswith(POSITION_ONLY_STAR_ID_PREFIX),
        not row["hasPhotometry"],
        not known_magnitude,
        row["magnitude"] if known_magnitude else 0.0,
        str(row["id"]),
    )


def library_stats(storage: Any) -> dict[str, Any]:
    """Count how much data the star catalog holds.

    The counts come from the indexed summary columns, so no full star record
    is loaded. Every record is counted, single-frame detections included.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.

    Returns
    -------
    stats : `dict` [`str`, `Any`]
        ``total_objects``, ``identified_objects``, ``spectral_coverage`` and
        ``photometric_coverage`` (percentages), and the raw counts.
    """
    summaries = storage.list_star_summaries()
    total = len(summaries)
    with_names = len([s for s in summaries if s.name and "Star_" not in s.id])
    with_spectral = len([s for s in summaries if s.spectral_type and s.spectral_type != "Unknown"])
    # None means "not known yet"; 0.0 is a real measured magnitude.
    with_magnitude = len([s for s in summaries if s.magnitude is not None])
    return {
        "total_objects": total,
        "identified_objects": with_names,
        "spectral_coverage": round((with_spectral / total * 100), 2) if total > 0 else 0,
        "photometric_coverage": round((with_magnitude / total * 100), 2) if total > 0 else 0,
        "stats": {"names": with_names, "spectral": with_spectral, "magnitude": with_magnitude},
    }


def _library_summaries(storage: Any, include_unresolved: bool) -> list[Any]:
    """Read every star's indexed summary, without single-frame detections.

    Returns
    -------
    summaries : `list` [`StarSummary`]
        Every star, or every star but the single-frame detections when
        ``include_unresolved`` is `False`.
    """
    summaries = storage.list_star_summaries()
    if include_unresolved:
        return summaries
    return [summary for summary in summaries if not is_unresolved_detection(summary.id)]


def spectral_class_counts(storage: Any, include_unresolved: bool = False) -> list[dict[str, Any]]:
    """Count the library's stars by catalog spectral class.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    include_unresolved : `bool`, optional
        Also count single-frame detections. Defaults to `False`.

    Returns
    -------
    classes : `list` [`dict`]
        One entry per class present, in class order, with the letter
        (``spectralClass``), a short ``label`` and the ``count``. Stars with
        no catalog type are not counted.
    """
    counts: dict[str, int] = {}
    for summary in _library_summaries(storage, include_unresolved):
        letter = spectral_class_letter(summary.spectral_type or "")
        if letter:
            counts[letter] = counts.get(letter, 0) + 1
    return [
        {"spectralClass": letter, "label": SPECTRAL_CLASS_LABELS[letter], "count": counts[letter]}
        for letter in SPECTRAL_CLASS_LABELS
        if letter in counts
    ]


def target_star_counts(storage: Any, include_unresolved: bool = False) -> dict[str, TargetStarCount]:
    """Count each target's stars and say what data they have.

    One pass over every star's indexed summary, so a target list can show
    a star count and spectra and photometry marks for every target at once.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    include_unresolved : `bool`, optional
        Also count single-frame detections. Defaults to `False`.

    Returns
    -------
    counts : `dict` [`str`, `TargetStarCount`]
        One entry per target id that at least one star belongs to.
    """
    counts: dict[str, TargetStarCount] = {}
    for summary in _library_summaries(storage, include_unresolved):
        for target_id in summary.target_ids or []:
            entry = counts.setdefault(target_id, TargetStarCount())
            entry.star_count += 1
            entry.has_spectra = entry.has_spectra or bool(summary.has_spectra)
            entry.has_photometry = entry.has_photometry or bool(summary.has_photometry)
    return counts


def _refuse_unused(detail: str, given: dict[str, bool]) -> None:
    """Refuse arguments that a detail level does not use.

    Parameters
    ----------
    detail : `str`
        The detail level asked for.
    given : `dict` [`str`, `bool`]
        For each argument the detail does not use, whether the caller gave
        it.

    Raises
    ------
    InvalidArgumentError
        If any of those arguments was given.
    """
    unused = sorted(name for name, was_given in given.items() if was_given)
    if unused:
        raise InvalidArgumentError(
            f"detail={detail!r} does not use: {', '.join(unused)}.",
            details={"detail": detail, "unused": unused},
        )


def _filter_arguments_given(query: StarQuery) -> dict[str, bool]:
    """Say which row filters a query gives.

    Returns
    -------
    given : `dict` [`str`, `bool`]
        For each filter argument, whether it differs from its default.
    """
    return {
        "magnitude_min/magnitude_max": query.magnitude_range is not None,
        "has_spectra": query.has_spectra is not None,
        "has_photometry": query.has_photometry is not None,
        "has_catalog_magnitude": query.has_catalog_magnitude is not None,
        "spectral_class": query.spectral_class is not None,
        "search": query.search is not None,
    }


def _whole_library_answer(
    storage: Any, query: StarQuery, selector: str | None, detail: str
) -> StarQueryResult:
    """Answer a detail level that covers the whole library.

    Returns
    -------
    answer : `StarQueryResult`
        Library statistics, class counts or per-target counts.

    Raises
    ------
    InvalidArgumentError
        If a selector, a filter, or (for statistics) ``include_unresolved``
        is given.
    """
    if selector is not None:
        raise InvalidArgumentError(f"detail={detail!r} covers the whole library and takes no selector.")
    _refuse_unused(
        detail,
        {
            **_filter_arguments_given(query),
            "include_unresolved": detail == "stats" and query.include_unresolved,
        },
    )
    if detail == "stats":
        return StarQueryResult(detail=detail, stats=library_stats(storage))
    if detail == "class_counts":
        return StarQueryResult(
            detail=detail, classes=spectral_class_counts(storage, query.include_unresolved)
        )
    return StarQueryResult(detail=detail, target_counts=target_star_counts(storage, query.include_unresolved))


def _filtered_rows(storage: Any, query: StarQuery, selector: str | None) -> list[dict[str, Any]]:
    """Find the stars a query selects and keep those its filters allow.

    Returns
    -------
    rows : `list` [`dict`]
        Summary rows, not yet sorted.
    """
    rows = _selected_rows(storage, query, selector)
    if not query.include_unresolved:
        rows = [row for row in rows if not is_unresolved_detection(str(row["id"]))]
    if query.has_spectra is not None:
        rows = [row for row in rows if bool(row["hasSpectra"]) is query.has_spectra]
    if query.has_photometry is not None:
        rows = [row for row in rows if bool(row["hasPhotometry"]) is query.has_photometry]
    if query.has_catalog_magnitude is not None:
        rows = [row for row in rows if row["hasCatalogMagnitude"] is query.has_catalog_magnitude]
    if query.spectral_class is not None:
        wanted = spectral_class_letter(query.spectral_class)
        rows = [row for row in rows if spectral_class_letter(row["spectralType"] or "") == wanted]
    needle = (query.search or "").strip().lower()
    if needle:
        rows = [
            row
            for row in rows
            if needle in str(row["id"]).lower() or needle in str(row["name"] or "").lower()
        ]
    return rows


def run_star_query(
    storage: Any,
    query: StarQuery,
    detail: str,
    limit: int | None,
    offset: int,
    order: str | None = None,
) -> StarQueryResult:
    """Answer a star query with a cap on how many stars come back.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    query : `StarQuery`
        The selectors and filters.
    detail : `str`
        One of `QUERY_DETAILS`.
    limit : `int` or `None`
        Most stars to return, cut to the detail's cap in `QUERY_LIMITS`.
        `None` returns every match.
    offset : `int`
        How many stars to skip, for paging.
    order : `str` or `None`, optional
        One of `QUERY_ORDERS`, or `None` for the default: a name search
        keeps the exact id match first, ``"analysis"`` with a spectral
        class ranks by spectrum match, and everything else is in id order.

    Returns
    -------
    answer : `StarQueryResult`
        The stars for the detail, ``total_matching``, and whether the
        answer was cut.

    Raises
    ------
    InvalidArgumentError
        If the request does not make sense: several selectors, a region that
        is too wide, an unknown spectral class, ``detail="exists"`` without
        ids, a selector with a detail that covers the whole library, or an
        argument the detail does not use.
    """
    from astrometricslib.pipelines.shared.star_overlay import build_overlay

    selector = query.selector()
    if detail in ("stats", "class_counts", "target_counts"):
        _refuse_unused(detail, {"order": order is not None, "offset": bool(offset)})
        return _whole_library_answer(storage, query, selector, detail)
    if query.spectral_class is not None and not spectral_class_letter(query.spectral_class):
        raise InvalidArgumentError(
            f"spectral_class must start with one of: {', '.join(SPECTRAL_CLASS_LABELS)}."
        )
    if detail == "exists":
        if query.ids is None:
            raise InvalidArgumentError("detail='exists' needs ids.")
        _refuse_unused(
            detail,
            {
                **_filter_arguments_given(query),
                "include_unresolved": query.include_unresolved,
                "order": order is not None,
            },
        )
        if limit is not None and len(query.ids) > QUERY_LIMITS["ids"]:
            raise InvalidArgumentError(f"At most {QUERY_LIMITS['ids']} ids per call.")
        return StarQueryResult(detail=detail, found=sorted(storage.existing_star_ids(query.ids)))
    if detail == "overlay":
        if selector != "target_id":
            raise InvalidArgumentError("detail='overlay' needs target_id, and no other selector.")
        _refuse_unused(
            detail,
            {
                **_filter_arguments_given(query),
                "include_unresolved": query.include_unresolved,
                "order": order is not None,
                "offset": bool(offset),
            },
        )
        cap = None if limit is None else max(1, min(int(limit), QUERY_LIMITS["overlay"]))
        overlay = build_overlay(storage, query.target_id, cap)
        return StarQueryResult(detail=detail, total_matching=len(overlay), offset=0, overlay=overlay)
    # The radius cap protects a capped answer, meant for a person or an AI
    # client. A program that asks for every match (limit=None), such as the
    # sky map, may search a wider circle.
    if selector == "region" and (
        query.radius_deg <= 0 or (limit is not None and query.radius_deg > QUERY_MAXIMUM_RADIUS_DEGREES)
    ):
        raise InvalidArgumentError(f"radius_deg must be above 0 and at most {QUERY_MAXIMUM_RADIUS_DEGREES}.")

    rows = _filtered_rows(storage, query, selector)
    if order is None:
        order = "match" if detail == "analysis" and query.spectral_class is not None else None
    match_rms: dict[str, float] | None = None
    if order == "useful":
        rows.sort(key=_useful_order_key)
    elif order is not None or selector != "name":
        # A name search keeps the exact id match first; every other list is
        # in id order, so offset paging is stable.
        rows.sort(key=lambda row: row["id"])
    if order == "match":
        rows, match_rms = _best_matched_first(storage, rows, None if limit is None else MAXIMUM_MATCH_RANKING)
    total = len(rows)
    offset = max(0, int(offset))
    page = (
        rows[offset:]
        if limit is None
        else rows[offset : offset + max(1, min(int(limit), QUERY_LIMITS[detail]))]
    )
    answer = StarQueryResult(
        detail=detail, total_matching=total, offset=offset, truncated=offset + len(page) < total
    )
    if detail == "ids":
        answer.ids = [row["id"] for row in page]
    elif detail == "summary":
        if match_rms is not None:
            page = [{**row, "selfDeterminedSpectralTypeRms": match_rms.get(row["id"])} for row in page]
        answer.stars = page
    else:
        by_id = {star.id: star for star in storage.get_by_ids("stellar_catalog", [row["id"] for row in page])}
        records = [by_id[row["id"]] for row in page if row["id"] in by_id]
        if detail == "objects":
            answer.objects = records
        else:
            answer.stars = [summarize_star(star) for star in records]
    return answer


def find_or_create_star(
    storage: Any,
    update: Callable[[str, dict[str, Any]], Any],
    create: Callable[..., Any],
    delete: Callable[[str], bool],
    ra_deg: float,
    dec_deg: float,
    name: str | None,
    spectral_type: str | None,
    magnitude: float | None,
    target_id: str | None,
    tolerance_arcsec: float,
) -> StellarObject:
    """Find the star at a spot on the sky, or record a new one there.

    A star with the given name is the same star wherever it sits. Otherwise
    a star within ``tolerance_arcsec`` is the same star. Only when neither
    exists is a new star recorded. Whichever star is used gets any of the
    spectral type, magnitude and target that it does not have yet.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    update, create, delete : `Callable`
        `StellarCatalog.update`, `StellarCatalog.create` and
        `StellarCatalog.delete`, which write the changes.
    ra_deg, dec_deg : `float`
        The position, in degrees.
    name : `str` or `None`
        The star's catalog name. Also becomes the id of a new star.
    spectral_type : `str` or `None`
        Spectral type to record if the star has none.
    magnitude : `float` or `None`
        Magnitude to record if the star has none.
    target_id : `str` or `None`
        Target to add to the star's list of targets.
    tolerance_arcsec : `float`
        How close in position counts as the same star, in arcseconds.

    Returns
    -------
    star : `StellarObject`
        The star that was found or created.
    """

    def missing_fields(star: StellarObject) -> dict[str, Any]:
        """List the fields this call adds to an existing star.

        Returns
        -------
        updates : `dict` [`str`, `Any`]
            The spectral type, magnitude and target the star lacks.
        """
        updates: dict[str, Any] = {}
        if spectral_type and not star.spectral_type:
            updates["spectral_type"] = spectral_type
            updates["stellar_spectral_type"] = spectral_type
        if magnitude is not None and star.magnitude is None:
            updates["magnitude"] = magnitude
        if target_id and target_id not in star.target_ids:
            updates["target_ids"] = [*list(star.target_ids), target_id]
        return updates

    # 1. A star with this exact id is the same star, wherever it sits.
    if name:
        matches = storage.get_by_ids("stellar_catalog", [name])
        if matches:
            existing = matches[0]
            updates = missing_fields(existing)
            if updates:
                update(existing.id, updates)
                existing = find_star(storage, existing.id)
            return existing

    # 2. Otherwise a star at the same spot is the same star.
    nearby = _find_nearest(storage, ra_deg, dec_deg, tolerance_arcsec)
    if nearby is not None:
        updates = {}
        new_id = nearby.id
        if name and (not nearby.name or "Star_" in nearby.id) and not find_star(storage, name):
            # A field detection that a catalog has now named takes the
            # catalog name, unless that name is already another star.
            updates["name"] = name
            if "Star_" in nearby.id:
                new_id = name
                updates["id"] = name
        updates.update(missing_fields(nearby))
        if updates:
            if new_id != nearby.id:
                delete(nearby.id)
                create(new_id, ra=ra_deg, dec=dec_deg)
            update(new_id, updates)
            nearby = find_star(storage, new_id)
        return nearby

    # 3. Nothing there yet: record a new star.
    if name:
        new_star_id = name
    else:
        base_id = f"Star_{len(storage.list_star_ids()) + 1}"
        new_star_id = base_id
        counter = 1
        while find_star(storage, new_star_id):
            new_star_id = f"{base_id}_{counter}"
            counter += 1

    create(new_star_id, ra=ra_deg, dec=dec_deg)
    new_star_updates: dict[str, Any] = {"name": name or new_star_id}
    if spectral_type:
        new_star_updates["spectral_type"] = spectral_type
        new_star_updates["stellar_spectral_type"] = spectral_type
    if magnitude is not None:
        new_star_updates["magnitude"] = magnitude
    if target_id:
        new_star_updates["target_ids"] = [target_id]
    update(new_star_id, new_star_updates)
    return find_star(storage, new_star_id)
