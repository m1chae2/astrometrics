"""Purpose: Serve the app's star catalog, sky map sources and visibility.

Description: `StellarService` answers the Astronomy Manager's and the
Planetarium's requests by calling the libraries: `StellarCatalog.query` for
star listings, counts, spectral classes and the image overlay, and
`ObservationPlanning` for sky map sources and visibility. It adds only what
a server needs on top: caching of the slow whole-catalog answers, a limit on
how many period searches run at once, archiving a star before it is
deleted, and the mapping from the app's request arguments to library
arguments.
"""

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from astrometricslib import (
    Astrometrics,
    AstrometricsError,
    InvalidArgumentError,
    StarQueryResult,
    StellarObject,
)
from backend.services.data.deletion_archive import archive_record_before_delete

logger = logging.getLogger(__name__)

_Answer = TypeVar("_Answer")

# The command that downloads the deep-star catalog the Planetarium draws
# from. Sent to the UI with the catalog's status so the first-launch prompt
# shows the same command the script documents.
DEEP_CATALOG_INSTALL_COMMAND = "python -m wayfindinglib.scripts.build_deep_star_catalog"

# How many period searches may run at once. A search is a few seconds of
# work on one processor core (a dip search shuffles the light curve 150 to
# 300 times), so two at a time cannot swamp the computer, while a click
# never waits behind a long stacking job the way it would if it shared the
# heavy-job slots that stacking and image analysis use.
_MAXIMUM_CONCURRENT_PERIOD_SEARCHES = 2

# The whole-catalog answers (per-target counts, class counts, a class's
# stars) each read every star's summary, which takes seconds on a large
# library. They are kept until `CatalogAccess.get_dataset_version`
# says a write has happened, not on a timer. This is only the fallback: how
# long an answer may be served for a version already confirmed current, in
# case a program outside this process (such as a maintenance script) wrote
# to the library without the version counter seeing it.
_CATALOG_ANSWER_CACHE_FALLBACK_MAX_AGE_SECONDS = 300.0

# How many overlay answers are remembered (see
# `get_astrometry_overlay_stars`).
_OVERLAY_CACHE_SIZE = 64

# The app's ``filter_type`` values and the library filter each one sets.
_SPECTRA_FILTERS = ("with spectra", "spectra", "hasspectra")
_PHOTOMETRY_FILTERS = ("with photometry", "photometry", "hasphotometry")


def _overlay_reference_image_path(target: Any) -> str | None:
    """Find the image the overlay star positions are measured on.

    Used only to notice when that image changes, so a remembered overlay is
    not served for a new image.

    Parameters
    ----------
    target : `Any`
        The target record, or `None` when the target is unknown.

    Returns
    -------
    path : `str` or `None`
        The stacked image path, else the processed image path, or `None`
        when the target has neither.
    """
    stacking = getattr(target, "stacking", None)
    path = getattr(stacking, "stacked_image", None) or getattr(stacking, "processed_image", None)
    return str(path) if path else None


class StellarService:
    """Answer the app's star catalog, sky map and visibility requests."""

    def __init__(
        self, config: Any, astrometrics: Astrometrics | None = None, wayfinder: Any | None = None
    ) -> None:
        """Hold the libraries and set up the caches.

        Parameters
        ----------
        config : `AppConfiguration`
            The application configuration.
        astrometrics : `Astrometrics`, optional
            The science library. Built from ``config`` when omitted.
        wayfinder : `Wayfinder`, optional
            The planning library. Built on first use when omitted.
        """
        self.config = config
        self._overlay_cache: OrderedDict[tuple, list[dict]] = OrderedDict()
        self.astrometrics = astrometrics or Astrometrics(config)
        self._wayfinder = wayfinder
        self._period_search_slots = threading.BoundedSemaphore(_MAXIMUM_CONCURRENT_PERIOD_SEARCHES)
        self._catalog_answers: dict[str, tuple[float, int, Any]] = {}
        self._catalog_answers_lock = threading.Lock()
        self._announced_catalog_version: int | None = None
        self._socket_manager = None

    def set_socket_manager(self, socket_manager: Any) -> None:
        """Give this service a socket manager to notify the UI through.

        Set once, after construction, by `backend.container` -- the socket
        manager is built after this service is, so it can't be a
        constructor argument. Notifications are silently skipped until
        this is called (e.g. in a test that constructs `StellarService`
        directly).

        Parameters
        ----------
        socket_manager : `SocketManager`
            Socket manager used to broadcast catalog-change events to
            connected UI clients.
        """
        self._socket_manager = socket_manager

    @property
    def wayfinder(self) -> Any:
        """The planning library, built on first use when not injected.

        Returns
        -------
        wayfinder : `Wayfinder`
            The injected instance, or one built over the configuration.
        """
        if self._wayfinder is None:
            from wayfindinglib import Wayfinder

            self._wayfinder = Wayfinder(config=self.config)
        return self._wayfinder

    def _cached_catalog_answer(self, key: str, compute: Callable[[], _Answer]) -> _Answer:
        """Answer a whole-catalog question, reusing the last answer if current.

        Freshness is checked against `CatalogAccess.get_dataset_version`
        rather than a timer, so nothing is recomputed while the catalog is
        unchanged. `_CATALOG_ANSWER_CACHE_FALLBACK_MAX_AGE_SECONDS` is only a
        backstop for a write that counter missed.

        The first answer computed for a new catalog version broadcasts a
        ``"catalog:changed"`` UI event (see `set_socket_manager`), so a
        connected client can refetch instead of polling on a timer.

        Parameters
        ----------
        key : `str`
            Names the question, such as ``"class_counts"``.
        compute : `Callable`
            Computes the answer from the library.

        Returns
        -------
        answer : `Any`
            The cached or newly computed answer.
        """
        current_version = self.astrometrics.catalog_access.get_dataset_version("stellar_catalog")
        now = time.monotonic()
        with self._catalog_answers_lock:
            cached = self._catalog_answers.get(key)
            if cached is not None:
                cached_at, cached_version, answer = cached
                if (
                    cached_version == current_version
                    and now - cached_at < _CATALOG_ANSWER_CACHE_FALLBACK_MAX_AGE_SECONDS
                ):
                    return answer

        answer = compute()

        with self._catalog_answers_lock:
            self._catalog_answers[key] = (now, current_version, answer)
            announce = self._announced_catalog_version != current_version
            self._announced_catalog_version = current_version
        if announce:
            self._notify_catalog_changed()
        return answer

    def _notify_catalog_changed(self) -> None:
        """Tell connected UI clients the stellar catalog changed.

        A no-op until `set_socket_manager` has been called.
        """
        if self._socket_manager is None:
            return
        self._socket_manager.broadcast_ui_event_sync("catalog:changed", {"dataset": "stellar_catalog"})

    def get_stellar_objects(self, target_id: str | None = None) -> list[StellarObject]:
        """Read full star records, single-frame detections included.

        Warning: with no ``target_id`` this reads every star in the library
        into new objects -- about 12 seconds and 2.8 GB on a 274,000-star
        library -- so it is only for a caller that truly needs all of them.
        Pass a ``target_id`` to read just that target's stars.

        Returns
        -------
        result : `list` of `StellarObject`
            Stellar objects, optionally only those of ``target_id``.
        """
        return (
            self.astrometrics.stars.query(
                target_id=target_id or None, detail="objects", include_unresolved=True, limit=None
            ).objects
            or []
        )

    def _catalog_version(self) -> tuple[int, ...]:
        """Say whether the star database may have changed since last asked.

        Returns
        -------
        version : `tuple` [`int`, ...]
            The modification times of the database file and its write-ahead
            log. Any write to the library changes it, whichever code made
            the write.
        """
        library = Path(self.config.get_library_path())
        times = []
        for name in ("astrometrics.db", "astrometrics.db-wal"):
            try:
                times.append((library / name).stat().st_mtime_ns)
            except OSError:
                times.append(0)
        return tuple(times)

    def get_astrometry_overlay_stars(self, target_id: str, limit: int = 35) -> list[dict]:
        """Place a target's catalog stars on its image, remembering answers.

        Working out the overlay for a target with tens of thousands of
        stars takes seconds. The answer only changes when the library
        database or the target's reference image changes, so it is kept
        (the 64 most recent) and reused until one of them does.

        Parameters
        ----------
        target_id : `str`
            Target identifier to retrieve overlay stars for.
        limit : `int`, optional
            Maximum number of stars to return (default 35). Zero or less
            returns every star the library places, up to its cap.

        Returns
        -------
        result : `list` of `dict`
            One `OverlayStar` per star, with the keys ``id``, ``name``,
            ``x``, ``y``, ``spectralType``, ``isCatalogIdentified``,
            ``referenceWidth``, ``referenceHeight`` and ``radiusPx``.
        """
        if not target_id:
            return []
        try:
            target = self.astrometrics.targets.get(target_id)
            image_path = _overlay_reference_image_path(target)
            image_time = Path(image_path).stat().st_mtime_ns if image_path else 0
            key = (target_id, limit, str(image_path), image_time, self._catalog_version())
        except AstrometricsError, OSError:
            return self._compute_overlay(target_id, limit)

        cache = self._overlay_cache
        if key in cache:
            cache.move_to_end(key)
            return [dict(star) for star in cache[key]]
        result = self._compute_overlay(target_id, limit)
        cache[key] = [dict(star) for star in result]
        while len(cache) > _OVERLAY_CACHE_SIZE:
            cache.popitem(last=False)
        return result

    def _compute_overlay(self, target_id: str, limit: int) -> list[dict]:
        """Ask the library to place a target's stars on its image.

        Returns
        -------
        result : `list` of `dict`
            The library's overlay stars, with the app's camelCase keys.
        """
        answer = self.astrometrics.stars.query(
            target_id=target_id, detail="overlay", limit=limit if limit and limit > 0 else None
        )
        return [star.model_dump(by_alias=True) for star in answer.overlay or []]

    def _query_listing(
        self,
        target_id: str | None,
        search: str | None,
        filter_type: str | None,
        detail: str,
        limit: int | None,
        offset: int,
    ) -> StarQueryResult:
        """Run a star listing query with the app's scope arguments.

        Parameters
        ----------
        target_id : `str`, optional
            Restrict to stars belonging to this target.
        search : `str`, optional
            Text to find in a star's id or name.
        filter_type : `str`, optional
            The app's filter, such as ``"With Spectra"`` or
            ``"photometry"``. An unknown value filters nothing.
        detail : `str`
            The library detail level, ``"summary"`` or ``"ids"``.
        limit : `int` or `None`
            Most stars to return. `None` returns every match.
        offset : `int`
            How many matching stars to skip.

        Returns
        -------
        answer : `StarQueryResult`
            The library's answer. A scoped, searched or filtered listing is
            sorted most useful first; a plain browse is in id order.
        """
        needle = search.strip() if search and search.strip() else None
        normalized_filter = filter_type.strip().lower() if filter_type else ""
        return self.astrometrics.stars.query(
            target_id=target_id or None,
            search=needle,
            has_spectra=True if normalized_filter in _SPECTRA_FILTERS else None,
            has_photometry=True if normalized_filter in _PHOTOMETRY_FILTERS else None,
            detail=detail,
            order="useful" if (target_id or needle or filter_type) else "id",
            limit=limit,
            offset=offset,
        )

    def count_displayable_stellar_objects(
        self,
        target_id: str | None = None,
        search: str | None = None,
        filter_type: str | None = None,
    ) -> int:
        """Count the stars a scoped, filtered listing would return.

        Single-frame photometry detections are not counted.

        Parameters
        ----------
        target_id : `str`, optional
            Restrict to stars belonging to this target.
        search : `str`, optional
            Text to find in a star's id or name.
        filter_type : `str`, optional
            Filter category, e.g. "With Spectra" / "spectra" or
            "With Photometry" / "photometry".

        Returns
        -------
        total : `int`
            Number of stars matching `target_id`, `search`, and
            `filter_type`.
        """
        return self._query_listing(target_id, search, filter_type, "ids", 1, 0).total_matching or 0

    def get_displayable_stellar_object_summaries(
        self,
        target_id: str | None = None,
        limit: int | None = 100,
        offset: int | None = 0,
        search: str | None = None,
        filter_type: str | None = None,
    ) -> list[dict]:
        """List one page of star summaries for the Astronomy Manager.

        Single-frame photometry detections are left out.

        Parameters
        ----------
        target_id : `str`, optional
            Restrict to stars belonging to this target.
        limit : `int`, optional
            Maximum number of stars to return. Defaults to 100. Zero or
            `None` returns every match (the library caps a page at 500).
        offset : `int`, optional
            Number of initial matching stars to skip for pagination.
            Defaults to 0.
        search : `str`, optional
            Text to find in a star's id or name.
        filter_type : `str`, optional
            Filter category, e.g. "With Spectra" / "spectra" or
            "With Photometry" / "photometry".

        Returns
        -------
        summaries : `list` [`dict`]
            One summary row per star (``id``, ``name``, ``ra``, ``dec``,
            ``targetIds``, ``hasSpectra``, ``hasPhotometry``,
            ``magnitude``, ``hasCatalogMagnitude`` and ``spectralType``).
            A listing for a target, a search or a filter is sorted with
            spectra first, then named stars, then photometry, then
            brightest first; an unfiltered listing is in id order.
        """
        answer = self._query_listing(
            target_id,
            search,
            filter_type,
            "summary",
            limit if limit is not None and limit > 0 else None,
            max(0, offset or 0),
        )
        return answer.stars or []

    def warm_catalog_summary_cache(self) -> None:
        """Compute the slow whole-catalog answers now, so they are cached.

        Call this once during backend startup, so a user who opens the
        Astronomy Manager first does not wait for the per-target counts and
        the class counts there.
        """
        self.get_target_data_availability()
        self.get_spectral_class_summary()

    def get_target_data_availability(self) -> dict[str, dict[str, bool | int]]:
        """Say how many stars each target has, and what data they have.

        Returns
        -------
        availability : `dict` [`str`, `dict` [`str`, `bool` or `int`]]
            One entry per target id a star belongs to, each holding
            ``hasSpectra`` and ``hasPhotometry`` (true if any of that
            target's stars has that kind of data) and ``starCount`` (how
            many stars belong to it).
        """

        def compute() -> dict[str, dict[str, bool | int]]:
            """Ask the library for the per-target counts.

            Returns
            -------
            availability : `dict`
                The counts, with the app's camelCase keys.
            """
            counts = self.astrometrics.stars.query(detail="target_counts").target_counts or {}
            return {target_id: entry.model_dump(by_alias=True) for target_id, entry in counts.items()}

        return self._cached_catalog_answer("target_counts", compute)

    def get_spectral_class_summary(self) -> list[dict[str, Any]]:
        """List the catalog spectral classes present, with counts.

        Stars are grouped by the first letter of their catalog spectral type
        (O, B, A, F, G, K, M, C or W).

        Returns
        -------
        classes : `list` [`dict`]
            One entry per spectral class present, in class order, each with
            ``spectralClass`` (the letter), ``label`` (a short description),
            and ``count`` (how many stars have that class).
        """
        return self._cached_catalog_answer(
            "class_counts", lambda: self.astrometrics.stars.query(detail="class_counts").classes or []
        )

    def get_stars_by_spectral_class(self, spectral_class: str) -> list[dict[str, Any]]:
        """List a catalog spectral class's stars, best matched first.

        Stars are ranked by how closely their own extracted spectrum matched
        its best reference spectrum (lower is closer). Stars with no match
        yet are listed last, in id order.

        Parameters
        ----------
        spectral_class : `str`
            The spectral class letter, as `get_spectral_class_summary`
            returns it (e.g. "G"). A full catalog string like "G2V" also
            works.

        Returns
        -------
        stars : `list` [`dict`]
            One summary row per matching star, best match first, each with
            ``selfDeterminedSpectralTypeRms`` (`None` when not yet matched)
            added. A class the library does not group by raises the
            library's `InvalidArgumentError`.
        """
        return self._cached_catalog_answer(
            f"class:{spectral_class.strip().upper()[:1]}",
            lambda: (
                self.astrometrics.stars.query(spectral_class=spectral_class, order="match", limit=None).stars
                or []
            ),
        )

    def save_objects(self) -> str:
        """Report that the stellar catalog is saved; there is nothing to do.

        Every change to a star is written to the database at the moment it
        is made (see `StellarCatalog.update`, `create` and
        `find_or_create_by_position`), so there is no unsaved list to write.
        This used to load every star from the database and write them all
        back, replacing the whole table. That cost about 12 seconds and
        2.8 GB each time, ran at the end of every re-index, and could
        delete a star that another process added between the read and the
        write. It stays as a method because the ``astronomy:save`` request
        and the re-index job still call it.

        Returns
        -------
        result : `str`
            A status message.
        """
        return "stellar catalog saved"

    def get_object(self, object_id: str) -> StellarObject | None:
        """Retrieve a stellar object by ID.

        Returns
        -------
        result : `StellarObject` or `None`
            The matching stellar object, or `None` if not found.
        """
        return self.astrometrics.stars.get(object_id)

    def get_object_fuzzy(self, search_term: str) -> StellarObject | None:
        """Retrieve a stellar object by ID or name.

        Matching is case-insensitive and allows partial matches.

        Returns
        -------
        result : `StellarObject` or `None`
            The matching stellar object, or `None` if not found.

        REQ: BKD-7.2
        """
        return self.astrometrics.stars.get(search_term)

    def get_object_fuzzy_by_id(
        self, object_id: str | None = None, search_term: str | None = None
    ) -> StellarObject | None:
        """RPC wrapper to resolve an astronomy object using fuzzy matching.

        Accepts either param name (object_id or search_term).

        Returns
        -------
        result : `StellarObject` or `None`
            The resolved stellar object, or `None` if not found.

        Raises
        ------
        InvalidArgumentError
            If neither ``object_id`` nor ``search_term`` is provided.
        """
        term = search_term or object_id
        if not term:
            raise InvalidArgumentError("Missing search term or object_id")
        return self.get_object_fuzzy(term)

    def analyze_periodicity(self, object_id: str) -> StellarObject | None:
        """Search a star's light curve for a repeating pattern, and save it.

        Parameters
        ----------
        object_id : `str`
            The id of the star to analyze.

        Returns
        -------
        result : `StellarObject` or `None`
            The star with any new analysis saved, or `None` if no such
            star exists.
        """
        with self._period_search_slots:
            return self.astrometrics.stars.analyze_periodicity(object_id)

    def add_object(self, new_object: StellarObject):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Add or update a stellar object."""
        self.astrometrics.stars.create(
            new_object.id,
            ra=getattr(new_object, "right_ascension", None),
            dec=getattr(new_object, "declination", None),
        )
        updates = new_object.serialize()
        self.astrometrics.stars.update(new_object.id, updates)

    def delete_object(self, object_id: str) -> bool:
        """Remove a stellar object from the database.

        Returns
        -------
        result : `bool`
            `True` if the object was deleted, `False` otherwise.
        """
        existing = self.astrometrics.stars.get(object_id)
        if existing is not None:
            archive_record_before_delete(
                self.config.get_library_path(), "stellar_object", existing.id, existing.serialize()
            )
        return self.astrometrics.stars.delete(object_id)

    def update_object(self, object_id: str, updates: dict) -> StellarObject | None:
        """Update stellar object properties.

        Returns
        -------
        result : `StellarObject` or `None`
            The updated stellar object, or `None` if not found.
        """
        return self.astrometrics.stars.update(object_id, updates)

    def get_spectroscopy_list(self) -> list[str]:
        """Get list of objects with processed spectrum data.

        Returns
        -------
        result : `list` of `str`
            IDs of objects with processed spectrum data.
        """
        return self.astrometrics.stars.query(has_spectra=True, detail="ids", limit=None).ids or []

    def find_or_create_by_position(
        self,
        ra: float,
        dec: float,
        name: str | None = None,
        spectral_type: str | None = None,
        magnitude: float | None = None,
        target_id: str | None = None,
        tolerance_arcsec: float = 5.0,
    ) -> StellarObject:
        """Find or create a StellarObject near (ra, dec).

        Delegates to the astrometrics library, which owns the catalog and
        looks only at the stars near that position.

        Returns
        -------
        result : `StellarObject`
            The matched or newly created stellar object.
        """
        return self.astrometrics.stars.find_or_create_by_position(
            ra,
            dec,
            name=name,
            spectral_type=spectral_type,
            magnitude=magnitude,
            target_id=target_id,
            tolerance_arcsec=tolerance_arcsec,
        )

    def get_audit(self) -> dict:
        """Return a statistical summary of the stellar library.

        Returns
        -------
        result : `dict`
            Statistical summary of the stellar library.
        """
        return self.astrometrics.stars.query(detail="stats").stats

    def get_sources(
        self,
        ra: float,
        dec: float,
        radius: float,
        include_catalog: bool = False,
        limiting_magnitude: float | None = None,
        include_stars_without_catalog_magnitude: bool = True,
    ) -> list[dict]:
        """List the library targets and stars to draw in a sky region.

        Parameters
        ----------
        ra : float
            Center Right Ascension in degrees.
        dec : float
            Center Declination in degrees.
        radius : float
            Viewport radius in degrees.
        include_catalog : bool
            Also add SIMBAD's objects that the library does not hold.
        limiting_magnitude : float, optional
            Faintest catalog magnitude worth returning. `None` disables the
            filter. Targets are never filtered.
        include_stars_without_catalog_magnitude : bool
            Whether to return stars that have no real catalog magnitude. The
            Planetarium passes `False` above the field of view at which it
            stops drawing them.

        Returns
        -------
        sources : `list` [`dict`]
            One `SkySource` per object, with the Planetarium's camelCase
            keys.
        """
        sources = self.wayfinder.planning.get_sources(
            ra,
            dec,
            radius,
            include=["stars", "online"] if include_catalog else ["stars"],
            limiting_magnitude=limiting_magnitude,
            include_stars_without_catalog_magnitude=include_stars_without_catalog_magnitude,
        )
        return [source.model_dump(by_alias=True) for source in sources]

    def get_planetarium_targets(self) -> list[dict]:
        """List every library target that has coordinates, for the sky map.

        A target with no stack shows its longest LIGHT frame instead.

        Returns
        -------
        targets : `list` [`dict`]
            One `SkySource` per target, with the Planetarium's camelCase
            keys.

        REQ: PLN-2.2
        """
        whole_sky_radius_deg = 180.0
        sources = self.wayfinder.planning.get_sources(0.0, 0.0, whole_sky_radius_deg, include=[])
        return [source.model_dump(by_alias=True) for source in sources]

    def get_online_catalog_sources(
        self,
        ra: float,
        dec: float,
        radius: float,
        enabled_drivers: list[str],
        limiting_magnitude: float | None = None,
    ) -> list[dict]:
        """List the stars the enabled online catalog drivers find in a region.

        Never reads or changes the library. Each source names the driver
        that found it in ``catalogSource``.

        Parameters
        ----------
        ra : float
            Center Right Ascension in degrees.
        dec : float
            Center Declination in degrees.
        radius : float
            Search radius in degrees.
        enabled_drivers : List[str]
            Registry keys of drivers to query, e.g. ['deep_stars'].
        limiting_magnitude : float, optional
            Faintest star magnitude the map can draw at the current zoom
            (the same value the UI sends to get_sources). Drivers that can
            use it fetch fewer stars.

        Returns
        -------
        sources : `list` [`dict`]
            One `SkySource` per star, with the Planetarium's camelCase keys.

        REQ: PLN-3.1, PLN-3.2
        """
        sources = self.wayfinder.planning.get_online_catalog_sources(
            ra_deg=ra,
            dec_deg=dec,
            radius_deg=radius,
            enabled_driver_names=enabled_drivers,
            magnitude_limit=limiting_magnitude,
        )
        return [source.model_dump(by_alias=True) for source in sources]

    def deep_catalog_status(self) -> dict:
        """Say how much of the downloaded deep-star catalog is installed.

        The Planetarium draws faint stars from a copy of Gaia DR3 saved on
        this computer. This tells it whether that copy is there, so it can
        prompt to download it when it is not.

        Returns
        -------
        dict
            ``installed``, ``complete``, ``star_count``, ``pixels_downloaded``,
            ``pixels_total``, ``healpix_level``, ``magnitude_limit`` and
            ``size_megabytes`` (from ``planning.deep_catalog_status``),
            plus ``installCommand``: the command that downloads it.
        """
        status = self.wayfinder.planning.deep_catalog_status(register_job=False).model_dump(
            mode="json", exclude={"estimate"}
        )
        status["installCommand"] = DEEP_CATALOG_INSTALL_COMMAND
        return status

    def list_catalog_drivers(self) -> list[dict]:
        """Return display metadata for all registered catalog drivers.

        Returns
        -------
        List[dict]
            One entry per driver with keys: driver_name, display_name,
            maximum_query_radius_degrees.

        REQ: PLN-3.1
        """
        return self.wayfinder.planning.list_catalog_driver_metadata()

    def get_constellation_lines(self) -> list[dict]:
        """Return all bundled constellation stick-figure line segments.

        Unlike get_sources()/get_online_catalog_sources(), this takes no
        ra/dec/radius — the full bundled dataset (a few hundred segments) is
        small enough to return in one shot and let the frontend project/cull
        it client-side, decoupled from whatever stars the user currently has
        toggled on screen.

        Returns
        -------
        result : `list` of `dict`
            Constellation stick-figure line segments.

        REQ: PLN-3.3
        """
        return self.wayfinder.planning.get_constellation_lines()

    def get_visibility(self, objects: list[dict], time: str | None = None) -> list[dict]:
        """Say where objects are in the sky, for the Planetarium's details.

        Parameters
        ----------
        objects : `list` [`dict`]
            Each with ``id`` and ``type``. A ``"star"`` is read from the
            library's stars; any other type is looked up by name in the
            library, then SIMBAD.
        time : `str`, optional
            ISO 8601 moment. Defaults to now.

        Returns
        -------
        visibility : `list` [`dict`]
            One `ObjectVisibility` per object found, with its meridian
            status. A star the library does not hold is left out.
        """
        resolved: list[Any] = []
        for object_entry in objects:
            if object_entry.get("type", "star") == "star":
                star = self.get_object(object_entry.get("id"))
                if star is not None:
                    resolved.append(star)
            else:
                resolved.append(object_entry.get("id"))
        if not resolved:
            return []
        report = self.wayfinder.planning.get_visibility(resolved, time=time, include=["meridian"])
        return [entry.model_dump(mode="json") for entry in report.objects]

    def get_target_status(self, target_id: str) -> dict:
        """Say where one target is in the sky now.

        Parameters
        ----------
        target_id : `str`
            The target's id or name, looked up in the library, then SIMBAD.

        Returns
        -------
        status : `dict`
            The target's `ObjectVisibility`.
        """
        report = self.wayfinder.planning.get_visibility([target_id])
        return report.objects[0].model_dump(mode="json")

    def list_visible_targets(self) -> list[dict]:
        """List the library targets above the horizon now, highest first.

        Returns
        -------
        targets : `list` [`dict`]
            One `ObjectVisibility` per target that is above the horizon.
        """
        report = self.wayfinder.planning.get_visibility(clear_only=True)
        return [entry.model_dump(mode="json") for entry in report.objects]
