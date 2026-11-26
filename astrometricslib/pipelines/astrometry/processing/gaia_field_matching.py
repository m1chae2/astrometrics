"""Names stars from the Gaia stars of the field, and keeps the run's state.

This is the lowest layer of `StarIdentifier` (see `star_identifier`). It
holds the state a run keeps, the projection of detected stars onto the sky,
and the Gaia part of the naming: the Gaia stars under the image are read,
moved from the catalog epoch (2016.0) to the time the image was taken
(`propagate_gaia_positions`), and matched to the stars SIMBAD did not name.
A star no catalog matched gets a name from its position.
"""

import logging
import math
import sqlite3
import warnings
from typing import Any

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time
from astropy.wcs import WCS
from erfa import ErfaWarning

from astrometricslib._internal.drivers.interfaces.plate_solve_driver import (
    PlateSolveDriver,
)
from astrometricslib._internal.drivers.interfaces.simbad_driver import SimbadDriver
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.astrometry.post_processing.assess_match_quality import assess_match_quality
from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector
from astrometricslib.pipelines.astrometry.processing.catalog_matching import (
    nearest_catalog_entries,
    pixels_to_sky,
)
from astrometricslib.pipelines.astrometry.processing.gaia_depth import (
    GaiaDepthPrediction,
    depth_exceeded,
)
from astrometricslib.pipelines.astrometry.processing.gaia_field_stars import (
    FieldGaiaStars,
    field_catalog_density,
    load_field_gaia_stars,
    tiles_under_image,
)
from astrometricslib.pipelines.astrometry.processing.gaia_tile_queries import (
    GAIA_DEFAULT_MAGNITUDE_LIMIT,
    make_gaia_tile_fetcher,
)
from astrometricslib.pipelines.astrometry.processing.star_detection import (
    estimate_faintest_detected_magnitude,
)
from astrometricslib.pipelines.astrometry.processing.wcs_refinement import (
    WcsRefinement,
)
from astrometricslib.pipelines.shared.catalog_star_identity import (
    CATALOG_MATCH_RADIUS_ARCSEC,
    gaia_dr3_source_id_from_names,
)
from astrometricslib.pipelines.shared.star_kinds import position_only_star_id
from astrometricslib.utilities.exceptions import DATA_ERRORS
from astrometricslib.utilities.gaia_record_ids import gaia_record_id
from astrometricslib.utilities.table_cells import optional_float

logger = logging.getLogger(__name__)


# Gaia DR3 positions are for this epoch (the year 2016.0, in Julian years).
# A star's position at the time of the image is its Gaia position moved along
# its proper motion for the years in between.
GAIA_DR3_REFERENCE_EPOCH = "J2016.0"


# Flags recorded on the quality summary (`astrometry_flags` of the
# astrometry metrics). Each is a stable key; tests and readers match on it.
FLAG_GAIA_TILES_MISSING = "gaia_tiles_missing"


FLAG_GAIA_DEPTH_EXCEEDED = "gaia_depth_exceeded"


FLAG_GAIA_PROPER_MOTION_UNKNOWN = "gaia_proper_motion_unknown"


FLAG_GAIA_EPOCH_UNKNOWN = "gaia_epoch_unknown"


def read_catalog_magnitude(match: Any, column_names: list[str]) -> float | None:
    """Read a star's brightness from a catalog row, if the catalog has one.

    A catalog often has no brightness for a star (the value is masked, or
    the column is missing). That case must stay `None`, meaning "not yet
    known" (see `StellarObject.magnitude`). Returning ``0.0`` instead
    would look like a real measurement, since magnitude 0 is about as
    bright as Vega, and it would also stop a real magnitude from being
    filled in later.

    Parameters
    ----------
    match : `astropy.table.Row`
        One row of a SIMBAD or Gaia result table.
    column_names : `list` [`str`]
        Names of the columns that can hold this catalog's magnitude, in
        the order to try them. Only the first one that exists is used.

    Returns
    -------
    magnitude : `float` or `None`
        The magnitude, or `None` if the catalog has no usable value.
    """
    for column_name in column_names:
        if column_name in match.colnames:
            return optional_float(match[column_name])
    return None


def _table_has_proper_motion(result_table: Any) -> bool:
    """Check whether any star of a Gaia table has a proper motion.

    Parameters
    ----------
    result_table : `astropy.table.Table`
        A Gaia table.

    Returns
    -------
    known : `bool`
        True if the table has ``pmra`` and ``pmdec`` columns and at least one
        row has a finite value in both. Some Gaia stars have none (their
        solution has no motion), so a few missing values are normal.
    """
    if "pmra" not in result_table.colnames or "pmdec" not in result_table.colnames:
        return False
    pm_ra = np.ma.filled(np.ma.asarray(result_table["pmra"], dtype=float), np.nan)
    pm_dec = np.ma.filled(np.ma.asarray(result_table["pmdec"], dtype=float), np.nan)
    return bool(np.any(np.isfinite(pm_ra) & np.isfinite(pm_dec)))


def propagate_gaia_positions(
    ra_deg: Any,
    dec_deg: Any,
    pmra_mas_per_year: Any,
    pmdec_mas_per_year: Any,
    observation_time: Time,
) -> tuple[np.ndarray, np.ndarray]:
    """Move Gaia DR3 star positions from epoch 2016.0 to the observation time.

    A star's position changes over the years because the star moves across the
    sky (its proper motion). Gaia reports each position for the year 2016.0.
    A fast star such as Barnard's Star has moved by several arcseconds by the
    time of an image taken years later, which is enough to miss a match.

    Parameters
    ----------
    ra_deg, dec_deg : array-like
        The Gaia positions at epoch 2016.0, in degrees.
    pmra_mas_per_year, pmdec_mas_per_year : array-like
        The proper motion in milliarcseconds per year. ``pmra`` is Gaia's
        ``pmra`` column, which already includes the cosine of the
        declination. A value that is not finite (Gaia has no motion for the
        star) leaves that star where it is.
    observation_time : `astropy.time.Time`
        When the image was taken.

    Returns
    -------
    ra_deg, dec_deg : `numpy.ndarray`
        The positions at `observation_time`, in degrees.
    """
    ra = np.atleast_1d(np.asarray(ra_deg, dtype=float))
    dec = np.atleast_1d(np.asarray(dec_deg, dtype=float))
    pm_ra = np.nan_to_num(np.atleast_1d(np.asarray(pmra_mas_per_year, dtype=float)), nan=0.0)
    pm_dec = np.nan_to_num(np.atleast_1d(np.asarray(pmdec_mas_per_year, dtype=float)), nan=0.0)
    catalog_coords = SkyCoord(
        ra=ra * u.deg,
        dec=dec * u.deg,
        pm_ra_cosdec=pm_ra * u.mas / u.yr,
        pm_dec=pm_dec * u.mas / u.yr,
        obstime=Time(GAIA_DR3_REFERENCE_EPOCH),
    )
    with warnings.catch_warnings():
        # Gaia gives no distance here. astropy then assumes one and says so.
        # The shift over a few years does not depend on it.
        warnings.simplefilter("ignore", ErfaWarning)
        moved = catalog_coords.apply_space_motion(new_obstime=observation_time)
    return np.asarray(moved.ra.deg, dtype=float), np.asarray(moved.dec.deg, dtype=float)


def read_observation_time(header: Any) -> Time | None:
    """Read when an image was taken from its ``DATE-OBS`` header card.

    Parameters
    ----------
    header : `astropy.io.fits.Header` or `None`
        The image header.

    Returns
    -------
    observation_time : `astropy.time.Time` or `None`
        The time in UTC, or `None` if the header is missing, has no
        ``DATE-OBS``, or the value cannot be read as a date.
    """
    if header is None:
        return None
    value = header.get("DATE-OBS")
    if not value:
        return None
    try:
        return Time(str(value), scale="utc")
    except ValueError:
        logger.warning("Could not read DATE-OBS %r as a date.", value)
        return None


class GaiaFieldMatching:
    """The Gaia matching layer of `StarIdentifier`, with the run's state.

    `StarIdentifier` builds on three layers, each a class in its own module:
    this one, `SimbadMatching` and `PlateSolutionSteps`. Each layer may use
    the methods of the layers below it. The annotations below list the state
    `StarIdentifier.__init__` sets, which every layer reads and writes.
    """

    config: AppConfiguration
    detector: SourceDetector
    solver: PlateSolveDriver
    simbad: SimbadDriver
    stellar_objects: list[StellarObject]
    sources_detected: int
    detected_unique_sources: list[dict]
    solve_attempted: bool
    frame_peak: float | None
    catalog_match_separations_arcsec: list[float]
    _nearest_catalog_by_star: dict[int, float]
    plate_solve_fit_residual_rms_arcsec: float | None
    plate_solve_matched_star_count: int | None
    observation_time: Time | None
    astrometry_flags: list[str]
    wcs_refinement: WcsRefinement | None
    wcs_refit_note: str
    gaia_fit_peak_over_noise: tuple[float, ...]
    gaia_fit_signal_reason: str
    _detected_on_binned_copy: bool
    gaia_depth_prediction: GaiaDepthPrediction | None
    field_gaia_stars: FieldGaiaStars | None
    field_catalog_density_per_square_degree: float | None

    def _add_flag(self, flag: str) -> None:
        """Record a limiting condition once for the current image.

        Parameters
        ----------
        flag : `str`
            One of the ``FLAG_*`` constants.
        """
        if flag not in self.astrometry_flags:
            self.astrometry_flags.append(flag)

    @property
    def nearest_catalog_separations_arcsec(self) -> list[float]:
        """Give each star's distance to its nearest catalog entry.

        One value for each star that had a sky position in the last
        `identify_stars_with_wcs` call: the distance to the nearest SIMBAD
        or Gaia entry, the smaller of the two when both were searched. A
        star matched in SIMBAD gives its SIMBAD distance. Distances beyond
        `CATALOG_MATCH_RADIUS_ARCSEC` are kept, so the plate solution check
        can compare how many neighbors fall inside the match radius with
        how many fall in a wider circle.

        Returns
        -------
        separations : `list` [`float`]
            The distances, in arcseconds.
        """
        return list(self._nearest_catalog_by_star.values())

    def _record_nearest_catalog_distance(
        self, stellar_object: StellarObject, separation_arcsec: float
    ) -> None:
        """Keep the smaller of a star's recorded and new catalog distances.

        Parameters
        ----------
        stellar_object : `StellarObject`
            The star.
        separation_arcsec : `float`
            The distance to the nearest entry of one catalog, in arcseconds.
        """
        key = id(stellar_object)
        recorded = self._nearest_catalog_by_star.get(key)
        if recorded is None or separation_arcsec < recorded:
            self._nearest_catalog_by_star[key] = separation_arcsec

    def _project_stars_to_sky(
        self,
        stellar_objects: list[StellarObject],
        wcs: WCS,
        ra_center: float,
        dec_center: float,
    ) -> tuple[dict[int, tuple[float, float]], float, float, float | None]:
        """Project every star's pixel position to sky coordinates.

        This allows us to ask SIMBAD/Gaia only for the specific area
        where our stars are, rather than the entire picture. If the
        stars are only in one corner of the image, this makes the
        download much smaller and faster, and prevents the internet
        connection from timing out.

        Returns
        -------
        sky_positions : `dict`
            Maps `id(stellar_object)` to its `(ra, dec)` in degrees,
            for every star that could be projected.
        ra_center, dec_center : `float`
            The query center: the star field's bounding-box midpoint
            if any star projected, otherwise `ra_center`/`dec_center`
            unchanged.
        query_radius_deg : `float` or `None`
            The radius that covers every projected star, or `None` if
            no star could be projected.
        """
        # `sky_positions` stores the RA/Dec for every star we successfully
        # located.
        sky_positions: dict[int, tuple[float, float]] = {}  # id(obj) -> (ra, dec)
        for stellar_object in stellar_objects:
            star = stellar_object.star_data
            x = star.get("x_centroid", star.get("xcentroid"))
            y = star.get("y_centroid", star.get("ycentroid"))
            if x is None or y is None:
                continue
            try:
                ra, dec = pixels_to_sky(wcs, x, y)
                sky_positions[id(stellar_object)] = (float(ra[0]), float(dec[0]))
            except DATA_ERRORS as e:
                logger.warning("Failed to project pixel (%s, %s) to sky: %s", x, y, e)

        query_radius_deg = None
        if sky_positions:
            star_coords = SkyCoord(
                ra=[p[0] for p in sky_positions.values()] * u.deg,
                dec=[p[1] for p in sky_positions.values()] * u.deg,
            )
            # Midpoint of the star field's bounding sky region, not the
            # WCS reference pixel -- the two can differ when detected
            # stars cluster off-center in the frame.
            bbox_center = SkyCoord(
                ra=(star_coords.ra.deg.min() + star_coords.ra.deg.max()) / 2.0 * u.deg,
                dec=(star_coords.dec.deg.min() + star_coords.dec.deg.max()) / 2.0 * u.deg,
            )
            # +15 arcsec buffer keeps the search radius comfortably above
            # the 10 arcsec nearest-neighbour match tolerance used below.
            # The radius reaches every detected star, also in the corners of
            # a field wider than 2 degrees across.
            query_radius_deg = float(bbox_center.separation(star_coords).max().deg) + (15 / 3600)
            ra_center, dec_center = bbox_center.ra.deg, bbox_center.dec.deg

        return sky_positions, ra_center, dec_center, query_radius_deg

    def _nearest_entries(
        self,
        stellar_objects: list[StellarObject],
        sky_positions: dict[int, tuple[float, float]],
        catalog: SkyCoord | None,
    ) -> dict[int, tuple[int, float]]:
        """Find each placed star's nearest catalog entry and keep its distance.

        The shared matching step (`catalog_matching.nearest_catalog_entries`)
        runs once for all the stars. Each distance is also kept for the plate
        solution check (see `nearest_catalog_separations_arcsec`).

        Parameters
        ----------
        stellar_objects : `list` [`StellarObject`]
            The stars to match.
        sky_positions : `dict`
            Maps `id(stellar_object)` to its `(ra, dec)` in degrees; stars
            without a position are skipped.
        catalog : `SkyCoord` or `None`
            The catalog's positions, or `None` when there is no catalog.

        Returns
        -------
        nearest : `dict` [`int`, `tuple` [`int`, `float`]]
            For each placed star, by `id(stellar_object)`, the catalog index
            of its nearest entry and the distance in arcseconds. Empty when
            there is no catalog.
        """
        placed = [star for star in stellar_objects if id(star) in sky_positions]
        if catalog is None or not placed:
            return {}
        coords = SkyCoord(
            ra=[sky_positions[id(star)][0] for star in placed] * u.deg,
            dec=[sky_positions[id(star)][1] for star in placed] * u.deg,
        )
        indices, separations = nearest_catalog_entries(coords, catalog)
        nearest: dict[int, tuple[int, float]] = {}
        for star, index, separation in zip(placed, indices, separations, strict=True):
            if math.isfinite(separation):
                self._record_nearest_catalog_distance(star, float(separation))
                nearest[id(star)] = (int(index), float(separation))
        return nearest

    def _match_stars_against_gaia(
        self,
        unmatched_after_simbad: list[StellarObject],
        sky_positions: dict[int, tuple[float, float]],
        wcs: WCS,
        width: int,
        height: int,
    ) -> list[StellarObject]:
        """Match stars SIMBAD couldn't identify against the field's Gaia tiles.

        The Gaia stars come from the complete tiles under the image (see
        `_prepare_field_gaia_stars`). Their positions are moved from epoch
        2016.0 to the time of the image before matching (see
        `_move_gaia_coords_to_observation_epoch`). A detection in a tile that
        is not complete is not matched and keeps a position-only name.

        Parameters
        ----------
        unmatched_after_simbad : `list` [`StellarObject`]
            The stars SIMBAD could not identify.
        sky_positions : `dict`
            Maps `id(stellar_object)` to its `(ra, dec)` in degrees.
        wcs : `astropy.wcs.WCS`
            The solved sky map.
        width, height : `int`
            The image size in pixels.

        Returns
        -------
        still_unmatched : `list` [`StellarObject`]
            The stars neither catalog could identify.
        """
        try:
            field = self._prepare_field_gaia_stars(wcs, width, height)
        except DATA_ERRORS:
            logger.exception("Could not list the Gaia tiles under the image; Gaia naming is skipped.")
            return unmatched_after_simbad
        gaia_table, gaia_coords = field.table, field.coords
        try:
            self.field_catalog_density_per_square_degree = field_catalog_density(field, wcs, width, height)
        except DATA_ERRORS:
            logger.debug("Could not count the Gaia stars over the image; the density stays unknown.")
        if gaia_table is not None and gaia_coords is not None:
            gaia_coords = self._move_gaia_coords_to_observation_epoch(gaia_table, gaia_coords)

        still_unmatched: list[StellarObject] = []
        match_radius_arcsec = resolve(CATALOG_MATCH_RADIUS_ARCSEC).value
        if gaia_table is not None and gaia_coords is not None:
            logger.info(
                "Matching %s stars against %s Gaia DR3 sources...",
                len(unmatched_after_simbad),
                len(gaia_coords),
            )
            nearest = self._nearest_entries(unmatched_after_simbad, sky_positions, gaia_coords)
            for stellar_object in unmatched_after_simbad:
                ra, dec = sky_positions.get(id(stellar_object), (None, None))
                if ra is None:
                    still_unmatched.append(stellar_object)
                    continue
                index, separation_arcsec = nearest.get(id(stellar_object), (0, math.inf))
                if separation_arcsec < match_radius_arcsec:
                    self._apply_gaia_match(stellar_object, gaia_table[index], ra, dec)
                    # Same reason as the SIMBAD match above: the residual
                    # RMS must cover every catalog match, not just
                    # whichever catalog happened to resolve a star first.
                    self.catalog_match_separations_arcsec.append(separation_arcsec)
                    # Gaia matching runs no unresolved-companion check
                    # today (see _is_unresolved_match/
                    # brightest_unresolved_entry_index, SIMBAD-only), so
                    # is_ambiguous is always False here, not unknown.
                    stellar_object.catalog_match_quality = assess_match_quality("gaia", separation_arcsec)
                    logger.info("  Gaia match: %s at (%.4f, %.4f)", stellar_object.name, ra, dec)
                else:
                    still_unmatched.append(stellar_object)
        else:
            still_unmatched = unmatched_after_simbad

        gaia_match_count = len(unmatched_after_simbad) - len(still_unmatched)
        logger.info(
            "Gaia matched %s additional stars; %s will receive position-based IDs.",
            gaia_match_count,
            len(still_unmatched),
        )

        return still_unmatched

    def _gaia_query_depth(self) -> float:
        """Give the Gaia G magnitude the current image's tiles are filled to.

        Returns
        -------
        depth : `float`
            The predicted query depth, or `GAIA_DEFAULT_MAGNITUDE_LIMIT` when
            there is no prediction.
        """
        if self.gaia_depth_prediction is not None:
            return float(self.gaia_depth_prediction.query_depth)
        return float(resolve(GAIA_DEFAULT_MAGNITUDE_LIMIT).value)

    def _prepare_field_gaia_stars(self, wcs: WCS, width: int, height: int) -> FieldGaiaStars:
        """Fill the Gaia tiles under an image and read the complete ones.

        The tiles are every tile under the image's four corners plus the
        catalog match radius. The missing ones are downloaded to the query
        depth (see `make_gaia_tile_fetcher`), unless this run may not go
        online. The answer is kept, so the refit and the naming of one image
        share one fill. When tiles stay missing, one warning names how many
        and why, and `FLAG_GAIA_TILES_MISSING` is recorded.

        Parameters
        ----------
        wcs : `astropy.wcs.WCS`
            The solved sky map.
        width, height : `int`
            The image size in pixels.

        Returns
        -------
        field : `FieldGaiaStars`
            The stars of the complete tiles and the tile lists.
        """
        tiles = tiles_under_image(wcs, width, height)
        depth = self._gaia_query_depth()
        kept = self.field_gaia_stars
        if kept is not None and kept.tiles == tiles and kept.depth == depth:
            return kept
        fetch, reason = make_gaia_tile_fetcher()
        try:
            field = load_field_gaia_stars(self.config, tiles, depth, fetch, skipped_reason=reason)
        except (sqlite3.Error, OSError) as cache_error:
            logger.warning("The Gaia cache could not be read or written: %s", cache_error)
            field = FieldGaiaStars(table=None, coords=None, depth=depth, tiles=tiles, missing_tiles=tiles)
        self.field_gaia_stars = field
        if field.missing_tiles:
            why = field.skipped_reason or (
                f"{len(field.failed_tiles)} failed to download" if field.failed_tiles else "not downloaded"
            )
            logger.warning(
                "%s of %s Gaia tiles under this image are not in the cache to G < %.1f (%s); detections in "
                "them get position-only names until a later run fills them.",
                len(field.missing_tiles),
                len(field.tiles),
                depth,
                why,
            )
            self._add_flag(FLAG_GAIA_TILES_MISSING)
        return field

    def _check_query_depth(self, stellar_objects: list[StellarObject]) -> None:
        """Warn when the image shows stars fainter than the Gaia query depth.

        Parameters
        ----------
        stellar_objects : `list` [`StellarObject`]
            Every detected star of the image, after the catalog matching.
        """
        faintest = estimate_faintest_detected_magnitude(stellar_objects)
        prediction = self.gaia_depth_prediction
        if prediction is None:
            prediction = GaiaDepthPrediction(
                query_depth=self._gaia_query_depth(),
                limiting_magnitude=None,
                is_fallback=True,
                reason="no image header",
            )
        if depth_exceeded(prediction, faintest):
            logger.warning(
                "The faintest detected star is about G %.1f, fainter than the Gaia query depth G %.1f, so "
                "the faintest stars cannot be named from Gaia.",
                faintest,
                prediction.query_depth,
            )
            self._add_flag(FLAG_GAIA_DEPTH_EXCEEDED)

    def _move_gaia_coords_to_observation_epoch(self, gaia_table: Any, gaia_coords: SkyCoord) -> SkyCoord:
        """Move Gaia star positions from epoch 2016.0 to the image's time.

        A star with a proper motion of 1 arcsecond per year sits 10
        arcseconds from its Gaia position ten years later. The match radius
        is also 10 arcseconds, so a fast star would otherwise go unmatched
        or be matched to a neighbour. The positions stay at epoch 2016.0,
        and a flag says so, when the table has no proper motions (an older
        cache) or the image has no ``DATE-OBS``.

        Parameters
        ----------
        gaia_table : `astropy.table.Table`
            The Gaia stars, with ``pmra`` and ``pmdec`` columns if known.
        gaia_coords : `astropy.coordinates.SkyCoord`
            The positions at epoch 2016.0, lined up with `gaia_table`.

        Returns
        -------
        coords : `astropy.coordinates.SkyCoord`
            The positions at the observation time, or `gaia_coords`
            unchanged when they cannot be moved.
        """
        if not _table_has_proper_motion(gaia_table):
            logger.warning(
                "The Gaia stars carry no proper motion (an older cache?), so their positions stay at "
                "epoch 2016.0."
            )
            self._add_flag(FLAG_GAIA_PROPER_MOTION_UNKNOWN)
            return gaia_coords
        if self.observation_time is None:
            logger.warning("The image has no usable DATE-OBS, so Gaia positions stay at epoch 2016.0.")
            self._add_flag(FLAG_GAIA_EPOCH_UNKNOWN)
            return gaia_coords
        try:
            ra_deg, dec_deg = propagate_gaia_positions(
                gaia_coords.ra.deg,
                gaia_coords.dec.deg,
                np.ma.filled(np.ma.asarray(gaia_table["pmra"], dtype=float), np.nan),
                np.ma.filled(np.ma.asarray(gaia_table["pmdec"], dtype=float), np.nan),
                self.observation_time,
            )
        except DATA_ERRORS:
            logger.exception("Could not move Gaia positions to the observation epoch.")
            return gaia_coords
        return SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg)

    def _assign_field_ids(
        self,
        still_unmatched: list[StellarObject],
        sky_positions: dict[int, tuple[float, float]],
    ) -> None:
        """Give a coordinate-based name to every star no catalog matched.

        Using the format FIELD_J{ra:.4f}{dec:+.4f} ensures that the
        same star will get the exact same name every time we run the
        program. It also makes it obvious that this isn't a famous
        star, so the user interface can easily hide them if desired.
        """
        for stellar_object in still_unmatched:
            ra, dec = sky_positions.get(id(stellar_object), (None, None))
            if ra is not None:
                field_id = position_only_star_id(ra, dec)
                stellar_object.id = field_id
                stellar_object.name = field_id
                stellar_object.right_ascension = ra
                stellar_object.declination = dec

    def _apply_gaia_match(self, stellar_object: StellarObject, match: Any, ra: float, dec: float) -> None:
        """Copy the star details from Gaia into our star object."""
        source_id = None
        for col in ["DESIGNATION", "designation", "source_id", "SOURCE_ID"]:
            if col in match.colnames:
                val = match[col]
                if val is not None and not (hasattr(val, "mask") and bool(val.mask)):
                    source_id = str(val).strip()
                    break

        if not source_id:
            source_id = gaia_record_id(f"J{ra:.4f}{dec:+.4f}")
        elif not source_id.startswith("Gaia"):
            source_id = gaia_record_id(source_id)

        # Stays None when Gaia has no G-band value for this star.
        magnitude = read_catalog_magnitude(match, ["phot_g_mean_mag", "PHOT_G_MEAN_MAG", "g_mag"])

        stellar_object.name = source_id
        stellar_object.id = source_id
        stellar_object.gaia_dr3_source_id = gaia_dr3_source_id_from_names([source_id])
        stellar_object.spectral_type = "Unknown"
        stellar_object.stellar_spectral_type = "Unknown"
        stellar_object.magnitude = magnitude
        stellar_object.right_ascension = float(ra)
        stellar_object.declination = float(dec)
        stellar_object.is_catalog_identified = True
        magnitude_text = f"{magnitude:.2f}" if magnitude is not None else "unknown"
        logger.info(
            "  Identified (Gaia): %s (mag: %s) at %.4f, %.4f", stellar_object.name, magnitude_text, ra, dec
        )
