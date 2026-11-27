"""Predicts how faint a Gaia query must go, from the equipment.

Identifying the stars of a frame needs Gaia stars as faint as the frame
shows. A query that stops too bright leaves faint stars without a match. A
query that goes too deep costs time and cache space. This module works out
the depth from the equipment and the exposure, so it does not depend on
SIMBAD matches or on bright, saturated stars.

The model asks when a star is "detected": a star counts when its light in a
small circle is at least ``k`` times the noise in that circle. ``k`` is
`DETECTION_SIGNAL_TO_NOISE`, fitted to where the star finder really finds half
the Gaia stars (the finder's own threshold of 5 is set on a smoothed image and
means a larger ``k`` in the circle). It has three steps.

1. **Star light.** A star of Gaia G magnitude ``G`` sends ``R0 * 10^(-0.4 G)``
   electrons per second to the sensor, where ``R0`` is the electron rate of a
   star at ``G = 0``::

       R0 = photon_rate_G0 * area * (1 - obstruction^2) * throughput * QE

   ``photon_rate_G0`` is the photon rate of a ``G = 0`` star in the Gaia G
   band, per square centimeter. ``area`` is the aperture's open area,
   ``obstruction`` is the share of the aperture's diameter blocked at the
   center (zero for a refractor), ``throughput`` is the share of light that
   passes lenses, mirrors and filters, and ``QE`` is the camera's quantum
   efficiency (the share of photons that make an electron), averaged over the
   G band from the camera profile's curve.
2. **Noise.** The circle has radius equal to the star's width (FWHM), the
   same choice as `depth_limits.py`, so it holds ``n = pi * r^2`` pixels. For
   a stack the noise is that of the rate (electrons per second), with the same
   two times as `expected_noise.py`: ``T`` is the effective exposure (the
   photon-noise time) and ``tau`` is the read-noise time (the length of a
   single frame with the same read noise as the stack). With a star's
   electrons ``C = r * T`` and a sky rate ``s`` per pixel, the variance of
   the circle's total is::

       variance = C + n * (s * T + RN^2 * T^2 / tau^2)

   For N equal frames of ``t`` seconds, ``T = N * t`` and
   ``tau = t * sqrt(N)``, so the read term is ``N * RN^2`` per pixel.
3. **The limit.** Setting ``C / sqrt(variance) = k`` gives a quadratic in
   ``C``: ``C = (k^2 + sqrt(k^4 + 4 k^2 B)) / 2``, where ``B`` is the noise
   term for the empty circle, ``n * (s * T + RN^2 * T^2 / tau^2)``. The limit
   is ``G_lim = -2.5 log10(C / (T * R0))``.

The query depth is ``G_lim`` plus a margin, never deeper than a cap (Gaia's
catalog is complete only to about G 20.7).

`depth_limits.py` does not give this prediction. It works backward from a
stack's measured noise and a zero point from the stack's own stars, so it
needs the finished stack. The two share the radius, and nothing is
duplicated.

`predict_gaia_depth_from_header` gathers the inputs from an image's FITS
header (camera, aperture, pixel scale, exposure and frame count) and the
camera profiles, for the star identifier. It reads the camera profiles and
changes nothing. When an input is missing, the prediction says why and gives
the offline default depth.
"""

import math
from dataclasses import dataclass
from typing import Any, TypeGuard

import numpy as np

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.camera_profile import CameraProfile
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)

__all__ = [
    "GAIA_BAND_PHOTON_RATE_G0",
    "GAIA_DEPTH_CAP",
    "GAIA_DEPTH_MARGIN",
    "GaiaDepthPrediction",
    "depth_exceeded",
    "predict_gaia_depth_from_header",
    "predict_gaia_query_depth",
]

# The photon rate of a G = 0 star in the Gaia G band, per square centimeter
# per second. Derived from the Gaia DR3 G passband entry of the SVO Filter
# Profile Service: a Vega-system zero-point flux of 2.54e-9 erg/s/cm^2/A, an
# equivalent width of 4053 A and a reference wavelength of 6218 A. The
# energy of one photon there is h*c/lambda = 3.19e-12 erg, so the rate is
# 2.54e-9 * 4053 / 3.19e-12 = 3.2e6 photons/s/cm^2. The passband's peak is
# scaled to 1, so the optics and the camera's QE are applied on top of it.
# A design estimate, not yet checked against a star on a real stack.
GAIA_BAND_PHOTON_RATE_G0 = TunedParameter(
    name="astrometry.gaia_band_photon_rate_g0",
    default=3.2e6,
    unit="photons/s/cm^2",
    description="Photon rate of a Gaia G = 0 star in the G band, per square centimeter of aperture.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.PUBLISHED_CONSTANT,
    register_entry="AST-D21",
)

# The G band is taken as 400 to 850 nm, where the Gaia G response is above
# half of its peak (Jordi et al. 2010; Riello et al. 2021). The camera's QE
# is averaged over this range with equal weight, which matches the
# equivalent width of 405 nm used for the photon rate above.
GAIA_BAND_LOWER_NM = TunedParameter(
    name="astrometry.gaia_band_lower_nm",
    default=400.0,
    unit="nanometers",
    description="Short-wavelength end of the band over which the camera's QE is averaged.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.PUBLISHED_CONSTANT,
    register_entry="AST-D21",
)
GAIA_BAND_UPPER_NM = TunedParameter(
    name="astrometry.gaia_band_upper_nm",
    default=850.0,
    unit="nanometers",
    description="Long-wavelength end of the band over which the camera's QE is averaged.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.PUBLISHED_CONSTANT,
    register_entry="AST-D21",
)

# The share of the G-band light that gets through the lenses (or mirrors),
# the filter and the camera window. Measured on 12 ASI 533MM stacks through
# the 75 mm refractor with the L filter (2026-11-20, AST-D21): the electrons
# per second of a G = 0 star, from the Gaia stars of G 13 to 16 matched in
# each stack, had a median of 5.5e7 (3.3e7 to 9.1e7) against 7.56e7 from
# the old value 0.85, so the value is 0.62. It is lower than the optics alone
# because the L filter cuts the G band above about 700 nm.
OPTICS_THROUGHPUT = TunedParameter(
    name="astrometry.gaia_depth_optics_throughput",
    default=0.62,
    unit="fraction",
    description="Share of the light that passes the telescope's optics and filter.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-D21",
)

# The star width (FWHM) used when the image gives none. Measured: the ASI
# 533MM deep-sky L stacks have star widths of 2.19 to 3.10 px, median 2.6 px,
# at 1.92 arcsec per pixel, so 5.0 arcsec (AST-D21, 2026-11-20).
DEFAULT_FWHM_ARCSEC = TunedParameter(
    name="astrometry.gaia_depth_default_fwhm_arcsec",
    default=5.0,
    unit="arcsec",
    description="Star width assumed for the depth prediction when no width is measured.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-D21",
)

# The sky brightness used when no sky rate is measured. Measured: the median
# frame sky of the ASI 533MM stacks is 6.5 electrons per second per pixel,
# which with the measured G = 0 rate of 5.5e7 electrons per second is G 18.7
# per square arcsecond (range 17.9 to 19.1; AST-D21, 2026-11-20). This is
# brighter than a dark site in V because the G band reaches far into the red,
# where the night sky is bright. A measured sky rate replaces it.
FALLBACK_SKY_MAG_PER_ARCSEC2 = TunedParameter(
    name="astrometry.gaia_depth_fallback_sky_mag_per_arcsec2",
    default=18.7,
    unit="magnitudes/arcsec^2",
    description="Sky brightness assumed for the depth prediction when no sky rate is measured.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-D21",
)

# The query goes this far past the predicted limit, so stars at the edge of
# detection, and stars that a longer exposure than predicted reaches, still
# have a catalog partner. 1 magnitude matches the margin the SIMBAD-based
# estimate already used (`GAIA_MAGNITUDE_LIMIT_MARGIN`). A design estimate.
GAIA_DEPTH_MARGIN = TunedParameter(
    name="astrometry.gaia_depth_margin",
    default=1.0,
    unit="magnitudes",
    description="Magnitudes added to the predicted limit to set the Gaia query depth.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-D21",
)

# No query goes deeper than this. The Gaia catalog is complete only to about
# G 20.7 (Gaia Collaboration 2023), and a deeper query returns mostly
# noise-level sources and very many rows. A design estimate.
GAIA_DEPTH_CAP = TunedParameter(
    name="astrometry.gaia_depth_cap",
    default=21.0,
    unit="magnitude",
    description="Deepest Gaia G magnitude a query may ask for.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-D21",
)

# The signal-to-noise a star must reach, in a circle of radius one FWHM, for
# the star finder to find it. The finder's own threshold is 5, but it is set
# on a smoothed image whose kernel is cut and made zero-mean, which gives
# about 0.66 times the signal-to-noise of the circle; the stack's noise is
# also about 1.15 times what the frames predict, and the shape cuts drop some
# faint stars. Fitted on the 50 percent completeness of isolated Gaia stars
# in three ASI 533MM stacks (NGC 7331, M 31, M 101; AST-D21, 2026-11-20):
# 10.8, 12.2 and 11.8, so 11.6.
DETECTION_SIGNAL_TO_NOISE = TunedParameter(
    name="astrometry.gaia_depth_detection_signal_to_noise",
    default=11.6,
    unit="sigma",
    description="Signal-to-noise in a one-FWHM circle at which the star finder finds half the stars.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="AST-D21",
)

# Number of wavelengths used to average the QE curve over the band.
_BAND_SAMPLES = 451


@dataclass(frozen=True)
class GaiaDepthPrediction:
    """The predicted Gaia query depth and the inputs behind it.

    Attributes
    ----------
    query_depth : `float`
        The Gaia G magnitude to query to. When `is_fallback` is true, it is
        the offline default.
    limiting_magnitude : `float` or `None`
        The G magnitude at which a star reaches the detection threshold, or
        `None` for a fallback.
    is_fallback : `bool`
        True when an input was missing and the default depth is returned.
    reason : `str`
        Why the prediction is a fallback; empty otherwise.
    threshold_sigma : `float` or `None`
        The signal-to-noise a star needs to count as detected.
    mean_qe : `float` or `None`
        The camera's QE averaged over the G band.
    star_rate_g0_e_per_s : `float` or `None`
        Electrons per second from a G = 0 star.
    sky_rate_e_per_s_per_px : `float` or `None`
        The sky rate used, measured or from the fallback brightness.
    sky_rate_is_measured : `bool`
        True when the caller's measured sky rate was used.
    read_noise_e : `float` or `None`
        The camera's read noise at the frame's setting, in electrons.
    aperture_pixels : `float` or `None`
        Pixels in the circle the signal-to-noise is taken over.
    fwhm_px : `float` or `None`
        The star width used, in pixels.
    exposure_time_s : `float` or `None`
        The effective exposure (photon-noise time), in seconds.
    read_noise_time_s : `float` or `None`
        The read-noise time, in seconds.
    """

    query_depth: float
    limiting_magnitude: float | None
    is_fallback: bool
    reason: str
    threshold_sigma: float | None = None
    mean_qe: float | None = None
    star_rate_g0_e_per_s: float | None = None
    sky_rate_e_per_s_per_px: float | None = None
    sky_rate_is_measured: bool = False
    read_noise_e: float | None = None
    aperture_pixels: float | None = None
    fwhm_px: float | None = None
    exposure_time_s: float | None = None
    read_noise_time_s: float | None = None


def _fallback(reason: str) -> GaiaDepthPrediction:
    """Build the fallback prediction: the offline default depth and why.

    Parameters
    ----------
    reason : `str`
        What input was missing.

    Returns
    -------
    prediction : `GaiaDepthPrediction`
        A prediction flagged as a fallback.
    """
    # Imported here: the star identifier imports this module.
    from astrometricslib.pipelines.astrometry.processing.gaia_tile_queries import GAIA_DEFAULT_MAGNITUDE_LIMIT

    return GaiaDepthPrediction(
        query_depth=float(resolve(GAIA_DEFAULT_MAGNITUDE_LIMIT).value),
        limiting_magnitude=None,
        is_fallback=True,
        reason=reason,
    )


def _positive(value: float | None) -> TypeGuard[float]:
    """Say whether a number is given, finite and above zero.

    Parameters
    ----------
    value : `float` or `None`
        The number to check.

    Returns
    -------
    is_positive : `bool`
        True for a finite number above zero.
    """
    return value is not None and math.isfinite(value) and value > 0.0


def _mean_qe_over_band(camera_profile: CameraProfile) -> float | None:
    """Average the camera's QE curve over the Gaia G band.

    Parameters
    ----------
    camera_profile : `CameraProfile`
        The camera's profile.

    Returns
    -------
    mean_qe : `float` or `None`
        The mean QE (0 to 1), or `None` when the profile has no curve, the
        curve does not cover the band, or the mean is not above zero. A curve
        that stops inside the band is not extended: that would invent QE.
    """
    curve = camera_profile.quantum_efficiency
    if curve is None:
        return None
    lower = float(resolve(GAIA_BAND_LOWER_NM).value)
    upper = float(resolve(GAIA_BAND_UPPER_NM).value)
    if curve.wavelength_nm[0] > lower or curve.wavelength_nm[-1] < upper:
        return None
    wavelengths = np.linspace(lower, upper, _BAND_SAMPLES)
    values = np.interp(wavelengths, curve.wavelength_nm, curve.quantum_efficiency_fraction)
    mean_qe = float(np.mean(values))
    return mean_qe if mean_qe > 0.0 else None


def predict_gaia_query_depth(
    camera_profile: CameraProfile | None,
    *,
    gain: object,
    offset: object,
    binning: object,
    aperture_diameter_mm: float | None,
    pixel_scale_arcsec_per_px: float | None,
    exposure_time_s: float | None,
    read_noise_time_s: float | None = None,
    sky_rate_e_per_s_per_px: float | None = None,
    fwhm_px: float | None = None,
    central_obstruction_fraction: float = 0.0,
) -> GaiaDepthPrediction:
    """Predict how deep a Gaia query must go for a frame or a stack.

    Parameters
    ----------
    camera_profile : `CameraProfile` or `None`
        The camera's profile (QE curve and read noise).
    gain : `Any`
        The frame's gain setting (or ISO), to find its read noise.
    offset : `Any`
        The frame's offset.
    binning : `Any`
        The frame's binning.
    aperture_diameter_mm : `float` or `None`
        The telescope's aperture diameter, in millimeters.
    pixel_scale_arcsec_per_px : `float` or `None`
        The image's pixel scale, in arcseconds per pixel.
    exposure_time_s : `float` or `None`
        The exposure of a single frame, or the stack's effective exposure
        (``stacking.effective_time_s``), in seconds.
    read_noise_time_s : `float` or `None`, optional
        The stack's read-noise time (``stacking.effective_time_read_noise_s``).
        Defaults to ``exposure_time_s``, which is right for a single frame.
    sky_rate_e_per_s_per_px : `float` or `None`, optional
        The measured sky rate. When missing, `FALLBACK_SKY_MAG_PER_ARCSEC2`
        gives the rate.
    fwhm_px : `float` or `None`, optional
        The measured star width, in pixels. When missing,
        `DEFAULT_FWHM_ARCSEC` gives it.
    central_obstruction_fraction : `float`, optional
        The share of the aperture's diameter blocked at the center. Zero for
        a refractor.

    Returns
    -------
    prediction : `GaiaDepthPrediction`
        The prediction, or a fallback with a reason when an input is missing.
    """
    if camera_profile is None:
        return _fallback("no camera profile")
    if not _positive(aperture_diameter_mm):
        return _fallback("no aperture diameter")
    if not _positive(pixel_scale_arcsec_per_px):
        return _fallback("no pixel scale")
    if not _positive(exposure_time_s):
        return _fallback("no exposure time")
    if not 0.0 <= central_obstruction_fraction < 1.0:
        return _fallback("central obstruction outside 0 to 1")

    mean_qe = _mean_qe_over_band(camera_profile)
    if mean_qe is None:
        return _fallback("no quantum efficiency curve that covers the Gaia G band")
    # A stack's sky is far above zero, so the read noise of the same gain at
    # another offset may stand in (see `CameraProfile.sensor_noise_for`).
    noise_entry = camera_profile.sensor_noise_for(gain, offset, binning, clipped_at_zero=False)
    if noise_entry is None or noise_entry.read_noise_e is None:
        return _fallback("no read noise for this camera setting")
    read_noise_e = noise_entry.read_noise_e.value

    read_time = exposure_time_s if read_noise_time_s is None else read_noise_time_s
    if not _positive(read_time):
        return _fallback("read-noise time is not above zero")

    area_cm2 = math.pi * (aperture_diameter_mm / 20.0) ** 2 * (1.0 - central_obstruction_fraction**2)
    star_rate_g0 = (
        float(resolve(GAIA_BAND_PHOTON_RATE_G0).value)
        * area_cm2
        * float(resolve(OPTICS_THROUGHPUT).value)
        * mean_qe
    )

    sky_is_measured = _positive(sky_rate_e_per_s_per_px)
    if _positive(sky_rate_e_per_s_per_px):
        sky_rate = float(sky_rate_e_per_s_per_px)
    else:
        sky_mag = float(resolve(FALLBACK_SKY_MAG_PER_ARCSEC2).value)
        sky_rate = star_rate_g0 * 10.0 ** (-0.4 * sky_mag) * pixel_scale_arcsec_per_px**2

    width_px = (
        fwhm_px
        if _positive(fwhm_px)
        else float(resolve(DEFAULT_FWHM_ARCSEC).value) / pixel_scale_arcsec_per_px
    )
    aperture_pixels = max(math.pi * width_px**2, 1.0)

    threshold = float(resolve(DETECTION_SIGNAL_TO_NOISE).value)
    empty_circle_variance = aperture_pixels * (
        sky_rate * exposure_time_s + read_noise_e**2 * (exposure_time_s / read_time) ** 2
    )
    star_electrons = 0.5 * (
        threshold**2 + math.sqrt(threshold**4 + 4.0 * threshold**2 * empty_circle_variance)
    )
    limiting_magnitude = -2.5 * math.log10(star_electrons / (exposure_time_s * star_rate_g0))

    query_depth = min(
        limiting_magnitude + float(resolve(GAIA_DEPTH_MARGIN).value),
        float(resolve(GAIA_DEPTH_CAP).value),
    )
    return GaiaDepthPrediction(
        query_depth=query_depth,
        limiting_magnitude=limiting_magnitude,
        is_fallback=False,
        reason="",
        threshold_sigma=threshold,
        mean_qe=mean_qe,
        star_rate_g0_e_per_s=star_rate_g0,
        sky_rate_e_per_s_per_px=sky_rate,
        sky_rate_is_measured=sky_is_measured,
        read_noise_e=read_noise_e,
        aperture_pixels=aperture_pixels,
        fwhm_px=width_px,
        exposure_time_s=exposure_time_s,
        read_noise_time_s=read_time,
    )


def depth_exceeded(prediction: GaiaDepthPrediction, faintest_detected_g: float | None) -> bool:
    """Say whether a detected star is fainter than the query reached.

    A true result means the prediction was too shallow (the frame shows
    stars the query could not match), so the query should be repeated deeper.

    Parameters
    ----------
    prediction : `GaiaDepthPrediction`
        The prediction the query used.
    faintest_detected_g : `float` or `None`
        The Gaia G magnitude of the faintest detected star, or `None` when
        unknown.

    Returns
    -------
    exceeded : `bool`
        True when ``faintest_detected_g`` is fainter than the query depth.
        False when the magnitude is unknown.
    """
    if faintest_detected_g is None or not math.isfinite(faintest_detected_g):
        return False
    return faintest_detected_g > prediction.query_depth


def _header_float(header: Any, *keywords: str) -> float | None:
    """Read the first of several header keywords that holds a positive number.

    Parameters
    ----------
    header : `Any`
        A FITS header, or any mapping with ``get``.
    *keywords : `str`
        The keywords to try, in order.

    Returns
    -------
    value : `float` or `None`
        The first finite value above zero, or `None`.
    """
    for keyword in keywords:
        try:
            value = float(header.get(keyword))
        except TypeError, ValueError:
            continue
        if math.isfinite(value) and value > 0.0:
            return value
    return None


def _aperture_from_configuration(header: Any, config: Any) -> float | None:
    """Find the aperture of the header's telescope in the configured optics.

    Parameters
    ----------
    header : `Any`
        A FITS header with ``TELESCOP``.
    config : `AppConfiguration` or `None`
        The configuration whose optics list a focal length and focal ratio.

    Returns
    -------
    diameter_mm : `float` or `None`
        The focal length over the focal ratio, or `None` when the optic is
        not listed.
    """
    name = str(header.get("TELESCOP") or "").strip()
    if not name or config is None:
        return None
    try:
        optic = config.get_observatory_setups().optic_named(name)
    except AttributeError, KeyError, TypeError, ValueError:
        return None
    if optic is None or not optic.focal_ratio:
        return None
    return float(optic.focal_length_mm) / float(optic.focal_ratio)


def predict_gaia_depth_from_header(
    header: Any, config: Any = None, *, fwhm_px: float | None = None
) -> GaiaDepthPrediction:
    """Predict the Gaia query depth of an image from its FITS header.

    The camera profile comes from ``INSTRUME``. The aperture is ``APTDIA``,
    or the configured optic named by ``TELESCOP``. The pixel scale is
    ``SCALE`` or ``SECPIX1``, or the pixel size (``XPIXSZ`` times
    ``XBINNING``) over ``FOCALLEN``. The exposure is ``EXPTIME``; for a stack
    of ``STACKCNT`` frames that is the total, and the read-noise time is the
    total over the square root of the frame count. The sky is not in the
    header, so the fallback sky brightness is used.

    Parameters
    ----------
    header : `Any`
        The image's FITS header.
    config : `AppConfiguration`, optional
        The configuration, for the camera profiles and the optics.
    fwhm_px : `float`, optional
        The measured star width, in pixels.

    Returns
    -------
    prediction : `GaiaDepthPrediction`
        The prediction, or a fallback with a reason.
    """
    from astrometricslib._internal.drivers.camera_profile_store import resolve_camera_profile

    if header is None:
        return _fallback("no FITS header")
    camera_name = str(header.get("INSTRUME") or "").strip() or None
    profile = resolve_camera_profile(camera_name, config) if camera_name else None
    if profile is not None and profile.is_generic_fallback:
        profile = None
    aperture_mm = _header_float(header, "APTDIA") or _aperture_from_configuration(header, config)
    binning = _header_float(header, "XBINNING") or 1.0
    scale = _header_float(header, "SCALE", "SECPIX1")
    if scale is None:
        pixel_um = _header_float(header, "XPIXSZ")
        focal_mm = _header_float(header, "FOCALLEN")
        if pixel_um is not None and focal_mm is not None:
            scale = 206.265 * pixel_um * binning / focal_mm
    total_s = _header_float(header, "EXPTIME", "EXPOSURE")
    frame_count = _header_float(header, "STACKCNT") or 1.0
    read_time_s = None if total_s is None else total_s / math.sqrt(frame_count)
    binning_text = f"{int(binning)}x{int(binning)}"
    return predict_gaia_query_depth(
        profile,
        gain=header.get("GAIN", header.get("ISOSPEED")),
        offset=header.get("OFFSET"),
        binning=binning_text,
        aperture_diameter_mm=aperture_mm,
        pixel_scale_arcsec_per_px=scale,
        exposure_time_s=total_s,
        read_noise_time_s=read_time_s,
        fwhm_px=fwhm_px,
    )
