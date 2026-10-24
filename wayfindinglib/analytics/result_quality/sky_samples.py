"""Purpose: Gather the sky rates of the user's frames and fit the sky model.

Description: The sky model (`sky_model`) is fitted to the sky rate of every
measured frame of the user's own library, with the time and the place of each
frame. Two readers need that fit: tonight's ranking (Planning) and the night
review's Moon share (Control). Both are served here, in Foundation, so neither
imports the other.

A frame's time is the middle of its exposure, from the frame record. Its place
is the position of its target, because the frame record keeps no pointing of
its own. A frame with no time, no sky rate, no capture setting or no night, and
a target with no verified position, are left out. The fit itself, its limits
and its error are in `sky_model`.
"""

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence

from astrometricslib import (
    CaptureSetting,
    QualityMeasurement,
    Target,
    frame_subject_id,
    parse_coordinate_string,
)
from wayfindinglib.analytics.result_quality.efficiency import (
    PINNED_METRIC_VERSIONS,
    SKY_RATE_METRIC,
    FrameObservation,
    baseline_key,
)
from wayfindinglib.analytics.result_quality.sky_model import (
    SkyModel,
    SkyModelOutcome,
    SkyRateSample,
    fit_sky_model,
    predict_sky_rate_at,
)


def frame_exposure_seconds(exposure_text: str) -> float | None:
    """Read a frame record's exposure text as seconds.

    Parameters
    ----------
    exposure_text : `str`
        The frame record's ``exposure`` field.

    Returns
    -------
    seconds : `float` or `None`
        The exposure, or ``None`` when it is not a positive number.
    """
    try:
        seconds = float(exposure_text)
    except TypeError, ValueError:
        return None
    return seconds if seconds > 0.0 and math.isfinite(seconds) else None


def frame_times(target: Target) -> dict[str, float]:
    """Give the middle of each light frame's exposure.

    Parameters
    ----------
    target : `Target`
        The library target.

    Returns
    -------
    times : `dict` [`str`, `float`]
        Seconds since the Unix epoch, by frame id. A frame with no time stamp
        is left out.
    """
    times = {}
    for frame in target.frames:
        if frame.role.upper() != "LIGHT" or frame.timestamp is None:
            continue
        half = (frame_exposure_seconds(frame.exposure) or 0.0) / 2.0
        times[frame_subject_id(frame.path, target.id)] = float(frame.timestamp) + half
    return times


def target_direction(target: Target) -> tuple[float, float] | None:
    """Read a target's position in degrees.

    Parameters
    ----------
    target : `Target`
        The library target.

    Returns
    -------
    direction : `tuple` [`float`, `float`] or `None`
        Right ascension and declination, or ``None`` when the target's
        position is unverified or cannot be read.
    """
    if target.position_unverified:
        return None
    try:
        return (
            float(parse_coordinate_string(target.ra, is_ra=True)),
            float(parse_coordinate_string(target.dec, is_ra=False)),
        )
    except ValueError, TypeError:
        return None


def collect_sky_samples(
    targets: Iterable[Target],
    sky_rows: Iterable[QualityMeasurement],
    capture_settings: Mapping[int, CaptureSetting],
) -> dict[str, list[SkyRateSample]]:
    """Gather the sky rate of every measured frame with its time and place.

    Parameters
    ----------
    targets : `Iterable` [`Target`]
        The library targets, for their frames' times and positions.
    sky_rows : `Iterable` [`QualityMeasurement`]
        The newest ``frame.sky_rate_e_per_s_per_px`` row of each frame
        (``quality.query(metric=...)``).
    capture_settings : `Mapping` [`int`, `CaptureSetting`]
        The capture settings by id.

    Returns
    -------
    samples : `dict` [`str`, `list` [`SkyRateSample`]]
        The samples by equipment key (`baseline_key`). Rows that are not
        measured, are of another definition version, have no capture setting,
        no night, no time or no target position are left out.
    """
    pinned = PINNED_METRIC_VERSIONS[SKY_RATE_METRIC]
    rows_by_frame: dict[str, QualityMeasurement] = {}
    for row in sky_rows:
        if row.metric != SKY_RATE_METRIC or row.definition_version != pinned or row.value is None:
            continue
        rows_by_frame.setdefault(row.subject.subject_id, row)
    samples: dict[str, list[SkyRateSample]] = defaultdict(list)
    for target in targets:
        direction = target_direction(target)
        if direction is None:
            continue
        for frame_id, epoch in frame_times(target).items():
            row = rows_by_frame.get(frame_id)
            if row is None or row.value is None or row.value <= 0.0:
                continue
            setting_id = row.subject.capture_setting_id
            setting = capture_settings.get(setting_id) if setting_id is not None else None
            night = row.subject.observing_night_id
            if setting is None or night is None:
                continue
            samples[baseline_key(setting)].append(
                SkyRateSample(
                    frame_id=frame_id,
                    night_id=night,
                    epoch_seconds=epoch,
                    ra_deg=direction[0],
                    dec_deg=direction[1],
                    sky_rate_e_per_s_per_px=row.value,
                )
            )
    return dict(samples)


def fit_sky_models(
    samples: Mapping[str, Sequence[SkyRateSample]],
    latitude_deg: float,
    longitude_deg: float,
    elevation_m: float,
) -> tuple[dict[str, SkyModel], list[str]]:
    """Fit one sky model for each equipment setting.

    Parameters
    ----------
    samples : `Mapping` [`str`, `Sequence` [`SkyRateSample`]]
        The samples by equipment key (`collect_sky_samples`).
    latitude_deg, longitude_deg, elevation_m : `float`
        The observing site.

    Returns
    -------
    models, unavailable : `tuple`
        The fitted models by key, and one sentence for each key that could
        not be fitted, with the reason.
    """
    models: dict[str, SkyModel] = {}
    unavailable: list[str] = []
    for key, key_samples in sorted(samples.items()):
        outcome: SkyModelOutcome = fit_sky_model(
            list(key_samples), key, latitude_deg, longitude_deg, elevation_m
        )
        if outcome.model is not None:
            models[key] = outcome.model
        else:
            unavailable.append(f"{key}: {outcome.reason}")
    return models, unavailable


def moonless_sky_rates(
    observations: Iterable[FrameObservation],
    models: Mapping[str, SkyModel],
    latitude_deg: float,
    longitude_deg: float,
    elevation_m: float,
) -> dict[str, float]:
    """Predict what each frame's sky rate would have been with no Moon.

    The night review uses these to split the sky-brightness loss into the
    Moon's share and the rest (labeled model-derived).

    Parameters
    ----------
    observations : `Iterable` [`FrameObservation`]
        The frames. A frame with no time, no position or no model for its
        equipment is left out.
    models : `Mapping` [`str`, `SkyModel`]
        The fitted sky models by equipment key.
    latitude_deg, longitude_deg, elevation_m : `float`
        The observing site.

    Returns
    -------
    rates : `dict` [`str`, `float`]
        The no-Moon sky rate by frame id, in electrons per second per pixel.
    """
    rates: dict[str, float] = {}
    for observation in observations:
        model = models.get(baseline_key(observation.capture_setting))
        if (
            model is None
            or observation.start_epoch_seconds is None
            or observation.ra_deg is None
            or observation.dec_deg is None
        ):
            continue
        prediction = predict_sky_rate_at(
            model,
            observation.start_epoch_seconds,
            observation.ra_deg,
            observation.dec_deg,
            latitude_deg,
            longitude_deg,
            elevation_m,
        )
        if prediction.moonless_sky_rate_e_per_s_per_px is not None:
            rates[observation.frame_id] = prediction.moonless_sky_rate_e_per_s_per_px
    return rates


__all__ = [
    "collect_sky_samples",
    "fit_sky_models",
    "frame_exposure_seconds",
    "frame_times",
    "moonless_sky_rates",
    "target_direction",
]
