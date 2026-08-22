"""Ephemeris cross-match (cascade step 5) for the asteroid-recovery pipeline.

Queries IMCCE's SkyBoT service for known solar-system bodies in a
field at a given epoch, then matches each surviving candidate's mean
sky position against that result -- the actual recovery confirmation,
not just another filter. Confirmed against the installed astroquery
package: `astroquery.imcce.Skybot.cone_search` is the right primary
tool (a real FOV cone search with rate columns included);
`astroquery.mpc.MPC.get_ephemeris` needs an already-known
designation, so it's only useful as an optional secondary
confirmation, not implemented here.
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

# SkyBoT reports -1 for bodies with no assigned MPC number
# (unnumbered/provisional designations) -- not a real number to
# persist.
_SKYBOT_UNASSIGNED_MPC_NUMBER = -1


class EphemerisCrossMatcher:
    """Query SkyBoT for known solar-system bodies and match candidates.

    Parameters
    ----------
    config : `MovingObjectConfig`
        Configuration supplying the cross-match radius and
        observatory code.
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
        """Run one SkyBoT cone-search query for known bodies in a field.

        Parameters
        ----------
        center_right_ascension_deg : `float`
            Cone-search center right ascension, in degrees (ICRS).
        center_declination_deg : `float`
            Cone-search center declination, in degrees (ICRS).
        epoch_unix : `float`
            Epoch of the search, as Unix-epoch seconds.
        radius_deg : `float`
            Cone-search radius, in degrees (SkyBoT clips this to its
            own 10-degree maximum).

        Returns
        -------
        field_table : `astropy.table.Table` or `None`
            The query result (one row per known body in the field),
            or `None` if the query failed (e.g. no network access) or
            returned no rows.
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
        """Match a candidate's mean sky position against a field table.

        Parameters
        ----------
        candidate : `AsteroidRecoveryCandidate`
            The candidate to match, expected to already carry
            `frame_detections`.
        field_table : `astropy.table.Table` or `None`
            The result of `query_field`, or `None` if no query
            succeeded.

        Returns
        -------
        ephemeris_match : `EphemerisMatch` or `None`
            The closest known body within
            `config.ephemeris_cross_match_radius_arcsec`, or `None`
            if no query result is available or nothing matched within
            that radius.
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
        """Run one field query and match every rate-confirmed candidate.

        Queries the field once and matches every candidate that has
        reached `CascadeStage.RATE_LINEARITY_CONFIRMED` against it.

        Parameters
        ----------
        candidates : `list` [`AsteroidRecoveryCandidate`]
            All candidates from the discrimination cascade (steps
            1-4); only those at
            `CascadeStage.RATE_LINEARITY_CONFIRMED` are queried
            against.
        center_right_ascension_deg : `float`
            Field query center right ascension, in degrees.
        center_declination_deg : `float`
            Field query center declination, in degrees.
        epoch_unix : `float`
            Field query epoch, as Unix-epoch seconds.
        radius_deg : `float`
            Field query radius, in degrees.

        Returns
        -------
        candidates : `list` [`AsteroidRecoveryCandidate`]
            The same candidates, with
            `cascade_stage`/`ephemeris_match` updated to
            `EPHEMERIS_MATCHED` for any that matched a known body.
            Candidates that reached `RATE_LINEARITY_CONFIRMED` but
            matched nothing are left as-is (surfaced, not discarded)
            rather than rejected outright.
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
