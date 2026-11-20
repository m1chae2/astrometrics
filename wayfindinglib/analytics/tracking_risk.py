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

from astrometricslib.parameters import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    RunBasis,
    TunedParameter,
    factor_for,
    resolve_parameter,
    run_basis_value,
)
from wayfindinglib.models.equipment_and_site.performance_envelope import TrackingRiskMap

__all__ = [
    "CAUTION_SCORE",
    "HIGH_SCORE",
    "BlendSettings",
    "TrackedTarget",
    "blended_score",
    "build_tracking_risk_map",
    "prior_risk",
    "rms_risk_score",
]

CAUTION_SCORE = TunedParameter(
    name="wayfinding.caution_score",
    default=0.28,
    unit="fraction",
    description="Scores from here up are a caution (amber).",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="WAY-D130",
)
"""Scores from here up are a caution (amber).

It is the score of a jitter at the safe limit (`SAFE_JITTER_PX`): a label of
that limit on the score scale, not a separate threshold, so it stays fixed.
"""

HIGH_SCORE = TunedParameter(
    name="wayfinding.high_score",
    default=0.58,
    unit="fraction",
    description="Scores from here up are a high risk (red).",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="WAY-D130",
)
"""Scores from here up are a high risk (red).

It is the score of a jitter at the trailing limit (`TRAILING_JITTER_PX`).
"""

HA_STEP_DEG = 10.0
"""Spacing of the grid in hour angle."""

DEC_STEP_DEG = 5.0
"""Spacing of the grid in declination."""

INFLUENCE_RADIUS_DEG = TunedParameter(
    name="wayfinding.influence_radius_deg",
    default=25.0,
    unit="deg",
    description="Width of the Gaussian pull of a measured target.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT, EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="WAY-D130",
    allowed_range=(2.0, 90.0),
    minimum_sample=5,  # a sample is one measured target (a group of solves with a position and a jitter)
)
"""Width of the Gaussian pull of a measured target."""

FULL_CONFIDENCE_SOLVES = TunedParameter(
    name="wayfinding.full_confidence_solves",
    default=30,
    unit="solves",
    description="A measured target with this many solves has its full weight.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT, EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="WAY-D130",
    allowed_range=(2.0, 200.0),
    minimum_sample=5,  # a sample is one measured target
)
"""A measured target with this many solves has its full weight."""

MAX_MEASURED_SHARE = TunedParameter(
    name="wayfinding.max_measured_share",
    default=0.85,
    unit="fraction",
    description="The largest share of a score that measured targets can decide.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT, EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="WAY-D130",
    allowed_range=(0.1, 1.0),
    minimum_sample=5,  # a sample is one measured target
)
"""The largest share of a score that measured targets can decide."""

FALLBACK_IDEAL_RMS_ARCSEC = TunedParameter(
    name="wayfinding.fallback_ideal_rms_arcsec",
    default=1.2,
    unit="arcsec",
    description="Safe jitter when the plate scale is unknown.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="WAY-D130",
)
"""Safe jitter when no plate scale is known at all, not even the setup's.

A last resort for a config with no setup: the setup's optic and camera
normally give the plate scale.
"""

FALLBACK_TRAILING_RMS_ARCSEC = TunedParameter(
    name="wayfinding.fallback_trailing_rms_arcsec",
    default=2.0,
    unit="arcsec",
    description="Trailing jitter when the plate scale is unknown.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="WAY-D130",
)
"""Trailing jitter when no plate scale is known, not even the setup's."""


# Guide jitter blurs a star in quadrature: its width grows to
# sqrt(FWHM^2 + (2.355 x RMS)^2). The safe and trailing jitter limits are
# set by how much widening they allow, so they follow the star width. On the
# reference setup (2.33 px stars) the safe limit of 0.75 px RMS widens the
# stars by about 25% and the trailing limit of 1.25 px by about 63%.
SAFE_JITTER_PX = TunedParameter(
    name="wayfinding.safe_jitter_px",
    default=factor_for(0.75, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="Guide jitter (RMS) up to which tracking is safe: it widens the stars by about 25%.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.MOUNT, EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="WAY-D130",
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=0.75,
)
"""Guide jitter up to which tracking is safe, in pixels."""

TRAILING_JITTER_PX = TunedParameter(
    name="wayfinding.trailing_jitter_px",
    default=factor_for(1.25, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="Guide jitter (RMS) beyond which stars trail: it widens them by about 63%.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.MOUNT, EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="WAY-D130",
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=1.25,
)
"""Guide jitter beyond which stars trail, in pixels."""


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


@dataclass(frozen=True)
class BlendSettings:
    """The three numbers that decide how measured targets pull a score.

    Attributes
    ----------
    influence_radius_deg : `float`
        Width of a measured target's Gaussian pull, in degrees.
    full_confidence_solves : `float`
        Solve count at which a measured target has its full weight.
    max_measured_share : `float`
        The largest share of a score that measured targets can decide.
    """

    influence_radius_deg: float
    full_confidence_solves: float
    max_measured_share: float

    @classmethod
    def from_parameters(cls) -> BlendSettings:
        """Read the three numbers from the resolved parameters.

        Returns
        -------
        settings : `BlendSettings`
            The numbers in use for the current run.
        """
        return cls(
            influence_radius_deg=resolve_parameter(INFLUENCE_RADIUS_DEG).value,
            full_confidence_solves=resolve_parameter(FULL_CONFIDENCE_SOLVES).value,
            max_measured_share=resolve_parameter(MAX_MEASURED_SHARE).value,
        )


def _rms_limits(plate_scale_arcsec_per_px: float | None) -> tuple[float, float]:
    """Return the safe and trailing jitter for a plate scale.

    Parameters
    ----------
    plate_scale_arcsec_per_px : `float` or `None`
        Arcseconds of sky per camera pixel. When it is missing, the run's or
        the setup's plate scale (from the optic and camera) is used.

    Returns
    -------
    ideal_rms, trailing_rms : `float`
        `SAFE_JITTER_PX` and `TRAILING_JITTER_PX` in arcseconds; the fixed
        fallbacks only when no plate scale is known at all.
    """
    scale = plate_scale_arcsec_per_px
    if not scale or scale <= 0:
        scale = run_basis_value(RunBasis.PLATE_SCALE_ARCSEC_PER_PX)
    if scale and scale > 0:
        return (
            float(resolve_parameter(SAFE_JITTER_PX).value) * scale,
            float(resolve_parameter(TRAILING_JITTER_PX).value) * scale,
        )
    return resolve_parameter(FALLBACK_IDEAL_RMS_ARCSEC).value, resolve_parameter(
        FALLBACK_TRAILING_RMS_ARCSEC
    ).value


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
        Arcseconds of sky per camera pixel. Without it, the run's or the
        setup's plate scale is used (see `_rms_limits`).

    Returns
    -------
    score : `float`
        0.05 to 0.27 up to the safe limit, 0.28 to 0.57 up to the trailing
        limit, and 0.58 to 0.95 beyond it.
    """
    if rms_arcsec <= 0.0:
        return 0.05
    ideal, trailing = _rms_limits(plate_scale_arcsec_per_px)
    caution = float(resolve_parameter(CAUTION_SCORE).value)
    high = float(resolve_parameter(HIGH_SCORE).value)
    if rms_arcsec <= ideal:
        return 0.05 + (caution - 0.06) * (rms_arcsec / ideal)
    if rms_arcsec <= trailing:
        return caution + (high - caution - 0.01) * (rms_arcsec - ideal) / (trailing - ideal)
    excess = (rms_arcsec - trailing) / trailing
    return min(0.95, high + (0.95 - high) * min(1.0, math.sqrt(excess)))


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


def blended_score(
    ha_deg: float,
    dec_deg: float,
    latitude_deg: float,
    targets: list[TrackedTarget],
    plate_scale_arcsec_per_px: float | None,
    settings: BlendSettings | None = None,
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
    settings : `BlendSettings`, optional
        Trial numbers for the pull of the targets, used by the tuner. The
        resolved parameters are used when it is left out.

    Returns
    -------
    score : `float`
        The blended score, 0 to 1.
    """
    if settings is None:
        settings = BlendSettings.from_parameters()
    prior = prior_risk(ha_deg, dec_deg, _altitude_deg(ha_deg, dec_deg, latitude_deg))
    total_weight = weighted_sum = 0.0
    for target in targets:
        distance = _separation_deg(ha_deg, dec_deg, target.ha_deg, target.dec_deg)
        if distance >= settings.influence_radius_deg * 1.8:
            continue
        confidence = min(1.0, target.solve_count / settings.full_confidence_solves)
        weight = math.exp(-0.5 * (distance / settings.influence_radius_deg) ** 2) * confidence
        weighted_sum += rms_risk_score(target.rms_arcsec, plate_scale_arcsec_per_px) * weight
        total_weight += weight
    if total_weight <= 0.001:
        return prior
    share = min(settings.max_measured_share, total_weight)
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
        [
            round(blended_score(ha, dec, latitude_deg, measured, plate_scale_arcsec_per_px), 3)
            for ha in ha_values
        ]
        for dec in dec_values
    ]
    ideal, trailing = _rms_limits(plate_scale_arcsec_per_px)
    return TrackingRiskMap(
        latitude_deg=latitude_deg,
        ha_deg=ha_values,
        dec_deg=dec_values,
        scores=scores,
        caution_score=resolve_parameter(CAUTION_SCORE).value,
        high_score=resolve_parameter(HIGH_SCORE).value,
        ideal_rms_arcsec=ideal,
        trailing_rms_arcsec=trailing,
        plate_scale_arcsec_per_px=plate_scale_arcsec_per_px,
        measured_target_count=len(measured),
        solve_count=solve_count,
    )
