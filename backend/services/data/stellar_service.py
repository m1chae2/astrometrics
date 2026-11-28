"""Purpose: Serve the app's star catalog, sky map sources and visibility.

Description: `StellarService` answers the Astronomy Manager's and the
Planetarium's requests by calling the libraries: `StellarCatalog.query`,
`count_by_target` and `count_by_class` for star listings, counts and
spectral classes, `Visualization.get_star_overlay` for the image overlay,
and `ObservationPlanning` for sky map sources and visibility. It adds only
what a server needs on top: caching of the slow whole-catalog answers, a
limit on how many period searches run at once, archiving a star before it
is deleted, the mapping from the app's request arguments to library
arguments, and the summary rows the app's lists read.
"""

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from astrometricslib import Astrometrics, StellarObject, Target
from astrometricslib.arguments import Page
from astrometricslib.drivers import (
    VersionedCache,
    is_path_inside,
    resolve_mounted_path,
)
from astrometricslib.exceptions import AstrometricsError, ConflictError, InvalidArgumentError, NotFoundError
from backend.services.data.deletion_archive import archive_record_before_delete
from backend.services.infrastructure.heavy_read_limiter import DEFAULT_SHARED_WAIT_SECONDS, HeavyReadLimiter

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
# library. They are kept until the star catalog's data version
# (`star_catalog_version`, the library's ``stars.get_data_version``) says a
# write has happened, not on a timer. The version comes from a counter in
# the database, so it sees a write by another program too. This is only the
# fallback: how long an answer may be served for a version already confirmed
# current, in case a write was made by raw SQL that did not count itself.
_CATALOG_ANSWER_CACHE_FALLBACK_MAX_AGE_SECONDS = 300.0

# How many overlay answers are remembered (see
# `get_astrometry_overlay_stars`).
_OVERLAY_CACHE_SIZE = 64

# Most stars one overlay answer places when the app gives a limit.
_OVERLAY_STAR_CAP = 200

# The app's ``filter_type`` values and the library filter each one sets.
_SPECTRA_FILTERS = ("with spectra", "spectra", "hasspectra")
_PHOTOMETRY_FILTERS = ("with photometry", "photometry", "hasphotometry")

# Most stars one Astronomy Manager page lists, also when it asks for all.
_LISTING_PAGE_CAP = 500


def star_catalog_version(astrometrics: Astrometrics) -> str | None:
    """Read the library's opaque version of the star catalog.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library.

    Returns
    -------
    version : `str` or `None`
        The text (``stars.get_data_version``), or `None` when the library
        cannot be read, so no answer is kept.
    """
    try:
        return astrometrics.stars.get_data_version()
    except AstrometricsError:
        logger.warning("Could not read the data version of the star catalog.", exc_info=True)
        return None


def library_version(astrometrics: Astrometrics) -> tuple[str, str, str] | None:
    """Read the library's opaque versions of its targets, quality and stars.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library.

    Returns
    -------
    version : `tuple` [`str`, `str`, `str`] or `None`
        The three texts, or `None` when the library cannot be read, so no
        answer is kept. A write of any of the three changes one of them.
    """
    try:
        return (
            astrometrics.targets.get_data_version(),
            astrometrics.quality.records.get_data_version(),
            astrometrics.stars.get_data_version(),
        )
    except AstrometricsError:
        logger.warning("Could not read the data versions of the library.", exc_info=True)
        return None


def _rounded(value: Any, digits: int) -> Any:
    """Round a number, leaving `None` and non-numbers alone.

    Returns
    -------
    value : `Any`
        The rounded number, or the value as it was.
    """
    if isinstance(value, int | float) and not isinstance(value, bool):
        return round(float(value), digits)
    return value


def _summary_row(star: StellarObject) -> dict[str, Any]:
    """Describe one star as the summary row the app's lists read.

    Parameters
    ----------
    star : `StellarObject`
        The star, with or without its light curve.

    Returns
    -------
    row : `dict` [`str`, `Any`]
        ``id``, ``name``, ``ra``, ``dec``, ``targetIds``, ``hasSpectra``,
        ``hasPhotometry``, ``magnitude``, ``hasCatalogMagnitude`` and
        ``spectralType``.
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
        "hasCatalogMagnitude": star.has_catalog_magnitude,
        "spectralType": star.spectral_type,
    }


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
        self._overlay_cache = VersionedCache(maximum_entries=_OVERLAY_CACHE_SIZE)
        self.astrometrics = astrometrics or Astrometrics(config)
        self._wayfinder = wayfinder
        self._period_search_slots = threading.BoundedSemaphore(_MAXIMUM_CONCURRENT_PERIOD_SEARCHES)
        self._catalog_answers: dict[str, tuple[float, int, Any]] = {}
        self._catalog_answers_lock = threading.Lock()
        # One lock per question, so two requests for the same slow answer
        # make one scan between them instead of racing two.
        self._catalog_compute_locks: dict[str, threading.Lock] = {}
        self._announced_catalog_version: int | None = None
        # Caps slow catalog reads so abandoned requests cannot use every
        # RPC worker thread, and shares identical concurrent ones.
        self._heavy_reads = HeavyReadLimiter()
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

        Freshness is checked against the library's star write counter
        rather than a timer, so nothing is recomputed while the catalog is
        unchanged. `_CATALOG_ANSWER_CACHE_FALLBACK_MAX_AGE_SECONDS` is only a
        backstop for a write that counter missed.

        Only one thread computes a given answer at a time; others asking for
        the same answer wait for it rather than starting a second scan.

        An empty answer (no targets, no classes) is never kept, so a catalog
        that was still empty at start-up cannot hide the data that arrives
        later.

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

        Raises
        ------
        ConflictError
            If a scan of the same answer is still running after the wait.
        """
        with self._catalog_answers_lock:
            compute_lock = self._catalog_compute_locks.setdefault(key, threading.Lock())

        # A request that arrives while the same answer is being computed
        # waits here, then finds the finished answer in the cache.
        # Wait for a running scan of the same answer only as long as a client
        # would; after that the request is refused so it frees its thread.
        if not compute_lock.acquire(timeout=DEFAULT_SHARED_WAIT_SECONDS):
            logger.info("Catalog answer %r refused: the scan is still running", key)
            raise ConflictError(
                "The star catalog is busy with other large reads. Try again in a moment.",
                details={"reason": "busy"},
                retryable=True,
            )
        try:
            current_version = star_catalog_version(self.astrometrics)
            now = time.monotonic()
            with self._catalog_answers_lock:
                cached = self._catalog_answers.get(key)
                if cached is not None and current_version is not None:
                    cached_at, cached_version, answer = cached
                    if (
                        cached_version == current_version
                        and now - cached_at < _CATALOG_ANSWER_CACHE_FALLBACK_MAX_AGE_SECONDS
                    ):
                        return answer

            answer = self._heavy_reads.run(("catalog_answer", key), compute)

            if not answer:
                # An empty answer is cheap to compute again and often wrong,
                # because the library may not be filled yet (start-up, or a
                # library copy swapped in). Keeping it would show "0 stars"
                # for every target until the cache expires. Not keeping it
                # also means the first real answer is announced.
                return answer

            with self._catalog_answers_lock:
                self._catalog_answers[key] = (now, current_version, answer)
                announce = self._announced_catalog_version != current_version
                self._announced_catalog_version = current_version
        finally:
            compute_lock.release()
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

    def _allowed_image_roots(self) -> list[str]:
        """List the folders an overlay image may be in.

        Returns
        -------
        roots : `list` [`str`]
            The real paths of the library folder, plus the frames and
            stacks folders when the configuration names them.
        """
        roots = [Path(self.config.get_library_path())]
        for getter in ("get_frames_path", "get_stacks_path"):
            method = getattr(self.config, getter, None)
            if callable(method):
                roots.append(Path(method()))
        return [os.path.realpath(root) for root in roots]

    def _checked_image_path(self, image_path: str) -> str:
        """Refuse an image path that is outside the library folders.

        Returns
        -------
        path : `str`
            The resolved path.

        Raises
        ------
        InvalidArgumentError
            If the path is not inside the library, frames or stacks folder.
        """
        # The same check the AI tools make (`_check_path_sandbox` in
        # mcp_servers/common/tool_registry.py), so both refuse the same paths.
        resolved = os.path.realpath(resolve_mounted_path(image_path))
        if not any(is_path_inside(root, resolved) for root in self._allowed_image_roots()):
            raise InvalidArgumentError("The image must be inside the library folders.")
        return resolved

    def get_astrometry_overlay_stars(
        self, target_id: str, limit: int = 35, image_path: str | None = None
    ) -> dict[str, Any]:
        """Place a target's catalog stars on an image, remembering answers.

        Working out the overlay for a target with tens of thousands of
        stars takes seconds. The answer only changes when the library
        data (see `library_version`) or the
        image changes, so it is kept in a `VersionedCache` (the 64 most
        recent) and reused until one of them does.

        Parameters
        ----------
        target_id : `str`
            Target identifier to retrieve overlay stars for.
        limit : `int`, optional
            Maximum number of stars to return (default 35). Zero or less
            returns every star the library places, up to its cap.
        image_path : `str`, optional
            The image the app is showing. The stars are placed on it. By
            default, the target's current stack. It must be inside the
            library, frames or stacks folder; otherwise the call is refused.

        Returns
        -------
        result : `dict`
            ``stars``: one `OverlayStar` per star, with the keys ``id``,
            ``name``, ``x``, ``y``, ``spectralType``, ``isCatalogIdentified``,
            ``referenceWidth``, ``referenceHeight`` and ``radiusPx``.
            ``reason``: why there are no stars when the image has no sky
            solution, else `None`.
        """
        if not target_id:
            return {"stars": [], "reason": None}
        checked_path = self._checked_image_path(image_path) if image_path else None
        try:
            target = self.astrometrics.targets.get(target_id)
            reference_path = checked_path or _overlay_reference_image_path(target)
            image_time = Path(reference_path).stat().st_mtime_ns if reference_path else 0
            key = (target_id, limit, str(reference_path), image_time)
        except AstrometricsError, OSError:
            return self._compute_overlay(target_id, limit, checked_path)

        # The version is the library's write counters, which see a write by
        # any program, so the answer is rebuilt after any change to the stars,
        # the targets or the quality rows.
        return self._overlay_cache.get_or_build(
            key,
            lambda: library_version(self.astrometrics),
            lambda: self._compute_overlay(target_id, limit, checked_path),
        )

    def _compute_overlay(self, target_id: str, limit: int, image_path: str | None = None) -> dict[str, Any]:
        """Ask the library to place a target's stars on an image.

        Returns
        -------
        result : `dict`
            ``stars``: the library's overlay stars, with the app's camelCase
            keys. ``reason``: why there are none, or `None`.
        """
        # The library reads the target again under each spelling of its id,
        # so a target record it does not hold still finds its stars.
        target = self.astrometrics.targets.get(target_id) or Target(id=target_id)
        # A limit of zero or less places every star; a positive limit is
        # cut to the most stars one overlay draws.
        cap = min(limit, _OVERLAY_STAR_CAP) if limit and limit > 0 else None
        overlay = self.astrometrics.visualization.get_star_overlay(
            target, image=image_path or None, limit=cap
        )
        return {
            "stars": [star.model_dump(by_alias=True) for star in overlay.stars],
            "reason": overlay.reason,
        }

    def _query_listing(
        self,
        target_id: str | None,
        search: str | None,
        filter_type: str | None,
        limit: int | None,
        offset: int,
    ) -> Any:
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
        limit : `int`
            Most stars to return.
        offset : `int`
            How many matching stars to skip.

        Returns
        -------
        page : `Page` [`StellarObject`]
            The library's answer, or an empty page when ``target_id`` names
            no target. A scoped, searched or filtered listing is sorted most
            useful first; a plain browse is in id order.
        """
        return self._heavy_reads.run(
            ("listing", target_id, search, filter_type, limit, offset),
            lambda: self._run_listing_query(target_id, search, filter_type, limit, offset),
        )

    def _run_listing_query(
        self,
        target_id: str | None,
        search: str | None,
        filter_type: str | None,
        limit: int | None,
        offset: int,
    ) -> Any:
        """Ask the library for a star listing, without any limiting.

        Parameters are those of `_query_listing`.

        Returns
        -------
        page : `Page` [`StellarObject`]
            The library's answer.
        """
        needle = search.strip() if search and search.strip() else None
        normalized_filter = filter_type.strip().lower() if filter_type else ""
        target = None
        if target_id:
            target = self.astrometrics.targets.get(target_id)
            if target is None:
                return Page[StellarObject](items=(), total=0)
        return self.astrometrics.stars.query(
            target=target,
            text=needle,
            has_spectra=True if normalized_filter in _SPECTRA_FILTERS else None,
            has_photometry=True if normalized_filter in _PHOTOMETRY_FILTERS else None,
            order_by="useful" if (target_id or needle or filter_type) else "id",
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
        return self._query_listing(target_id, search, filter_type, 1, 0).total

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
        page = self._query_listing(
            target_id,
            search,
            filter_type,
            min(limit, _LISTING_PAGE_CAP) if limit is not None and limit > 0 else _LISTING_PAGE_CAP,
            max(0, offset or 0),
        )
        return [_summary_row(star) for star in page]

    def warm_catalog_summary_cache(self) -> None:
        """Compute the slow whole-catalog answers now, so they are cached.

        Call this once during backend startup, so a user who opens the
        Astronomy Manager first does not wait for the per-target counts and
        the class counts there. It also looks up one id that no star has,
        which builds the library's map of spelling-free star ids, so the
        first Planetarium click on a star does not wait for it.
        """
        self.get_target_data_availability()
        self.get_spectral_class_summary()
        self.get_object("warm-up: no such star")

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
            counts = self.astrometrics.stars.count_by_target()
            return {
                entry.target_id: entry.model_dump(by_alias=True, exclude={"target_id"}) for entry in counts
            }

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
            "class_counts",
            lambda: [entry.model_dump(by_alias=True) for entry in self.astrometrics.stars.count_by_class()],
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
            lambda: [
                {
                    **_summary_row(star),
                    "selfDeterminedSpectralTypeRms": (
                        star.spectroscopy.self_determined_spectral_type_rms
                        if star.has_spectra and star.spectroscopy
                        else None
                    ),
                }
                for star in self.astrometrics.stars.query(spectral_class=spectral_class, order_by="match")
            ],
        )

    def get_class_star_colors(self) -> list[dict[str, Any]]:
        """List the stars in the spectral class lists, and their color facts.

        The Planetarium draws these stars in their own color when its left
        panel browses by spectral class: every star with a catalog spectral
        class (the stars the class lists count) and every star with a
        spectrum. A star's color comes from the spectral type our own
        spectrum gave, else the catalog type, else the catalog B-V color.
        The screen picks the color; this only gathers the facts.

        Returns
        -------
        stars : `list` [`dict`]
            One row per star, with the summary row keys (``id``, ``name``,
            ``ra``, ``dec``, ``magnitude``, ``hasCatalogMagnitude``,
            ``spectralType``, ``hasSpectra``, ``hasPhotometry`` and
            ``targetIds``) plus ``ownSpectralType`` (the type our spectrum
            matched, empty when none) and ``bMinusV`` (`None` when unknown;
            only read for stars with a spectrum).
        """

        def compute() -> list[dict[str, Any]]:
            """Gather the color facts of classed stars and stars with spectra.

            Returns
            -------
            stars : `list` [`dict`]
                One row per star, as described above.
            """
            stars = self.astrometrics.stars
            stars_by_id: dict[str, StellarObject] = {}
            for spectral_class in self.get_spectral_class_summary():
                for star in stars.query(spectral_class=spectral_class["spectralClass"]):
                    stars_by_id.setdefault(star.id, star)
            for star in stars.query(has_spectra=True):
                stars_by_id.setdefault(star.id, star)
            answer = []
            for star in stars_by_id.values():
                # Our own spectral type and the B-V color are given only for
                # the stars with a spectrum.
                spectrum = star.spectroscopy if star.has_spectra else None
                answer.append({
                    **_summary_row(star),
                    "ownSpectralType": (spectrum.self_determined_spectral_type or "") if spectrum else "",
                    "bMinusV": _rounded(star.b_minus_v, 2) if spectrum else None,
                })
            return answer

        return self._cached_catalog_answer("class_star_colors", compute)

    def get_stack_sky_pictures(self) -> list[dict[str, Any]]:
        """List each target's stack picture with where it lies on the sky.

        For the Planetarium's Images layer. A target is listed only when it
        has a stretched stack with a plate solution and a picture file; the
        rest are left out. The library reads each stack's header once and
        keeps the answer until the stack or its picture changes
        (`Visualization.get_sky_picture`).

        Returns
        -------
        pictures : `list` [`dict`]
            One entry per target with a placed picture: ``targetId``,
            ``picturePath``, ``width``, ``height``, ``gridSize`` and
            ``grid`` (``[ra, dec]`` pairs in degrees, row by row from the
            picture's top-left corner).
        """
        pictures = []
        for target in self.astrometrics.targets.query(include_empty=True).items:
            placement = self.astrometrics.visualization.get_sky_picture(target)
            if placement is not None:
                pictures.append({**placement.model_dump(by_alias=True), "targetId": target.id})
        return pictures

    def save_objects(self) -> str:
        """Report that the stellar catalog is saved; there is nothing to do.

        Every change to a star is written to the database at the moment it
        is made (see `StellarCatalog.update`, `create` and
        `delete`), so there is no unsaved list to write.
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

    def get_object(self, star_id: str) -> StellarObject | None:
        """Retrieve a stellar object by ID.

        Returns
        -------
        result : `StellarObject` or `None`
            The matching stellar object, or `None` if not found.
        """
        return self.astrometrics.stars.get(star_id, include=["light_curves"])

    def get_object_fuzzy(self, search_term: str) -> StellarObject | None:
        """Retrieve a stellar object by ID or name.

        Matching is case-insensitive and allows partial matches.

        Returns
        -------
        result : `StellarObject` or `None`
            The matching stellar object, or `None` if not found.
        """
        return self.astrometrics.stars.get(search_term, include=["light_curves"])

    def get_object_fuzzy_by_id(
        self, star_id: str | None = None, search_term: str | None = None
    ) -> StellarObject | None:
        """RPC wrapper to resolve an astronomy object using fuzzy matching.

        Accepts either param name (star_id or search_term).

        Returns
        -------
        result : `StellarObject` or `None`
            The resolved stellar object, or `None` if not found.

        Raises
        ------
        InvalidArgumentError
            If neither ``star_id`` nor ``search_term`` is provided.
        """
        term = search_term or star_id
        if not term:
            raise InvalidArgumentError("Missing search term or star_id")
        return self.get_object_fuzzy(term)

    def analyze_periodicity(self, star_id: str) -> StellarObject | None:
        """Search a star's light curve for a repeating pattern, and save it.

        Parameters
        ----------
        star_id : `str`
            The id of the star to analyze.

        Returns
        -------
        result : `StellarObject` or `None`
            The star with any new analysis saved, or `None` if no such
            star exists.
        """
        star = self.astrometrics.stars.get(star_id)
        if star is None:
            return None
        with self._period_search_slots:
            return self.astrometrics.stars.record_periodicity(star)

    def add_object(self, new_object: StellarObject) -> None:
        """Add a stellar object, or update the stored one with the same id."""
        # The position goes in with the other fields, by field name, so it is
        # stored as the record holds it (degrees or sexagesimal text).
        fields = {name: getattr(new_object, name) for name in type(new_object).model_fields if name != "id"}
        existing = self.astrometrics.stars.get(new_object.id)
        if existing is None:
            self.astrometrics.stars.create(new_object.id, **fields)
        else:
            self.astrometrics.stars.update(existing, **fields)

    def delete_object(self, star_id: str) -> bool:
        """Remove a stellar object from the database.

        Returns
        -------
        result : `bool`
            `True` if the object was deleted, `False` otherwise.
        """
        existing = self.astrometrics.stars.get(star_id, include=["light_curves"])
        if existing is None:
            return False
        archive_record_before_delete(
            self.config.get_library_path(), "stellar_object", existing.id, existing.serialize()
        )
        self.astrometrics.stars.delete(existing)
        return True

    def update_object(self, star_id: str, updates: dict) -> StellarObject | None:
        """Update stellar object properties.

        Returns
        -------
        result : `StellarObject` or `None`
            The updated stellar object, or `None` if not found.
        """
        existing = self.astrometrics.stars.get(star_id)
        if existing is None:
            return None
        return self.astrometrics.stars.update(existing, **updates)

    def get_spectroscopy_list(self) -> list[str]:
        """Get list of objects with processed spectrum data.

        Returns
        -------
        result : `list` of `str`
            IDs of objects with processed spectrum data.
        """
        return [star.id for star in self.astrometrics.stars.query(has_spectra=True)]

    def get_audit(self) -> dict:
        """Return a statistical summary of the stellar library.

        Returns
        -------
        result : `dict`
            Statistical summary of the stellar library.
        """
        statistics = self.astrometrics.stars.get_statistics()
        return {
            "total_objects": statistics.total_objects,
            "identified_objects": statistics.identified_objects,
            "spectral_coverage": statistics.spectral_coverage_pct,
            "photometric_coverage": statistics.photometric_coverage_pct,
            "stats": {
                "names": statistics.identified_objects,
                "spectral": statistics.with_spectral_type,
                "magnitude": statistics.with_magnitude,
            },
        }

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
        result = self.wayfinder.planning.catalogs.query_sources(
            ra,
            dec,
            radius,
            source=["targets", "stars", "simbad"] if include_catalog else ["targets", "stars"],
            magnitude_max=limiting_magnitude,
            include_stars_without_catalog_magnitude=include_stars_without_catalog_magnitude,
        )
        return [source.model_dump(by_alias=True) for source in result.sources]

    def get_planetarium_targets(self) -> list[dict]:
        """List every library target that has coordinates, for the sky map.

        A target with no stack shows its longest LIGHT frame instead. Each
        target also carries its catalog size (``majorAxisArcmin``, SIMBAD's
        major axis), when the library has one, so the map can draw a marker
        the size of the object.

        Returns
        -------
        targets : `list` [`dict`]
            One `SkySource` per target, with the Planetarium's camelCase
            keys.
        """
        whole_sky_radius_deg = 180.0
        result = self.wayfinder.planning.catalogs.query_sources(
            0.0, 0.0, whole_sky_radius_deg, source=["targets"]
        )
        return [source.model_dump(by_alias=True) for source in result.sources]

    def get_online_catalog_sources(
        self,
        ra: float,
        dec: float,
        radius: float,
        enabled_drivers: list[str],
        limiting_magnitude: float | None = None,
    ) -> dict[str, Any]:
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
        result : `dict`
            ``sources`` is one `SkySource` per star, with the Planetarium's
            camelCase keys. ``starsTruncated`` is true when a driver cut its
            answer short (the Gaia driver stops at 20,000 stars), so fainter
            stars in the region are missing and the map must ask again for a
            different view instead of reusing this answer. ``failedCatalogs``
            names the drivers that could not answer (empty when all did), so
            an empty ``sources`` list is not mistaken for empty sky.
        """
        result = self.wayfinder.planning.catalogs.query_sources(
            ra,
            dec,
            radius,
            source=["catalogs"],
            catalog_drivers=enabled_drivers,
            magnitude_max=limiting_magnitude,
        )
        return {
            "sources": [source.model_dump(by_alias=True) for source in result.sources],
            "starsTruncated": bool(result.stars_truncated),
            "failedCatalogs": list(result.failed_catalogs),
        }

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
            ``size_megabytes`` (from ``planning.catalogs.get_deep_status``),
            plus ``installCommand``: the command that downloads it.
        """
        status = self.wayfinder.planning.catalogs.get_deep_status().model_dump(
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
            maximum_query_radius_deg.
        """
        return [driver.model_dump() for driver in self.wayfinder.planning.catalogs.query()]

    def get_constellation_lines(self) -> list[dict]:
        """Return all bundled constellation stick-figure line segments.

        Unlike get_sources() and get_online_catalog_sources(), this takes no
        ra/dec/radius — the full bundled dataset (a few hundred segments) is
        small enough to return in one shot and let the frontend project/cull
        it client-side, decoupled from whatever stars the user currently has
        toggled on screen.

        Returns
        -------
        result : `list` of `dict`
            Constellation stick-figure line segments.
        """
        return [segment.model_dump() for segment in self.wayfinder.planning.sky.get_constellation_lines()]

    def _sky_object(self, name: str) -> Target | StellarObject:
        """Turn a name into the object the visibility read takes.

        The library looks among its targets, then its stars, then SIMBAD
        (``targets.resolve``). A name only SIMBAD knows becomes a point with
        that name at SIMBAD's position.

        Parameters
        ----------
        name : `str`
            A target id or common name, a star name, or a catalog name.

        Returns
        -------
        sky_object : `Target` or `StellarObject`
            The library target or star, or a point for a SIMBAD name.

        Raises
        ------
        NotFoundError
            If neither the library nor SIMBAD knows the name.
        """
        resolution = self.astrometrics.targets.resolve(name)
        if resolution is None:
            raise NotFoundError(f"Cannot find {name!r} in the library or SIMBAD.", details={"name": name})
        if resolution.target is not None:
            return resolution.target
        if resolution.star is not None:
            return resolution.star
        return StellarObject(id=name, name=resolution.name, ra=resolution.ra_deg, dec=resolution.dec_deg)

    def get_visibility(
        self,
        objects: list[dict],
        time: str | None = None,
        end: str | None = None,
        include: list[str] | None = None,
    ) -> list[dict]:
        """Say where objects are in the sky, for the Planetarium's details.

        Parameters
        ----------
        objects : `list` [`dict`]
            Each with ``id`` and ``type``, and optionally ``ra`` and
            ``dec`` in degrees. A ``"star"`` is read from the library's
            stars; a star the library does not hold (a background catalog
            star) is placed by its ``ra`` and ``dec`` when they are given.
            Any other type is looked up by name in the library, then
            SIMBAD.
        time : `str`, optional
            ISO 8601 moment. Defaults to now.
        end : `str`, optional
            ISO 8601 end of a span. With it the library reports over the
            whole span instead of one moment.
        include : `list` [`str`], optional
            Report sections to add. ``["meridian"]`` when left out.

        Returns
        -------
        visibility : `list` [`dict`]
            One `ObjectVisibility` per object found, with its meridian
            status. A star the library does not hold, sent without a
            position, is left out.
        """
        resolved: list[Any] = []
        for object_entry in objects:
            if object_entry.get("type", "star") == "star":
                star = self.get_object(object_entry.get("id"))
                if star is not None:
                    resolved.append(star)
                elif isinstance(object_entry.get("ra"), int | float) and isinstance(
                    object_entry.get("dec"), int | float
                ):
                    point_id = str(object_entry.get("id"))
                    resolved.append(
                        StellarObject(
                            id=point_id,
                            name=point_id,
                            ra=float(object_entry["ra"]),
                            dec=float(object_entry["dec"]),
                        )
                    )
            else:
                resolved.append(self._sky_object(str(object_entry.get("id"))))
        if not resolved:
            return []
        report = self.wayfinder.planning.sky.get_visibility(
            resolved, time=time, until=end, include=include if include is not None else ["meridian"]
        )
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
        report = self.wayfinder.planning.sky.get_visibility([self._sky_object(target_id)])
        return report.objects[0].model_dump(mode="json")

    def list_visible_targets(self) -> list[dict]:
        """List the library targets above the horizon now, highest first.

        Returns
        -------
        targets : `list` [`dict`]
            One `ObjectVisibility` per target that is above the horizon.
        """
        report = self.wayfinder.planning.sky.get_visibility(clear_only=True)
        return [entry.model_dump(mode="json") for entry in report.objects]
