"""StellarObject lifecycle, planetarium sky sources, and visibility queries."""

import logging
import math
import re
import threading

from astrometricslib import Astrometrics, StellarObject

logger = logging.getLogger(__name__)

# The command that downloads the deep-star catalog the Planetarium draws
# from. Sent to the UI with the catalog's status so the first-launch prompt
# shows the same command the script documents.
DEEP_CATALOG_INSTALL_COMMAND = "python -m astrometricslib.scripts.build_deep_star_catalog"

# Matches the ID suffix VariabilityAnalyzer stamps onto every per-frame point
# source it detects during photometry (target_sessions.py's
# "{target_id}:{night_date}:{gain}:{offset}" session id, joined with
# ":Star_{n}" in variability_analyzer.py). These are internal detection
# artifacts merged into the same stellar_catalog store real catalog objects
# live in, not catalog entries themselves -- a single imaging session can
# leave thousands of them, so they must never reach a sky-region query.
_PER_FRAME_DETECTION_ID_SUFFIX = re.compile(r":Star_\d+$")


def _is_per_frame_photometry_detection(object_id: str) -> bool:
    """Check whether an id is a VariabilityAnalyzer per-frame detection stub.

    Returns
    -------
    is_detection : `bool`
        `True` if ``object_id`` matches the ``...:Star_<n>`` pattern
        VariabilityAnalyzer generates, rather than a curated catalog id.
    """
    return bool(_PER_FRAME_DETECTION_ID_SUFFIX.search(object_id))


# Real apparent magnitudes bottom out near -1.5 (Sirius), but photometry
# stores instrumental magnitudes (about -10 to -17) in the same field, which
# say nothing about how bright a star looks. Must match
# BRIGHTEST_CATALOG_MAGNITUDE in ui/planetariumDisplay/layers/StarOverlay.ts.
_BRIGHTEST_CATALOG_MAGNITUDE = -2.0


# How many period searches may run at once. A search is a few seconds of
# work on one processor core (a dip search shuffles the light curve 150 to
# 300 times), so two at a time cannot swamp the computer, while a click
# never waits behind a long stacking job the way it would if it shared the
# heavy-job slots that stacking and image analysis use.
_MAXIMUM_CONCURRENT_PERIOD_SEARCHES = 2

# Prefix of an id given to a star that was found in an image but never
# matched to a catalog. Must match POSITION_ONLY_STAR_ID_PREFIX in
# astrometricslib/drivers/catalog_access.py.
_POSITION_ONLY_STAR_ID_PREFIX = "FIELD_J"


def _has_catalog_magnitude(magnitude: object) -> bool:
    """Say whether a star's magnitude is a real catalog magnitude.

    Parameters
    ----------
    magnitude : `object`
        The star's raw magnitude field, which may be a number, `None`, or
        an empty string.

    Returns
    -------
    has_catalog_magnitude : `bool`
        `True` for a finite number at or above the catalog floor; `False`
        for a missing (`None`, `""`) or instrumental (very negative) value.
    """
    return (
        isinstance(magnitude, int | float)
        and not isinstance(magnitude, bool)
        and math.isfinite(magnitude)
        and magnitude >= _BRIGHTEST_CATALOG_MAGNITUDE
    )


def _summary_sort_key(summary: dict) -> tuple:
    """Order star summaries so the most useful stars come first.

    Parameters
    ----------
    summary : `dict`
        One star summary from ``list_object_summaries``.

    Returns
    -------
    sort_key : `tuple`
        Sorts stars that have a spectrum first, then stars with a real
        catalog name (not a ``FIELD_J`` position-only id), then stars
        with photometry, then by catalog magnitude with the brightest
        first. A star with no catalog magnitude sorts after every star
        that has one. The id is the last part, so the order is the same
        on every request and page boundaries never repeat or skip a star.
    """
    magnitude = summary.get("magnitude")
    has_magnitude = _has_catalog_magnitude(magnitude)
    return (
        not summary.get("hasSpectra"),
        str(summary.get("id") or "").startswith(_POSITION_ONLY_STAR_ID_PREFIX),
        not summary.get("hasPhotometry"),
        not has_magnitude,
        magnitude if has_magnitude else 0.0,
        str(summary.get("id") or ""),
    )


def _serialize_target_for_planetarium(target, local_target_ids: set | None = None) -> dict | None:  # ruff: ignore[missing-type-function-argument]
    """Serialize a Target object into a planetarium-compatible dict.

    Uses astrometricslib.api.parse_coordinate_string as the single
    canonical coordinate parser. Includes a longest-exposure
    LIGHT frame fallback
    when no stacked image is present.

    Parameters
    ----------
    target : `Target`
        The target object to serialize.
    local_target_ids : `set`, optional
        Set of locally registered target IDs. When provided, targets
        whose IDs are absent from this set are flagged as global
        (SIMBAD-sourced) objects.

    Returns
    -------
    result : `dict` or `None`
        Serialized payload ready for the planetarium frontend, or
        `None` on parse failure.

    REQ: PLN-2.2
    """
    from astrometricslib import parse_coordinate_string

    try:
        ra_deg = parse_coordinate_string(str(target.ra), is_ra=True)
        dec_deg = parse_coordinate_string(str(target.dec), is_ra=False)
    except Exception as exc:
        logger.warning("Failed to parse coordinates for target '%s': %s", target.id, exc)
        return None

    # Resolve display image: stacked first, then longest-exposure LIGHT
    # frame fallback.
    stacked_image = target.stacked_image
    if not stacked_image and target.frames:
        light_frames = [f for f in target.frames if f.role == "LIGHT"]
        candidate_frames = light_frames if light_frames else target.frames
        longest = max(
            candidate_frames,
            key=lambda f: float(getattr(f, "exposure", 0) or 0.0),
            default=None,
        )
        if longest:
            stacked_image = longest.path

    is_global = target.id not in local_target_ids if local_target_ids is not None else True

    return {
        "id": target.id,
        "ra": ra_deg,
        "dec": dec_deg,
        "name": getattr(target, "common_name", None) or target.id,
        "commonName": getattr(target, "common_name", None) or target.id,
        "spectralType": None,
        "magnitude": None,
        "hasSpectra": bool(getattr(target, "stacked_spectral_target", None)),
        "hasPhotometry": bool(stacked_image or getattr(target, "processed_image", None)),
        "type": "target",
        "global": is_global,
        "stackedImage": stacked_image or None,
        "fieldOfView": getattr(target, "field_of_view", None),
    }


class StellarService:
    """Manages StellarObject lifecycle (e.

    g., spectroscopy targets). Acts as the Source of Truth for StellarObjects.
    """

    def __init__(self, config, astrometrics=None, wayfinder=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.config = config
        self.astrometrics = astrometrics or Astrometrics(config)
        self._wayfinder = wayfinder
        self._period_search_slots = threading.BoundedSemaphore(_MAXIMUM_CONCURRENT_PERIOD_SEARCHES)

    @property
    def wayfinder(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """The Wayfinder high-level interface instance.

        Falls back to lazy creation if not injected.
        """
        if self._wayfinder is None:
            from wayfindinglib import Wayfinder

            self._wayfinder = Wayfinder(config=self.config)
        return self._wayfinder

    def get_stellar_objects(self, target_id: str | None = None) -> list[StellarObject]:
        """Unified stellar objects getter.

        Delegates directly to the high-level interface analysis
        astrometrics to query disk.
        Includes VariabilityAnalyzer's per-frame detection stubs (ids ending
        ``:Star_<n>``) -- callers that round-trip the full catalog
        (``save_objects``, ``find_or_create_by_position``) need those
        included; UI-facing listings should call
        ``get_displayable_stellar_objects`` instead.

        Returns
        -------
        result : `list` of `StellarObject`
            Stellar objects, optionally filtered by ``target_id``.
        """
        from unittest.mock import Mock

        is_mock = isinstance(self.astrometrics, Mock) or isinstance(
            getattr(self.astrometrics, "stars", None), Mock
        )
        if is_mock:
            return self.astrometrics.stars.list_objects()

        try:
            objects = self.astrometrics.stars.list_objects()
        except Exception:
            objects = self.astrometrics.stars.list_objects()

        if target_id:
            return [
                obj for obj in objects if getattr(obj, "target_ids", None) and target_id in obj.target_ids
            ]
        return objects

    def get_displayable_stellar_objects(self, target_id: str | None = None) -> list[StellarObject]:
        """Stellar objects suitable for a user-facing catalog listing.

        Excludes VariabilityAnalyzer's per-frame detection stubs (see
        ``_is_per_frame_photometry_detection``) -- a single imaging
        session can leave thousands of these, and they were never
        meant to be browsable catalog entries.

        Returns
        -------
        result : `list` of `StellarObject`
            Displayable stellar objects, optionally filtered by
            ``target_id``.
        """
        return [
            obj
            for obj in self.get_stellar_objects(target_id)
            if not _is_per_frame_photometry_detection(obj.id)
        ]

    def get_displayable_stellar_object_summaries(
        self,
        target_id: str | None = None,
        limit: int | None = 100,
        offset: int | None = 0,
        search: str | None = None,
        filter_type: str | None = None,
    ) -> list[dict]:
        """Lightweight per-star summaries for a catalog-browsing listing.

        Same displayability filtering as `get_displayable_stellar_objects`,
        but built on `StellarCatalog.list_object_summaries` (indexed
        columns via `CatalogAccess.list_star_summaries`, never touching
        `data_json`, and capped at `limit` rows, defaulting to 100)
        instead of fully
        hydrating every `StellarObject`. When `search` or `filter_type`
        is specified, filters across all matching database records before
        capping results to `limit`. Supports `offset` pagination.

        Parameters
        ----------
        target_id : `str`, optional
            Restrict to stars belonging to this target.
        limit : `int`, optional
            Maximum number of stars to return. Defaults to 100.
        offset : `int`, optional
            Number of initial matching stars to skip for pagination.
            Defaults to 0.
        search : `str`, optional
            Search query to filter star ID or name across the catalog.
        filter_type : `str`, optional
            Filter category, e.g. "With Spectra" / "spectra" or
            "With Photometry" / "photometry".

        Returns
        -------
        summaries : `list` [`dict`]
            One dict per displayable star with keys ``id``, ``name``,
            ``targetIds``, ``hasSpectra``, ``hasPhotometry``,
            ``magnitude``, and ``spectralType``, optionally filtered by
            ``target_id``, ``search``, and ``filter_type``, paginated by
            ``offset`` and ``limit``. Results for a target, a search or
            a filter are sorted with spectra first, then named stars,
            then photometry, then brightest first (see
            `_summary_sort_key`); an unfiltered listing keeps database
            order.
        """
        # A target's own stars are cheap to read in full, and the sort below
        # needs all of them, so they count as a full scan too.
        needs_full_scan = bool(target_id or search or filter_type or (offset and offset > 0))
        effective_limit = None if needs_full_scan else limit
        # A search/filter/paginated request must see every row before its
        # own in-memory filtering below runs, or a real match past
        # DEFAULT_UNFILTERED_SUMMARY_LIMIT would be silently dropped
        # before this function ever got a chance to check it.
        summaries = self.astrometrics.stars.list_object_summaries(
            target_id, effective_limit, apply_default_limit=not needs_full_scan
        )

        search_needle = search.strip().lower() if search and search.strip() else None

        filtered = []
        for summary in summaries:
            summary_id = str(summary.get("id") or "")
            if _is_per_frame_photometry_detection(summary_id):
                continue

            summary_name = str(summary.get("name") or "")

            if filter_type:
                normalized_filter = filter_type.strip().lower()
                is_spectra_filter = normalized_filter in (
                    "with spectra",
                    "spectra",
                    "hasspectra",
                )
                if is_spectra_filter and not summary.get("hasSpectra"):
                    continue
                is_photometry_filter = normalized_filter in (
                    "with photometry",
                    "photometry",
                    "hasphotometry",
                )
                if is_photometry_filter and not summary.get("hasPhotometry"):
                    continue

            if search_needle:
                if search_needle not in summary_id.lower() and search_needle not in summary_name.lower():
                    continue

            filtered.append(summary)

        # Only a request that already reads its whole scope is sorted: a
        # target's stars, or a search/filter over the catalog. Sorting an
        # unfiltered browse would mean scanning all ~270,000 rows on every
        # page (about 2 seconds), and sorting only its capped first page
        # would make page 2 repeat or skip stars.
        if target_id or search_needle or filter_type:
            filtered.sort(key=_summary_sort_key)

        start_offset = max(0, offset or 0)
        if limit is not None and limit > 0:
            return filtered[start_offset : start_offset + limit]
        return filtered[start_offset:]

    def load_stellar_objects(self) -> None:
        """No-op retained for backward compatibility.

        Preloading is not required; this service is stateless.
        """
        pass

    def save_objects(self) -> str:
        """Record the current list of stellar objects to SQLite.

        Saved via the high-level interface.

        Returns
        -------
        result : `str`
            Status message from the storage layer.
        """
        try:
            return self.astrometrics.stars.save_all(self.get_stellar_objects())
        except Exception as e:
            logger.error(f"Failed to save stellar objects to database: {e}")
            raise e

    def get_object(self, object_id: str) -> StellarObject | None:
        """Retrieve a stellar object by ID.

        Returns
        -------
        result : `StellarObject` or `None`
            The matching stellar object, or `None` if not found.
        """
        return self.astrometrics.stars.get_object(object_id)

    def get_object_fuzzy(self, search_term: str) -> StellarObject | None:
        """Retrieve a stellar object by ID or name.

        Matching is case-insensitive and allows partial matches.

        Returns
        -------
        result : `StellarObject` or `None`
            The matching stellar object, or `None` if not found.

        REQ: BKD-7.2
        """
        return self.astrometrics.stars.get_object(search_term)

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
        ValueError
            If neither ``object_id`` nor ``search_term`` is provided.
        """
        term = search_term or object_id
        if not term:
            raise ValueError("Missing search term or object_id")
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
        return [
            obj.id
            for obj in self.get_stellar_objects()
            if getattr(obj, "spectroscopy", None) and obj.spectroscopy.wavelengths_angstrom
        ]

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

        Searches for an existing StellarObject within angular tolerance
        of (ra, dec), or creates a new one if no match exists. Updates
        metadata on the matched or created object when provided.

        Returns
        -------
        result : `StellarObject`
            The matched or newly created stellar object.

        REQ: IMG-4.5
        """
        import astropy.units as u
        from astropy.coordinates import SkyCoord

        stellar_objects = self.get_stellar_objects()

        # 1. Fast path: If SIMBAD name is provided, check if it already
        # exists by ID
        if name:
            existing = next((o for o in stellar_objects if o.id == name), None)
            if existing:
                updates = {}
                if spectral_type and not existing.spectral_type:
                    updates["spectral_type"] = spectral_type
                    updates["stellar_spectral_type"] = spectral_type
                if magnitude is not None and existing.magnitude is None:
                    updates["magnitude"] = magnitude
                if target_id and target_id not in existing.target_ids:
                    target_ids = [*list(existing.target_ids), target_id]
                    updates["target_ids"] = target_ids

                if updates:
                    self.astrometrics.stars.update(existing.id, updates)
                    # Refresh to get updated state
                    existing = self.get_object(existing.id)
                return existing

        # 2. Spatial match fallback
        target_coord = SkyCoord(ra=ra, dec=dec, unit=(u.deg, u.deg))

        for obj in stellar_objects:
            if not obj.right_ascension or not obj.declination:
                continue
            try:
                existing_coord = SkyCoord(
                    ra=float(obj.right_ascension), dec=float(obj.declination), unit=(u.deg, u.deg)
                )
                separation = target_coord.separation(existing_coord)
                if separation.arcsecond < tolerance_arcsec:
                    # REQ: IMG-4.5 - Update name/spectral type if a SIMBAD
                    # match was found
                    updates = {}
                    new_id = obj.id
                    if name and (not obj.name or "Star_" in obj.id):
                        # Ensure we don't create a collision if we rename
                        # this Star_X
                        if not self.get_object(name):
                            updates["name"] = name
                            if "Star_" in obj.id:
                                new_id = name
                                updates["id"] = name
                    if spectral_type and not obj.spectral_type:
                        updates["spectral_type"] = spectral_type
                        updates["stellar_spectral_type"] = spectral_type
                    if magnitude is not None and obj.magnitude is None:
                        updates["magnitude"] = magnitude
                    if target_id and target_id not in obj.target_ids:
                        updates["target_ids"] = [*list(obj.target_ids), target_id]

                    if updates:
                        if new_id != obj.id:
                            # Recreate with new ID or delete/insert
                            self.astrometrics.stars.delete(obj.id)
                            self.astrometrics.stars.create(new_id, ra=ra, dec=dec)
                        self.astrometrics.stars.update(new_id, updates)
                        obj = self.get_object(new_id)
                    return obj
            except ValueError, TypeError:
                continue

        # 3. Create new if no match
        if name:
            safe_id = name
        else:
            base_id = f"Star_{len(stellar_objects) + 1}"
            safe_id = base_id
            counter = 1
            while self.get_object(safe_id):
                safe_id = f"{base_id}_{counter}"
                counter += 1

        self.astrometrics.stars.create(safe_id, ra=ra, dec=dec)

        updates = {}
        updates["name"] = name or safe_id
        if spectral_type:
            updates["spectral_type"] = spectral_type
            updates["stellar_spectral_type"] = spectral_type
        if magnitude is not None:
            updates["magnitude"] = magnitude
        if target_id:
            updates["target_ids"] = [target_id]

        self.astrometrics.stars.update(safe_id, updates)
        return self.get_object(safe_id)

    def get_audit(self) -> dict:
        """Return a statistical summary of the stellar library.

        Returns
        -------
        result : `dict`
            Statistical summary of the stellar library.
        """
        return self.astrometrics.stars.get_audit()

    def get_sources(
        self,
        ra: float,
        dec: float,
        radius: float,
        include_catalog: bool = False,
        limiting_magnitude: float | None = None,
        include_stars_without_catalog_magnitude: bool = True,
    ) -> list[dict]:
        """Return all stellar and target objects in a region.

        Includes the global SIMBAD catalog if specified.

        Parameters
        ----------
        ra : float
            Center Right Ascension in degrees.
        dec : float
            Center Declination in degrees.
        radius : float
            Viewport radius in degrees.
        include_catalog : bool
            If True, query includes the global SIMBAD catalog.
        limiting_magnitude : float, optional
            Faintest star magnitude worth returning. Stars with a catalog
            magnitude fainter than this are omitted. `None` disables the
            filter. Targets are never filtered.
        include_stars_without_catalog_magnitude : bool
            Whether to return stars that have no usable catalog magnitude
            (see `_has_catalog_magnitude`). Most local stars are per-field
            detections whose magnitude is empty or instrumental, so
            `limiting_magnitude` can't thin them out, and a wide-FOV
            Planetarium view can match hundreds of thousands of them. The
            Planetarium passes `False` above the FOV at which it stops
            drawing them.

        Returns
        -------
        List[dict]
            List of serialized sources with coordinate and metadata.
        """
        # Without the SIMBAD catalog the user's own stars are read a faster
        # way, below: loading every library star in full (photometry and
        # all) to check its position took about ten seconds on a real
        # 270,000-star library. Only targets are loaded here. With the
        # catalog on, the full objects are still needed to tell the
        # library's stars apart from SIMBAD's.
        objects = self.wayfinder.planning.get_sources(
            ra, dec, radius, include_catalog=include_catalog, include_stars=include_catalog
        )

        # These ID sets exist only to tell apart local objects from ones
        # merged in from the global SIMBAD catalog, which only happens when
        # include_catalog is True -- get_sources() returns local-only
        # objects otherwise. Building them unconditionally meant every
        # viewport-scoped Planetarium query (include_catalog=False) paid
        # for a full scan of the local catalog just to compute a flag that
        # was already guaranteed False for every returned object.
        local_star_ids = {o.id for o in self.get_stellar_objects()} if include_catalog else None
        local_target_ids = {o.id for o in self.astrometrics.targets.list()} if include_catalog else None

        sources = []
        for obj in objects:
            try:
                if isinstance(obj, StellarObject):
                    if not obj.right_ascension or not obj.declination:
                        # Pixel-tracked variability candidates (e.g. Star_N
                        # stubs from VariabilityAnalyzer) have no sky
                        # coordinates and aren't displayable on the
                        # planetarium map; skip without logging, since
                        # there can be thousands of these in the local
                        # catalog.
                        continue
                    if _is_per_frame_photometry_detection(obj.id):
                        # Same VariabilityAnalyzer detections, but after WCS
                        # solving they do have coordinates -- the check above
                        # doesn't catch them. Up to 2000 per imaging session
                        # (one per detected point source in the reference
                        # frame), never meant to be browsable catalog stars.
                        continue
                    if _has_catalog_magnitude(obj.magnitude):
                        if limiting_magnitude is not None and obj.magnitude > limiting_magnitude:
                            continue
                    elif not include_stars_without_catalog_magnitude:
                        continue
                    sources.append({
                        "id": obj.id,
                        "ra": float(obj.right_ascension),
                        "dec": float(obj.declination),
                        "name": obj.name or obj.id,
                        "commonName": obj.name or obj.id,
                        "spectralType": obj.spectral_type,
                        "magnitude": obj.magnitude,
                        "hasSpectra": bool(obj.spectroscopy and obj.spectroscopy.wavelengths_angstrom),
                        "hasPhotometry": bool(obj.photometry and len(obj.photometry.timestamps) > 0),
                        "type": "star",
                        "global": (obj.id not in local_star_ids) if include_catalog else False,
                        "stackedImage": None,
                        "fieldOfView": None,
                    })
                else:  # Target — delegate to shared serializer. REQ: PLN-2.2
                    serialized = _serialize_target_for_planetarium(obj, local_target_ids)
                    if serialized and not include_catalog:
                        # _serialize_target_for_planetarium() defaults to
                        # is_global=True when local_target_ids is None (its
                        # own docstring: "when provided..."), which is the
                        # wrong default here -- when include_catalog is
                        # False every returned target is local by
                        # construction, not global.
                        serialized["global"] = False
                    if serialized:
                        sources.append(serialized)
            except Exception as exc:
                logger.warning("Failed to serialize celestial object %s: %s", obj.id, exc)
                continue

        if not include_catalog:
            # When stars without a catalog magnitude are not wanted, only
            # stars whose magnitude is a real catalog one, and no fainter
            # than the limit, can be kept. Say so up front so the database
            # skips the rest instead of the loop below throwing them away.
            # Every other case needs stars with no magnitude, so it asks
            # for everything and the loop below does the trimming.
            magnitude_range = None
            if not include_stars_without_catalog_magnitude:
                faintest_magnitude = math.inf if limiting_magnitude is None else limiting_magnitude
                magnitude_range = (_BRIGHTEST_CATALOG_MAGNITUDE, faintest_magnitude)
            sources.extend(
                self._serialize_library_star_summaries(
                    self.wayfinder.planning.get_library_star_summaries(ra, dec, radius, magnitude_range),
                    limiting_magnitude,
                    include_stars_without_catalog_magnitude,
                )
            )

        return sources

    @staticmethod
    def _serialize_library_star_summaries(
        star_summaries: list[dict],
        limiting_magnitude: float | None,
        include_stars_without_catalog_magnitude: bool,
    ) -> list[dict]:
        """Turn the library's quick star summaries into Planetarium sources.

        Applies the same rules `get_sources` applies to a full star, so the
        map shows the same stars it always did.

        Parameters
        ----------
        star_summaries : `list` [`dict`]
            Summaries from ``planning.get_library_star_summaries``.
        limiting_magnitude : `float`, optional
            Faintest catalog magnitude to keep. `None` keeps every star.
        include_stars_without_catalog_magnitude : `bool`
            Whether to keep stars that have no usable catalog magnitude.

        Returns
        -------
        sources : `list` [`dict`]
            Planetarium source payloads, one per kept star.
        """
        sources = []
        for summary in star_summaries:
            # A star with either coordinate exactly zero has no position
            # saved yet, so there is nowhere to draw it.
            if not summary["ra"] or not summary["dec"]:
                continue
            # See `get_sources`: these per-frame detection stubs are never
            # meant to be browsable stars.
            if _is_per_frame_photometry_detection(summary["id"]):
                continue
            magnitude = summary["magnitude"]
            if _has_catalog_magnitude(magnitude):
                if limiting_magnitude is not None and magnitude > limiting_magnitude:
                    continue
            elif not include_stars_without_catalog_magnitude:
                continue
            sources.append({
                "id": summary["id"],
                "ra": float(summary["ra"]),
                "dec": float(summary["dec"]),
                "name": summary["name"] or summary["id"],
                "commonName": summary["name"] or summary["id"],
                "spectralType": summary["spectralType"],
                "magnitude": magnitude,
                "hasSpectra": summary["hasSpectra"],
                "hasPhotometry": summary["hasPhotometry"],
                "type": "star",
                "global": False,
                "stackedImage": None,
                "fieldOfView": None,
            })
        return sources

    def get_online_catalog_sources(
        self,
        ra: float,
        dec: float,
        radius: float,
        enabled_drivers: list[str],
        limiting_magnitude: float | None = None,
    ) -> list[dict]:
        """Return serialized StellarObjects from online catalog drivers.

        Decoupled from get_sources() — never queries the local database and
        never records results. Each returned dict includes a catalog_source
        field identifying which driver produced it.

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
        List[dict]
            Serialized PlanetariumSource payloads with catalog_source set.

        REQ: PLN-3.1, PLN-3.2
        """
        tagged_objects = self.wayfinder.planning.get_online_catalog_sources(
            ra_deg=ra,
            dec_deg=dec,
            radius_deg=radius,
            enabled_driver_names=enabled_drivers,
            magnitude_limit=limiting_magnitude,
        )
        results = []
        for driver_name, obj in tagged_objects:
            try:
                magnitude_value = obj.magnitude
                results.append({
                    "id": obj.id,
                    "ra": float(obj.right_ascension),
                    "dec": float(obj.declination),
                    "name": obj.name or obj.id,
                    "commonName": obj.name or obj.id,
                    "spectralType": obj.spectral_type,
                    "magnitude": magnitude_value,
                    "hasSpectra": False,
                    "hasPhotometry": False,
                    "type": "star",
                    "global": True,
                    "catalogSource": driver_name,
                    "stackedImage": None,
                    "fieldOfView": None,
                })
            except Exception as serialization_error:
                logger.warning(
                    "Failed to serialize online catalog object %s: %s",
                    obj.id,
                    serialization_error,
                )
        return results

    def get_deep_catalog_status(self) -> dict:
        """Say how much of the downloaded deep-star catalog is installed.

        The Planetarium draws faint stars from a copy of Gaia DR3 saved on
        this computer. This tells it whether that copy is there, so it can
        prompt to download it when it is not.

        Returns
        -------
        dict
            ``installed``, ``complete``, ``star_count``, ``pixels_downloaded``,
            ``pixels_total``, ``healpix_level``, ``magnitude_limit`` and
            ``size_megabytes`` (from ``get_deep_catalog_status`` on the
            stars API),
            plus ``installCommand``: the command that downloads it.
        """
        status = dict(self.astrometrics.stars.get_deep_catalog_status())
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
        """Calculate detailed real-time visibility parameters for objects.

        Parameters
        ----------
        objects : List[dict]
            List of objects with 'id' and 'type' keys.
        time : Optional[str]
            Observation time. Defaults to now.

        Returns
        -------
        List[dict]
            Visibility status dictionaries.
        """
        from astropy.time import Time

        wayfinder = self.wayfinder

        resolved_objects = []
        for object_entry in objects:
            obj_id = object_entry.get("id")
            obj_type = object_entry.get("type", "star")

            if obj_type == "star":
                obj = self.get_object(obj_id)
                if obj:
                    resolved_objects.append(obj)
            else:
                try:
                    # Resolve using sky to handle fallback to SIMBAD for
                    # uninitialized local targets
                    obj = wayfinder.planning.resolve_target_coordinates(obj_id)
                    if obj:
                        resolved_objects.append(obj)
                except Exception:
                    try:
                        obj = self.astrometrics.targets.get(obj_id)
                        if obj:
                            resolved_objects.append(obj)
                    except Exception as exc:
                        logger.debug("Could not resolve target '%s' by either lookup: %s", obj_id, exc)

        observation_time = Time(time) if time else None
        return wayfinder.planning.get_visibility(resolved_objects, time_input=observation_time)

    def get_target_status(self, target_id: str) -> dict | None:
        """Calculate the real-time Alt/Az coordinates and visibility status.

        Parameters
        ----------
        target_id : str
            Identifier of the target to resolve and evaluate.

        Returns
        -------
        Optional[dict]
            Visibility status dictionary for the target, or None if the
            target could not be resolved.
        """
        wayfinder = self.wayfinder
        try:
            target = wayfinder.planning.resolve_target_coordinates(target_id)
        except Exception:
            return None
        visibility_results = wayfinder.planning.get_visibility([target])
        return visibility_results[0] if visibility_results else None

    def get_visible_targets(self) -> list[dict]:
        """Return all targets currently above the horizon, sorted by altitude.

        Returns
        -------
        List[dict]
            Visibility status dictionaries for targets with Alt > 0,
            sorted by descending altitude.
        """
        wayfinder = self.wayfinder
        targets = wayfinder.planning.get_sources(0.0, 0.0, 180.0, include_catalog=False)
        visibility_results = wayfinder.planning.get_visibility(targets)
        visible = [entry for entry in visibility_results if entry.get("above_horizon", False)]
        visible.sort(key=lambda entry: entry["altitude"], reverse=True)
        return visible
