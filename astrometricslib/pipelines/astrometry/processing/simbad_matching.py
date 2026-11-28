"""Names stars from SIMBAD and finds the star the frame is aimed at.

This layer of `StarIdentifier` (see `star_identifier`) asks SIMBAD for the
stars in the image area in one region query, keeps the single stars, and
matches each detected star to its nearest entry. A star close to a brighter
entry it cannot be told apart from takes that entry
(`brightest_unresolved_entry_index`). `identify_stars_with_wcs` runs the
whole naming: SIMBAD first, then Gaia, then a name from the position. For a
frame aimed at a named star, the star at the frame center is found and named
after the target.
"""

import logging
import math
from typing import Any

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales

from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import factor_for
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    RunBasis,
    TunedParameter,
)
from astrometricslib.pipelines.astrometry.post_processing.assess_match_quality import assess_match_quality
from astrometricslib.pipelines.astrometry.processing.extended_catalog_entry import (
    is_extended_catalog_entry,
    is_extended_object_type,
)
from astrometricslib.pipelines.astrometry.processing.gaia_field_matching import (
    GaiaFieldMatching,
    read_catalog_magnitude,
)
from astrometricslib.pipelines.astrometry.processing.gaia_match import record_plate_scale
from astrometricslib.pipelines.shared.catalog_star_identity import (
    CATALOG_MATCH_RADIUS_ARCSEC,
    name_simbad_star,
)
from astrometricslib.pipelines.shared.simbad_object_types import (
    STAR_REGION_VOTABLE_FIELDS,
    read_simbad_object_types,
    simbad_row_identifiers,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS
from astrometricslib.utilities.solar_system_targets import is_solar_system_target

logger = logging.getLogger(__name__)


# A centre star matched to a catalog star farther than this from the position
# hint is reported as a probable mislabel. Only a warning, nothing is changed
# by it. Unvalidated: 30 arcsec is about 16 pixels at this rig's 1.92 arcsec
# per pixel. The correct Vega match is 25 arcsec from the solved stack's
# centre, and the wrong one (TYC 3105-827-1) was 145 arcsec from the mount's
# reported position, so 30 lies between the two cases seen.
HINT_MATCH_WARNING_ARCSEC = TunedParameter(
    name="astrometry.hint_match_warning_arcsec",
    default=30.0,
    unit="arcsec",
    description=(
        "Distance from the position hint beyond which a centre-star match is reported as a probable mislabel."
    ),
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="AST-D14",
    allowed_range=(5.0, 300.0),
    # A sample is one target (spectral stack plus solved stack).
    minimum_sample=5,
)


# How far from the position hint the star at the frame centre may really be,
# when the star is picked by name or by brightness instead of by distance. The
# hint is the mount's report, so it is only as good as the mount's pointing.
# Measured on the 2026-10-04 spectral stacks: Aldebaran 24, Sirius 24, Mars
# 22 and Alhena 138 arcseconds. The wrong Vega match in an earlier session
# was 145 arcseconds from the mount's position. 300 arcseconds (5 arcminutes)
# covers every case seen with about twice the largest error, and stays well
# inside the 12 arcminute SIMBAD region searched on the hint path.
POINTING_ERROR_SEARCH_RADIUS_ARCSEC = TunedParameter(
    name="astrometry.pointing_error_search_radius_arcsec",
    default=300.0,
    unit="arcsec",
    description=(
        "How far from the position hint the centre star "
        "may really be, allowing for the mount's pointing error."
    ),
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="AST-09",
    allowed_range=(60.0, 700.0),
    # A sample is one target (spectral stack plus solved stack).
    minimum_sample=5,
)


# When the centre star is named by the target's name or by brightness, the
# name goes to the brightest detection within this fraction of the frame's
# shorter side from the centre, not to whatever is nearest the centre. The
# mount's error (5 arcminutes, about 157 pixels at 1.9 arcseconds per pixel,
# so 5 percent of a 3008 pixel frame) plus a re-centring or drift sets the
# size: on 2026-10-04 the Sirius zero order sat 151 pixels from the centre.
# 10 percent (about 300 pixels) covers that with margin.
CENTRE_STAR_SEARCH_RADIUS_FRACTION = TunedParameter(
    name="astrometry.centre_star_search_radius_fraction",
    default=0.1,
    unit="× shorter frame side",
    description=(
        "How far from the frame centre, as a fraction "
        "of the shorter side, the target's detection is looked for."
    ),
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="AST-09",
    allowed_range=(0.02, 0.4),
    # A sample is one target (spectral stack plus solved stack).
    minimum_sample=5,
)


# The detection that gets the target's name must reach at least this fraction
# of the frame's brightest pixel. The target of a spectral frame makes the
# brightest compact source in it, because the exposure is chosen to fill the
# zero order. Measured on the 2026-10-04 stacks: the zero orders reached 0.64
# to 1.0 of the brightest pixel; the noise detections near the centre of the
# Sirius stack (whose zero order was not detected) reached 0.00003. 10 percent
# lies between the two cases.
CENTRE_STAR_MINIMUM_PEAK_FRACTION = TunedParameter(
    name="astrometry.centre_star_minimum_peak_fraction",
    default=0.1,
    unit="× brightest pixel",
    description=(
        "Smallest peak, as a fraction of the frame's brightest "
        "pixel, for a detection to be named as the target."
    ),
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="AST-09",
)


# SIMBAD columns that may hold a star's V magnitude, tried in order.
_SIMBAD_V_MAGNITUDE_COLUMNS = ["V", "FLUX_V", "flux_v", "flux(V)"]


# Catalog entries closer together than this cannot be told apart in one of our
# frames, so the light comes from all of them and the brightest dominates. The
# scale is 1.9 arcseconds per pixel and a star's blur spans a few pixels; 3
# arcseconds is about one and a half pixels. Albireo's two stars (beta1 Cyg A,
# a K3II star, and its B9.5V companion, 0.4 arcseconds apart) were the case
# that showed the problem: the spectrum was K-type, but the star was named
# after the companion because it happened to be nearer by a fraction of a
# pixel. A judgement call, checked on that one pair.
# Stands for the sky distance within which a catalog neighbor blends with a
# star. It follows the star width: the value is a factor times the star
# width the run measured (3.0 on the reference setup).
UNRESOLVED_COMPANION_RADIUS_ARCSEC = TunedParameter(
    name="astrometry.unresolved_companion_radius_arcsec",
    default=factor_for(3.0, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="Catalog entries closer together than this cannot be told apart in a frame.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=3.0,
    equipment=frozenset({EquipmentKind.TELESCOPE}),
    register_entry="AST-10",
)


def brightest_unresolved_entry_index(
    star_coord: SkyCoord, nearest_index: int, simbad_coords: SkyCoord, result_table: Any
) -> int:
    """Pick the brightest catalog entry that a star cannot be told from.

    Parameters
    ----------
    star_coord : `astropy.coordinates.SkyCoord`
        Where the detected star is on the sky.
    nearest_index : `int`
        The index of the nearest catalog entry.
    simbad_coords : `astropy.coordinates.SkyCoord`
        The positions of every catalog entry, lined up with `result_table`.
    result_table : `astropy.table.Table`
        The catalog rows.

    Returns
    -------
    index : `int`
        The index of the brightest entry (lowest V magnitude) within
        `UNRESOLVED_COMPANION_RADIUS_ARCSEC` of the star, or
        `nearest_index` when there is only one, or none has a magnitude.
        An entry with no magnitude is treated as the faintest.
    """
    separations = star_coord.separation(simbad_coords).arcsec
    candidates = np.flatnonzero(separations <= resolve(UNRESOLVED_COMPANION_RADIUS_ARCSEC).value)
    if candidates.size < 2:
        return nearest_index
    magnitudes = [
        read_catalog_magnitude(result_table[int(index)], ["V", "FLUX_V", "flux_v", "flux(V)"])
        for index in candidates
    ]
    if all(magnitude is None for magnitude in magnitudes):
        return nearest_index
    brightest = min(
        range(len(candidates)),
        key=lambda position: math.inf if magnitudes[position] is None else magnitudes[position],
    )
    return int(candidates[brightest])


def _is_unresolved_match(star_coord: SkyCoord, simbad_coords: SkyCoord) -> bool:
    """Check whether a star's SIMBAD match was ambiguous.

    Shares `UNRESOLVED_COMPANION_RADIUS_ARCSEC` with
    `brightest_unresolved_entry_index`, which is the function that
    actually resolves the ambiguity by picking the brightest candidate;
    this only reports whether that resolution happened, so a caller can
    record it on `CatalogMatchQuality.is_ambiguous`.

    Returns
    -------
    is_ambiguous : `bool`
        True if two or more catalog entries sat within
        `UNRESOLVED_COMPANION_RADIUS_ARCSEC` of the star.
    """
    separations = star_coord.separation(simbad_coords).arcsec
    return int(np.count_nonzero(separations <= resolve(UNRESOLVED_COMPANION_RADIUS_ARCSEC).value)) >= 2


class SimbadMatching(GaiaFieldMatching):
    """The SIMBAD matching layer of `StarIdentifier`."""

    @staticmethod
    def _names_an_extended_entry(result_table: Any, target_name: str | None) -> bool:
        """Tell whether the target's name belongs to an extended SIMBAD entry.

        Parameters
        ----------
        result_table : `astropy.table.Table`
            The SIMBAD rows, before the star filter.
        target_name : `str` or `None`
            The target's name, such as ``"M 57"``.

        Returns
        -------
        names_extended_entry : `bool`
            True when an extended row lists the name among its identifiers.
            Spaces and case are ignored.
        """
        if not target_name:
            return False

        def squash(text: object) -> str:
            """Remove spaces and capitals so ``M  57`` equals ``m 57``.

            Returns
            -------
            squashed : `str`
                The text without spaces, in upper case.
            """
            return str(text).replace(" ", "").replace("_", "").upper()

        wanted = squash(target_name)
        id_columns = [name for name in ("main_id", "ids") if name in result_table.colnames]
        for row in range(len(result_table)):
            if not is_extended_catalog_entry(result_table, row):
                continue
            for column in id_columns:
                if wanted in {squash(part) for part in str(result_table[column][row]).split("|")}:
                    return True
        return False

    def _query_simbad_region(
        self,
        ra_center: float,
        dec_center: float,
        wcs: WCS | None,
        width: int,
        height: int,
        radius_deg_override: float | None = None,
        target_name: str | None = None,
    ) -> tuple[Any, SkyCoord] | tuple[None, None]:
        """Download a chunk of the SIMBAD database for our specific image area.

        We filter out galaxies and nebulae because our algorithm only
        cares about stars. When `target_name` names one of the dropped
        extended entries, the target is extended and an empty result is
        expected, so it is logged as information, not as a warning.

        Returns
        -------
        result_table, simbad_coords : `astropy.table.Table`, `SkyCoord`
            The list of known stars and their coordinates, or None if the
            download failed.
        """
        # Calculate a reasonable radius based on image size (if
        # WCS is available)
        radius_deg = 0.2  # default 12 arcmin
        if radius_deg_override is not None:
            radius_deg = radius_deg_override
        elif wcs and width and height:
            try:
                pixel_scales = proj_plane_pixel_scales(wcs)  # in degrees
                fov_x = pixel_scales[0] * width
                fov_y = pixel_scales[1] * height
                radius_deg = max(fov_x, fov_y) / 2.0 * 1.1  # 10% buffer
            except DATA_ERRORS as e:
                logger.warning("Failed to calculate FOV from WCS: %s", e)

        logger.info(
            "Querying SIMBAD bulk region at %.4f, %.4f with %.3f degree radius...",
            ra_center,
            dec_center,
            radius_deg,
        )
        try:
            coord = SkyCoord(ra_center * u.deg, dec_center * u.deg)
            result_table = self.simbad.query_region(
                coord,
                radius=f"{radius_deg}d",
                # "ids" carries the common names used to label a star.
                # "alltypes" lists every object type, not just the main one:
                # the main type of the eclipsing binary Algol is "SB*".
                votable_fields=STAR_REGION_VOTABLE_FIELDS,
                row_limit=5000,  # Prevent massive result sets
            )
        except (ExternalServiceError, ValueError) as simbad_error:
            logger.warning("%s", simbad_error)
            return None, None

        if result_table is None or len(result_table) == 0:
            logger.info("No SIMBAD results for this region.")
            return None, None

        logger.info("  Found %s potential matches in SIMBAD.", len(result_table))

        # Exclude non-stellar SIMBAD entries (galaxies, nebulae,
        # clusters, etc). A detected point source can never
        # legitimately be a galaxy, no matter how close the catalog
        # position is, so these must be dropped before any nearest-
        # neighbor matching is done below - otherwise a coincidentally
        # nearby (or arbitrarily first-listed) extended object can be
        # mistaken for the star.
        target_is_extended = self._names_an_extended_entry(result_table, target_name)
        result_table = self._filter_stellar_rows(result_table)
        if result_table is None or len(result_table) == 0:
            message = (
                "No stellar-type SIMBAD entries found in field (only non-stellar catalog objects, "
                "e.g. galaxies, were nearby); leaving detected star(s) with generic 'Star N' labels."
            )
            # An extended target (a nebula, cluster or galaxy) has no star of
            # its own to name, so this is expected, not a problem.
            (logger.info if target_is_extended else logger.warning)(message)
            return None, None

        logger.info("SIMBAD table columns: %s", result_table.colnames)
        logger.info("Creating SkyCoord for SIMBAD matches...")
        try:
            # Use actual column names from SIMBAD response
            ra_col = next((c for c in ["ra", "RA_d", "RA"] if c in result_table.colnames), "ra")
            dec_col = next((c for c in ["dec", "DEC_d", "DEC"] if c in result_table.colnames), "dec")

            # Check if columns already have units
            ra_vals = result_table[ra_col]
            dec_vals = result_table[dec_col]

            if not hasattr(ra_vals, "unit") or ra_vals.unit is None:
                ra_vals = ra_vals * u.deg
            if not hasattr(dec_vals, "unit") or dec_vals.unit is None:
                dec_vals = dec_vals * u.deg

            simbad_coords = SkyCoord(ra=ra_vals, dec=dec_vals)
        except DATA_ERRORS:
            logger.exception("SkyCoord creation failed")
            return None, None

        return result_table, simbad_coords

    def identify_stars_with_wcs(
        self,
        stellar_objects: list[StellarObject],
        wcs: WCS,
        width: int,
        height: int,
    ) -> list[StellarObject]:
        """Name every detected star using SIMBAD, then Gaia, then position.

        Here is the order we follow to name a star:

        1. **SIMBAD**: We check SIMBAD first because it has the most famous
        stars.
        2. **Gaia DR3**: If SIMBAD doesn't know the star, we check Gaia, which
           has over a billion faint stars.
        3. **Position**: If neither database knows the star, we name it based
           on its coordinates (like `FIELD_J123.4+45.6`). This gives the star
           a permanent name without making up a fake one.

        Parameters
        ----------
        stellar_objects : `list` [`StellarObject`]
            The stars we want to name.
        wcs : `astropy.wcs.WCS`
            The map data that tells us where in the sky we are looking.
        width, height : `int`
            The size of the image, which helps us know how big an area to
            search.

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            The updated list of named stars.
        """
        self._nearest_catalog_by_star = {}
        self.field_catalog_density_per_square_degree = None
        if not stellar_objects:
            return stellar_objects

        record_plate_scale(wcs)
        logger.info("Starting SIMBAD identification for %s sources...", len(stellar_objects))

        try:
            ra_center = wcs.wcs.crval[0]
            dec_center = wcs.wcs.crval[1]
        except DATA_ERRORS:
            logger.warning("Could not determine field center from WCS; skipping SIMBAD identification.")
            return stellar_objects

        sky_positions, ra_center, dec_center, query_radius_deg = self._project_stars_to_sky(
            stellar_objects, wcs, ra_center, dec_center
        )

        unmatched_after_simbad = self._match_stars_against_simbad(
            stellar_objects, sky_positions, ra_center, dec_center, wcs, width, height, query_radius_deg
        )

        if not unmatched_after_simbad:
            return stellar_objects

        still_unmatched = self._match_stars_against_gaia(
            unmatched_after_simbad, sky_positions, wcs, width, height
        )
        self._check_query_depth(stellar_objects)

        self._assign_field_ids(still_unmatched, sky_positions)

        return stellar_objects

    def _match_stars_against_simbad(
        self,
        stellar_objects: list[StellarObject],
        sky_positions: dict[int, tuple[float, float]],
        ra_center: float,
        dec_center: float,
        wcs: WCS,
        width: int,
        height: int,
        query_radius_deg: float | None,
    ) -> list[StellarObject]:
        """Match every detected star against a bulk SIMBAD region query.

        A star within `CATALOG_MATCH_RADIUS_ARCSEC` of a SIMBAD entry
        is identified from that match; every other star is left with
        its sky position recorded and deferred to the Gaia fallback.

        Returns
        -------
        unmatched_after_simbad : `list` [`StellarObject`]
            The stars SIMBAD could not identify.
        """
        result_table, simbad_coords = self._query_simbad_region(
            ra_center, dec_center, wcs, width, height, radius_deg_override=query_radius_deg
        )

        unmatched_after_simbad: list[StellarObject] = []
        match_radius_arcsec = resolve(CATALOG_MATCH_RADIUS_ARCSEC).value

        logger.info(
            "Matching %s detected stars against %s SIMBAD entries...",
            len(stellar_objects),
            len(simbad_coords) if simbad_coords is not None else 0,
        )
        nearest = self._nearest_entries(
            stellar_objects, sky_positions, simbad_coords if result_table is not None else None
        )
        for stellar_object in stellar_objects:
            position = sky_positions.get(id(stellar_object))
            if position is None:
                logger.warning("Star %s missing centroid in star_data; skipping.", stellar_object.name)
                unmatched_after_simbad.append(stellar_object)
                continue
            ra, dec = position
            index, separation_arcsec = nearest.get(id(stellar_object), (0, math.inf))
            if separation_arcsec < match_radius_arcsec:
                star_coord = SkyCoord(ra * u.deg, dec * u.deg)
                is_ambiguous = _is_unresolved_match(star_coord, simbad_coords)
                index = brightest_unresolved_entry_index(star_coord, index, simbad_coords, result_table)
                self._apply_simbad_match(stellar_object, result_table[index], ra, dec)
                # The separation between where the solved WCS put this star
                # and where the catalog says it is, which is the only direct
                # measure of how good the solution is.
                self.catalog_match_separations_arcsec.append(separation_arcsec)
                stellar_object.catalog_match_quality = assess_match_quality(
                    "simbad", separation_arcsec, is_ambiguous=is_ambiguous
                )
                logger.info("  SIMBAD match: %s at (%.4f, %.4f)", stellar_object.name, ra, dec)
                continue

            # SIMBAD had no match — record sky position and defer to Gaia.
            stellar_object.right_ascension = ra
            stellar_object.declination = dec
            unmatched_after_simbad.append(stellar_object)

        simbad_match_count = len(stellar_objects) - len(unmatched_after_simbad)
        logger.info(
            "SIMBAD matched %s / %s stars; %s deferred to Gaia DR3 fallback.",
            simbad_match_count,
            len(stellar_objects),
            len(unmatched_after_simbad),
        )

        return unmatched_after_simbad

    def _identify_stars_with_simbad(
        self,
        wcs: WCS | None,
        center_ra: float | None = None,
        center_dec: float | None = None,
        width: int = 1000,
        height: int = 1000,
        target_name: str | None = None,
    ) -> None:
        """Ask SIMBAD for stars in the image area and match them up.

        If we successfully mapped the image, we name every star. If the map
        failed, we just try to name the star closest to the center of the image
        using our best guess of where the telescope was pointing.

        That guess is the mount's report and can be minutes of arc off, so the
        nearest catalog entry is not always the right star. When `target_name`
        is given, `_choose_center_catalog_entry` picks the entry by the
        target's name first, and by brightness second. A solar-system target
        (a planet, the Moon, the Sun) is skipped: it has no catalog entry, and
        the nearest star would be a chance background star.
        """
        if not self.stellar_objects:
            return

        if wcs:
            self.identify_stars_with_wcs(self.stellar_objects, wcs, width, height)
            return

        if center_ra is None or center_dec is None:
            logger.warning("No center coordinates available for SIMBAD query.")
            return

        if is_solar_system_target(target_name):
            logger.info(
                "Target %r is a solar-system body: leaving the frame-centre star without a catalog name.",
                target_name,
            )
            return

        result_table, simbad_coords = self._query_simbad_region(
            center_ra, center_dec, None, width, height, target_name=target_name
        )
        if result_table is None:
            return

        for stellar_object in self.stellar_objects:
            star = stellar_object.star_data
            x = star.get("x_centroid", star.get("xcentroid"))
            y = star.get("y_centroid", star.get("ycentroid"))

            if x is None or y is None:
                logger.warning(
                    "Star %s is missing centroid coordinates in star_data: %s", stellar_object.name, star
                )
                continue

            dist_sq = (x - (width / 2)) ** 2 + (y - (height / 2)) ** 2
            star["center_dist_sq"] = dist_sq

        if not self.stellar_objects:
            return

        center_star_obj = min(self.stellar_objects, key=lambda o: o.star_data.get("center_dist_sq", 999999))
        try:
            hint_coord = SkyCoord(center_ra * u.deg, center_dec * u.deg)
            idx, basis = self._choose_center_catalog_entry(
                result_table, simbad_coords, hint_coord, target_name
            )
            hint_offset_arcsec = float(hint_coord.separation(simbad_coords[idx]).arcsec)
            logger.info(
                "Frame-centre star labelled by %s: SIMBAD entry %s, %.0f arcsec from the hint.",
                basis,
                result_table[idx]["main_id"] if "main_id" in result_table.colnames else idx,
                hint_offset_arcsec,
            )
            if basis != "nearest":
                target_star = self._select_target_star(width, height)
                if target_star is None:
                    logger.warning(
                        "No detection near the frame centre is bright enough to be the target, so the "
                        "catalog entry %s is not applied to any star.",
                        result_table[idx]["main_id"] if "main_id" in result_table.colnames else idx,
                    )
                    return
                center_star_obj = target_star
            if basis == "nearest" and hint_offset_arcsec > resolve(HINT_MATCH_WARNING_ARCSEC).value:
                logger.warning(
                    "The nearest stellar SIMBAD entry is %.0f arcsec from the position hint, so the star at "
                    "the frame centre may be labelled with the wrong catalog star. Check the hint: the FITS "
                    "position is only the mount's report.",
                    hint_offset_arcsec,
                )
            # The star gets the catalog star's own position. The hint is only
            # where the telescope was thought to point, and on the Vega
            # session it was 22 arcsec (13 pixels) from Vega itself, so
            # stamping it on the star put the overlay marker in the wrong
            # place.
            matched_position = simbad_coords[idx]
            self._apply_simbad_match(
                center_star_obj,
                result_table[idx],
                float(matched_position.ra.deg),
                float(matched_position.dec.deg),
            )
        except DATA_ERRORS as e:
            logger.warning("Failed to match hint coordinates against SIMBAD results: %s", e)

    def _choose_center_catalog_entry(
        self,
        result_table: Any,
        simbad_coords: SkyCoord,
        hint_coord: SkyCoord,
        target_name: str | None,
    ) -> tuple[int, str]:
        """Pick the SIMBAD entry that names the star at the frame centre.

        The position hint is the mount's report. On the 2026-10-04 spectral
        stacks it was 2.3 arcminutes from Alhena, and the nearest stellar
        entry was a magnitude 14 star 90 arcseconds from the hint, so
        distance alone named Alhena's zero order after the wrong star. The
        order of choices here avoids that:

        1. With no target name, take the nearest entry (the plain behaviour).
        2. If SIMBAD knows the target's name, take that entry, provided it is
           within `POINTING_ERROR_SEARCH_RADIUS_ARCSEC` of the hint. A name
           that resolves to something that is not a star in the region (a
           nebula or a cluster) falls back to the nearest entry.
        3. If SIMBAD does not know the name, take the brightest entry within
           the same radius. The star at the centre of a spectral frame is the
           target, which is chosen for being bright, so a faint neighbour is
           the less likely answer. With no magnitude to compare, take the
           nearest entry.

        Parameters
        ----------
        result_table : `astropy.table.Table`
            The stellar SIMBAD entries around the hint.
        simbad_coords : `astropy.coordinates.SkyCoord`
            The position of each entry in `result_table`.
        hint_coord : `astropy.coordinates.SkyCoord`
            The mount's reported position.
        target_name : `str` or `None`
            The name of the target being imaged.

        Returns
        -------
        index : `int`
            The row of `result_table` to use.
        basis : `str`
            ``"target name"``, ``"brightest"`` or ``"nearest"``: how the row
            was chosen.
        """
        search_radius_arcsec = resolve(POINTING_ERROR_SEARCH_RADIUS_ARCSEC).value
        separations_arcsec = np.atleast_1d(hint_coord.separation(simbad_coords).arcsec)
        nearest_index = int(np.argmin(separations_arcsec))
        if not target_name:
            return nearest_index, "nearest"

        status, named_coord = self._resolve_target_position(target_name)
        if status == "error":
            return nearest_index, "nearest"
        if status == "resolved":
            named_index = self._entry_at_position(named_coord, simbad_coords)
            if named_index is not None and separations_arcsec[named_index] <= search_radius_arcsec:
                return named_index, "target name"
            return nearest_index, "nearest"

        nearby_indices = np.flatnonzero(separations_arcsec <= search_radius_arcsec)
        brightest_index = self._brightest_entry_index(result_table, nearby_indices)
        if brightest_index is not None:
            return brightest_index, "brightest"
        return nearest_index, "nearest"

    def _select_target_star(self, width: int, height: int) -> StellarObject | None:
        """Find the detection that is the target of the frame.

        The brightest detection within `CENTRE_STAR_SEARCH_RADIUS_FRACTION` of
        the frame centre, provided it reaches
        `CENTRE_STAR_MINIMUM_PEAK_FRACTION` of the frame's brightest pixel.
        Used when the catalog entry was picked by name or by brightness. The
        nearest detection to the centre is not safe then: when the real star
        is missed (a smeared zero order can fail the roundness test), the
        nearest detection is a noise blob, and it would be given the star's
        name.

        Parameters
        ----------
        width, height : `int`
            The size of the image in pixels.

        Returns
        -------
        star : `StellarObject` or `None`
            The detection to name, or `None` when no detection qualifies.
        """
        radius_px = resolve(CENTRE_STAR_SEARCH_RADIUS_FRACTION).value * min(width, height)
        candidates = [
            star
            for star in self.stellar_objects
            if star.star_data.get("center_dist_sq", math.inf) <= radius_px**2
        ]
        if not candidates:
            return None
        brightest = max(candidates, key=lambda star: star.star_data.get("peak", 0.0))
        if (
            self.frame_peak
            and brightest.star_data.get("peak", 0.0)
            < resolve(CENTRE_STAR_MINIMUM_PEAK_FRACTION).value * self.frame_peak
        ):
            return None
        return brightest

    def _resolve_target_position(self, target_name: str) -> tuple[str, SkyCoord | None]:
        """Look a target's name up in SIMBAD and return where it is.

        Parameters
        ----------
        target_name : `str`
            The target's name, for example ``"Alhena"`` or ``"M 57"``.

        Returns
        -------
        status : `str`
            ``"resolved"`` when SIMBAD knows the name, ``"unresolved"`` when
            it does not, and ``"error"`` when the lookup failed (no network,
            for example) so nothing is known either way.
        position : `astropy.coordinates.SkyCoord` or `None`
            The object's position when `status` is ``"resolved"``.
        """
        try:
            table = self.simbad.query_object(target_name.replace("_", " "))
        except IndexError:
            # astroquery raises IndexError, not an empty result, for a name
            # SIMBAD does not know.
            return "unresolved", None
        except ExternalServiceError as lookup_error:
            logger.warning("SIMBAD name lookup for %r failed: %s", target_name, lookup_error)
            return "error", None
        if table is None or len(table) == 0:
            return "unresolved", None
        try:
            return "resolved", SkyCoord(float(table["ra"][0]) * u.deg, float(table["dec"][0]) * u.deg)
        except DATA_ERRORS as column_error:
            logger.warning(
                "SIMBAD name lookup for %r returned no usable position: %s", target_name, column_error
            )
            return "error", None

    @staticmethod
    def _entry_at_position(position: SkyCoord, simbad_coords: SkyCoord) -> int | None:
        """Find the catalog entry that sits at a given position.

        Parameters
        ----------
        position : `astropy.coordinates.SkyCoord`
            The position to look for.
        simbad_coords : `astropy.coordinates.SkyCoord`
            The position of each catalog entry.

        Returns
        -------
        index : `int` or `None`
            The entry within `CATALOG_MATCH_RADIUS_ARCSEC` of `position`, or
            `None` when there is none.
        """
        index, separation, _ = position.match_to_catalog_sky(simbad_coords)
        if float(np.ravel(separation.arcsec)[0]) <= resolve(CATALOG_MATCH_RADIUS_ARCSEC).value:
            return int(np.ravel(index)[0])
        return None

    @staticmethod
    def _brightest_entry_index(result_table: Any, candidate_indices: np.ndarray) -> int | None:
        """Find the brightest of some catalog entries.

        Parameters
        ----------
        result_table : `astropy.table.Table`
            The SIMBAD entries.
        candidate_indices : `numpy.ndarray`
            The rows to compare.

        Returns
        -------
        index : `int` or `None`
            The row with the smallest V magnitude, or `None` when no
            candidate has a V magnitude.
        """
        brightest_index: int | None = None
        brightest_magnitude = math.inf
        for candidate in candidate_indices:
            magnitude = read_catalog_magnitude(result_table[int(candidate)], _SIMBAD_V_MAGNITUDE_COLUMNS)
            if magnitude is not None and magnitude < brightest_magnitude:
                brightest_index = int(candidate)
                brightest_magnitude = magnitude
        return brightest_index

    @staticmethod
    def _filter_stellar_rows(result_table):  # ruff: ignore[missing-return-type-static-method, missing-type-function-argument]
        """Filter the SIMBAD results to only include individual stars.

        This removes galaxies, nebulae, and star clusters. Our algorithm only
        looks for small dots of light, so it won't find a whole galaxy anyway.

        Returns
        -------
        result_table : `astropy.table.Table`
            The list with only single stars included.
        """
        otype_col = next((c for c in ["OTYPE", "otype"] if c in result_table.colnames), None)
        sp_type_col = next((c for c in ["SP_TYPE", "sp_type"] if c in result_table.colnames), None)

        if otype_col is None:
            logger.warning("SIMBAD result has no OTYPE column; cannot exclude non-stellar catalog matches.")
            return result_table

        def _is_masked(value):  # ruff: ignore[missing-return-type-private-function, missing-type-function-argument]
            return value is None or (hasattr(value, "mask") and bool(value.mask))

        stellar_mask = []
        for row in result_table:
            otype_val = row[otype_col]
            otype = "" if _is_masked(otype_val) else str(otype_val).strip().upper()
            if not _is_masked(otype_val) and is_extended_object_type(str(otype_val).strip()):
                # An extended object is never the star at the frame centre,
                # even when SIMBAD gives it a spectral type (M 57 has one).
                stellar_mask.append(False)
                continue

            spectral_type_val = row[sp_type_col] if sp_type_col else None
            has_spectral_type = not _is_masked(spectral_type_val) and str(spectral_type_val).strip() != ""

            # In SIMBAD, codes for individual stars end in '*' (like 'V*' or
            # 'WD*').
            # The code 'Cl*' stands for a star cluster, which is a group of
            # stars,
            # so we want to filter those out.
            is_individual_star = otype.endswith("*") and "CL" not in otype
            stellar_mask.append(is_individual_star or "STAR" in otype or has_spectral_type)

        stellar_mask = np.array(stellar_mask, dtype=bool)
        return result_table[stellar_mask]

    def _apply_simbad_match(self, stellar_object: StellarObject, match: Any, ra: float, dec: float) -> None:
        """Copy the star details from SIMBAD into our star object.

        The id, name and aliases come from the naming step both
        identification paths share (`catalog_star_identity.name_simbad_star`).
        """
        main_id, identifiers = simbad_row_identifiers(match)
        names = name_simbad_star(main_id or "Unknown", identifiers)

        spectral_type = "Unknown"
        for col in ["SP_TYPE", "sp_type"]:
            if col in match.colnames:
                spectral_type = match[col]
                break
        if isinstance(spectral_type, bytes):
            spectral_type = spectral_type.decode("utf-8")
        if not spectral_type or str(spectral_type).strip() == "":
            spectral_type = "Unknown"

        # Map SIMBAD flux (magnitude); stays None when SIMBAD has no V value.
        magnitude = read_catalog_magnitude(match, ["V", "FLUX_V", "flux_v", "flux(V)"])
        blue_magnitude = read_catalog_magnitude(match, ["B", "FLUX_B", "flux_b", "flux(B)"])

        stellar_object.simbad_object_types = read_simbad_object_types(match)
        stellar_object.gaia_dr3_source_id = names.gaia_source_id
        stellar_object.name = names.name
        stellar_object.id = names.record_id
        stellar_object.aliases = list(names.aliases)
        stellar_object.spectral_type = str(spectral_type)
        stellar_object.stellar_spectral_type = str(spectral_type)
        stellar_object.magnitude = magnitude
        stellar_object.b_minus_v = (
            blue_magnitude - magnitude if blue_magnitude is not None and magnitude is not None else None
        )
        stellar_object.right_ascension = float(ra)
        stellar_object.declination = float(dec)
        stellar_object.is_catalog_identified = True
        magnitude_text = f"{magnitude:.2f}" if magnitude is not None else "unknown"
        logger.info(
            "  Identified: %s (%s, mag: %s) at %.4f, %.4f",
            stellar_object.name,
            stellar_object.spectral_type,
            magnitude_text,
            ra,
            dec,
        )
