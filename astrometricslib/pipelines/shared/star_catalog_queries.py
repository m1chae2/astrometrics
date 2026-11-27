"""Purpose: Read, count and change the stars of the library's star catalog.

Description: `StellarCatalog` is the public door to the star catalog. Each of
its methods is a thin wrapper of one function here, which does the work
against the catalog storage (`AbstractCatalogAccess`):

* `get_star` finds one star by id, allowing small spelling differences, and
  `get_star_spectrum` reads its spectrum.
* `query_stars` lists the stars that match some filters (a target, a patch
  of sky, a magnitude range, the data they hold, a spectral class, whether
  the catalogs list them as variable, a search text) as a `Page` of star
  records. `star_summary_rows` gives the same matches as short rows, for
  library code that must stay cheap on a large catalog (the image overlay).
* `count_stars_by_class`, `count_stars_by_target` and
  `get_star_catalog_statistics` count stars without reading their records.
* `create_star`, `update_star` and `delete_star` change the catalog, and
  `find_or_create_star` finds the star at a sky position or gives a new,
  unsaved one there (the catalog keeps only measured stars).

A star's light curve is the bulk of its record, so it is read only when the
caller asks for it (``include=["light_curves"]``). The filters run on the
indexed summary columns, and only the stars of the page asked for are read
in full.
"""

import threading
import weakref
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Any

from astrometricslib._internal.drivers.catalog_access import UNRESOLVED_DETECTION_ID
from astrometricslib.foundation.arguments import check_choice, check_include, check_limit
from astrometricslib.foundation.errors import ConflictError, InvalidArgumentError, NotFoundError
from astrometricslib.models.catalog_queries import TargetStarCount
from astrometricslib.models.known_variability import KnownVariability
from astrometricslib.models.page import Page
from astrometricslib.models.star_catalog import SpectralClassCount, StarCatalogStatistics
from astrometricslib.models.stellar_source import (
    BRIGHTEST_CATALOG_MAGNITUDE,
    SpectroscopyResult,
    StellarObject,
    has_catalog_magnitude,
)
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.star_analysis import SPECTRAL_CLASS_LABELS, spectral_class_letter

__all__ = [
    "STAR_INCLUDE_PARTS",
    "STAR_ORDERS",
    "StarFilters",
    "count_stars_by_class",
    "count_stars_by_target",
    "create_star",
    "delete_star",
    "find_or_create_star",
    "find_star",
    "get_star",
    "get_star_catalog_statistics",
    "get_star_spectrum",
    "is_unresolved_detection",
    "query_stars",
    "read_stars",
    "star_summary_rows",
    "summary_row",
    "target_filter_id",
    "update_star",
]

STAR_INCLUDE_PARTS = ("light_curves",)
"""The heavy parts a star read can add with ``include=``."""

STAR_ORDERS = ("id", "useful", "match")
"""The orders `query_stars` can sort stars in."""

POSITION_ONLY_STAR_ID_PREFIX = "FIELD_J"
"""Start of the id given to a star found in an image but never matched to a
catalog. Must match `POSITION_ONLY_STAR_ID_PREFIX` in
`astrometricslib/_internal/drivers/catalog_access.py`."""

_VARIABLE_ANSWERS = frozenset({KnownVariability.KNOWN_VARIABLE, KnownVariability.SUSPECTED_VARIABLE})
"""What the catalogs must say about a star for ``variable=True`` to keep it."""


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
    return bool(UNRESOLVED_DETECTION_ID.search(star_id))


@dataclass(frozen=True)
class StarFilters:
    """The filters of one star query.

    Attributes
    ----------
    target_id : `str` or `None`
        Stars that belong to this target.
    ra_deg, dec_deg, radius_deg : `float` or `None`
        A circle on the sky (ICRS), in degrees. All three or none.
    magnitude_range : `tuple` [`float`, `float`] or `None`
        Lowest and highest magnitude to keep.
    has_spectra, has_photometry : `bool` or `None`
        Keep only stars with (or without) a spectrum or a light curve.
    has_catalog_magnitude : `bool` or `None`
        Keep only stars whose magnitude is (or is not) a real catalog
        magnitude (see `has_catalog_magnitude`).
    spectral_class : `str` or `None`
        Keep only stars of this catalog spectral class.
    variable : `bool` or `None`
        Keep only stars the catalogs list (or do not list) as variable.
    text : `str` or `None`
        Keep only stars whose id or name contains this text, ignoring case.
    include_unresolved : `bool`
        Keep the single-frame photometry detections (see
        `is_unresolved_detection`). They are hidden by default.
    """

    target_id: str | None = None
    ra_deg: float | None = None
    dec_deg: float | None = None
    radius_deg: float | None = None
    magnitude_range: tuple[float, float] | None = None
    has_spectra: bool | None = None
    has_photometry: bool | None = None
    has_catalog_magnitude: bool | None = None
    spectral_class: str | None = None
    variable: bool | None = None
    text: str | None = None
    include_unresolved: bool = False

    def check(self) -> None:
        """Refuse filters that do not make sense together.

        Raises
        ------
        InvalidArgumentError
            If a circle is given only in part, its radius is not above 0,
            or the spectral class is not one the catalog groups by.
        """
        circle = (self.ra_deg, self.dec_deg, self.radius_deg)
        if any(value is not None for value in circle) and any(value is None for value in circle):
            raise InvalidArgumentError("A sky circle needs ra_deg, dec_deg and radius_deg together.")
        if self.radius_deg is not None and self.radius_deg <= 0:
            raise InvalidArgumentError("radius_deg must be above 0.", details={"radius_deg": self.radius_deg})
        if self.spectral_class is not None and not spectral_class_letter(self.spectral_class):
            raise InvalidArgumentError(
                f"spectral_class must start with one of: {', '.join(SPECTRAL_CLASS_LABELS)}.",
                details={"spectral_class": self.spectral_class},
            )

    @property
    def in_region(self) -> bool:
        """Whether the filters name a circle on the sky.

        Returns
        -------
        in_region : `bool`
            `True` when ``radius_deg`` is given.
        """
        return self.radius_deg is not None


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


_STAR_ID_INDEX_LOCK = threading.Lock()
_STAR_ID_INDEXES: weakref.WeakKeyDictionary[Any, tuple[int, dict[str, str]]] = weakref.WeakKeyDictionary()
"""For each catalog storage: the star catalog's write count and the map from
a spelling-free id to the first stored id that has it."""


def _spelling_free(star_id: str) -> str:
    """Strip an id to what `find_star` compares.

    That is the id without spaces, underscores or capital letters.

    Parameters
    ----------
    star_id : `str`
        A star id.

    Returns
    -------
    key : `str`
        The id in lower case without spaces and underscores.
    """
    return star_id.lower().replace(" ", "").replace("_", "")


def _spelling_free_index(storage: Any) -> dict[str, str]:
    """Give the map from spelling-free id to stored id.

    The map is rebuilt only after a write to the star catalog.

    Reading every star id takes about half a second in a library of a few
    hundred thousand stars, and the Planetarium asks for one star every ten
    seconds. The map is kept per storage, together with the star catalog's
    write count (`get_dataset_version`), and is rebuilt when that count has
    changed. A storage that cannot report the count gets a fresh map every
    time, so it never answers from stale data.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.

    Returns
    -------
    index : `dict` [`str`, `str`]
        The first stored id for each spelling-free id, in the storage's order.
    """
    get_version = getattr(storage, "get_dataset_version", None)
    version = get_version("stellar_catalog") if callable(get_version) else None
    if not isinstance(version, int):
        version = None
    with _STAR_ID_INDEX_LOCK:
        cached = _STAR_ID_INDEXES.get(storage) if version is not None else None
        if cached is not None and cached[0] == version:
            return cached[1]
    index: dict[str, str] = {}
    for candidate_id in storage.list_star_ids():
        index.setdefault(_spelling_free(candidate_id), candidate_id)
    if version is not None:
        with _STAR_ID_INDEX_LOCK:
            try:
                _STAR_ID_INDEXES[storage] = (version, index)
            except TypeError:  # a storage that cannot be weakly referenced is not cached
                pass
    return index


def _stored_star_id(storage: Any, star_id: str) -> str | None:
    """Find the stored id that a given id names.

    An exact id is tried first, which is fast. If that fails, the ids are
    compared ignoring spaces, underscores and capital letters, using a map
    of the ids that is kept until the star catalog is next written to (see
    `_spelling_free_index`). Only the id column is read.

    Returns
    -------
    stored_id : `str` or `None`
        The stored id, or `None` when no star has it.
    """
    if storage.existing_star_ids([star_id]):
        return star_id
    return _spelling_free_index(storage).get(_spelling_free(star_id))


def read_stars(storage: Any, star_ids: list[str], *, light_curves: bool) -> list[StellarObject]:
    """Read some stars by their exact ids, in the order given.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    star_ids : `list` [`str`]
        The stored ids.
    light_curves : `bool`
        Read the light-curve points too. Without them a star reads much
        faster; its `StellarObject.light_curves_included` is then `False`.

    Returns
    -------
    stars : `list` [`StellarObject`]
        The stars found, in the order of ``star_ids``. An id with no star is
        skipped.
    """
    if not star_ids:
        return []
    if light_curves:
        found = storage.get_by_ids("stellar_catalog", star_ids)
    else:
        found = storage.get_stars_without_light_curves(star_ids)
    by_id = {star.id: star for star in found}
    return [by_id[star_id] for star_id in star_ids if star_id in by_id]


def find_star(storage: Any, star_id: str) -> StellarObject | None:
    """Find one star by id, with its light curve, allowing spelling slips.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    star_id : `str`
        The id to look for.

    Returns
    -------
    star : `StellarObject` or `None`
        The whole star, or `None` if there is none.
    """
    exact = storage.get_by_ids("stellar_catalog", [star_id])
    if exact:
        return exact[0]
    candidate_id = _spelling_free_index(storage).get(_spelling_free(star_id))
    if candidate_id is None:
        return None
    matches = storage.get_by_ids("stellar_catalog", [candidate_id])
    return matches[0] if matches else None


def get_star(
    storage: Any, star_id: str, *, include: Iterable[str] | None = None, required: bool = False
) -> StellarObject | None:
    """Find one star by its id, allowing small differences in spelling.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    star_id : `str`
        The star's id. Spaces, underscores and capital letters may differ
        from the stored id.
    include : `Iterable` [`str`] or `None`, optional
        Heavy parts to read too: ``"light_curves"``.
    required : `bool`, optional
        Raise instead of returning `None` when no star has the id.

    Returns
    -------
    star : `StellarObject` or `None`
        The star, or `None` when none has the id and ``required`` is
        `False`.

    Raises
    ------
    InvalidArgumentError
        If ``star_id`` is empty or an include part is unknown.
    NotFoundError
        If ``required`` is `True` and no star has the id.
    """
    parts = check_include(include, STAR_INCLUDE_PARTS)
    if not isinstance(star_id, str) or not star_id.strip():
        raise InvalidArgumentError("star_id must be a non-empty star id.", details={"star_id": star_id})
    stored_id = _stored_star_id(storage, star_id)
    found = read_stars(storage, [stored_id], light_curves="light_curves" in parts) if stored_id else []
    if not found:
        if required:
            raise NotFoundError(f"No star with id {star_id!r} in the library.", details={"star_id": star_id})
        return None
    return found[0]


def get_star_spectrum(storage: Any, star: StellarObject) -> SpectroscopyResult | None:
    """Read a star's spectrum.

    The spectrum lives on the star's own record. Older libraries also hold
    a separate copy of the star whose id ends in ``::spectroscopy``. When
    the star's own record has no spectrum, that copy is read instead, so a
    script does not need to know the copy exists.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    star : `StellarObject`
        The star, as `get_star` or `query_stars` gave it.

    Returns
    -------
    spectrum : `SpectroscopyResult` or `None`
        The star's spectrum, or `None` when it has none.
    """
    if star.has_spectra:
        return star.spectroscopy
    suffix = "::spectroscopy"
    base_id = star.id[: -len(suffix)] if star.id.endswith(suffix) else star.id
    for candidate in read_stars(storage, [base_id, base_id + suffix], light_curves=False):
        if candidate.has_spectra:
            return candidate.spectroscopy
    return None


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


def _selected_rows(storage: Any, filters: StarFilters) -> list[dict[str, Any]]:
    """Find the stars of a target or a sky circle, as summary rows.

    Returns
    -------
    rows : `list` [`dict`]
        One row per star, inside the magnitude range.
    """
    if filters.in_region:
        magnitude_range = filters.magnitude_range
        if filters.has_catalog_magnitude:
            # Only catalog magnitudes are wanted, so the database can skip
            # every star whose magnitude is missing or instrumental.
            low, high = magnitude_range or (BRIGHTEST_CATALOG_MAGNITUDE, 60.0)
            magnitude_range = (max(low, BRIGHTEST_CATALOG_MAGNITUDE), high)
        rows = [
            summary_row(star)
            for star in storage.list_stars_in_region(
                filters.ra_deg, filters.dec_deg, filters.radius_deg, magnitude_range
            )
        ]
        if filters.target_id is not None:
            rows = [row for row in rows if filters.target_id in (row["targetIds"] or [])]
        return rows
    summaries = storage.list_star_summaries(target_id=filters.target_id)
    return _within_magnitudes([summary_row(star) for star in summaries], filters.magnitude_range)


def _known_variability_of(storage: Any, star_ids: list[str]) -> dict[str, KnownVariability]:
    """Say what the catalogs record about each star being variable.

    Only the three catalog fields are read where the storage can read
    single fields; other storages read the stars without their light
    curves.

    Returns
    -------
    answers : `dict` [`str`, `KnownVariability`]
        One answer per star found.
    """
    reader = getattr(storage, "read_star_known_variability", None)
    if callable(reader):
        return reader(star_ids)
    return {star.id: star.known_variability for star in read_stars(storage, star_ids, light_curves=False)}


def _filtered_rows(storage: Any, filters: StarFilters) -> list[dict[str, Any]]:
    """Find the stars the filters select, as summary rows, not yet sorted.

    Returns
    -------
    rows : `list` [`dict`]
        Summary rows.
    """
    rows = _selected_rows(storage, filters)
    if not filters.include_unresolved:
        rows = [row for row in rows if not is_unresolved_detection(str(row["id"]))]
    if filters.has_spectra is not None:
        rows = [row for row in rows if bool(row["hasSpectra"]) is filters.has_spectra]
    if filters.has_photometry is not None:
        rows = [row for row in rows if bool(row["hasPhotometry"]) is filters.has_photometry]
    if filters.has_catalog_magnitude is not None:
        rows = [row for row in rows if row["hasCatalogMagnitude"] is filters.has_catalog_magnitude]
    if filters.spectral_class is not None:
        wanted = spectral_class_letter(filters.spectral_class)
        rows = [row for row in rows if spectral_class_letter(row["spectralType"] or "") == wanted]
    needle = (filters.text or "").strip().lower()
    if needle:
        rows = [
            row
            for row in rows
            if needle in str(row["id"]).lower() or needle in str(row["name"] or "").lower()
        ]
    if filters.variable is not None:
        answers = _known_variability_of(storage, [row["id"] for row in rows])
        rows = [row for row in rows if (answers.get(row["id"]) in _VARIABLE_ANSWERS) is filters.variable]
    return rows


def _best_matched_first(storage: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Put stars whose own spectrum matched best first.

    Only stars that have a spectrum are read, without their light curves;
    the rest keep their order at the end.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    rows : `list` [`dict`]
        Summary rows, already in id order.

    Returns
    -------
    rows : `list` [`dict`]
        The same rows, matched stars first, lowest difference first.
    """
    with_spectra = [row["id"] for row in rows if row["hasSpectra"]]
    scored: dict[str, float] = {}
    for star in read_stars(storage, with_spectra, light_curves=False):
        rms = star.spectroscopy.self_determined_spectral_type_rms if star.spectroscopy else None
        if rms is not None:
            scored[star.id] = rms
    return sorted(rows, key=lambda row: (row["id"] not in scored, scored.get(row["id"], 0.0)))


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


def _page_in_storage(
    storage: Any, filters: StarFilters, order_by: str, limit: int | None, offset: int
) -> tuple[int, list[dict[str, Any]]] | None:
    """Let the storage filter, order, count and page the listing.

    Only the plain listing qualifies: no sky circle, spectral class or
    variability filter, and the id or useful order. The database then counts
    the matches and reads one page, instead of this module loading every
    star and filtering it in Python.

    Returns
    -------
    answer : `tuple` [`int`, `list` [`dict`]] or `None`
        The total number of matches and the page as summary rows, or
        `None` when the query needs the full listing.
    """
    if filters.in_region or filters.spectral_class is not None or filters.variable is not None:
        return None
    if order_by not in ("id", "useful"):
        return None
    method = getattr(storage, "query_star_summary_page", None)
    if method is None:
        return None
    answer = method(
        target_id=filters.target_id,
        magnitude_range=filters.magnitude_range,
        has_spectra=filters.has_spectra,
        has_photometry=filters.has_photometry,
        has_catalog_magnitude=filters.has_catalog_magnitude,
        search=filters.text,
        include_unresolved=filters.include_unresolved,
        order=order_by,
        limit=limit,
        offset=offset,
    )
    if answer is None:
        return None
    total, summaries = answer
    return total, [summary_row(star) for star in summaries]


def _matching_page(
    storage: Any, filters: StarFilters, order_by: str | None, limit: int | None, offset: int
) -> tuple[int, list[dict[str, Any]]]:
    """Find, sort and page the stars the filters select, as summary rows.

    Returns
    -------
    answer : `tuple` [`int`, `list` [`dict`]]
        How many stars match in all, and the rows of the page.

    Notes
    -----
    Raises `InvalidArgumentError` if the filters do not make sense, the
    order is unknown, or ``limit`` or ``offset`` is out of range.
    """
    filters.check()
    order = order_by or "id"
    check_choice("order_by", order, STAR_ORDERS)
    check_limit(limit, 1, None)
    check_limit(offset, 0, None, name="offset", allow_none=False)
    paged = _page_in_storage(storage, filters, order, limit, offset)
    if paged is not None:
        return paged
    rows = _filtered_rows(storage, filters)
    if order == "useful":
        rows.sort(key=_useful_order_key)
    else:
        rows.sort(key=lambda row: row["id"])
    if order == "match":
        rows = _best_matched_first(storage, rows)
    page = rows[offset:] if limit is None else rows[offset : offset + limit]
    return len(rows), page


def star_summary_rows(
    storage: Any, filters: StarFilters, *, order_by: str | None = None, limit: int | None = None
) -> list[dict[str, Any]]:
    """List the stars the filters select as short summary rows.

    For library code that needs only the indexed facts of many stars (the
    image overlay, the catalog checks): no star record is read.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    filters : `StarFilters`
        The filters.
    order_by : `str`, optional
        One of `STAR_ORDERS`. By default, id order.
    limit : `int` or `None`, optional
        Most rows to return. `None` returns every match.

    Returns
    -------
    rows : `list` [`dict`]
        One `summary_row` per star.
    """
    return _matching_page(storage, filters, order_by, limit, 0)[1]


def query_stars(
    storage: Any,
    filters: StarFilters,
    *,
    order_by: str | None = None,
    include: Iterable[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> Page[StellarObject]:
    """List the stars that match some filters.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    filters : `StarFilters`
        The filters.
    order_by : `str`, optional
        ``"id"`` (the default), ``"useful"`` (stars with spectra first, then
        named stars, then stars with photometry, then brightest first) or
        ``"match"`` (stars whose own spectrum best matches a reference
        spectrum first).
    include : `Iterable` [`str`] or `None`, optional
        Heavy parts to read too: ``"light_curves"``.
    limit : `int` or `None`, optional
        Most stars to return, at least 1. `None` returns every match.
    offset : `int`, optional
        How many matches to skip, at least 0.

    Returns
    -------
    page : `Page` [`StellarObject`]
        The stars of the page, in order, with ``total`` and
        ``next_offset``.

    Notes
    -----
    Raises `InvalidArgumentError` if the filters do not make sense, an
    argument is out of range, or an include part is unknown.
    """
    parts = check_include(include, STAR_INCLUDE_PARTS)
    total, rows = _matching_page(storage, filters, order_by, limit, offset)
    stars = read_stars(storage, [row["id"] for row in rows], light_curves="light_curves" in parts)
    end = offset + len(rows)
    return Page[StellarObject](items=tuple(stars), total=total, next_offset=end if end < total else None)


def count_stars_by_class(
    storage: Any, *, target_id: str | None = None, include_unresolved: bool = False
) -> Page[SpectralClassCount]:
    """Count the library's stars by catalog spectral class.

    One grouped count over the stars' indexed columns; no star record is
    read.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    target_id : `str`, optional
        Count only the stars of this target.
    include_unresolved : `bool`, optional
        Also count single-frame detections. Defaults to `False`.

    Returns
    -------
    classes : `Page` [`SpectralClassCount`]
        One entry per class present, in class order (O, B, A, F, G, K, M, C,
        W). Stars with no catalog type are not counted.
    """
    counts: dict[str, int] = {}
    for group in storage.count_star_groups(include_unresolved):
        if target_id is not None and target_id not in group.target_ids:
            continue
        letter = spectral_class_letter(group.spectral_type)
        if letter:
            counts[letter] = counts.get(letter, 0) + group.count
    return Page[SpectralClassCount].of([
        SpectralClassCount(spectral_class=letter, label=SPECTRAL_CLASS_LABELS[letter], count=counts[letter])
        for letter in SPECTRAL_CLASS_LABELS
        if letter in counts
    ])


def count_stars_by_target(storage: Any, *, include_unresolved: bool = False) -> Page[TargetStarCount]:
    """Count each target's stars and say what data they have.

    One grouped count over the stars' indexed columns, so a target list can
    show a star count and spectra and photometry marks for every target
    at once.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    include_unresolved : `bool`, optional
        Also count single-frame detections. Defaults to `False`.

    Returns
    -------
    counts : `Page` [`TargetStarCount`]
        One entry per target id that at least one star belongs to, in
        target id order.
    """
    tallies: dict[str, list[Any]] = {}
    for group in storage.count_star_groups(include_unresolved):
        for target_id in group.target_ids:
            tally = tallies.setdefault(target_id, [0, False, False])
            tally[0] += group.count
            tally[1] = tally[1] or group.has_spectra
            tally[2] = tally[2] or group.has_photometry
    return Page[TargetStarCount].of([
        TargetStarCount(target_id=target_id, star_count=count, has_spectra=spectra, has_photometry=photometry)
        for target_id, (count, spectra, photometry) in sorted(tallies.items())
    ])


def get_star_catalog_statistics(storage: Any) -> StarCatalogStatistics:
    """Count how much data the star catalog holds.

    The counts come from the indexed summary columns, so no full star record
    is loaded. Every record is counted, single-frame detections included.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.

    Returns
    -------
    statistics : `StarCatalogStatistics`
        The counts and the coverage percentages.
    """
    summaries = storage.list_star_summaries()
    total = len(summaries)
    with_names = len([s for s in summaries if s.name and "Star_" not in s.id])
    with_spectral = len([s for s in summaries if s.spectral_type and s.spectral_type != "Unknown"])
    # None means "not known yet"; 0.0 is a real measured magnitude.
    with_magnitude = len([s for s in summaries if s.magnitude is not None])
    return StarCatalogStatistics(
        total_objects=total,
        identified_objects=with_names,
        with_spectral_type=with_spectral,
        with_magnitude=with_magnitude,
        spectral_coverage_pct=round((with_spectral / total * 100), 2) if total > 0 else 0.0,
        photometric_coverage_pct=round((with_magnitude / total * 100), 2) if total > 0 else 0.0,
    )


def _check_fields(changes: Collection[str], refused: Collection[str] = ()) -> None:
    """Refuse change names that are not star fields.

    Raises
    ------
    InvalidArgumentError
        If a name is not a `StellarObject` field, or is one of ``refused``.
    """
    unknown = sorted(set(changes) - set(StellarObject.model_fields))
    if unknown:
        raise InvalidArgumentError(
            f"Unknown star field(s): {', '.join(unknown)}.", details={"unknown": unknown}
        )
    not_allowed = sorted(set(changes) & set(refused))
    if not_allowed:
        raise InvalidArgumentError(
            f"These star fields cannot be changed this way: {', '.join(not_allowed)}.",
            details={"refused": not_allowed},
        )


def _check_position(ra_deg: float | None, dec_deg: float | None) -> None:
    """Refuse a sky position that is not a number in range.

    Raises
    ------
    InvalidArgumentError
        If ``ra_deg`` is outside 0 to 360 or ``dec_deg`` outside -90 to 90.
    """
    for name, value, low, high in (("ra_deg", ra_deg, 0.0, 360.0), ("dec_deg", dec_deg, -90.0, 90.0)):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int | float) or not low <= value <= high:
            raise InvalidArgumentError(
                f"{name} must be a number of degrees from {low:g} to {high:g}.", details={name: value}
            )


def create_star(
    storage: Any, name: str, ra_deg: float | None = None, dec_deg: float | None = None, **fields: Any
) -> StellarObject:
    """Record a new star in the catalog.

    The catalog keeps only stars with a light curve or a spectrum, so a new
    star given neither (in ``fields``) is returned but not saved.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    name : `str`
        The star's name, which is also its id.
    ra_deg, dec_deg : `float`, optional
        The position (ICRS), in degrees: 0 up to 360 and -90 to 90.
    **fields
        Other `StellarObject` fields, by field name (``spectral_type``,
        ``photometry``, ``target_ids`` and so on).

    Returns
    -------
    star : `StellarObject`
        The new star, read back with its light curve when it was saved.

    Raises
    ------
    InvalidArgumentError
        If ``name`` is empty, a position is not a number in range, or a
        field is not a star field (or is ``id``, ``right_ascension`` or
        ``declination``, which the arguments give).
    ConflictError
        If a star with that id is already in the catalog.
    """
    if not isinstance(name, str) or not name.strip():
        raise InvalidArgumentError("A star's name cannot be empty.", details={"name": name})
    _check_position(ra_deg, dec_deg)
    _check_fields(fields, refused=("id", "right_ascension", "declination"))
    if _stored_star_id(storage, name) is not None:
        raise ConflictError(f"A star with id {name!r} is already in the catalog.", details={"star_id": name})
    new_star = StellarObject(id=name, name=fields.pop("name", name) or name)
    if ra_deg is not None:
        new_star.right_ascension = float(ra_deg)
    if dec_deg is not None:
        new_star.declination = float(dec_deg)
    for field_name, value in fields.items():
        setattr(new_star, field_name, value)
    storage.merge_and_record(
        "stellar_catalog", [new_star], lambda current, updated: current if current is not None else updated
    )
    saved = read_stars(storage, [name], light_curves=True)
    return saved[0] if saved else new_star


def update_star(storage: Any, star: StellarObject, **changes: Any) -> StellarObject:
    """Change some of a stored star's fields and save it.

    The stored record is read again, with its light curve, and the changes
    are made to it, so a star read without its light curve can be updated
    safely. The star given is not changed.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    star : `StellarObject`
        The star, as `get_star` or `query_stars` gave it.
    **changes
        New values, by `StellarObject` field name (``spectral_type``, not
        ``spectralType``). The id cannot change.

    Returns
    -------
    star : `StellarObject`
        The saved star, with its light curve. When the change leaves it with
        no measurement, the catalog drops it and the changed star is
        returned unsaved.

    Raises
    ------
    NotFoundError
        If the star is not in the catalog.

    Notes
    -----
    Raises `InvalidArgumentError` if a name is not a star field, or is
    ``id``. Nothing is changed then.
    """
    _check_fields(changes, refused=("id",))
    stored = read_stars(storage, [star.id], light_curves=True)
    if not stored:
        raise NotFoundError(f"No star with id {star.id!r} in the catalog.", details={"star_id": star.id})
    changed = stored[0].model_copy(deep=True)
    for key, value in changes.items():
        setattr(changed, key, value)

    def _apply_changes(current: StellarObject | None, updated: StellarObject) -> StellarObject:
        """Write the changed fields onto the stored star.

        Returns
        -------
        star : `StellarObject`
            The stored star (or the new one) with the changes.
        """
        target_obj = current if current is not None else updated
        for key, value in changes.items():
            setattr(target_obj, key, value)
        return target_obj

    storage.merge_and_record("stellar_catalog", [changed], _apply_changes)
    saved = read_stars(storage, [star.id], light_curves=True)
    return saved[0] if saved else changed


def delete_star(storage: Any, star: StellarObject) -> None:
    """Delete a star from the catalog.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    star : `StellarObject`
        The star to delete.

    Raises
    ------
    NotFoundError
        If the star is not in the catalog.
    """
    if not storage.existing_star_ids([star.id]):
        raise NotFoundError(f"No star with id {star.id!r} in the catalog.", details={"star_id": star.id})
    storage.delete_by_ids("stellar_catalog", [star.id])


def _find_nearest(
    storage: Any, ra_deg: float, dec_deg: float, tolerance_arcsec: float
) -> StellarObject | None:
    """Find the star nearest a spot on the sky, within a tolerance.

    Only stars near the spot are read, through the declination index.

    Returns
    -------
    star : `StellarObject` or `None`
        The nearest star inside the tolerance, with its light curve, or
        `None`.
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


def _save_changes(storage: Any, star: StellarObject, changes: dict[str, Any]) -> StellarObject:
    """Apply changes to a star `find_or_create_star` found, saving if stored.

    Returns
    -------
    star : `StellarObject`
        The saved star, or the star changed in memory when it is not stored
        (it holds no measurement).
    """
    try:
        return update_star(storage, star, **changes)
    except NotFoundError:
        for field_name, value in changes.items():
            setattr(star, field_name, value)
        return star


def find_or_create_star(
    storage: Any,
    ra_deg: float,
    dec_deg: float,
    name: str | None = None,
    spectral_type: str | None = None,
    magnitude: float | None = None,
    target_id: str | None = None,
    tolerance_arcsec: float | None = None,
) -> StellarObject:
    """Find the star at a spot on the sky, or make a new one there.

    A star with the given name is the same star wherever it sits. Otherwise
    a star within ``tolerance_arcsec`` is the same star. Whichever saved
    star is used gets any of the spectral type, magnitude and target that it
    does not have yet. When neither exists, a new star is returned but not
    saved: it holds no light curve or spectrum, and the catalog keeps only
    measured stars (see `catalog_access.holds_measurements`).

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    ra_deg, dec_deg : `float`
        The position, in degrees.
    name : `str` or `None`, optional
        The star's catalog name. Also becomes the id of a new star.
    spectral_type : `str` or `None`, optional
        Spectral type to record if the star has none.
    magnitude : `float` or `None`, optional
        Magnitude to record if the star has none.
    target_id : `str` or `None`, optional
        Target to add to the star's list of targets.
    tolerance_arcsec : `float` or `None`, optional
        How close in position counts as the same star, in arcseconds. By
        default the library's registered same-star distance (2
        arcseconds), the one every other star-recording step uses.

    Returns
    -------
    star : `StellarObject`
        The star that was found or created.
    """
    if tolerance_arcsec is None:
        from astrometricslib.foundation.parameters import resolve
        from astrometricslib.pipelines.shared.catalog_star_identity import SAME_STAR_POSITION_TOLERANCE_ARCSEC

        tolerance_arcsec = float(resolve(SAME_STAR_POSITION_TOLERANCE_ARCSEC).value)

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
            return _save_changes(storage, existing, updates) if updates else existing

    # 2. Otherwise a star at the same spot is the same star.
    nearby = _find_nearest(storage, ra_deg, dec_deg, tolerance_arcsec)
    if nearby is not None:
        updates = {}
        if name and (not nearby.name or "Star_" in nearby.id) and not find_star(storage, name):
            # A field detection that a catalog has now named takes the
            # catalog name, unless that name is already another star.
            updates["name"] = name
            if "Star_" in nearby.id:
                # Move the whole record to its new id, keeping its light
                # curve and spectra, then remove the old one.
                old_id = nearby.id
                renamed = nearby.model_copy(deep=True)
                renamed.id = name
                storage.merge_and_record("stellar_catalog", [renamed], lambda _existing, updated: updated)
                if storage.existing_star_ids([old_id]):
                    storage.delete_by_ids("stellar_catalog", [old_id])
                nearby = renamed
        updates.update(missing_fields(nearby))
        return _save_changes(storage, nearby, updates) if updates else nearby

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

    # A new star holds no measurement, so it is returned without saving.
    return StellarObject(
        id=new_star_id,
        name=name or new_star_id,
        right_ascension=ra_deg,
        declination=dec_deg,
        spectral_type=spectral_type or "",
        stellar_spectral_type=spectral_type or "",
        magnitude=magnitude,
        target_ids=[target_id] if target_id else [],
    )


def target_filter_id(target: Target | None) -> str | None:
    """Give the target id a star filter compares, for a `Target` or `None`.

    Parameters
    ----------
    target : `Target` or `None`
        The target.

    Returns
    -------
    target_id : `str` or `None`
        The target's id, or `None` for no target.

    Raises
    ------
    InvalidArgumentError
        If ``target`` is not a `Target`.
    """
    if target is None:
        return None
    if not isinstance(target, Target):
        raise InvalidArgumentError(
            "target must be a Target; read one with astrometrics.targets.get(...).",
            details={"target": repr(target)[:80]},
        )
    return target.id
