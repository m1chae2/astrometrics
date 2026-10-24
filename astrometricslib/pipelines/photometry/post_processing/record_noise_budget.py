"""Turn a run's noise model into quality measurements.

There is one measurement for each noise source at each stated magnitude.

The photometry run saves the error of every point but not the reasons for it.
This module writes the reasons down (see `processing/noise_budget.py`): for a
star of each stated catalog magnitude, the noise that photon counting, the
sky and the camera's read-out each add, and the scintillation of the air. They
are stored separately and are not summed; a reader who wants the total adds
the variances.

Scintillation needs facts that a run only has if its inputs carry them: the
aperture of the telescope, the airmass in the frame headers, and the height of
the site. A fact that is missing leaves the value out with the reason. The
airmass is never taken from a default, because the photometry's own airmass
list holds 1.0 for a frame whose header had none. The site height is not in
the configuration (it holds 0 for "unknown"), so scintillation is stored for a
site at sea level and marked assumed. No bound is claimed: a real site is
higher, which lowers the value, and measured coefficients run about 1.5
times Young's.

A value that could not be found is a ``not_measured`` record with the reason,
never a default.
"""

import logging
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from astrometricslib.models.quality.measurement import MeasurementStatus, QualityMeasurement
from astrometricslib.pipelines.photometry.metrics import (
    NOISE_MODEL_MAGNITUDES,
    NOISE_MODEL_PARTS,
    PHOTOMETRY_METRICS,
    PHOTOMETRY_MODEL_NOISE_SCINTILLATION_MAG,
    model_noise_metric_name,
)
from astrometricslib.pipelines.photometry.processing.catalog_zero_point import CatalogBand, SessionZeroPoints
from astrometricslib.pipelines.photometry.processing.noise_budget import (
    MAGNITUDES_PER_FRACTION,
    NoiseBudgetInputs,
    NoiseVariances,
    SessionNoiseInputs,
    magnitude_noise,
    median_header_airmass,
    noise_variances_at_magnitude,
    scintillation_relative_noise,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_and_record_measurements,
    build_measurement,
)

logger = logging.getLogger(__name__)

_SPEC_BY_NAME = {spec.name: spec for spec in PHOTOMETRY_METRICS}

GAIN_ASSUMED_REASON = (
    "the flux errors were made with a gain of 1 e-/ADU because neither the camera profile nor the "
    "frame header gave one, so the electron counts behind this value are a guess"
)
ELEVATION_UNKNOWN_REASON = (
    "the site height is not known (the configuration holds 0 for unknown), so a site at sea level is "
    "assumed; the real height would lower the value by about 12 percent for each kilometer, and "
    "measured coefficients run about 1.5 times Young's, so no bound is claimed"
)
READ_NOISE_ASSUMED_REASON = (
    "the read noise was assumed to be zero because neither the camera profile nor the frame header gave "
    "one, so the sky and read terms of the flux errors cannot be told apart"
)
NEGATIVE_SKY_REASON = (
    "the saved flux errors carry less background variance than the camera's read noise alone gives, so "
    "the read noise or the aperture geometry does not match how the errors were made"
)


def _why_no_budget(inputs: NoiseBudgetInputs) -> str:
    """Say why the photon, sky and read terms cannot be found.

    Parameters
    ----------
    inputs : `NoiseBudgetInputs`
        The run's noise inputs.

    Returns
    -------
    reason : `str`
        The first missing input, in words.
    """
    session = inputs.session
    if session is None:
        return "no session of the run had light-curve points with saved flux errors"
    if inputs.zero_point_mag is None:
        return (
            inputs.zero_point_reason
            or "the run has no catalog zero point, so a magnitude cannot be turned into a flux"
        )
    if session.exposure_seconds is None:
        return "the session's frames gave no exposure time"
    return "no point of the session's light curves had a positive flux error"


def _scintillation_relative(inputs: NoiseBudgetInputs) -> tuple[float | None, str]:
    """Find the scintillation as a fraction of flux, or why it is missing.

    Parameters
    ----------
    inputs : `NoiseBudgetInputs`
        The run's noise inputs.

    Returns
    -------
    relative_noise : `float` or `None`
        The fractional noise, or `None`.
    reason : `str`
        Why it is `None`. Empty when it is not.
    """
    session = inputs.session
    if session is None or session.exposure_seconds is None:
        return None, "the session's frames gave no exposure time"
    if inputs.aperture_diameter_mm is None:
        return None, inputs.aperture_reason or "the telescope's aperture is not known"
    if inputs.airmass is None:
        return None, inputs.airmass_reason or "no frame header gave an airmass"
    return (
        scintillation_relative_noise(
            inputs.aperture_diameter_mm,
            inputs.airmass,
            session.exposure_seconds,
            inputs.site_elevation_m if inputs.site_elevation_m is not None else 0.0,
        ),
        "",
    )


def _assumptions(inputs: NoiseBudgetInputs, *, needs_gain: bool, needs_elevation: bool) -> list[str]:
    """List the assumptions behind a value, in words.

    Parameters
    ----------
    inputs : `NoiseBudgetInputs`
        The run's noise inputs.
    needs_gain : `bool`
        Whether the value depends on the camera gain.
    needs_elevation : `bool`
        Whether the value depends on the site height.

    Returns
    -------
    assumptions : `list` [`str`]
        One sentence for each assumption that applies.
    """
    found: list[str] = []
    session = inputs.session
    if needs_gain and session is not None and session.gain_is_assumed:
        found.append(GAIN_ASSUMED_REASON)
    if needs_elevation and inputs.site_elevation_m is None:
        found.append(ELEVATION_UNKNOWN_REASON)
    return found


_BAND_NAMES = {CatalogBand.GAIA_G: "Gaia G", CatalogBand.SIMBAD_V: "SIMBAD V"}


def site_elevation_m(configuration: Any) -> float | None:
    """Read the site's height above sea level from the configuration.

    Parameters
    ----------
    configuration : `AppConfiguration`
        The configuration, offering ``get_value(section, key, fallback)``.

    Returns
    -------
    elevation_m : `float` or `None`
        The height in meters, or `None` when it is absent, not a number or
        not above zero. The configuration holds 0 for "unknown" ("Elevation
        is unknown, so it is 0"), so 0 is read as unknown and never as sea
        level.
    """
    try:
        value = float(configuration.get_value("Observatory.Location", "elevation", "0"))
    except TypeError, ValueError:
        return None
    return value if np.isfinite(value) and value > 0 else None


def aperture_diameter_mm(run_frames: Sequence[Any], configuration: Any) -> tuple[float | None, str]:
    """Find the aperture diameter of the optic the run's frames used.

    Parameters
    ----------
    run_frames : `Sequence`
        The run's frame records; each has a ``telescope`` name.
    configuration : `AppConfiguration`
        The configuration, whose optics list a focal length and focal ratio.

    Returns
    -------
    diameter_mm : `float` or `None`
        The focal length over the focal ratio of the most common optic, in
        millimeters. `None` when it cannot be found.
    reason : `str`
        Why it is `None`. Empty when it is not.
    """
    names = Counter(str(getattr(frame, "telescope", "")) for frame in run_frames)
    names.pop("Unknown", None)
    names.pop("", None)
    if not names:
        return None, "the run's frames name no telescope"
    optic_name = names.most_common(1)[0][0]
    optic = configuration.get_observatory_setups().optic_named(optic_name)
    if optic is None:
        return None, f"the optic {optic_name!r} is not listed in the configuration"
    if optic.focal_ratio is None:
        return None, f"the configuration gives no focal ratio for {optic_name!r}"
    return optic.focal_length_mm / optic.focal_ratio, ""


def dominant_session(sessions: Sequence[SessionNoiseInputs]) -> SessionNoiseInputs | None:
    """Pick the session with the most light-curve points.

    Parameters
    ----------
    sessions : `Sequence` [`SessionNoiseInputs`]
        The run's sessions.

    Returns
    -------
    session : `SessionNoiseInputs` or `None`
        The session with the most points that has any, or `None`.
    """
    candidates = [session for session in sessions if session.point_count > 0]
    return max(candidates, key=lambda session: session.point_count) if candidates else None


def session_zero_point(
    session_id: str, session_zero_points: Sequence[SessionZeroPoints]
) -> tuple[float | None, int, str, str]:
    """Find the median frame zero point of one session.

    Parameters
    ----------
    session_id : `str`
        The session's id.
    session_zero_points : `Sequence` [`SessionZeroPoints`]
        The zero points of each session.

    Returns
    -------
    zero_point_mag : `float` or `None`
        The median of the session's frame zero points.
    frame_count : `int`
        How many frames it is the median of.
    reason : `str`
        Why there is none. Empty when there is one.
    band_name : `str`
        The catalog band, in words.
    """
    record = next((item for item in session_zero_points if item.session_id == session_id), None)
    if record is None:
        return None, 0, "the session has no catalog zero point record", ""
    values = [
        frame.estimate.zero_point_mag for frame in record.frames if frame.estimate.zero_point_mag is not None
    ]
    if not values:
        return None, 0, record.band_reason or "no frame of the session has a zero point", ""
    band_name = _BAND_NAMES.get(record.band, "") if record.band is not None else ""
    return float(np.median(values)), len(values), "", band_name


def noise_budget_inputs(
    session_inputs: Sequence[SessionNoiseInputs],
    session_zero_points: Sequence[SessionZeroPoints],
    run_frames: Sequence[Any],
    configuration: Any,
) -> NoiseBudgetInputs:
    """Gather what the noise model of one run needs.

    Parameters
    ----------
    session_inputs : `Sequence` [`SessionNoiseInputs`]
        What each session's frames say about their own noise.
    session_zero_points : `Sequence` [`SessionZeroPoints`]
        The zero points of each session.
    run_frames : `Sequence`
        The frame records of the frames the run measured. Each has a
        ``telescope`` name and an ``airmass`` (`None` when the header had
        none).
    configuration : `AppConfiguration`
        The configuration: the optics and the site height.

    Returns
    -------
    inputs : `NoiseBudgetInputs`
        The inputs, with a reason for each that is missing.
    """
    session = dominant_session(session_inputs)
    zero_point, zero_point_count, zero_point_reason, band_name = (
        (None, 0, "no session had saved flux errors", "")
        if session is None
        else session_zero_point(session.session_id, session_zero_points)
    )
    diameter, aperture_reason = aperture_diameter_mm(run_frames, configuration)
    airmass, airmass_count = median_header_airmass([getattr(frame, "airmass", None) for frame in run_frames])
    return NoiseBudgetInputs(
        session=session,
        zero_point_mag=zero_point,
        zero_point_frame_count=zero_point_count,
        zero_point_reason=zero_point_reason,
        band_name=band_name,
        aperture_diameter_mm=diameter,
        aperture_reason=aperture_reason,
        airmass=airmass,
        airmass_frame_count=airmass_count,
        airmass_reason="" if airmass is not None else "no frame header of the run gave an airmass",
        site_elevation_m=site_elevation_m(configuration),
    )


def noise_budget_measurements(
    inputs: NoiseBudgetInputs | None, context: PipelineRunContext
) -> list[QualityMeasurement]:
    """Build every noise-model measurement of one run.

    Parameters
    ----------
    inputs : `NoiseBudgetInputs` or `None`
        The run's noise inputs. `None` stands for a run that gathered none.
    context : `PipelineRunContext`
        The run's shared facts.

    Returns
    -------
    measurements : `list` [`QualityMeasurement`]
        One measurement for each noise source at each stated magnitude, the
        scintillation and its share. A value that could not be found is
        ``not_measured`` with the reason; a value that rests on an
        assumption is ``assumed`` and the reason names the assumption.
    """
    subject = context.run_subject()
    if inputs is None:
        inputs = NoiseBudgetInputs(session=None)
    point_count = inputs.session.point_count if inputs.session is not None else None
    measurements: list[QualityMeasurement] = []

    variances_by_magnitude: dict[int, NoiseVariances | None] = {
        magnitude: noise_variances_at_magnitude(float(magnitude), inputs)
        for magnitude in NOISE_MODEL_MAGNITUDES
    }
    for magnitude in NOISE_MODEL_MAGNITUDES:
        variances = variances_by_magnitude[magnitude]
        for part in NOISE_MODEL_PARTS:
            value: float | None = None
            reason = _why_no_budget(inputs)
            if variances is not None:
                part_variance = {
                    "photon": variances.photon_e2,
                    "sky": variances.sky_e2,
                    "read": variances.read_e2,
                }[part]
                if part_variance is None:
                    reason = (
                        READ_NOISE_ASSUMED_REASON
                        if inputs.session is not None and inputs.session.read_noise_is_assumed
                        else NEGATIVE_SKY_REASON
                    )
                else:
                    value = magnitude_noise(part_variance, variances.star_electrons)
            assumptions = _assumptions(inputs, needs_gain=True, needs_elevation=False)
            measurements.append(
                build_measurement(
                    _SPEC_BY_NAME[model_noise_metric_name(part, magnitude)],
                    value,
                    context,
                    subject,
                    reason="; ".join(assumptions) if value is not None else reason,
                    sample_count=point_count,
                    status=MeasurementStatus.ASSUMED if assumptions else MeasurementStatus.MEASURED,
                    provenance=f"band: {inputs.band_name}" if inputs.band_name else "",
                )
            )

    relative, scintillation_reason = _scintillation_relative(inputs)
    scintillation_assumptions = _assumptions(inputs, needs_gain=False, needs_elevation=True)
    measurements.append(
        build_measurement(
            _SPEC_BY_NAME[PHOTOMETRY_MODEL_NOISE_SCINTILLATION_MAG],
            None if relative is None else MAGNITUDES_PER_FRACTION * relative,
            context,
            subject,
            reason="; ".join(scintillation_assumptions) if relative is not None else scintillation_reason,
            sample_count=inputs.airmass_frame_count or None,
            status=MeasurementStatus.ASSUMED if scintillation_assumptions else MeasurementStatus.MEASURED,
            provenance=(
                f"airmass {inputs.airmass:.2f} (median of {inputs.airmass_frame_count} frame headers), "
                f"aperture {inputs.aperture_diameter_mm:.1f} mm"
                if relative is not None
                else ""
            ),
        )
    )
    return measurements


def record_noise_budget_quality(
    inputs_builder: Callable[[], NoiseBudgetInputs | None],
    context: PipelineRunContext,
    *,
    config: object = None,
) -> int:
    """Store the run's noise-model measurements; no failure here fails the run.

    Parameters
    ----------
    inputs_builder : `Callable`
        Builds the run's `NoiseBudgetInputs`. It is called inside the guarded
        step, so an error in gathering the inputs is logged and never fails
        the run.
    context : `PipelineRunContext`
        The run's shared facts.
    config : `AppConfiguration`, optional
        Where the quality store lives. The process-wide one if omitted.

    Returns
    -------
    new_count : `int`
        How many rows were new. It is 0 when anything went wrong (logged).
    """
    return build_and_record_measurements(
        lambda: noise_budget_measurements(inputs_builder(), context),
        f"photometry noise model on {context.target_id}",
        config=config,
    )


__all__ = [
    "aperture_diameter_mm",
    "dominant_session",
    "noise_budget_inputs",
    "noise_budget_measurements",
    "record_noise_budget_quality",
    "session_zero_point",
    "site_elevation_m",
]
