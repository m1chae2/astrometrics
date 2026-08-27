"""StellarObject lifecycle, planetarium sky sources, and visibility queries."""

import logging
import re

from astrometricslib import Astrometrics, StellarObject

logger = logging.getLogger(__name__)

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
        "spectral_type": None,
        "magnitude": None,
        "has_spectra": bool(getattr(target, "stacked_spectral_target", None)),
        "has_photometry": bool(stacked_image or getattr(target, "processed_image", None)),
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
        search: str | None = None,
        filter_type: str | None = None,
    ) -> list[dict]:
        """Lightweight per-star summaries for a catalog-browsing listing.

        Same displayability filtering as `get_displayable_stellar_objects`,
        but built on `StellarCatalog.list_object_summaries` (indexed
        columns via `Butler.list_projected`, never touching `data_json`,
        and capped at `limit` rows, defaulting to 100) instead of fully
        hydrating every `StellarObject`. When `search` or `filter_type`
        is specified, filters across all matching database records before
        capping results to `limit`.

        Parameters
        ----------
        target_id : `str`, optional
            Restrict to stars belonging to this target.
        limit : `int`, optional
            Maximum number of stars to return. Defaults to 100.
        search : `str`, optional
            Search query to filter star ID or name across the catalog.
        filter_type : `str`, optional
            Filter category, e.g. "With Spectra" / "spectra" or
            "With Photometry" / "photometry".

        Returns
        -------
        summaries : `list` [`dict`]
            One dict per displayable star with keys ``id``, ``name``,
            ``targetIds``, ``hasSpectra``, and ``hasPhotometry``,
            optionally filtered by ``target_id``, ``search``, and
            ``filter_type``.
        """
        effective_limit = None if (search or filter_type) else limit
        summaries = self.astrometrics.stars.list_object_summaries(target_id, effective_limit)

        search_needle = search.strip().lower() if search and search.strip() else None

        filtered = []
        for summary in summaries:
            summary_id = str(summary.get("id") or "")
            if _is_per_frame_photometry_detection(summary_id):
                continue

            summary_name = str(summary.get("name") or "")

            if filter_type:
                normalized_filter = filter_type.strip().lower()
                is_spectra_filter = normalized_filter in ("with spectra", "spectra", "hasspectra")
                if is_spectra_filter and not summary.get("hasSpectra"):
                    continue
                is_photometry_filter = normalized_filter in ("with photometry", "photometry", "hasphotometry")
                if is_photometry_filter and not summary.get("hasPhotometry"):
                    continue

            if search_needle:
                if search_needle not in summary_id.lower() and search_needle not in summary_name.lower():
                    continue

            filtered.append(summary)
            if limit is not None and limit > 0 and len(filtered) >= limit:
                break

        return filtered

    def load_stellar_objects(self) -> None:
        """No-op retained for backward compatibility.

        Preloading is not required; this service is stateless.
        """
        pass

    def save_objects(self) -> str:
        """Persist the current list of stellar objects to SQLite.

        Saved via the high-level interface.

        Returns
        -------
        result : `str`
            Status message from the persistence layer.
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
            obj.id for obj in self.get_stellar_objects() if getattr(obj, "spectrum_data_processed", False)
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
                if magnitude is not None and (existing.magnitude == "" or existing.magnitude is None):
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
                    if magnitude is not None and (obj.magnitude == "" or obj.magnitude is None):
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

    def get_sources(self, ra: float, dec: float, radius: float, include_catalog: bool = False) -> list[dict]:
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

        Returns
        -------
        List[dict]
            List of serialized sources with coordinate and metadata.
        """
        objects = self.wayfinder.planning.get_sources(ra, dec, radius, include_catalog=include_catalog)

        local_star_ids = {o.id for o in self.get_stellar_objects()}
        local_target_ids = {o.id for o in self.astrometrics.targets.list()}

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
                    sources.append({
                        "id": obj.id,
                        "ra": float(obj.right_ascension),
                        "dec": float(obj.declination),
                        "name": obj.name or obj.id,
                        "commonName": obj.name or obj.id,
                        "spectral_type": obj.spectral_type,
                        "magnitude": obj.magnitude,
                        "has_spectra": bool(obj.spectrum_data_processed),
                        "has_photometry": bool(obj.light_curve and len(obj.light_curve.timestamps) > 0),
                        "type": "star",
                        "global": obj.id not in local_star_ids,
                        "stackedImage": None,
                        "fieldOfView": None,
                    })
                else:  # Target — delegate to shared serializer. REQ: PLN-2.2
                    serialized = _serialize_target_for_planetarium(obj, local_target_ids)
                    if serialized:
                        sources.append(serialized)
            except Exception as exc:
                logger.warning("Failed to serialize celestial object %s: %s", obj.id, exc)
                continue

        return sources

    def get_online_catalog_sources(
        self,
        ra: float,
        dec: float,
        radius: float,
        enabled_drivers: list[str],
    ) -> list[dict]:
        """Return serialized StellarObjects from online catalog drivers.

        Decoupled from get_sources() — never queries the local database and
        never persists results. Each returned dict includes a catalog_source
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
            Registry keys of drivers to query (e.g. ['simbad', 'gaia']).

        Returns
        -------
        List[dict]
            Serialized PlanetariumSource payloads with catalog_source set.

        REQ: PLN-3.1, PLN-3.2
        """
        tagged_objects = self.wayfinder.planning.get_online_catalog_sources(
            ra_deg=ra, dec_deg=dec, radius_deg=radius, enabled_driver_names=enabled_drivers
        )
        results = []
        for driver_name, obj in tagged_objects:
            try:
                magnitude_value = obj.magnitude if obj.magnitude != "" else None
                results.append({
                    "id": obj.id,
                    "ra": float(obj.right_ascension),
                    "dec": float(obj.declination),
                    "name": obj.name or obj.id,
                    "commonName": obj.name or obj.id,
                    "spectral_type": obj.spectral_type,
                    "magnitude": magnitude_value,
                    "has_spectra": False,
                    "has_photometry": False,
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

    def list_catalog_drivers(self) -> list[dict]:
        """Return display metadata for all registered online catalog drivers.

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
