"""Purpose: Derive the Moon's extinction from the library's sky rates.

Description: The sky model (`sky_model`) predicts the sky background of a
frame from the target's airmass and the Moon's light. The Moon's light is
dimmed by its own path through the air, ``10^(-0.4 k X_moon)``, and the
coefficient k, in magnitudes per airmass, is a property of this site's air.

The model's own held-out error says how well a value of k works: the model
is fitted with a night left out and asked for that night's sky rates. This
module tries values of k between the parameter's allowed limits, takes for
each the frame-weighted mean of the held-out errors of the capture settings
whose Moon term could be fitted, and keeps the k with the smallest error. If
the error hardly changes between the best k and the worst limit, the sky
rates do not constrain k and no value is given.

Only the search is a pure function (`best_extinction`); the data come from the
library's stored sky rates through `LibraryInputs`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from scipy.optimize import minimize_scalar

from astrometricslib.parameters import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    Tuner,
    TuningInputs,
    TuningResult,
    TuningSkippedError,
    resolve_parameter,
)
from wayfindinglib.analytics.result_quality.efficiency import baseline_key
from wayfindinglib.analytics.result_quality.library_inputs import LibraryInputs
from wayfindinglib.analytics.result_quality.observing_values import sky_geometry
from wayfindinglib.analytics.result_quality.sky_model import (
    MOON_EXTINCTION_PER_AIRMASS,
    SkyModel,
    SkyRateSample,
    fit_sky_model,
)
from wayfindinglib.analytics.tuning_support import butler_of

__all__ = [
    "MINIMUM_ERROR_CONTRAST",
    "MOON_EXTINCTION_TUNER",
    "best_extinction",
    "weighted_mean_error",
]

MINIMUM_ERROR_CONTRAST = TunedParameter(
    name="wayfinding.minimum_error_contrast",
    default=0.01,
    unit="fraction",
    description="The held-out error must fall by this share, from the worse limit of k to the best k.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="WAY-D29",
)
"""The held-out error must fall by this share from the worse limit of k to the
best k. A smaller fall means the sky rates do not tell one k from another, and
a value picked from them would be noise. One percent of the error is about the
night-to-night scatter of a median over a few nights. Register entry
WAY-D29."""


def weighted_mean_error(errors: Sequence[float], weights: Sequence[float]) -> float:
    """Combine the held-out errors of several capture settings.

    Parameters
    ----------
    errors : `~collections.abc.Sequence` [`float`]
        The held-out relative error of each setting.
    weights : `~collections.abc.Sequence` [`float`]
        How many frames each setting's model used.

    Returns
    -------
    mean : `float`
        The frame-weighted mean error.
    """
    total = sum(weights)
    return sum(error * weight for error, weight in zip(errors, weights, strict=True)) / total


def best_extinction(
    objective: Callable[[float], float], low: float, high: float, minimum_contrast: float
) -> float | None:
    """Find the extinction coefficient with the smallest error.

    Parameters
    ----------
    objective : `~collections.abc.Callable` [[`float`], `float`]
        The error for a trial k.
    low, high : `float`
        The limits of the search.
    minimum_contrast : `float`
        The least relative fall of the error, from the larger of the two limit
        values to the best value, that counts as a minimum.

    Returns
    -------
    extinction : `float` or `None`
        The k with the smallest error, or `None` when the error is flat.
    """
    outcome = minimize_scalar(objective, bounds=(low, high), method="bounded")
    worst_at_limits = max(objective(low), objective(high))
    if worst_at_limits <= 0.0 or (worst_at_limits - float(outcome.fun)) / worst_at_limits < minimum_contrast:
        return None
    return float(outcome.x)


def tune_moon_extinction(inputs: TuningInputs) -> TuningResult:
    """Derive the Moon's extinction coefficient from the camera's sky rates.

    Parameters
    ----------
    inputs : `TuningInputs`
        The tuning run's inputs. Reads the ``"wayfinder"`` service (and,
        through it, the library's stored sky rates of the setup's camera).

    Returns
    -------
    result : `TuningResult`
        The coefficient k, resting on the nights of the setting with the
        fewest.

    Raises
    ------
    TuningSkippedError
        If no capture setting has enough moonlit nights, or the error does
        not depend on k.
    """
    wayfinder = inputs.service("wayfinder")
    library = LibraryInputs(wayfinder.astrometrics, butler_of(wayfinder), wayfinder.config)
    site = library.site
    by_key: dict[str, list[SkyRateSample]] = {}
    for observation in library.camera_observations(inputs.setup.camera_name):
        if (
            observation.set_aside_reason is None
            and observation.night_id is not None
            and observation.start_epoch_seconds is not None
            and observation.ra_deg is not None
            and observation.dec_deg is not None
            and observation.sky_rate_e_per_s_per_px
        ):
            by_key.setdefault(baseline_key(observation.capture_setting), []).append(
                SkyRateSample(
                    frame_id=observation.frame_id,
                    night_id=observation.night_id,
                    epoch_seconds=observation.start_epoch_seconds,
                    ra_deg=observation.ra_deg,
                    dec_deg=observation.dec_deg,
                    sky_rate_e_per_s_per_px=observation.sky_rate_e_per_s_per_px,
                )
            )
    geometries = {
        key: sky_geometry(
            [s.epoch_seconds for s in samples],
            [s.ra_deg for s in samples],
            [s.dec_deg for s in samples],
            site.latitude_deg,
            site.longitude_deg,
            site.elevation_m,
        )
        for key, samples in by_key.items()
    }

    def fitted_models(extinction: float) -> list[SkyModel]:
        """Fit each setting's model for a trial k.

        Parameters
        ----------
        extinction : `float`
            The trial k.

        Returns
        -------
        models : `list` [`SkyModel`]
            The models that could fit the Moon term.
        """
        models = []
        for key, samples in by_key.items():
            outcome = fit_sky_model(
                samples,
                key,
                site.latitude_deg,
                site.longitude_deg,
                site.elevation_m,
                extinction_per_airmass=extinction,
                geometries=geometries[key],
            )
            if outcome.model is not None and outcome.model.moon_fitted:
                models.append(outcome.model)
        return models

    default_models = fitted_models(resolve_parameter(MOON_EXTINCTION_PER_AIRMASS).value)
    if not default_models:
        raise TuningSkippedError("no capture setting of the camera has enough moonlit nights to fit the Moon")
    keys = {model.setting_key for model in default_models}

    def objective(extinction: float) -> float:
        """Score a trial k by the held-out error of the models.

        Parameters
        ----------
        extinction : `float`
            The trial k.

        Returns
        -------
        error : `float`
            The frame-weighted held-out relative error, or infinity when no
            model could fit the Moon term.
        """
        models = [model for model in fitted_models(extinction) if model.setting_key in keys]
        if not models:
            return float("inf")
        return weighted_mean_error(
            [model.held_out_relative_error for model in models], [model.frames_used for model in models]
        )

    low, high = MOON_EXTINCTION_PER_AIRMASS.allowed_range
    best = best_extinction(objective, low, high, resolve_parameter(MINIMUM_ERROR_CONTRAST).value)
    if best is None:
        raise TuningSkippedError("the held-out sky-rate error does not depend on the Moon's extinction")
    nights = min(model.nights_used for model in default_models)
    frames = sum(model.frames_used for model in default_models)
    return TuningResult(
        values={MOON_EXTINCTION_PER_AIRMASS.name: best},
        sample=nights,
        source=(
            f"The k with the smallest held-out sky-rate error of the sky model, over {frames} frames of "
            f"{len(default_models)} capture setting(s) of {inputs.setup.camera_name}; the sample is the "
            "nights of the setting with the fewest."
        ),
    )


MOON_EXTINCTION_TUNER = Tuner(
    name="wayfinding.moon_extinction",
    parameters=(MOON_EXTINCTION_PER_AIRMASS,),
    method=(
        "Fits the sky model to the stored sky rates of the camera with trial values of the Moon's "
        "extinction coefficient and keeps the value with the smallest error on nights left out of the fit. "
        "Gives no value when the error is flat."
    ),
    tune=tune_moon_extinction,
)
