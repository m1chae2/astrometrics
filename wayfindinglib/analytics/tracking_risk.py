"""Purpose: Score how risky each part of the sky is for the mount's tracking.

Description: A German equatorial mount tracks better in some parts of the
sky than others. This module builds a grid of risk scores over hour angle
(HA, how far west of the meridian a position is) and declination, from 0
(safe) to 1 (likely to trail). The grid is fixed to the mount, not to the
stars, so it does not change as the night goes on.

Each grid point starts from a prior score, set by geometry alone:

- West of the meridian (HA > 0), a mount balanced east-heavy can let its
  gears float. The score rises by up to 0.10 by HA = 75 degrees.
- Above declination 75 degrees, the declination bearing can stick. The
  score rises by up to 0.08 by declination 90.
- Below 20 degrees altitude, the air blurs stars and the load arm is long.
  The score rises by up to 0.40 at the horizon. Below the horizon it is 1.

Then measured targets pull the score toward what was seen. Each target
with two or more plate solves gives a score from its measured jitter (the
root-mean-square, RMS, scatter of its solves), compared with the camera's
plate scale (arcseconds of sky per pixel): jitter under 0.75 pixel is safe,
over 1.25 pixels trails. Each target's pull falls off as a Gaussian with a
25 degree width around the HA and declination where it was observed, and
grows with its solve count up to 30 solves. Where measured targets weigh
enough, they decide up to 85% of the score.

These are the rules the planetarium's tracking-risk overlay used to apply
itself. The overlay now only colors this grid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from wayfindinglib.models.equipment_and_site.performance_envelope import TrackingRiskMap

__all__ = [
    "CAUTION_SCORE",
    "HIGH_SCORE",
    "TrackedTarget",
    "build_tracking_risk_map",
    "prior_risk",
    "rms_risk_score",
]

CAUTION_SCORE = 0.28
"""Scores from here up are a caution (amber)."""

HIGH_SCORE = 0.58
"""Scores from here up are a high risk (red)."""

HA_STEP_DEG = 10.0
"""Spacing of the grid in hour angle."""

DEC_STEP_DEG = 5.0
"""Spacing of the grid in declination."""

INFLUENCE_RADIUS_DEG = 25.0
"""Width of the Gaussian pull of a measured target."""

FULL_CONFIDENCE_SOLVES = 30
"""A measured target with this many solves has its full weight."""

MAX_MEASURED_SHARE = 0.85
"""The largest share of a score that measured targets can decide."""

FALLBACK_IDEAL_RMS_ARCSEC = 1.2
"""Safe jitter when the plate scale is unknown."""

FALLBACK_TRAILING_RMS_ARCSEC = 2.0
"""Trailing jitter when the plate scale is unknown."""


@dataclass(frozen=True)
class TrackedTarget:
    """Where a measured target sat on the mount, and how well it tracked.

    Attributes
    ----------
    ha_deg : `float`
        Hour angle while it was observed, -180 to 180 degrees (west positive).
    dec_deg : `float`
        Declination in degrees.
    rms_arcsec : `float`
        Measured tracking jitter in arcseconds.
    solve_count : `int`
        How many plate solves the jitter rests on.
    """

    ha_deg: float
    dec_deg: float
    rms_arcsec: float
    solve_count: int


def _rms_limits(plate_scale_arcsec_per_px: float | None) -> tuple[float, float]:
    """Return the safe and trailing jitter for a plate scale.

    Parameters
    ----------
    plate_scale_arcsec_per_px : `float` or `None`
        Arcseconds of sky per camera pixel, if known.

    Returns
    -------
    ideal_rms, trailing_rms : `float`
        0.75 and 1.25 pixels in arcseconds, or 1.2 and 2.0 arcseconds when
        the plate scale is unknown.
    """
    if plate_scale_arcsec_per_px and plate_scale_arcsec_per_px > 0:
        return 0.75 * plate_scale_arcsec_per_px, 1.25 * plate_scale_arcsec_per_px
    return FALLBACK_IDEAL_RMS_ARCSEC, FALLBACK_TRAILING_RMS_ARCSEC


def prior_risk(ha_deg: float, dec_deg: float, alt_deg: float) -> float:
    """Score a mount position from geometry alone.

    Parameters
    ----------
    ha_deg : `float`
        Hour angle, -180 to 180 degrees (west positive).
    dec_deg : `float`
        Declination in degrees.
    alt_deg : `float`
        Altitude in degrees.

    Returns
    -------
    score : `float`
        From 0.05 (best) to 1 (below the horizon).
    """
    if alt_deg < 0:
        return 1.0
    risk = 0.05
    if ha_deg > 0:
        risk += 0.10 * min(1.0, ha_deg / 75.0)
    if dec_deg > 75.0:
        risk += 0.08 * min(1.0, (dec_deg - 75.0) / 15.0)
    if alt_deg < 20.0:
        risk += 0.40 * (20.0 - alt_deg) / 20.0
    return min(1.0, max(0.0, risk))


def rms_risk_score(rms_arcsec: float, plate_scale_arcsec_per_px: float | None = None) -> float:
    """Score a measured tracking jitter against the camera's pixels.

    Parameters
    ----------
    rms_arcsec : `float`
        Measured jitter in arcseconds.
    plate_scale_arcsec_per_px : `float`, optional
        Arcseconds of sky per camera pixel. Without it, 1.2 and 2.0
        arcseconds are the safe and trailing limits.

    Returns
    -------
    score : `float`
        0.05 to 0.27 up to the safe limit, 0.28 to 0.57 up to the trailing
        limit, and 0.58 to 0.95 beyond it.
    """
    if rms_arcsec <= 0.0:
        return 0.05
    ideal, trailing = _rms_limits(plate_scale_arcsec_per_px)
    if rms_arcsec <= ideal:
        return 0.05 + 0.22 * (rms_arcsec / ideal)
    if rms_arcsec <= trailing:
        return 0.28 + 0.29 * (rms_arcsec - ideal) / (trailing - ideal)
    excess = (rms_arcsec - trailing) / trailing
    return min(0.95, 0.58 + 0.37 * min(1.0, math.sqrt(excess)))


def _altitude_deg(ha_deg: float, dec_deg: float, latitude_deg: float) -> float:
    """Return the altitude of a mount position at a latitude.

    Parameters
    ----------
    ha_deg, dec_deg : `float`
        Hour angle and declination in degrees.
    latitude_deg : `float`
        Observer latitude in degrees.

    Returns
    -------
    alt_deg : `float`
        Altitude in degrees.
    """
    ha, dec, lat = math.radians(ha_deg), math.radians(dec_deg), math.radians(latitude_deg)
    sin_alt = math.sin(dec) * math.sin(lat) + math.cos(dec) * math.cos(lat) * math.cos(ha)
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))


def _separation_deg(ha1: float, dec1: float, ha2: float, dec2: float) -> float:
    """Return the great-circle distance between two mount positions.

    Parameters
    ----------
    ha1, dec1 : `float`
        First hour angle and declination, in degrees.
    ha2, dec2 : `float`
        Second hour angle and declination, in degrees.

    Returns
    -------
    distance_deg : `float`
        The angle between them, from the haversine formula.
    """
    phi1, phi2 = math.radians(dec1), math.radians(dec2)
    d_phi, d_lambda = math.radians(dec2 - dec1), math.radians(ha2 - ha1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return math.degrees(2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a))))


def _score(
    ha_deg: float,
    dec_deg: float,
    latitude_deg: float,
    targets: list[TrackedTarget],
    plate_scale_arcsec_per_px: float | None,
) -> float:
    """Blend the prior score with the measured targets at one grid point.

    Parameters
    ----------
    ha_deg, dec_deg : `float`
        The grid point.
    latitude_deg : `float`
        Observer latitude.
    targets : `list` [`TrackedTarget`]
        Measured targets.
    plate_scale_arcsec_per_px : `float` or `None`
        Camera plate scale.

    Returns
    -------
    score : `float`
        The blended score, 0 to 1.
    """
    prior = prior_risk(ha_deg, dec_deg, _altitude_deg(ha_deg, dec_deg, latitude_deg))
    total_weight = weighted_sum = 0.0
    for target in targets:
        distance = _separation_deg(ha_deg, dec_deg, target.ha_deg, target.dec_deg)
        if distance >= INFLUENCE_RADIUS_DEG * 1.8:
            continue
        confidence = min(1.0, target.solve_count / FULL_CONFIDENCE_SOLVES)
        weight = math.exp(-0.5 * (distance / INFLUENCE_RADIUS_DEG) ** 2) * confidence
        weighted_sum += rms_risk_score(target.rms_arcsec, plate_scale_arcsec_per_px) * weight
        total_weight += weight
    if total_weight <= 0.001:
        return prior
    share = min(MAX_MEASURED_SHARE, total_weight)
    return prior * (1.0 - share) + (weighted_sum / total_weight) * share


def build_tracking_risk_map(
    latitude_deg: float,
    targets: list[TrackedTarget],
    plate_scale_arcsec_per_px: float | None,
    solve_count: int,
) -> TrackingRiskMap:
    """Score every point of an hour angle and declination grid.

    Parameters
    ----------
    latitude_deg : `float`
        Observer latitude, which sets each point's altitude.
    targets : `list` [`TrackedTarget`]
        Measured targets. Ones with no jitter are ignored.
    plate_scale_arcsec_per_px : `float` or `None`
        Camera plate scale, if known.
    solve_count : `int`
        How many plate solves the targets were measured from, for display.

    Returns
    -------
    risk_map : `TrackingRiskMap`
        Scores at hour angles -180 to 180 in 10 degree steps and
        declinations -90 to 90 in 5 degree steps, rounded to 0.001.
    """
    measured = [t for t in targets if t.rms_arcsec > 0 and t.solve_count >= 2]
    ha_values = [-180.0 + HA_STEP_DEG * i for i in range(int(360 / HA_STEP_DEG) + 1)]
    dec_values = [-90.0 + DEC_STEP_DEG * i for i in range(int(180 / DEC_STEP_DEG) + 1)]
    scores = [
        [round(_score(ha, dec, latitude_deg, measured, plate_scale_arcsec_per_px), 3) for ha in ha_values]
        for dec in dec_values
    ]
    ideal, trailing = _rms_limits(plate_scale_arcsec_per_px)
    return TrackingRiskMap(
        latitude_deg=latitude_deg,
        ha_deg=ha_values,
        dec_deg=dec_values,
        scores=scores,
        caution_score=CAUTION_SCORE,
        high_score=HIGH_SCORE,
        ideal_rms_arcsec=ideal,
        trailing_rms_arcsec=trailing,
        plate_scale_arcsec_per_px=plate_scale_arcsec_per_px,
        measured_target_count=len(measured),
        solve_count=solve_count,
    )
