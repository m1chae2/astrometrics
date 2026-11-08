"""Purpose: Predict the sky background rate from the library's own frames.

Description: Finds the sky model of the active telescope and camera (fitted
by `analytics.result_quality.sky_model` to the sky rates of the library's
frames) and predicts the rate for a pointing and a time. Nothing here writes.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING

from astrometricslib.exceptions import ConfigurationError, InvalidArgumentError
from wayfindinglib.analytics.capture_history import equipment_spellings, normalize_name
from wayfindinglib.analytics.result_quality.efficiency import baseline_key
from wayfindinglib.analytics.result_quality.forecast import camera_gain_for_setting
from wayfindinglib.analytics.result_quality.library_inputs import LibraryInputs
from wayfindinglib.analytics.result_quality.sky_model import predict_sky_rate_at
from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog
from wayfindinglib.models.session.sky_rate_prediction import SkyRatePrediction

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext


def get_sky_rate_prediction(
    context: ControlContext,
    epoch_seconds: float,
    ra_deg: float,
    dec_deg: float,
    filter_name: str | None = None,
    exclude_nights: Collection[str] | None = None,
) -> SkyRatePrediction:
    """Predict the sky rate of the active equipment for a pointing and time.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the active equipment, the science library and the site.
    epoch_seconds : `float`
        The time, in seconds since the Unix epoch.
    ra_deg, dec_deg : `float`
        The pointing, in degrees.
    filter_name : `str`, optional
        The filter. Of the equipment's capture settings, the one with the
        most fitted nights is used when omitted.
    exclude_nights : `Collection` [`str`], optional
        Nights whose frames are left out of the model fit.

    Returns
    -------
    prediction : `SkyRatePrediction`
        The rate, or a result without one whose message says why: no active
        equipment, no frames of it, too few nights, or a target below the
        horizon.

    Raises
    ------
    InvalidArgumentError
        If the declination is outside -90 to 90 degrees.
    """
    if not -90.0 <= dec_deg <= 90.0:
        raise InvalidArgumentError("dec_deg must be between -90 and 90 degrees.")
    excluded = sorted(set(exclude_nights or ()))
    catalog = get_equipment_catalog(context.config)
    telescope, camera = catalog.active_telescope(), catalog.active_camera()
    if telescope is None or camera is None:
        return SkyRatePrediction(
            nights_excluded=excluded, message="No imaging telescope and camera are active."
        )
    astrometrics = context.astrometrics
    camera_names = equipment_spellings(astrometrics, camera.name)
    telescope_name = normalize_name(telescope.name)
    representatives = {}
    for setting in astrometrics.quality.capture_settings().values():
        if normalize_name(setting.camera) not in camera_names:
            continue
        if normalize_name(setting.telescope) != telescope_name:
            continue
        if filter_name is not None and setting.filter_name != filter_name:
            continue
        representatives.setdefault(baseline_key(setting), setting)
    if not representatives:
        return SkyRatePrediction(
            nights_excluded=excluded,
            message="The library has no frames of this telescope, camera and filter.",
        )
    inputs = LibraryInputs(astrometrics, context.butler, context.config)
    best = None
    reasons = []
    for key, setting in sorted(representatives.items()):
        model, reason = inputs.sky_model_for(setting, excluded)
        if model is None:
            reasons.append(f"{key}: {reason}")
        elif best is None or model.frames_used > best[1].frames_used:
            best = (setting, model)
    if best is None:
        return SkyRatePrediction(nights_excluded=excluded, message=" ".join(reasons))
    setting, model = best
    site = inputs.site
    prediction = predict_sky_rate_at(
        model, epoch_seconds, ra_deg, dec_deg, site.latitude_deg, site.longitude_deg, site.elevation_m
    )
    try:
        gain = camera_gain_for_setting(astrometrics, setting)
    except ConfigurationError:
        gain = None
    rate = prediction.sky_rate_e_per_s_per_px
    return SkyRatePrediction(
        sky_rate_e_per_s_per_px=rate,
        moonless_sky_rate_e_per_s_per_px=prediction.moonless_sky_rate_e_per_s_per_px,
        sky_rate_adu_per_s_per_px=rate / gain if rate is not None and gain else None,
        electrons_per_adu=gain,
        setting_key=model.setting_key,
        relative_error=prediction.relative_error,
        moon_included=prediction.moon_included,
        reliable=prediction.reliable,
        nights_used=model.nights_used,
        nights_excluded=excluded,
        message=(
            f"Sky model {model.setting_key} from {model.nights_used} night(s)."
            if rate is not None
            else "The target is below the horizon at that time."
        ),
    )
