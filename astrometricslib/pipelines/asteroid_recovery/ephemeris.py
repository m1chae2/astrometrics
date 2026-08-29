"""Check if the moving objects we found are already known asteroids.

This is the final step. It asks the IMCCE SkyBoT database for a list of
all known asteroids that were in the area of the sky we were looking at,
at the exact time we took the photos. Then, it checks if any of our
moving dots match up with the known asteroids in that list.
"""

import logging
import statistics

import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.table import Table
from astropy.time import Time

from astrometricslib.models.moving_object import AsteroidRecoveryCandidate, CascadeStage, EphemerisMatch
from astrometricslib.models.moving_object_config import MovingObjectConfig

logger = logging.getLogger(__name__)

# The database uses the number '-1' to mean "this asteroid doesn't have an
# official number yet." We change this to 'None' so it doesn't get confused
# with a real asteroid number.
_SKYBOT_UNASSIGNED_MPC_NUMBER = -1


class EphemerisCrossMatcher:
    """Checks the SkyBoT database to see if we found a known asteroid.

    Parameters
    ----------
    config : `MovingObjectConfig`
        The settings for how close a match has to be to count.
    """

    def __init__(self, config: MovingObjectConfig):  # ruff: ignore[missing-return-type-special-method]
        self.config = config

    def query_field(
        self,
        center_right_ascension_deg: float,
        center_declination_deg: float,
        epoch_unix: float,
        radius_deg: float,
    ) -> Table | None:
        """Ask the database for all known asteroids in a circle on the sky.

        Parameters
        ----------
        center_right_ascension_deg : `float`
            The X-coordinate (RA) of the center of the circle.
        center_declination_deg : `float`
            The Y-coordinate (Dec) of the center of the circle.
        epoch_unix : `float`
            The exact time we took the photos.
        radius_deg : `float`
            How big of a circle to search, in degrees.

        Returns
        -------
        field_table : `astropy.table.Table` or `None`
            A list of all the asteroids in that area at that time, or None
            if the search failed or the area was empty.
        """
        from astroquery.imcce import Skybot

        coordinate = SkyCoord(center_right_ascension_deg * u.deg, center_declination_deg * u.deg)
        epoch = Time(epoch_unix, format="unix")
        try:
            field_table = Skybot.cone_search(
                coordinate,
                radius_deg * u.deg,
                epoch,
                location=self.config.mpc_observatory_code,
                position_error=120 * u.arcsec,
            )
        except Exception as query_error:
            logger.warning(f"SkyBoT cone-search query failed: {query_error}")
            return None

        if field_table is None or len(field_table) == 0:
            return None
        return field_table

    def match_candidate(
        self, candidate: AsteroidRecoveryCandidate, field_table: Table | None
    ) -> EphemerisMatch | None:
        """Check if one of our moving objects matches a known asteroid.

        Parameters
        ----------
        candidate : `AsteroidRecoveryCandidate`
            The moving object we found.
        field_table : `astropy.table.Table` or `None`
            The list of known asteroids from the database.

        Returns
        -------
        ephemeris_match : `EphemerisMatch` or `None`
            The details of the closest known asteroid we matched, or None
            if it doesn't match anything close enough.
        """
        if field_table is None or len(field_table) == 0:
            return None

        mean_right_ascension_deg = statistics.mean(
            detection.right_ascension_deg for detection in candidate.frame_detections
        )
        mean_declination_deg = statistics.mean(
            detection.declination_deg for detection in candidate.frame_detections
        )
        candidate_coordinate = SkyCoord(mean_right_ascension_deg * u.deg, mean_declination_deg * u.deg)

        closest_row = None
        closest_separation_arcsec = None
        for row in field_table:
            row_coordinate = SkyCoord(u.Quantity(row["RA"]).to(u.deg), u.Quantity(row["DEC"]).to(u.deg))
            separation_arcsec = candidate_coordinate.separation(row_coordinate).arcsec
            if separation_arcsec <= self.config.ephemeris_cross_match_radius_arcsec and (
                closest_separation_arcsec is None or separation_arcsec < closest_separation_arcsec
            ):
                closest_separation_arcsec = separation_arcsec
                closest_row = row

        if closest_row is None:
            return None

        mpc_number = int(closest_row["Number"])
        return EphemerisMatch(
            designation=str(closest_row["Name"]),
            mpc_number=None if mpc_number == _SKYBOT_UNASSIGNED_MPC_NUMBER else mpc_number,
            predicted_visual_magnitude=float(closest_row["V"]) if "V" in closest_row.colnames else None,
            predicted_right_ascension_rate_arcsec_per_hour=(
                float(u.Quantity(closest_row["RA_rate"]).to(u.arcsec / u.hour).value)
                if "RA_rate" in closest_row.colnames
                else None
            ),
            predicted_declination_rate_arcsec_per_hour=(
                float(u.Quantity(closest_row["DEC_rate"]).to(u.arcsec / u.hour).value)
                if "DEC_rate" in closest_row.colnames
                else None
            ),
            angular_separation_arcsec=float(closest_separation_arcsec),
        )

    def cross_match_candidates(
        self,
        candidates: list[AsteroidRecoveryCandidate],
        center_right_ascension_deg: float,
        center_declination_deg: float,
        epoch_unix: float,
        radius_deg: float,
    ) -> list[AsteroidRecoveryCandidate]:
        """Check every found moving object against the known asteroid database.

        Instead of asking the database about every single object one by one
        (which would be slow), we ask once for a map of everything in the whole
        photo area. Then we check all our objects against that one map.

        Parameters
        ----------
        candidates : `list` [`AsteroidRecoveryCandidate`]
            The list of possible moving objects we found. We only check the
            ones that passed all the previous tests.
        center_right_ascension_deg : `float`
            The X-coordinate (RA) of the center of our photos.
        center_declination_deg : `float`
            The Y-coordinate (Dec) of the center of our photos.
        epoch_unix : `float`
            The exact time we took the photos.
        radius_deg : `float`
            How big of an area the photos cover, in degrees.

        Returns
        -------
        candidates : `list` [`AsteroidRecoveryCandidate`]
            The original list. If a match was found, we add the asteroid's
            name and update its status to 'EPHEMERIS_MATCHED'. If no match
            was found, we leave it alone (meaning we might have discovered
            something new!).
        """
        rate_confirmed_candidates = [
            candidate
            for candidate in candidates
            if candidate.cascade_stage == CascadeStage.RATE_LINEARITY_CONFIRMED
        ]
        if not rate_confirmed_candidates:
            return candidates

        field_table = self.query_field(
            center_right_ascension_deg, center_declination_deg, epoch_unix, radius_deg
        )
        for candidate in rate_confirmed_candidates:
            ephemeris_match = self.match_candidate(candidate, field_table)
            if ephemeris_match is not None:
                candidate.ephemeris_match = ephemeris_match
                candidate.cascade_stage = CascadeStage.EPHEMERIS_MATCHED

        return candidates
