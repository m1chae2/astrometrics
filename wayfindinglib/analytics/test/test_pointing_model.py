"""Purpose: Unit tests for the geometric pointing model fit/predict pair.

Description: Builds made-up plate solves from known terms at chosen hour
angles and declinations, with the true hour angle (local sidereal time at the
site minus right ascension), and checks that `fit_pointing_model` recovers the
terms, that `predict_pointing_error` reproduces the made-up offsets, and that a
fit that cannot separate its terms is refused or marked low confidence.
"""

import math

import numpy as np
import pytest

from wayfindinglib.analytics.pointing_model import (
    _pointing_geometry,
    fit_pointing_model,
    hour_angle_deg,
    predict_pointing_error,
)
from wayfindinglib.models.session.telemetry import MountPointingModel

_TRUE_COEFFICIENTS = {"ih": 5.0, "id": -3.0, "me": 20.0, "ma": 10.0, "ch": 2.0, "tf": 1.0}
_LATITUDE_DEG = 45.68
_LONGITUDE_DEG = -111.04
_START_TIME = 1_795_000_000.0
"""A start time in unix seconds (late 2026)."""

#: Hour angle and declination, in degrees, of points spread over the sky.
_SPREAD_POINTS = [
    (-80.0, 10.0),
    (-55.0, -20.0),
    (-30.0, 45.0),
    (-10.0, -45.0),
    (5.0, 0.0),
    (25.0, 30.0),
    (45.0, -30.0),
    (65.0, 55.0),
    (85.0, -55.0),
    (-70.0, 20.0),
    (15.0, 60.0),
    (35.0, 5.0),
    (-45.0, -10.0),
    (55.0, 25.0),
    (-20.0, 35.0),
    (75.0, -15.0),
]


def _true_offsets(
    ra_deg: float, dec_deg: float, timestamp: float, coefficients: dict[str, float]
) -> tuple[float, float]:
    """Return the offset the made-up terms give at a position and time.

    Uses the public forward direction through a model built from the terms.

    Returns
    -------
    offsets : `tuple` [`float`, `float`]
        The RA and Dec offsets in arcseconds.
    """
    model = MountPointingModel(
        sample_count=0,
        raw_rms_arcsec=0.0,
        residual_rms_arcsec=0.0,
        ih_arcsec=coefficients["ih"],
        id_arcsec=coefficients["id"],
        me_arcsec=coefficients["me"],
        ma_arcsec=coefficients["ma"],
        ch_arcsec=coefficients["ch"],
        tf_arcsec=coefficients["tf"],
    )
    return predict_pointing_error(ra_deg, dec_deg, model, _LATITUDE_DEG, _LONGITUDE_DEG, timestamp)


def _synthetic_attempts(
    points: list[tuple[float, float]],
    noise_arcsec: float = 0.0,
    seed: int = 0,
    coefficients: dict[str, float] | None = None,
) -> list[dict[str, float]]:
    """Build plate-solve attempt dicts from known terms at chosen hour angles.

    Each point is a true hour angle and a declination. The solve time steps
    by 20 minutes per point, and the right ascension is the local sidereal
    time at that time minus the hour angle, so `hour_angle_deg` returns the
    chosen hour angle again.

    Returns
    -------
    attempts : `list` [`dict`]
        One attempt per point, with the offsets the terms give plus
        Gaussian noise of `noise_arcsec`.
    """
    terms = coefficients or _TRUE_COEFFICIENTS
    rng = np.random.default_rng(seed)
    attempts = []
    for index, (hour_angle, dec) in enumerate(points):
        timestamp = _START_TIME + 1200.0 * index
        ra_deg = (hour_angle_deg(0.0, timestamp, _LONGITUDE_DEG) - hour_angle) % 360.0
        d_ra, d_dec = _true_offsets(ra_deg, dec, timestamp, terms)
        attempts.append({
            "ra": ra_deg,
            "dec": dec,
            "timestamp": timestamp,
            "delta_ra_arcsec": d_ra + float(rng.normal(0.0, noise_arcsec)),
            "delta_dec_arcsec": d_dec + float(rng.normal(0.0, noise_arcsec)),
        })
    return attempts


def _fit(attempts: list[dict[str, float]]) -> MountPointingModel:
    """Fit the attempts at the test site.

    Returns
    -------
    model : `MountPointingModel`
        The fit.
    """
    return fit_pointing_model(attempts, latitude_deg=_LATITUDE_DEG, longitude_deg=_LONGITUDE_DEG)


def test_hour_angle_is_local_sidereal_time_minus_right_ascension() -> None:
    """Verify the hour angle follows the sidereal time."""
    first = hour_angle_deg(100.0, _START_TIME, _LONGITUDE_DEG)
    later = hour_angle_deg(100.0, _START_TIME + 3600.0, _LONGITUDE_DEG)
    other_site = hour_angle_deg(100.0, _START_TIME, _LONGITUDE_DEG + 15.0)

    # One hour later the sky has turned 15.041 degrees.
    assert (later - first) % 360.0 == pytest.approx(15.041, abs=0.01)
    assert (other_site - first) % 360.0 == pytest.approx(15.0, abs=0.01)
    assert -180.0 <= first <= 180.0


def test_fit_recovers_known_terms_without_noise() -> None:
    """Verify a noiseless run over well spread points recovers every term."""
    model = _fit(_synthetic_attempts(_SPREAD_POINTS))

    assert model.confidence == "high"
    assert model.ih_arcsec == pytest.approx(_TRUE_COEFFICIENTS["ih"], abs=0.01)
    assert model.id_arcsec == pytest.approx(_TRUE_COEFFICIENTS["id"], abs=0.01)
    assert model.me_arcsec == pytest.approx(_TRUE_COEFFICIENTS["me"], abs=0.01)
    assert model.ma_arcsec == pytest.approx(_TRUE_COEFFICIENTS["ma"], abs=0.01)
    assert model.ch_arcsec == pytest.approx(_TRUE_COEFFICIENTS["ch"], abs=0.01)
    assert model.tf_arcsec == pytest.approx(_TRUE_COEFFICIENTS["tf"], abs=0.01)
    assert model.total_polar_error_arcsec == pytest.approx(math.hypot(20.0, 10.0), abs=0.01)
    assert model.residual_rms_arcsec == pytest.approx(0.0, abs=0.01)


def test_fit_recovers_known_terms_within_its_stated_uncertainty_with_noise() -> None:
    """Verify one arcsecond of scatter moves the terms a few arcseconds."""
    model = _fit(_synthetic_attempts(_SPREAD_POINTS, noise_arcsec=1.0, seed=7))

    assert model.confidence in {"high", "medium"}
    for fitted, truth in (
        (model.ih_arcsec, _TRUE_COEFFICIENTS["ih"]),
        (model.id_arcsec, _TRUE_COEFFICIENTS["id"]),
        (model.me_arcsec, _TRUE_COEFFICIENTS["me"]),
        (model.ma_arcsec, _TRUE_COEFFICIENTS["ma"]),
        (model.ch_arcsec, _TRUE_COEFFICIENTS["ch"]),
        (model.tf_arcsec, _TRUE_COEFFICIENTS["tf"]),
    ):
        assert fitted == pytest.approx(truth, abs=3.0)
    assert "good to about" in model.message


def test_fit_uses_the_true_hour_angle_not_one_made_from_right_ascension() -> None:
    """Verify the same right ascension gives different fits at different times.

    The terms are recovered only if the fit's hour angle is the one the
    offsets were made with. Shifting every time by six sidereal hours turns
    the sky a quarter circle, so a fit that read only the right ascension
    would not notice and would give the same terms; the true fit must differ.
    """
    attempts = _synthetic_attempts(_SPREAD_POINTS)
    shifted = [{**attempt, "timestamp": attempt["timestamp"] + 6 * 3600.0} for attempt in attempts]

    right = _fit(attempts)
    wrong = _fit(shifted)

    assert right.me_arcsec == pytest.approx(_TRUE_COEFFICIENTS["me"], abs=0.01)
    assert abs(wrong.me_arcsec - _TRUE_COEFFICIENTS["me"]) > 1.0 or abs(wrong.residual_rms_arcsec) > 0.5


def test_predict_pointing_error_reproduces_the_synthetic_input() -> None:
    """Verify predict_pointing_error matches the made-up offsets after a fit.

    The fit and predict directions of this one geometric model must never
    drift apart: a perfect fit has to reproduce its own input.
    """
    attempts = _synthetic_attempts(_SPREAD_POINTS)
    model = _fit(attempts)

    for attempt in attempts:
        predicted_d_ra, predicted_d_dec = predict_pointing_error(
            attempt["ra"], attempt["dec"], model, _LATITUDE_DEG, _LONGITUDE_DEG, attempt["timestamp"]
        )
        assert predicted_d_ra == pytest.approx(attempt["delta_ra_arcsec"], abs=0.05)
        assert predicted_d_dec == pytest.approx(attempt["delta_dec_arcsec"], abs=0.05)


def test_predict_pointing_error_is_zero_for_a_zeroed_model() -> None:
    """Verify a model with every term at its default (0.0) predicts none."""
    zero_model = MountPointingModel(sample_count=0, raw_rms_arcsec=0.0, residual_rms_arcsec=0.0)

    predicted_d_ra, predicted_d_dec = predict_pointing_error(
        150.0, 20.0, zero_model, _LATITUDE_DEG, _LONGITUDE_DEG, _START_TIME
    )

    assert predicted_d_ra == pytest.approx(0.0, abs=1e-9)
    assert predicted_d_dec == pytest.approx(0.0, abs=1e-9)


def test_pointing_geometry_uses_the_hour_angle_it_is_given() -> None:
    """Verify the trig terms follow the hour angle passed in."""
    geometry = _pointing_geometry(30.0, _LATITUDE_DEG, 90.0)

    assert geometry["sin_h"] == pytest.approx(1.0)
    assert geometry["cos_h"] == pytest.approx(0.0, abs=1e-12)


def test_fit_reports_insufficient_data_below_four_points() -> None:
    """Verify fewer than 4 valid points short-circuits to insufficient_data."""
    model = _fit(_synthetic_attempts(_SPREAD_POINTS[:2]))

    assert model.confidence == "insufficient_data"
    assert model.sample_count == 2


def test_fit_leaves_out_solves_with_no_time_and_says_so() -> None:
    """Verify a solve with no time has no true hour angle and is not used."""
    attempts = _synthetic_attempts(_SPREAD_POINTS[:4])
    for attempt in attempts[:2]:
        del attempt["timestamp"]

    model = _fit(attempts)

    assert model.confidence == "insufficient_data"
    assert model.sample_count == 2
    assert "2 solves with no time" in model.message


def test_fit_refuses_points_that_are_too_close_together_on_the_sky() -> None:
    """Verify a tight cluster of points is refused, with terms left at zero.

    Ten solves within a few degrees cannot tell polar error from tube
    flexure or an index error, so a fit would give large terms that cancel.
    """
    cluster = [(-5.0 + step, 30.0 + 0.4 * step) for step in range(10)]

    model = _fit(_synthetic_attempts(cluster, noise_arcsec=1.0, seed=3))

    assert model.confidence == "insufficient_data"
    assert model.me_arcsec == pytest.approx(0.0)
    assert model.ma_arcsec == pytest.approx(0.0)
    assert model.tf_arcsec == pytest.approx(0.0)
    assert "too close together" in model.message


def test_fit_marks_a_weakly_separated_fit_low_confidence() -> None:
    """Verify few but spread points give a fit marked low, not high."""
    few = [_SPREAD_POINTS[index] for index in (0, 3, 6, 9, 12, 15)]

    model = _fit(_synthetic_attempts(few, noise_arcsec=1.0, seed=5))

    assert model.confidence in {"low", "insufficient_data"}
    assert model.confidence != "high"


def test_fit_with_a_wide_span_of_hour_angle_but_one_declination_is_not_trusted() -> None:
    """Verify points along one declination are not given high confidence."""
    one_declination = [(-80.0 + 12.0 * step, 20.0) for step in range(14)]

    model = _fit(_synthetic_attempts(one_declination, noise_arcsec=1.0, seed=11))

    assert model.confidence in {"low", "insufficient_data"}
