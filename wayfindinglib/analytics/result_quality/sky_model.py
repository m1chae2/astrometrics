"""Purpose: A model of the sky background, fitted to the user's own frames.

Description: The sky background (`frame.sky_rate_e_per_s_per_px`) depends on
the Moon and on how low the target is. One model, fitted to the sky rates of
the user's own frames, serves three readers (decision S16): the loss account
uses it to say how much of the sky-brightness loss the Moon caused, the
starting point for a new target uses it, and tonight's ranking uses it to
predict a target's efficiency.

The model for one capture setting is

    sky_rate = c0 + c1 (X - 1) + c2 * moon_term

where X is the airmass and

    moon_term = illumination * max(0, sin(moon altitude)) * scatter(separation)
    * X

The scatter function has the shape of Krisciunas and Schaefer (1991, PASP
103, 1033): ``1.06 + cos^2(rho) + 10^(0.79 - rho / 40)`` with the Moon
separation rho in degrees. The first two terms are Rayleigh scattering and
the last is the forward glow of the Mie scattering. The coefficients c0, c1
and c2 are fitted by non-negative least squares on relative errors, per
capture setting, so a sky rate is never predicted below zero and the Moon
cannot darken the sky. The constant c0 holds the dark sky, the airglow and,
because no dark frame is subtracted, the sensor's dark current.

The model is model-derived and labeled so. It has not been checked on a
photometric night; extinction is not modeled separately (decision S16). The
fit reports its error on nights held out of the fit. With fewer than
`MINIMUM_NIGHTS_FOR_SKY_MODEL` nights no model is made.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from scipy.optimize import nnls

from wayfindinglib.analytics.result_quality.observing_values import SkyGeometry, sky_geometry

_MODEL_CONFIG = ConfigDict(populate_by_name=True, alias_generator=to_camel)

MINIMUM_NIGHTS_FOR_SKY_MODEL = 5
"""Fewest nights that make a sky model. The fit has three coefficients, and
leaving one night out for the check must leave several nights to fit. Five
nights leave four. Design estimate, register row WAY-D29."""

MINIMUM_FRAMES_FOR_SKY_MODEL = 20
"""Fewest frames that make a sky model, so that the airmass term has a range of
airmasses to fit. Design estimate, register row WAY-D29."""

MINIMUM_MOON_NIGHTS = 2
"""The Moon coefficient is fitted only when at least this many nights have a
Moon
term above `MOON_TERM_MINIMUM`. With fewer, the coefficient would rest on one
night and could copy that night's other conditions. The coefficient is then
held at 0 and the model says so. Design estimate, register row WAY-D29."""

MOON_TERM_MINIMUM = 0.02
"""A frame counts as moonlit for the check above when its Moon term is at least
this. The term runs up to about 5 for a full Moon high in the sky, close to the
target, so 0.02 is a faint Moon. Design estimate, register row WAY-D29."""

MIE_EXPONENT_OFFSET = 0.79
MIE_ANGLE_SCALE_DEG = 40.0
RAYLEIGH_OFFSET = 1.06
"""Constants of the Krisciunas and Schaefer (1991) scattering shape. 0.79 is
log10(10^6.15 / 10^5.36), the ratio of their Mie and Rayleigh amplitudes; the
offset 1.06 is the Rayleigh part's constant. Published values; register row
WAY-D29."""


class SkyRateSample(BaseModel):
    """One frame's sky rate with the time and place needed to explain it.

    Parameters
    ----------
    frame_id : `str`
        The frame.
    night_id : `str`
        The observing night.
    epoch_seconds : `float`
        The frame's time (the middle of the exposure), in seconds since the
        Unix epoch.
    ra_deg, dec_deg : `float`
        Where the frame points, in degrees.
    sky_rate_e_per_s_per_px : `float`
        The measured sky rate, above 0.
    """

    model_config = _MODEL_CONFIG

    frame_id: str
    night_id: str
    epoch_seconds: float
    ra_deg: float
    dec_deg: float
    sky_rate_e_per_s_per_px: float = Field(gt=0.0)


class SkyModel(BaseModel):
    """A fitted sky model for one capture setting.

    Parameters
    ----------
    setting_key : `str`
        The equipment key the model was fitted for (`baseline_key`).
    constant : `float`
        c0, in electrons per second per pixel.
    airmass_coefficient : `float`
        c1.
    moon_coefficient : `float`
        c2. 0 when `moon_fitted` is false.
    moon_fitted : `bool`
        Whether enough moonlit nights existed to fit the Moon term.
    nights_used, frames_used : `int`
        What the fit used.
    in_sample_relative_error : `float`
        Root-mean-square of (predicted - measured) / measured on the frames
        the model was fitted to.
    held_out_relative_error : `float`
        The same on frames of nights left out of the fit, one night at a
        time. This is the error to expect on a new night.
    model_derived : `bool`
        Always true: the values come from a model, not a measurement.
    """

    model_config = _MODEL_CONFIG

    setting_key: str
    constant: float
    airmass_coefficient: float
    moon_coefficient: float
    moon_fitted: bool
    nights_used: int
    frames_used: int
    in_sample_relative_error: float
    held_out_relative_error: float
    model_derived: bool = True


class SkyModelOutcome(BaseModel):
    """A sky model, or the reason none could be made.

    Parameters
    ----------
    model : `SkyModel` or `None`
        The model.
    reason : `str` or `None`
        Why there is no model. Set exactly when `model` is ``None``.
    frames_skipped : `int`
        Samples dropped because the target was below the horizon.
    """

    model_config = _MODEL_CONFIG

    model: SkyModel | None = None
    reason: str | None = None
    frames_skipped: int = 0


class SkyPrediction(BaseModel):
    """A predicted sky rate.

    Parameters
    ----------
    sky_rate_e_per_s_per_px : `float` or `None`
        The predicted rate. ``None`` when the target is below the horizon.
    moonless_sky_rate_e_per_s_per_px : `float` or `None`
        The same with the Moon term removed.
    relative_error : `float`
        The held-out relative error of the model, to quote beside the value.
    moon_included : `bool`
        Whether the Moon term was fitted. When false and the Moon is up, the
        prediction does not include the Moon and is not reliable then.
    reliable : `bool`
        False when the Moon is up but the model could not fit a Moon term.
    model_derived : `bool`
        Always true.
    """

    model_config = _MODEL_CONFIG

    sky_rate_e_per_s_per_px: float | None = None
    moonless_sky_rate_e_per_s_per_px: float | None = None
    relative_error: float
    moon_included: bool
    reliable: bool
    model_derived: bool = True


def moon_scatter_shape(separation_deg: float) -> float:
    """Give the angular shape of scattered moonlight.

    Parameters
    ----------
    separation_deg : `float`
        The angle between the target and the Moon, in degrees.

    Returns
    -------
    shape : `float`
        ``1.06 + cos^2(rho) + 10^(0.79 - rho / 40)``. Larger close to the Moon.
    """
    cosine = math.cos(math.radians(separation_deg))
    return RAYLEIGH_OFFSET + cosine**2 + 10.0 ** (MIE_EXPONENT_OFFSET - separation_deg / MIE_ANGLE_SCALE_DEG)


def sky_features(geometry: SkyGeometry) -> tuple[float, float, float] | None:
    """Give the three model features of one moment.

    Parameters
    ----------
    geometry : `SkyGeometry`
        The target's and the Moon's place in the sky.

    Returns
    -------
    features : `tuple` [`float`, `float`, `float`] or `None`
        ``(1, X - 1, moon_term)``, or ``None`` when the target is below the
        horizon (no airmass).
    """
    if geometry.airmass is None:
        return None
    moon_up = max(0.0, math.sin(math.radians(geometry.moon_altitude_deg)))
    moon_term = (
        geometry.moon_illumination_fraction
        * moon_up
        * moon_scatter_shape(geometry.moon_separation_deg)
        * geometry.airmass
    )
    return 1.0, geometry.airmass - 1.0, moon_term


def _fit_coefficients(features: np.ndarray, rates: np.ndarray, *, fit_moon: bool) -> np.ndarray:
    """Fit the three coefficients by non-negative least squares.

    The fit is on relative errors.

    Parameters
    ----------
    features : `numpy.ndarray`
        One row ``(1, X - 1, moon_term)`` per frame.
    rates : `numpy.ndarray`
        The measured sky rates.
    fit_moon : `bool`
        Whether to fit the Moon coefficient. When false it is 0.

    Returns
    -------
    coefficients : `numpy.ndarray`
        ``[c0, c1, c2]``, all at least 0.
    """
    columns = [0, 1, 2] if fit_moon else [0, 1]
    weights = 1.0 / rates
    solution, _norm = nnls(features[:, columns] * weights[:, None], np.ones_like(rates))
    coefficients = np.zeros(3)
    coefficients[columns] = solution
    return coefficients


def _relative_errors(coefficients: np.ndarray, features: np.ndarray, rates: np.ndarray) -> np.ndarray:
    """Give (predicted - measured) / measured for each frame.

    Parameters
    ----------
    coefficients : `numpy.ndarray`
        ``[c0, c1, c2]``.
    features : `numpy.ndarray`
        The frames' features.
    rates : `numpy.ndarray`
        The measured rates.

    Returns
    -------
    errors : `numpy.ndarray`
        The relative errors.
    """
    return (features @ coefficients - rates) / rates


def fit_sky_model(
    samples: Sequence[SkyRateSample],
    setting_key: str,
    latitude_deg: float,
    longitude_deg: float,
    elevation_m: float,
) -> SkyModelOutcome:
    """Fit the sky model for one capture setting.

    Parameters
    ----------
    samples : `Sequence` [`SkyRateSample`]
        The sky rates of the user's own frames of ONE capture setting
        (`baseline_key`): the sky rate per second depends on the camera, gain
        and filter. Frames set aside should be left out.
    setting_key : `str`
        The key of that setting, kept on the model.
    latitude_deg, longitude_deg, elevation_m : `float`
        The observing site.

    Returns
    -------
    outcome : `SkyModelOutcome`
        The model with its held-out error, or ``None`` with a reason when
        there are fewer than `MINIMUM_NIGHTS_FOR_SKY_MODEL` nights or
        `MINIMUM_FRAMES_FOR_SKY_MODEL` frames above the horizon.
    """
    geometries = sky_geometry(
        [s.epoch_seconds for s in samples],
        [s.ra_deg for s in samples],
        [s.dec_deg for s in samples],
        latitude_deg,
        longitude_deg,
        elevation_m,
    )
    rows: list[tuple[str, tuple[float, float, float], float]] = []
    skipped = 0
    for sample, geometry in zip(samples, geometries, strict=True):
        found = sky_features(geometry)
        if found is None:
            skipped += 1
            continue
        rows.append((sample.night_id, found, sample.sky_rate_e_per_s_per_px))
    nights = sorted({row[0] for row in rows})
    if len(nights) < MINIMUM_NIGHTS_FOR_SKY_MODEL:
        return SkyModelOutcome(
            reason=(
                f"The sky model needs {MINIMUM_NIGHTS_FOR_SKY_MODEL} nights; "
                f"{len(nights)} have usable frames."
            ),
            frames_skipped=skipped,
        )
    if len(rows) < MINIMUM_FRAMES_FOR_SKY_MODEL:
        return SkyModelOutcome(
            reason=f"The sky model needs {MINIMUM_FRAMES_FOR_SKY_MODEL} frames; {len(rows)} are usable.",
            frames_skipped=skipped,
        )
    night_of = np.array([row[0] for row in rows])
    features = np.array([row[1] for row in rows], dtype=float)
    rates = np.array([row[2] for row in rows], dtype=float)
    moonlit_nights = {night for night, feature, _rate in rows if feature[2] >= MOON_TERM_MINIMUM}
    fit_moon = len(moonlit_nights) >= MINIMUM_MOON_NIGHTS
    coefficients = _fit_coefficients(features, rates, fit_moon=fit_moon)
    in_sample = float(math.sqrt(np.mean(_relative_errors(coefficients, features, rates) ** 2)))
    held_out_errors: list[np.ndarray] = []
    for night in nights:
        held = night_of == night
        if held.all() or (~held).sum() < 3:
            continue
        others_moonlit = {n for n, feature, _r in rows if n != night and feature[2] >= MOON_TERM_MINIMUM}
        partial = _fit_coefficients(
            features[~held], rates[~held], fit_moon=len(others_moonlit) >= MINIMUM_MOON_NIGHTS
        )
        held_out_errors.append(_relative_errors(partial, features[held], rates[held]))
    held_out = float(math.sqrt(np.mean(np.concatenate(held_out_errors) ** 2)))
    return SkyModelOutcome(
        model=SkyModel(
            setting_key=setting_key,
            constant=float(coefficients[0]),
            airmass_coefficient=float(coefficients[1]),
            moon_coefficient=float(coefficients[2]),
            moon_fitted=fit_moon,
            nights_used=len(nights),
            frames_used=len(rows),
            in_sample_relative_error=in_sample,
            held_out_relative_error=held_out,
        ),
        frames_skipped=skipped,
    )


def predict_sky_rate(model: SkyModel, geometry: SkyGeometry) -> SkyPrediction:
    """Predict the sky rate at a moment from the sky geometry.

    Find the geometry with `sky_geometry` (or `observing_values`) for the
    target, the time and the site, then call this.

    Parameters
    ----------
    model : `SkyModel`
        The model for the capture setting.
    geometry : `SkyGeometry`
        The target's and the Moon's place at the moment.

    Returns
    -------
    prediction : `SkyPrediction`
        The predicted rate and the rate without the Moon. Both are ``None``
        when the target is below the horizon.
    """
    features = sky_features(geometry)
    if features is None:
        return SkyPrediction(
            relative_error=model.held_out_relative_error, moon_included=model.moon_fitted, reliable=False
        )
    moonless = model.constant + model.airmass_coefficient * features[1]
    total = moonless + model.moon_coefficient * features[2]
    reliable = model.moon_fitted or features[2] < MOON_TERM_MINIMUM
    return SkyPrediction(
        sky_rate_e_per_s_per_px=total,
        moonless_sky_rate_e_per_s_per_px=moonless,
        relative_error=model.held_out_relative_error,
        moon_included=model.moon_fitted,
        reliable=reliable,
    )


def predict_sky_rate_at(
    model: SkyModel,
    epoch_seconds: float,
    ra_deg: float,
    dec_deg: float,
    latitude_deg: float,
    longitude_deg: float,
    elevation_m: float,
) -> SkyPrediction:
    """Predict the sky rate for a target, a time and a site.

    Parameters
    ----------
    model : `SkyModel`
        The model for the capture setting.
    epoch_seconds : `float`
        The time, in seconds since the Unix epoch.
    ra_deg, dec_deg : `float`
        The target, in degrees.
    latitude_deg, longitude_deg, elevation_m : `float`
        The observing site.

    Returns
    -------
    prediction : `SkyPrediction`
        See `predict_sky_rate`.
    """
    geometry = sky_geometry([epoch_seconds], [ra_deg], [dec_deg], latitude_deg, longitude_deg, elevation_m)[0]
    return predict_sky_rate(model, geometry)
