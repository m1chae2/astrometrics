"""Check if the moving objects we found are already known asteroids.

This is the final step. It asks the IMCCE SkyBoT database which known
asteroids were near a spot of the sky at a given moment. Then it checks
whether one of those known asteroids lines up with our moving dot.

A known asteroid moves, so one question for the whole sequence of pictures
would only be right for a sequence a few minutes long. Instead, for each
moving dot we ask twice: once at the time and place of its first detection
and once at the time and place of its last detection. A known asteroid
counts as a match only if it is close to the dot at both moments.
"""

import logging
import time
from typing import Any

import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.table import Table
from astropy.time import Time

from astrometricslib.models.moving_object import (
    AsteroidDetectionCandidate,
    CascadeStage,
    EphemerisMatch,
    FrameDetection,
)
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.utilities.exceptions import ONLINE_QUERY_ERRORS

logger = logging.getLogger(__name__)

# SkyBoT leaves out an asteroid whose predicted position is uncertain by
# more than this much.
_SKYBOT_POSITION_ERROR = 120 * u.arcsec

# Be polite to the public service: wait at least this long between two
# questions that really go out over the network.
_DEFAULT_SECONDS_BETWEEN_QUERIES = 0.5

# The most questions one run may send to SkyBoT. Each confirmed mover needs
# two (first and last detection), so 200 covers 100 movers. At the polite
# spacing above, 200 questions take about 100 s plus the network time. A
# run that confirms more movers than that is almost certainly mistaking
# stars or artifacts for movers (validation register MOV-04), and asking
# about each would hit the public service for minutes. Design estimate;
# see MOV-D11 in the validation register.
_DEFAULT_MAX_QUERIES_PER_RUN = 200


class EphemerisCrossMatcher:
    """Checks the SkyBoT database to see if we found a known asteroid.

    Parameters
    ----------
    config : `MovingObjectConfig`
        The settings for how close a match has to be to count.
    skybot_client : `Any`, optional
        Anything with the same ``cone_search`` method as
        ``astroquery.imcce.Skybot``. Tests pass a fake one. When omitted,
        the real ``Skybot`` is used.
    min_seconds_between_queries : `float`, optional
        The shortest wait between two questions sent to the service. Use
        ``0.0`` to turn the wait off.
    max_queries : `int`, optional
        The most questions this matcher may send in one run. A mover that
        would need more than the questions left is not checked at all, and
        is listed in `unchecked_candidate_ids`. Answers already in memory
        do not count.
    """

    def __init__(
        self,
        config: MovingObjectConfig,
        skybot_client: Any | None = None,
        min_seconds_between_queries: float = _DEFAULT_SECONDS_BETWEEN_QUERIES,
        max_queries: int = _DEFAULT_MAX_QUERIES_PER_RUN,
    ) -> None:
        self.config = config
        self._skybot_client = skybot_client
        self._min_seconds_between_queries = min_seconds_between_queries
        self.max_queries = max_queries
        self._last_query_time: float | None = None
        # Answers already received, so asking again for the same spot and
        # moment (for example two movers that share a first detection) does
        # not use the network. A failed question is not stored.
        self._answers: dict[tuple[float, float, float, float], Table | None] = {}
        # A failed query returns `None`, the same as a field with no known
        # asteroids, so the two are counted here to tell them apart.
        self.queries_attempted = 0
        self.queries_failed = 0
        # Confirmed movers left without an answer because the question
        # limit was reached. They are neither matched nor known to be new.
        self.unchecked_candidate_ids: list[str] = []

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
            The moment to ask about, as a Unix timestamp.
        radius_deg : `float`
            How big of a circle to search, in degrees.

        Returns
        -------
        field_table : `astropy.table.Table` or `None`
            A list of all the asteroids in that area at that time, or None
            if the search failed or the area was empty.
        """
        cache_key = _question_key(center_right_ascension_deg, center_declination_deg, epoch_unix, radius_deg)
        if cache_key in self._answers:
            return self._answers[cache_key]

        if self._skybot_client is None:
            from astroquery.imcce import Skybot

            client: Any = Skybot
        else:
            client = self._skybot_client

        self._wait_for_turn()
        coordinate = SkyCoord(center_right_ascension_deg * u.deg, center_declination_deg * u.deg)
        epoch = Time(epoch_unix, format="unix")
        self.queries_attempted += 1
        try:
            field_table = client.cone_search(
                coordinate,
                radius_deg * u.deg,
                epoch,
                location=self.config.mpc_observatory_code,
                position_error=_SKYBOT_POSITION_ERROR,
            )
        except (RuntimeError, *ONLINE_QUERY_ERRORS) as query_error:
            # astroquery raises RuntimeError when the service reports an error.
            logger.warning("SkyBoT cone-search query failed: %s", query_error)
            self.queries_failed += 1
            return None

        if field_table is None or len(field_table) == 0:
            field_table = None
        self._answers[cache_key] = field_table
        return field_table

    def _wait_for_turn(self) -> None:
        """Sleep, if needed, so questions are spaced out in time."""
        now = time.monotonic()
        if self._last_query_time is not None and self._min_seconds_between_queries > 0.0:
            remaining = self._min_seconds_between_queries - (now - self._last_query_time)
            if remaining > 0.0:
                time.sleep(remaining)
                now = time.monotonic()
        self._last_query_time = now

    def _separations_by_name(self, detection: FrameDetection, field_table: Table | None) -> dict[str, float]:
        """Measure how far each listed asteroid is from one detection.

        Parameters
        ----------
        detection : `FrameDetection`
            The dot to compare. The table must be for the time of this dot.
        field_table : `astropy.table.Table` or `None`
            The known asteroids near the dot at that time.

        Returns
        -------
        separations : `dict` [`str`, `float`]
            For each asteroid within the match radius, its name and its
            distance from the dot in arcseconds. If a name is listed twice,
            the smaller distance is kept.
        """
        if field_table is None or len(field_table) == 0:
            return {}
        detection_coordinate = SkyCoord(
            detection.right_ascension_deg * u.deg, detection.declination_deg * u.deg
        )
        separations: dict[str, float] = {}
        for row in field_table:
            row_coordinate = SkyCoord(u.Quantity(row["RA"]).to(u.deg), u.Quantity(row["DEC"]).to(u.deg))
            separation_arcsec = float(detection_coordinate.separation(row_coordinate).arcsec)
            if separation_arcsec > self.config.ephemeris_cross_match_radius_arcsec:
                continue
            name = str(row["Name"])
            if name not in separations or separation_arcsec < separations[name]:
                separations[name] = separation_arcsec
        return separations

    def match_candidate(
        self,
        candidate: AsteroidDetectionCandidate,
        first_field_table: Table | None,
        last_field_table: Table | None,
    ) -> EphemerisMatch | None:
        """Check if one of our moving objects matches a known asteroid.

        The asteroid must be within the match radius of the first detection
        in the first table and of the last detection in the last table.
        If several asteroids qualify, the one whose larger separation is
        smaller is returned.

        Parameters
        ----------
        candidate : `AsteroidDetectionCandidate`
            The moving object we found.
        first_field_table : `astropy.table.Table` or `None`
            The known asteroids near the first detection, at its time.
        last_field_table : `astropy.table.Table` or `None`
            The known asteroids near the last detection, at its time. For
            a chain with one detection this is the same table as the first.

        Returns
        -------
        ephemeris_match : `EphemerisMatch` or `None`
            The details of the closest known asteroid we matched, or None
            if it is not close enough at both ends.
        """
        first_detection, last_detection = _first_and_last_detection(candidate)
        first_separations = self._separations_by_name(first_detection, first_field_table)
        last_separations = self._separations_by_name(last_detection, last_field_table)

        best_name: str | None = None
        best_worst_separation_arcsec = 0.0
        for name, first_separation in first_separations.items():
            if name not in last_separations:
                continue
            worst_separation_arcsec = max(first_separation, last_separations[name])
            if best_name is None or worst_separation_arcsec < best_worst_separation_arcsec:
                best_name = name
                best_worst_separation_arcsec = worst_separation_arcsec

        if best_name is None:
            return None
        return EphemerisMatch(
            designation=best_name,
            angular_separation_arcsec=best_worst_separation_arcsec,
            first_detection_separation_arcsec=first_separations[best_name],
            last_detection_separation_arcsec=last_separations[best_name],
        )

    def cross_match_candidates(
        self, candidates: list[AsteroidDetectionCandidate]
    ) -> list[AsteroidDetectionCandidate]:
        """Check every found moving object against the known asteroid database.

        For each object that passed the straight-line test, the database is
        asked about the sky around its first detection at that detection's
        time, and again for its last detection. Identical questions are
        answered from memory. Once the run's question limit (`max_queries`)
        is reached, a mover that would need more questions is skipped and
        its id goes into `unchecked_candidate_ids`; it keeps its stage and
        gets no match, and a warning says how many were skipped.

        Parameters
        ----------
        candidates : `list` [`AsteroidDetectionCandidate`]
            The list of possible moving objects we found. We only check the
            ones that passed all the previous tests.

        Returns
        -------
        candidates : `list` [`AsteroidDetectionCandidate`]
            The original list. If a match was found, we add the asteroid's
            name and update its status to 'EPHEMERIS_MATCHED'. If no match
            was found, we leave it alone (meaning we might have discovered
            something new!). A skipped mover is also left alone, but it was
            not checked, so it does not mean the same thing.
        """
        query_radius_deg = self.config.ephemeris_cross_match_radius_arcsec / 3600.0
        for candidate in candidates:
            if candidate.cascade_stage != CascadeStage.RATE_LINEARITY_CONFIRMED:
                continue
            first_detection, last_detection = _first_and_last_detection(candidate)
            if self._questions_still_needed(first_detection, last_detection, query_radius_deg) > (
                self.max_queries - self.queries_attempted
            ):
                self.unchecked_candidate_ids.append(candidate.id)
                continue
            failures_before = self.queries_failed
            first_table = self.query_field(
                first_detection.right_ascension_deg,
                first_detection.declination_deg,
                first_detection.timestamp,
                query_radius_deg,
            )
            if self.queries_failed > failures_before:
                # Without the first answer no match is possible, so the
                # second question would only add load to a failing service.
                continue
            if last_detection is first_detection:
                last_table = first_table
            else:
                last_table = self.query_field(
                    last_detection.right_ascension_deg,
                    last_detection.declination_deg,
                    last_detection.timestamp,
                    query_radius_deg,
                )
            ephemeris_match = self.match_candidate(candidate, first_table, last_table)
            if ephemeris_match is not None:
                candidate.ephemeris_match = ephemeris_match
                candidate.cascade_stage = CascadeStage.EPHEMERIS_MATCHED

        if self.unchecked_candidate_ids:
            logger.warning(
                "SkyBoT question limit of %s reached after %s questions: %s confirmed mover(s) were "
                "not checked against known asteroids and are neither matched nor known to be new "
                "(ids: %s)",
                self.max_queries,
                self.queries_attempted,
                len(self.unchecked_candidate_ids),
                ", ".join(self.unchecked_candidate_ids),
            )
        return candidates

    def _questions_still_needed(
        self, first_detection: FrameDetection, last_detection: FrameDetection, radius_deg: float
    ) -> int:
        """Count the network questions a mover needs that are not yet answered.

        Parameters
        ----------
        first_detection, last_detection : `FrameDetection`
            The earliest and latest dot of the mover.
        radius_deg : `float`
            The search circle, in degrees.

        Returns
        -------
        needed : `int`
            0, 1 or 2. A question whose answer is in memory costs nothing.
        """
        keys = {
            _question_key(d.right_ascension_deg, d.declination_deg, d.timestamp, radius_deg)
            for d in (first_detection, last_detection)
        }
        return sum(1 for key in keys if key not in self._answers)


def _question_key(
    center_right_ascension_deg: float, center_declination_deg: float, epoch_unix: float, radius_deg: float
) -> tuple[float, float, float, float]:
    """Round one question so that equal questions share one stored answer.

    Parameters
    ----------
    center_right_ascension_deg, center_declination_deg : `float`
        Center of the circle, in degrees.
    epoch_unix : `float`
        The moment asked about, as a Unix timestamp.
    radius_deg : `float`
        Radius of the circle, in degrees.

    Returns
    -------
    key : `tuple` [`float`, `float`, `float`, `float`]
        The rounded values.
    """
    return (
        round(center_right_ascension_deg, 6),
        round(center_declination_deg, 6),
        round(epoch_unix, 3),
        round(radius_deg, 8),
    )


def _first_and_last_detection(candidate: AsteroidDetectionCandidate) -> tuple[FrameDetection, FrameDetection]:
    """Pick the earliest and the latest detection of a chain.

    Parameters
    ----------
    candidate : `AsteroidDetectionCandidate`
        A moving object with at least one detection.

    Returns
    -------
    first_detection, last_detection : `FrameDetection`
        The detections with the smallest and the largest timestamp. They
        are the same object for a chain of one detection.
    """
    detections = candidate.frame_detections
    return min(detections, key=lambda d: d.timestamp), max(detections, key=lambda d: d.timestamp)
