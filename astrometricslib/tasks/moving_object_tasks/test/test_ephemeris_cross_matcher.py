"""Purpose: Unit tests for EphemerisCrossMatcher.

Description: Verifies match_candidate/cross_match_candidates against a
synthetic SkyBoT-shaped result table -- astroquery.imcce.Skybot.cone_search
is mocked so these tests never make a real network call.
"""

import astropy.units as u
import pytest
from astropy.table import QTable

from astrometricslib.models.moving_object import AsteroidRecoveryCandidate, CascadeStage, FrameDetection
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.tasks.moving_object_tasks.moving_object_ephemeris_tasks import EphemerisCrossMatcher


def _make_candidate(
    cascade_stage,  # ruff: ignore[missing-type-function-argument]
    right_ascension_deg=150.0,  # ruff: ignore[missing-type-function-argument]
    declination_deg=30.0,  # ruff: ignore[missing-type-function-argument]
) -> AsteroidRecoveryCandidate:
    """Build a minimal candidate with one detection at a known sky position.

    Returns
    -------
    AsteroidRecoveryCandidate
        A candidate with a single frame detection at the given sky position.
    """
    detection = FrameDetection(
        frame_path="frame0.fits",
        timestamp=1700000000.0,
        pixel_x=100.0,
        pixel_y=100.0,
        right_ascension_deg=right_ascension_deg,
        declination_deg=declination_deg,
        flux=500.0,
        sharpness=0.5,
        photutils_roundness1=0.1,
    )
    return AsteroidRecoveryCandidate(
        id="candidate-1", target_id="TestTarget", frame_detections=[detection], cascade_stage=cascade_stage
    )


def _make_field_table() -> QTable:
    """Build a synthetic SkyBoT-shaped result table with one known body.

    Returns
    -------
    QTable
        A single-row table shaped like a SkyBoT cone-search result.
    """
    return QTable({
        "Number": [12345],
        "Name": ["2003 XY99"],
        "RA": [150.001] * u.deg,
        "DEC": [30.0009] * u.deg,
        "V": [15.2],
        "RA_rate": [12.0] * (u.arcsec / u.hour),
        "DEC_rate": [-4.0] * (u.arcsec / u.hour),
    })


def test_match_candidate_finds_close_known_body():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a candidate within the cross-match radius matches the body."""
    matcher = EphemerisCrossMatcher(MovingObjectConfig(ephemeris_cross_match_radius_arcsec=10.0))
    candidate = _make_candidate(CascadeStage.RATE_LINEARITY_CONFIRMED)
    field_table = _make_field_table()

    match = matcher.match_candidate(candidate, field_table)

    assert match is not None
    assert match.designation == "2003 XY99"
    assert match.mpc_number == 12345
    assert match.predicted_visual_magnitude == pytest.approx(15.2)
    assert match.predicted_right_ascension_rate_arcsec_per_hour == pytest.approx(12.0)
    assert match.predicted_declination_rate_arcsec_per_hour == pytest.approx(-4.0)
    assert match.angular_separation_arcsec < 10.0


def test_match_candidate_returns_none_when_nothing_within_radius():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a candidate far from every known body returns no match."""
    matcher = EphemerisCrossMatcher(MovingObjectConfig(ephemeris_cross_match_radius_arcsec=1.0))
    candidate = _make_candidate(CascadeStage.RATE_LINEARITY_CONFIRMED, right_ascension_deg=160.0)
    field_table = _make_field_table()

    assert matcher.match_candidate(candidate, field_table) is None


def test_match_candidate_returns_none_for_empty_field_table():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a missing/empty field table returns no match, not a raise."""
    matcher = EphemerisCrossMatcher(MovingObjectConfig())
    candidate = _make_candidate(CascadeStage.RATE_LINEARITY_CONFIRMED)
    assert matcher.match_candidate(candidate, None) is None


def test_match_candidate_treats_unassigned_mpc_number_as_none():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify SkyBoT's -1 unassigned-number sentinel becomes None."""
    matcher = EphemerisCrossMatcher(MovingObjectConfig(ephemeris_cross_match_radius_arcsec=10.0))
    candidate = _make_candidate(CascadeStage.RATE_LINEARITY_CONFIRMED)
    field_table = _make_field_table()
    field_table["Number"] = [-1]

    match = matcher.match_candidate(candidate, field_table)

    assert match is not None
    assert match.mpc_number is None


def test_cross_match_candidates_only_queries_rate_linearity_confirmed_candidates(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify cross_match_candidates skips unconfirmed candidates.

    Confirmed (RATE_LINEARITY_CONFIRMED) candidates get updated in place.
    """
    matcher = EphemerisCrossMatcher(MovingObjectConfig(ephemeris_cross_match_radius_arcsec=10.0))
    confirmed_candidate = _make_candidate(CascadeStage.RATE_LINEARITY_CONFIRMED)
    rejected_candidate = _make_candidate(CascadeStage.REJECTED_STATIONARY_SKY)

    mocker.patch(
        "astroquery.imcce.Skybot.cone_search",
        return_value=_make_field_table(),
    )

    updated_candidates = matcher.cross_match_candidates(
        [confirmed_candidate, rejected_candidate],
        center_right_ascension_deg=150.0,
        center_declination_deg=30.0,
        epoch_unix=1700000000.0,
        radius_deg=0.5,
    )

    assert updated_candidates[0].cascade_stage == CascadeStage.EPHEMERIS_MATCHED
    assert updated_candidates[0].ephemeris_match is not None
    assert updated_candidates[1].cascade_stage == CascadeStage.REJECTED_STATIONARY_SKY
    assert updated_candidates[1].ephemeris_match is None


def test_cross_match_candidates_skips_query_when_no_confirmed_candidates(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify no SkyBoT query is made when there's nothing to cross-match."""
    matcher = EphemerisCrossMatcher(MovingObjectConfig())
    rejected_candidate = _make_candidate(CascadeStage.REJECTED_STATIONARY_SKY)

    cone_search_mock = mocker.patch("astroquery.imcce.Skybot.cone_search")

    matcher.cross_match_candidates(
        [rejected_candidate],
        center_right_ascension_deg=150.0,
        center_declination_deg=30.0,
        epoch_unix=1700000000.0,
        radius_deg=0.5,
    )

    cone_search_mock.assert_not_called()


def test_query_field_returns_none_on_query_failure(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a raised exception during the SkyBoT query is caught."""
    matcher = EphemerisCrossMatcher(MovingObjectConfig())
    mocker.patch("astroquery.imcce.Skybot.cone_search", side_effect=RuntimeError("network error"))

    result = matcher.query_field(150.0, 30.0, 1700000000.0, 0.5)

    assert result is None
