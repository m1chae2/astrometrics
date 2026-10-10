"""Tests for the EphemerisCrossMatcher (the tool that checks the database).

These tests use a fake SkyBoT client, so they run quickly and never touch
the network. The fake answers like the real service: given a place and a
moment, it lists the known asteroids it thinks are there at that moment.
"""

from collections.abc import Callable

import astropy.units as u
import pytest
from astropy.table import QTable
from pytest_mock import MockerFixture

from astrometricslib.models.moving_object import AsteroidDetectionCandidate, CascadeStage, FrameDetection
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.pipelines.asteroid_detection.ephemeris import EphemerisCrossMatcher

_START_UNIX = 1_700_000_000.0
_ARCSEC_DEG = 1.0 / 3600.0


class FakeSkybot:
    """A stand-in for the SkyBoT service that records every question.

    Parameters
    ----------
    table_for_epoch : `Callable` [[`float`], `QTable` or `None`], optional
        Gives the table to answer with for a Unix time. Defaults to an
        empty answer.
    error : `Exception`, optional
        Raised by every question when given.
    """

    def __init__(
        self,
        table_for_epoch: Callable[[float], QTable | None] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.table_for_epoch = table_for_epoch or (lambda epoch_unix: None)
        self.error = error
        self.calls: list[tuple[float, float, float, float]] = []

    def cone_search(
        self, coordinate: object, radius: object, epoch: object, **keywords: object
    ) -> QTable | None:
        """Answer one question, as ``Skybot.cone_search`` would.

        Parameters
        ----------
        coordinate : `astropy.coordinates.SkyCoord`
            The centre of the circle.
        radius : `astropy.units.Quantity`
            The radius of the circle.
        epoch : `astropy.time.Time`
            The moment asked about.
        **keywords
            The remaining SkyBoT options, which the fake ignores.

        Returns
        -------
        table : `astropy.table.QTable` or `None`
            The fake answer for that moment.
        """
        self.calls.append((coordinate.ra.deg, coordinate.dec.deg, radius.to_value(u.deg), epoch.unix))
        if self.error is not None:
            raise self.error
        return self.table_for_epoch(float(epoch.unix))


def _asteroid_table(right_ascension_deg: float, declination_deg: float, name: str = "2003 XY99") -> QTable:
    """Build a fake SkyBoT answer with one asteroid in it.

    Returns
    -------
    table : `astropy.table.QTable`
        A table shaped like the real service's answer.
    """
    return QTable({
        "Number": [12345],
        "Name": [name],
        "RA": [right_ascension_deg] * u.deg,
        "DEC": [declination_deg] * u.deg,
        "V": [15.2],
        "RA_rate": [12.0] * (u.arcsec / u.hour),
        "DEC_rate": [-4.0] * (u.arcsec / u.hour),
    })


def _moving_asteroid(rate_arcsec_per_hour: float) -> Callable[[float], QTable]:
    """Make a fake asteroid that drifts east at a steady rate.

    Parameters
    ----------
    rate_arcsec_per_hour : `float`
        The drift along the Right Ascension axis, in arcseconds of true
        angle per hour. The asteroid sits at (150, 0) at `_START_UNIX`.

    Returns
    -------
    table_for_epoch : `Callable`
        Gives the asteroid's table at a Unix time.
    """

    def table_for_epoch(epoch_unix: float) -> QTable:
        """Place the asteroid at the given time.

        Returns
        -------
        table : `astropy.table.QTable`
            One row, with the asteroid's position at that time.
        """
        hours = (epoch_unix - _START_UNIX) / 3600.0
        return _asteroid_table(150.0 + rate_arcsec_per_hour * hours * _ARCSEC_DEG, 0.0)

    return table_for_epoch


def _make_candidate(
    cascade_stage: CascadeStage,
    right_ascension_deg: float = 150.0,
    declination_deg: float = 30.0,
    rate_arcsec_per_hour: float = 0.0,
    frame_count: int = 1,
) -> AsteroidDetectionCandidate:
    """Create a fake moving object that drifts east at a steady rate.

    Parameters
    ----------
    cascade_stage : `CascadeStage`
        The stage the object has reached.
    right_ascension_deg, declination_deg : `float`
        Where the first detection is.
    rate_arcsec_per_hour : `float`
        How fast the detections drift east, in arcseconds per hour.
    frame_count : `int`
        How many detections there are, ten minutes apart.

    Returns
    -------
    candidate : `AsteroidDetectionCandidate`
        Our fake moving object.
    """
    detections = [
        FrameDetection(
            frame_path=f"frame{index}.fits",
            timestamp=_START_UNIX + 600.0 * index,
            pixel_x=100.0 + 10.0 * index,
            pixel_y=100.0,
            right_ascension_deg=right_ascension_deg + rate_arcsec_per_hour * (index / 6.0) * _ARCSEC_DEG,
            declination_deg=declination_deg,
        )
        for index in range(frame_count)
    ]
    return AsteroidDetectionCandidate(
        id="candidate-1", target_id="TestTarget", frame_detections=detections, cascade_stage=cascade_stage
    )


def _matcher(client: FakeSkybot, radius_arcsec: float = 10.0) -> EphemerisCrossMatcher:
    """Build a matcher that uses the fake service and does not wait.

    Returns
    -------
    matcher : `EphemerisCrossMatcher`
        A matcher wired to `client`.
    """
    return EphemerisCrossMatcher(
        MovingObjectConfig(ephemeris_cross_match_radius_arcsec=radius_arcsec),
        skybot_client=client,
        min_seconds_between_queries=0.0,
    )


def test_match_candidate_finds_close_known_body() -> None:
    """Test that we match a dot to a known asteroid when they are close."""
    matcher = _matcher(FakeSkybot())
    candidate = _make_candidate(CascadeStage.RATE_LINEARITY_CONFIRMED, 150.0, 30.0)
    table = _asteroid_table(150.001, 30.0009)

    match = matcher.match_candidate(candidate, table, table)

    assert match is not None
    assert match.designation == "2003 XY99"
    assert match.angular_separation_arcsec < 10.0


def test_match_candidate_returns_none_when_nothing_within_radius() -> None:
    """Test that we don't match an asteroid if it's too far away."""
    matcher = _matcher(FakeSkybot(), radius_arcsec=1.0)
    candidate = _make_candidate(CascadeStage.RATE_LINEARITY_CONFIRMED, right_ascension_deg=160.0)
    table = _asteroid_table(150.001, 30.0009)

    assert matcher.match_candidate(candidate, table, table) is None


def test_match_candidate_returns_none_for_empty_field_table() -> None:
    """Test that we handle it correctly if the database is empty."""
    matcher = _matcher(FakeSkybot())
    candidate = _make_candidate(CascadeStage.RATE_LINEARITY_CONFIRMED)
    assert matcher.match_candidate(candidate, None, None) is None


def test_cross_match_asks_at_the_first_and_last_detection_and_reports_both_separations() -> None:
    """An asteroid moving with the dot matches; both ends are reported."""
    rate = 60.0
    client = FakeSkybot(_moving_asteroid(rate))
    candidate = _make_candidate(
        CascadeStage.RATE_LINEARITY_CONFIRMED, 150.0, 0.0, rate_arcsec_per_hour=rate, frame_count=7
    )
    # Shift the first detection 2 arcsec and the last 5 arcsec off the truth.
    candidate.frame_detections[0].right_ascension_deg += 2.0 * _ARCSEC_DEG
    candidate.frame_detections[-1].right_ascension_deg += 5.0 * _ARCSEC_DEG

    _matcher(client).cross_match_candidates([candidate])

    assert [call[3] for call in client.calls] == pytest.approx([_START_UNIX, _START_UNIX + 3600.0])
    assert candidate.cascade_stage == CascadeStage.EPHEMERIS_MATCHED
    match = candidate.ephemeris_match
    assert match is not None
    assert match.first_detection_separation_arcsec == pytest.approx(2.0, abs=0.01)
    assert match.last_detection_separation_arcsec == pytest.approx(5.0, abs=0.01)
    assert match.angular_separation_arcsec == pytest.approx(5.0, abs=0.01)


def test_cross_match_rejects_an_asteroid_that_is_only_close_at_the_middle_epoch() -> None:
    """A known asteroid that crosses the field the other way is not a match.

    It sits exactly on the mean position of the detections at the mean
    epoch, which is where the old single query looked. At the first and the
    last detection it is a few arcminutes away.
    """
    # The dot drifts east 600 arcsec in the hour; the asteroid drifts west.
    # They cross at the middle of the hour.
    client = FakeSkybot(
        lambda epoch_unix: _asteroid_table(
            150.0 + (600.0 - 600.0 * (epoch_unix - _START_UNIX) / 3600.0) * _ARCSEC_DEG, 0.0
        )
    )
    candidate = _make_candidate(
        CascadeStage.RATE_LINEARITY_CONFIRMED, 150.0, 0.0, rate_arcsec_per_hour=600.0, frame_count=7
    )

    _matcher(client).cross_match_candidates([candidate])

    assert candidate.cascade_stage == CascadeStage.RATE_LINEARITY_CONFIRMED
    assert candidate.ephemeris_match is None


def test_cross_match_requires_the_same_asteroid_at_both_ends() -> None:
    """Two different asteroids, one near each end, do not make a match."""

    def table_for_epoch(epoch_unix: float) -> QTable:
        """Give the asteroid a different name in the second half hour.

        Returns
        -------
        table : `astropy.table.QTable`
            An asteroid right on the dot, with a different name at each end.
        """
        name = "A" if epoch_unix < _START_UNIX + 1800.0 else "B"
        return _asteroid_table(150.0 + 60.0 * (epoch_unix - _START_UNIX) / 3600.0 * _ARCSEC_DEG, 0.0, name)

    candidate = _make_candidate(
        CascadeStage.RATE_LINEARITY_CONFIRMED, 150.0, 0.0, rate_arcsec_per_hour=60.0, frame_count=7
    )

    _matcher(FakeSkybot(table_for_epoch)).cross_match_candidates([candidate])

    assert candidate.ephemeris_match is None


def test_cross_match_candidates_only_queries_rate_linearity_confirmed_candidates() -> None:
    """Test we only check the database for objects that passed the others."""
    client = FakeSkybot(lambda epoch_unix: _asteroid_table(150.001, 30.0009))
    confirmed_candidate = _make_candidate(CascadeStage.RATE_LINEARITY_CONFIRMED)
    rejected_candidate = _make_candidate(CascadeStage.REJECTED_STATIONARY_SKY)

    updated_candidates = _matcher(client).cross_match_candidates([confirmed_candidate, rejected_candidate])

    assert updated_candidates[0].cascade_stage == CascadeStage.EPHEMERIS_MATCHED
    assert updated_candidates[0].ephemeris_match is not None
    assert updated_candidates[1].cascade_stage == CascadeStage.REJECTED_STATIONARY_SKY
    assert updated_candidates[1].ephemeris_match is None
    assert len(client.calls) == 1


def test_cross_match_candidates_skips_query_when_no_confirmed_candidates() -> None:
    """Test that we skip the database if we found no good moving objects."""
    client = FakeSkybot()
    matcher = _matcher(client)

    matcher.cross_match_candidates([_make_candidate(CascadeStage.REJECTED_STATIONARY_SKY)])

    assert client.calls == []
    assert matcher.queries_attempted == 0


def test_identical_questions_are_answered_from_memory() -> None:
    """Two movers that share a first detection cost one question, not two."""
    client = FakeSkybot()
    matcher = _matcher(client)
    first = _make_candidate(CascadeStage.RATE_LINEARITY_CONFIRMED)
    second = _make_candidate(CascadeStage.RATE_LINEARITY_CONFIRMED)

    matcher.cross_match_candidates([first, second])

    assert len(client.calls) == 1
    assert matcher.queries_attempted == 1


def test_queries_are_spaced_out_in_time(mocker: MockerFixture) -> None:
    """The matcher sleeps between two real questions when asked to."""
    sleep = mocker.patch("astrometricslib.pipelines.asteroid_detection.ephemeris.time.sleep")
    matcher = EphemerisCrossMatcher(
        MovingObjectConfig(), skybot_client=FakeSkybot(), min_seconds_between_queries=5.0
    )

    matcher.query_field(150.0, 30.0, _START_UNIX, 0.01)
    matcher.query_field(151.0, 30.0, _START_UNIX, 0.01)

    sleep.assert_called_once()
    assert 0.0 < sleep.call_args.args[0] <= 5.0


def test_query_field_returns_none_on_query_failure_and_counts_it() -> None:
    """Test that the program doesn't crash if the database is offline."""
    client = FakeSkybot(error=RuntimeError("network error"))
    matcher = _matcher(client)

    result = matcher.query_field(150.0, 30.0, _START_UNIX, 0.5)

    assert result is None
    assert matcher.queries_attempted == 1
    assert matcher.queries_failed == 1


def test_a_failed_question_is_asked_again_not_remembered() -> None:
    """A failure is not stored, so a later identical question is retried."""
    client = FakeSkybot(error=RuntimeError("network error"))
    matcher = _matcher(client)

    matcher.query_field(150.0, 30.0, _START_UNIX, 0.5)
    matcher.query_field(150.0, 30.0, _START_UNIX, 0.5)

    assert len(client.calls) == 2
