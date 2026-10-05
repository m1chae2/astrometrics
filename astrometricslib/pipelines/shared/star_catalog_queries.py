"""Purpose: Find stars in the library's star catalog, and record new ones.

Description: `StellarCatalog` is the public door to the star catalog. Its
methods check their arguments and call the functions here, which do the
work against the catalog storage (`AbstractCatalogAccess`):

* `run_star_query` answers `StellarCatalog.query`: it picks stars by ids,
  name, target, a region or a position, filters them by magnitude, spectrum
  and spectral class, pages them, and returns ids, summary rows, analysis
  records, full records, class counts or library statistics.
* `find_star` finds one star by id, allowing small spelling differences.
* `find_or_create_star` finds the star at a sky position, or records a new
  one there.

The storage reads only what each question needs. Summary rows come from the
indexed columns, so no full star record is loaded to list or count stars.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.catalog_queries import StarQueryResult
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.shared.star_analysis import (
    SPECTRAL_CLASS_LABELS,
    spectral_class_letter,
    summarize_star,
)

__all__ = [
    "QUERY_DETAILS",
    "QUERY_LIMITS",
    "QUERY_MAXIMUM_RADIUS_DEGREES",
    "StarQuery",
    "find_or_create_star",
    "find_star",
    "run_star_query",
]

QUERY_LIMITS = {"ids": 2000, "summary": 500, "analysis": 10, "objects": 10}
"""Most stars one answer holds, by detail level, when the caller gives a
limit. An analysis record is about two kilobytes, so ten fit well inside a
reply."""

MAXIMUM_MATCH_RANKING = 200
"""Most stars with spectra read when ranking a class by spectrum match."""

QUERY_MAXIMUM_RADIUS_DEGREES = 5.0
"""Widest region a query searches. A wider circle on a 274,000-star library
returns more than a client can use."""

QUERY_DETAILS = ("exists", "ids", "summary", "analysis", "objects", "class_counts", "stats")
"""The detail levels `StellarCatalog.query` accepts."""


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
    spectral_class : `str` or `None`
        Keep only stars of this spectral class.
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
    spectral_class: str | None = None

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
        ``hasPhotometry``, ``magnitude`` (`None` when unknown) and
        ``spectralType`` (empty when unknown).
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
        return [
            summary_row(star)
            for star in storage.list_stars_in_region(
                query.ra_deg, query.dec_deg, query.radius_deg, query.magnitude_range
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


def _best_matched_first(storage: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Put stars whose own spectrum matched best first.

    Only stars that have a spectrum are measured; the rest keep their order
    at the end. Ranking is skipped beyond `MAXIMUM_MATCH_RANKING` stars.

    Returns
    -------
    rows : `list` [`dict`]
        The same rows, matched stars first, lowest difference first.
    """
    with_spectra = [row for row in rows if row["hasSpectra"]][:MAXIMUM_MATCH_RANKING]
    scored = {}
    for star in storage.get_by_ids("stellar_catalog", [row["id"] for row in with_spectra]):
        rms = star.spectroscopy.self_determined_spectral_type_rms if star.spectroscopy else None
        if rms is not None:
            scored[star.id] = rms
    return sorted(rows, key=lambda row: (row["id"] not in scored, scored.get(row["id"], 0.0)))


def library_stats(storage: Any) -> dict[str, Any]:
    """Count how much data the star catalog holds.

    The counts come from the indexed summary columns, so no full star record
    is loaded.

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


def spectral_class_counts(storage: Any) -> list[dict[str, Any]]:
    """Count the library's stars by catalog spectral class.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.

    Returns
    -------
    classes : `list` [`dict`]
        One entry per class present, in class order, with the letter, a short
        label and the count. Stars with no catalog type are not counted.
    """
    counts: dict[str, int] = {}
    for summary in storage.list_star_summaries():
        letter = spectral_class_letter(summary.spectral_type or "")
        if letter:
            counts[letter] = counts.get(letter, 0) + 1
    return [
        {"spectralClass": letter, "label": SPECTRAL_CLASS_LABELS[letter], "count": counts[letter]}
        for letter in SPECTRAL_CLASS_LABELS
        if letter in counts
    ]


def run_star_query(
    storage: Any, query: StarQuery, detail: str, limit: int | None, offset: int
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
        ids, or a selector with a detail that covers the whole library.
    """
    selector = query.selector()
    if detail in ("stats", "class_counts"):
        if selector is not None:
            raise InvalidArgumentError(f"detail={detail!r} covers the whole library and takes no selector.")
        if detail == "stats":
            return StarQueryResult(detail=detail, stats=library_stats(storage))
        return StarQueryResult(detail=detail, classes=spectral_class_counts(storage))
    if query.spectral_class is not None and not spectral_class_letter(query.spectral_class):
        raise InvalidArgumentError(
            f"spectral_class must start with one of: {', '.join(SPECTRAL_CLASS_LABELS)}."
        )
    if detail == "exists":
        if query.ids is None:
            raise InvalidArgumentError("detail='exists' needs ids.")
        if limit is not None and len(query.ids) > QUERY_LIMITS["ids"]:
            raise InvalidArgumentError(f"At most {QUERY_LIMITS['ids']} ids per call.")
        return StarQueryResult(detail=detail, found=sorted(storage.existing_star_ids(query.ids)))
    # The radius cap protects a capped answer, meant for a person or an AI
    # client. A program that asks for every match (limit=None), such as the
    # sky map, may search a wider circle.
    if selector == "region" and (
        query.radius_deg <= 0 or (limit is not None and query.radius_deg > QUERY_MAXIMUM_RADIUS_DEGREES)
    ):
        raise InvalidArgumentError(f"radius_deg must be above 0 and at most {QUERY_MAXIMUM_RADIUS_DEGREES}.")

    rows = _selected_rows(storage, query, selector)
    if query.has_spectra is not None:
        rows = [row for row in rows if bool(row["hasSpectra"]) is query.has_spectra]
    if query.spectral_class is not None:
        wanted = spectral_class_letter(query.spectral_class)
        rows = [row for row in rows if spectral_class_letter(row["spectralType"] or "") == wanted]
    # A name search keeps the exact id match first; every other list is in
    # id order, so offset paging is stable.
    if selector != "name":
        rows.sort(key=lambda row: row["id"])
    total = len(rows)
    if detail == "analysis" and query.spectral_class is not None:
        rows = _best_matched_first(storage, rows)
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
