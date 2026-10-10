"""Corrects a slitless spectrum's wavelengths for atmospheric refraction.

Air lifts a star's image toward the zenith (the point straight overhead), and
it lifts blue light more than red (see `atmospheric_refraction`). In a
slitless spectrum, the zero-order image (the star's undispersed image, made of
all wavelengths together) sits at the position of a white-light average. The
light of each wavelength in the first-order spectrum is lifted by a different
amount. Compared with the zero order, the light of wavelength `L` is
displaced toward the zenith by `R(L) - R(L_eff)`. Here `R` is the refraction
and `L_eff` is the effective wavelength of the zero order.

The pipeline's wavelength scale assumes every wavelength lies on a straight
line from the zero order. A displacement along that line moves a wavelength
to a pixel that the scale labels with a different wavelength. A displacement
across the line only widens the trail. So only the component along the
dispersion changes the wavelengths. The steps are:

1. Find the time of mid-exposure from the frame header.
2. Find the target's altitude and the parallactic angle at that time, with
   astropy and the observatory site. The parallactic angle is the position
   angle on the sky of the direction from the target to the zenith.
3. Find the sky position angle of the dispersion direction. The pipeline
   knows that direction as a vector on the image. This module sends two
   points along it through the frame's WCS (the mapping from pixels to sky
   coordinates). The same step gives the pixel scale along the dispersion.
4. Find the angle between the dispersion direction and the direction to the
   zenith. The displacement splits into a part along the dispersion
   (`R * cos(angle)`) and a part across it (`R * sin(angle)`).
5. Find `L_eff`: the mean wavelength of the spectrum, weighted by the
   camera's sensitivity and the star's spectrum.
6. Convert the along-dispersion displacement from pixels to Angstroms with
   the local dispersion (the Angstroms per pixel, from the derivative of the
   grating equation), and shift every sample's wavelength back.

Conventions
-----------
* A position angle is measured on the sky from north toward east, in
  degrees. The parallactic angle `q` is the position angle of the zenith as
  seen from the target.
* The dispersion position angle `theta` is the position angle of the
  direction in which wavelength increases along the trail.
* The angle between them is `phi = theta - q`, wrapped to -180 to 180
  degrees. It is 0 when the spectrum runs toward the zenith (red end up),
  and 180 when it runs away from the zenith.
* The displacement of the light of wavelength `L`, relative to where the
  wavelength scale puts it, is `dR = R(L) - R(L_eff)` toward the zenith. Its
  along-dispersion part is `dR * cos(phi)`. It is positive when the light
  shifts toward longer wavelengths on the trail. Its across-dispersion part
  is `-dR * sin(phi)`. It is positive toward the position angle
  `theta + 90` degrees.
* The wavelength error before correction is the along-dispersion
  displacement in pixels times the Angstroms per pixel. It is the labelled
  wavelength minus the true wavelength.

The along-dispersion correction needs the plane-parallel model, so it is
skipped below an altitude of `MINIMUM_ALTITUDE_DEGREES`. It is also skipped
when the observatory site, the frame's WCS or the exposure time is missing.
The record says which.
"""

import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
from astropy import units as astropy_units
from astropy.coordinates import AltAz, EarthLocation, SkyCoord
from astropy.time import Time, TimeDelta

from astrometricslib.foundation.observatory_site import ObservatorySite
from astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_refraction import (
    MINIMUM_ALTITUDE_DEGREES,
    AtmosphericConditions,
    differential_refraction_arcsec,
    refraction_arcsec,
    standard_atmosphere,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.quantum_efficiency_correction import (
    QuantumEfficiencyCurve,
    interpolate_quantum_efficiency,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

# The two wavelengths, in Angstroms, at which the record reports the
# displacement. They are the ends of the range the instrument response and the
# classifier use.
BLUE_REPORT_WAVELENGTH_ANGSTROM = 4200.0
RED_REPORT_WAVELENGTH_ANGSTROM = 8000.0

# How far, in pixels, the dispersion direction is followed on the image to
# find its position angle on the sky. The angle does not depend on it for a
# plain WCS. A longer step only averages over a small distortion.
DIRECTION_PROBE_PX = 50.0

# How far toward the zenith, in degrees, the parallactic angle probe moves.
ZENITH_PROBE_DEGREES = 0.1

# Why the correction was not computed; see `DifferentialRefraction.reason`.
REASON_NO_SITE = "no observatory site is configured"
REASON_NO_WCS = "the frame has no sky coordinate system (WCS)"
REASON_NO_TIME = "the frame header has no usable DATE-OBS with a time"
REASON_NO_SPECTRUM = "the spectrum has no usable samples"
REASON_ASTROPY = "astropy could not convert the sky position to altitude and azimuth"
REASON_LOW_ALTITUDE = "the target is below the minimum altitude of the plane-parallel model"

# The number of fixed-point passes the wavelength correction makes. The
# displacement changes by under 0.01 pixel per 100 A, so two passes already
# agree to well under 0.001 A.
_CORRECTION_PASSES = 3


@dataclass(frozen=True)
class RefractionGeometry:
    """Where the target and the dispersion lie relative to the zenith.

    Attributes
    ----------
    mid_exposure_utc : `str`
        The time of mid-exposure, in UTC, as an ISO string.
    altitude_degrees : `float`
        The target's altitude above the horizon, without refraction.
    zenith_distance_degrees : `float`
        The apparent zenith distance: 90 degrees minus the altitude, minus
        the refraction at 5000 A. The refraction formula uses this angle.
    parallactic_angle_degrees : `float`
        The position angle of the zenith as seen from the target.
    dispersion_position_angle_degrees : `float`
        The position angle of the direction of increasing wavelength.
    pixel_scale_arcsec : `float`
        The sky angle covered by one pixel along the dispersion.
    effective_wavelength_angstrom : `float`
        The wavelength of the zero-order image, in Angstroms.
    conditions : `AtmosphericConditions`
        The air's pressure, temperature and humidity.
    """

    mid_exposure_utc: str
    altitude_degrees: float
    zenith_distance_degrees: float
    parallactic_angle_degrees: float
    dispersion_position_angle_degrees: float
    pixel_scale_arcsec: float
    effective_wavelength_angstrom: float
    conditions: AtmosphericConditions

    @property
    def parallactic_to_dispersion_angle_degrees(self) -> float:
        """`float`: The angle `phi` from the zenith to the dispersion.

        It is the dispersion position angle minus the parallactic angle,
        wrapped to -180 to 180 degrees.
        """
        return _wrap_degrees(self.dispersion_position_angle_degrees - self.parallactic_angle_degrees)

    @property
    def is_usable(self) -> bool:
        """`bool`: Whether the plane-parallel model may be used."""
        return self.altitude_degrees >= MINIMUM_ALTITUDE_DEGREES

    def displacement_arcsec(self, wavelength_angstrom: np.ndarray | float) -> tuple[np.ndarray, np.ndarray]:
        """Give how far the light of a wavelength sits from the model's place.

        Parameters
        ----------
        wavelength_angstrom : `numpy.ndarray` or `float`
            The wavelengths, in Angstroms.

        Returns
        -------
        along : `numpy.ndarray`
            The displacement along the dispersion, in arcseconds. It is
            positive toward longer wavelengths on the trail.
        across : `numpy.ndarray`
            The displacement across the dispersion, in arcseconds. It is
            positive toward the position angle `theta + 90` degrees.

        Notes
        -----
        The refraction model raises `ValueError` when the target is below
        `MINIMUM_ALTITUDE_DEGREES`. Check `is_usable` first.
        """
        wavelength = np.asarray(wavelength_angstrom, dtype=float)
        toward_zenith = np.asarray(
            differential_refraction_arcsec(
                wavelength, self.effective_wavelength_angstrom, self.zenith_distance_degrees, self.conditions
            ),
            dtype=float,
        )
        phi = math.radians(self.parallactic_to_dispersion_angle_degrees)
        return toward_zenith * math.cos(phi), -toward_zenith * math.sin(phi)


@dataclass(frozen=True)
class DifferentialRefraction:
    """A record of the refraction correction for one spectrum.

    Mirrors `DifferentialRefractionRecord` in `models/stellar_source.py`. A
    number is `None` when it could not be computed.

    Attributes
    ----------
    is_computed : `bool`
        `True` when the displacement was computed.
    is_applied : `bool`
        `True` when the wavelengths were shifted (`dar_correction_applied`).
    reason : `str` or `None`
        Why the displacement was not computed or the correction was not
        applied. `None` when both happened.
    mid_exposure_utc : `str` or `None`
        The time of mid-exposure, in UTC.
    altitude_degrees : `float` or `None`
        The target's altitude.
    parallactic_angle_degrees : `float` or `None`
        The position angle of the zenith as seen from the target.
    dispersion_position_angle_degrees : `float` or `None`
        The position angle of the direction of increasing wavelength.
    parallactic_to_dispersion_angle_degrees : `float` or `None`
        The angle from the zenith direction to the dispersion direction.
    pixel_scale_arcsec : `float` or `None`
        The sky angle of one pixel along the dispersion.
    effective_wavelength_angstrom : `float` or `None`
        The effective wavelength of the zero order.
    pressure_hpa, temperature_c, relative_humidity_percent : `float` or `None`
        The air's conditions.
    atmosphere_source : `str` or `None`
        Where the conditions came from.
    along_dispersion_arcsec_at_4200 : `float` or `None`
        The displacement along the dispersion at 4200 A, in arcseconds.
        Positive means toward longer wavelengths on the trail.
    along_dispersion_arcsec_at_8000 : `float` or `None`
        The same displacement at 8000 A.
    along_dispersion_angstrom_at_4200 : `float` or `None`
        The displacement at 4200 A as a wavelength error: the labelled
        wavelength minus the true wavelength, in Angstroms.
    along_dispersion_angstrom_at_8000 : `float` or `None`
        The same wavelength error at 8000 A.
    across_dispersion_arcsec_at_4200 : `float` or `None`
        The displacement across the dispersion at 4200 A, in arcseconds.
    across_dispersion_arcsec_at_8000 : `float` or `None`
        The same displacement at 8000 A.
    along_dispersion_span_angstrom : `float` or `None`
        The absolute difference of the two wavelength errors above.
    across_dispersion_span_px : `float` or `None`
        The absolute difference of the two across-dispersion displacements,
        in pixels. It is how much the trail widens.
    """

    is_computed: bool
    is_applied: bool
    reason: str | None = None
    mid_exposure_utc: str | None = None
    altitude_degrees: float | None = None
    parallactic_angle_degrees: float | None = None
    dispersion_position_angle_degrees: float | None = None
    parallactic_to_dispersion_angle_degrees: float | None = None
    pixel_scale_arcsec: float | None = None
    effective_wavelength_angstrom: float | None = None
    pressure_hpa: float | None = None
    temperature_c: float | None = None
    relative_humidity_percent: float | None = None
    atmosphere_source: str | None = None
    along_dispersion_arcsec_at_4200: float | None = None
    along_dispersion_arcsec_at_8000: float | None = None
    along_dispersion_angstrom_at_4200: float | None = None
    along_dispersion_angstrom_at_8000: float | None = None
    across_dispersion_arcsec_at_4200: float | None = None
    across_dispersion_arcsec_at_8000: float | None = None
    along_dispersion_span_angstrom: float | None = None
    across_dispersion_span_px: float | None = None

    def as_dict(self) -> dict[str, object]:
        """Give the record as plain values for JSON.

        Returns
        -------
        record : `dict`
            The fields of this record, by name.
        """
        return dict(self.__dict__)


def _wrap_degrees(angle_degrees: float) -> float:
    """Wrap an angle to the range -180 to 180 degrees.

    Parameters
    ----------
    angle_degrees : `float`
        The angle, in degrees.

    Returns
    -------
    wrapped : `float`
        The same angle in the range -180 (excluded) to 180 (included).
    """
    # Python's modulo of a positive divisor is never negative. Mirroring the
    # angle first puts 180 inside the range and -180 outside it.
    return 180.0 - (180.0 - angle_degrees) % 360.0


def local_dispersion_angstrom_per_px(
    wavelength_angstrom: np.ndarray | float,
    grating_distance_mm: float,
    lines_per_mm: float,
    pixel_size_um: float,
) -> np.ndarray:
    """Give the Angstroms of wavelength in one pixel along the trail.

    This is the derivative of the grating equation the pipeline uses to place
    wavelengths (see `optics_physics`). With the grating spacing `d`, the
    grating distance `L` and the angle `t` where `sin(t) = wavelength / d`,
    the wavelength changes by `d * cos(t)**3 / L` per unit of length on the
    sensor.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray` or `float`
        The wavelengths, in Angstroms.
    grating_distance_mm : `float`
        The distance from the grating to the sensor, in millimeters.
    lines_per_mm : `float`
        The grating's lines per millimeter.
    pixel_size_um : `float`
        The pixel size, in micrometers.

    Returns
    -------
    dispersion : `numpy.ndarray`
        The Angstroms per pixel at each wavelength. It is about 11.4 for a
        200 lines/mm grating 16.49 mm from a sensor with 3.76 micrometer
        pixels.
    """
    spacing_angstrom = 1.0e7 / lines_per_mm
    sine = np.asarray(wavelength_angstrom, dtype=float) / spacing_angstrom
    cosine_cubed = (1.0 - sine**2) ** 1.5
    return spacing_angstrom * cosine_cubed * (pixel_size_um * 1.0e-3) / grating_distance_mm


def effective_zero_order_wavelength(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    quantum_efficiency_curve: QuantumEfficiencyCurve | None = None,
) -> float | None:
    """Estimate the wavelength at which the zero-order image is centered.

    The zero-order image is made of all wavelengths together. Its centroid
    (the brightness-weighted center) sits where the refraction of the
    weighted mix of wavelengths puts it. The weight of a wavelength is the
    star's flux there times the camera's quantum efficiency (the fraction of
    light the sensor records). This function estimates it as the mean
    wavelength of the spectrum with those weights. That is an approximation:
    it leaves out the grating's own efficiency and the gaps in the range the
    spectrum covers.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The wavelengths of the samples, in Angstroms.
    intensity : `numpy.ndarray`
        The spectrum. Pass the quantum-efficiency-corrected spectrum (the
        star's own shape) with `quantum_efficiency_curve`, so this function
        weights by the star and by the camera. Pass the raw counts and no
        curve when the camera's curve is not known: the raw counts already
        carry the camera's sensitivity.
    quantum_efficiency_curve : `QuantumEfficiencyCurve`, optional
        The camera's sensitivity curve.

    Returns
    -------
    effective_wavelength : `float` or `None`
        The weighted mean wavelength, in Angstroms. `None` when there is no
        positive weight.
    """
    wavelength = np.asarray(wavelength_angstrom, dtype=float)
    flux = np.clip(np.nan_to_num(np.asarray(intensity, dtype=float), nan=0.0), 0.0, None)
    if quantum_efficiency_curve is not None:
        flux = flux * interpolate_quantum_efficiency(wavelength / 10.0, quantum_efficiency_curve)
    # Each sample covers a different span of wavelength when the dispersion
    # varies along the trail; weight by that span.
    if wavelength.size > 1:
        flux = flux * np.abs(np.gradient(wavelength))
    total = float(flux.sum())
    if wavelength.size == 0 or not math.isfinite(total) or total <= 0.0:
        return None
    return float((wavelength * flux).sum() / total)


def mid_exposure_time(header: Mapping[str, Any] | None) -> Time | None:
    """Find the time of mid-exposure from a frame header.

    The start is ``DATE-OBS``. When ``DATE-END`` is also present and later,
    the result is halfway between them. Otherwise it is the start plus half
    of ``EXPTIME``. For a stack of frames, ``EXPTIME`` is the sum of the
    exposures, so the middle is approximate unless ``DATE-END`` is present.

    Parameters
    ----------
    header : `Mapping`, optional
        The frame's header.

    Returns
    -------
    time : `astropy.time.Time` or `None`
        The mid-exposure time on the UTC scale, or `None` when ``DATE-OBS``
        is missing, has no time of day, or cannot be read.
    """
    if header is None:
        return None
    start_text = str(header.get("DATE-OBS", "") or "")
    # A date without a time of day is at least half a day off.
    if len(start_text) <= len("YYYY-MM-DD"):
        return None
    try:
        start = Time(start_text, scale="utc")
        end_text = str(header.get("DATE-END", "") or "")
        if len(end_text) > len("YYYY-MM-DD"):
            end = Time(end_text, scale="utc")
            if end > start:
                return start + (end - start) / 2.0
        exposure = float(header.get("EXPTIME", 0.0) or 0.0)
    except DATA_ERRORS:
        return None
    if not math.isfinite(exposure) or exposure < 0.0:
        exposure = 0.0
    return start + TimeDelta(exposure / 2.0, format="sec")


def _direction_on_sky(
    wcs: Any, pixel_xy: tuple[float, float], image_angle_degrees: float
) -> tuple[float, float]:
    """Find the sky position angle and pixel scale of a direction on the image.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The frame's WCS.
    pixel_xy : `tuple` [`float`, `float`]
        The start point `(x, y)`, in zero-based pixels.
    image_angle_degrees : `float`
        The direction on the image, measured from the +x axis toward the +y
        axis, in degrees.

    Returns
    -------
    position_angle : `float`
        The position angle of the direction on the sky, in degrees from
        north through east.
    pixel_scale : `float`
        The sky angle covered by one pixel along that direction, in
        arcseconds.
    """
    angle = math.radians(image_angle_degrees)
    start_x, start_y = float(pixel_xy[0]), float(pixel_xy[1])
    end_x = start_x + DIRECTION_PROBE_PX * math.cos(angle)
    end_y = start_y + DIRECTION_PROBE_PX * math.sin(angle)
    start, end = wcs.celestial.pixel_to_world([start_x, end_x], [start_y, end_y])
    position_angle = start.position_angle(end).to_value(astropy_units.deg)
    scale = start.separation(end).to_value(astropy_units.arcsec) / DIRECTION_PROBE_PX
    return float(position_angle), float(scale)


def parallactic_angle_degrees(target: SkyCoord, obstime: Time, site: ObservatorySite) -> tuple[float, float]:
    """Find the target's altitude and the position angle of the zenith.

    The function takes the target's altitude and azimuth, moves a short way
    straight up along the vertical circle, and converts that point back to
    the sky. The position angle from the target to that point is the
    parallactic angle. It needs no sign convention beyond astropy's own.

    Parameters
    ----------
    target : `astropy.coordinates.SkyCoord`
        The target's sky position.
    obstime : `astropy.time.Time`
        The time of the observation.
    site : `ObservatorySite`
        The observatory.

    Returns
    -------
    altitude_degrees : `float`
        The target's altitude, without refraction.
    parallactic_angle : `float`
        The position angle of the zenith as seen from the target, in
        degrees from north through east. It is 0.0 for a target within
        a thousandth of a degree of the zenith, where it is undefined and
        the refraction is zero anyway.
    """
    location = EarthLocation.from_geodetic(
        lon=site.longitude_deg * astropy_units.deg,
        lat=site.latitude_deg * astropy_units.deg,
        height=site.elevation_m * astropy_units.m,
    )
    frame = AltAz(obstime=obstime, location=location)
    horizontal = target.transform_to(frame)
    altitude = float(horizontal.alt.to_value(astropy_units.deg))
    step = min(ZENITH_PROBE_DEGREES, 90.0 - altitude)
    if step < 1.0e-3:
        return altitude, 0.0
    above = SkyCoord(alt=(altitude + step) * astropy_units.deg, az=horizontal.az, frame=frame).transform_to(
        target.frame
    )
    return altitude, float(target.position_angle(above).to_value(astropy_units.deg))


def measure_refraction_geometry(
    *,
    header: Mapping[str, Any] | None,
    wcs: Any,
    target_pixel_xy: tuple[float, float],
    dispersion_image_angle_degrees: float,
    site: ObservatorySite | None,
    conditions: AtmosphericConditions | None,
    effective_wavelength_angstrom: float,
) -> tuple[RefractionGeometry | None, str | None]:
    """Find the geometry of the refraction for one target in one frame.

    Parameters
    ----------
    header : `Mapping`, optional
        The frame's header, read for the time.
    wcs : `astropy.wcs.WCS`, optional
        The frame's WCS.
    target_pixel_xy : `tuple` [`float`, `float`]
        The zero-order position `(x, y)`, in zero-based pixels.
    dispersion_image_angle_degrees : `float`
        The image direction in which wavelength increases along the trail,
        measured from the +x axis toward the +y axis, in degrees.
    site : `ObservatorySite`, optional
        The observatory.
    conditions : `AtmosphericConditions`, optional
        The air's conditions. The standard atmosphere at the site's
        elevation is used when this is `None`.
    effective_wavelength_angstrom : `float`
        The effective wavelength of the zero order.

    Returns
    -------
    geometry : `RefractionGeometry` or `None`
        The geometry, or `None` when an input was missing. A target below
        `MINIMUM_ALTITUDE_DEGREES` still gets a geometry; check
        `RefractionGeometry.is_usable`.
    reason : `str` or `None`
        Why there is no geometry, or `None` when there is one.
    """
    if site is None:
        return None, REASON_NO_SITE
    if wcs is None or not getattr(wcs, "has_celestial", False):
        return None, REASON_NO_WCS
    obstime = mid_exposure_time(header)
    if obstime is None:
        return None, REASON_NO_TIME
    conditions = conditions if conditions is not None else standard_atmosphere(site.elevation_m)
    try:
        sky = wcs.celestial.pixel_to_world(float(target_pixel_xy[0]), float(target_pixel_xy[1]))
        target = SkyCoord(ra=sky.icrs.ra, dec=sky.icrs.dec, frame="icrs")
        altitude, parallactic = parallactic_angle_degrees(target, obstime, site)
        dispersion_angle, pixel_scale = _direction_on_sky(
            wcs, target_pixel_xy, dispersion_image_angle_degrees
        )
        true_zenith_distance = 90.0 - altitude
        apparent_zenith_distance = true_zenith_distance
        if altitude >= MINIMUM_ALTITUDE_DEGREES:
            # Refraction lifts the star, so the zenith distance the telescope
            # sees is smaller than the true one by the refraction at 5000 A.
            lift_degrees = refraction_arcsec(5000.0, max(true_zenith_distance, 0.0), conditions) / 3600.0
            apparent_zenith_distance = max(true_zenith_distance - lift_degrees, 0.0)
    except DATA_ERRORS as error:
        logger.debug("Refraction geometry failed: %s", error)
        return None, f"{REASON_ASTROPY}: {error}"
    geometry = RefractionGeometry(
        mid_exposure_utc=str(obstime.utc.isot),
        altitude_degrees=altitude,
        zenith_distance_degrees=apparent_zenith_distance,
        parallactic_angle_degrees=parallactic,
        dispersion_position_angle_degrees=dispersion_angle,
        pixel_scale_arcsec=pixel_scale,
        effective_wavelength_angstrom=float(effective_wavelength_angstrom),
        conditions=conditions,
    )
    return geometry, None


def shift_wavelengths(
    wavelength_angstrom: np.ndarray,
    geometry: RefractionGeometry,
    dispersion_angstrom_per_px: Callable[[np.ndarray], np.ndarray],
) -> np.ndarray:
    """Move each wavelength back to where the refraction left its light.

    The light at pixel `p` has true wavelength `L` where the scale's pixel
    for `L`, plus the displacement of `L` in pixels, equals `p`. The scale
    labels that pixel `L + displacement * dispersion`. So the true
    wavelength is the label minus `displacement * dispersion`, both
    evaluated at the true wavelength. The function solves that by repeating
    the subtraction a few times, starting from the label.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The wavelengths the scale gives, in Angstroms.
    geometry : `RefractionGeometry`
        The refraction geometry. It must be usable.
    dispersion_angstrom_per_px : `Callable`
        Gives the Angstroms per pixel at an array of wavelengths.

    Returns
    -------
    corrected : `numpy.ndarray`
        The true wavelength of each sample, in Angstroms.
    """
    label = np.asarray(wavelength_angstrom, dtype=float)
    true = label.copy()
    for _ in range(_CORRECTION_PASSES):
        along_arcsec, _ = geometry.displacement_arcsec(true)
        error = along_arcsec / geometry.pixel_scale_arcsec * dispersion_angstrom_per_px(true)
        true = label - error
    return true


def _describe(
    geometry: RefractionGeometry,
    dispersion_angstrom_per_px: Callable[[np.ndarray], np.ndarray],
    *,
    is_applied: bool,
    reason: str | None,
) -> DifferentialRefraction:
    """Build the record for a geometry.

    Parameters
    ----------
    geometry : `RefractionGeometry`
        The refraction geometry.
    dispersion_angstrom_per_px : `Callable`
        Gives the Angstroms per pixel at an array of wavelengths.
    is_applied : `bool`
        Whether the wavelengths were shifted.
    reason : `str` or `None`
        Why the displacement is missing or the shift was not made.

    Returns
    -------
    record : `DifferentialRefraction`
        The record. The displacement fields are `None` when the geometry is
        not usable.
    """
    fields: dict[str, Any] = {
        "is_computed": geometry.is_usable,
        "is_applied": is_applied,
        "reason": reason,
        "mid_exposure_utc": geometry.mid_exposure_utc,
        "altitude_degrees": geometry.altitude_degrees,
        "parallactic_angle_degrees": geometry.parallactic_angle_degrees,
        "dispersion_position_angle_degrees": geometry.dispersion_position_angle_degrees,
        "parallactic_to_dispersion_angle_degrees": geometry.parallactic_to_dispersion_angle_degrees,
        "pixel_scale_arcsec": geometry.pixel_scale_arcsec,
        "effective_wavelength_angstrom": geometry.effective_wavelength_angstrom,
        "pressure_hpa": geometry.conditions.pressure_hpa,
        "temperature_c": geometry.conditions.temperature_c,
        "relative_humidity_percent": geometry.conditions.relative_humidity_percent,
        "atmosphere_source": geometry.conditions.source,
    }
    if geometry.is_usable:
        wavelengths = np.array([BLUE_REPORT_WAVELENGTH_ANGSTROM, RED_REPORT_WAVELENGTH_ANGSTROM])
        along, across = geometry.displacement_arcsec(wavelengths)
        along_angstrom = along / geometry.pixel_scale_arcsec * dispersion_angstrom_per_px(wavelengths)
        fields.update(
            along_dispersion_arcsec_at_4200=float(along[0]),
            along_dispersion_arcsec_at_8000=float(along[1]),
            along_dispersion_angstrom_at_4200=float(along_angstrom[0]),
            along_dispersion_angstrom_at_8000=float(along_angstrom[1]),
            across_dispersion_arcsec_at_4200=float(across[0]),
            across_dispersion_arcsec_at_8000=float(across[1]),
            along_dispersion_span_angstrom=float(abs(along_angstrom[1] - along_angstrom[0])),
            across_dispersion_span_px=float(abs(across[1] - across[0]) / geometry.pixel_scale_arcsec),
        )
    return DifferentialRefraction(**fields)


def correct_differential_refraction(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    *,
    header: Mapping[str, Any] | None,
    wcs: Any,
    target_pixel_xy: tuple[float, float],
    dispersion_image_angle_degrees: float,
    site: ObservatorySite | None,
    conditions: AtmosphericConditions | None,
    dispersion_angstrom_per_px: Callable[[np.ndarray], np.ndarray],
    quantum_efficiency_curve: QuantumEfficiencyCurve | None = None,
    apply_correction: bool = True,
) -> tuple[np.ndarray, DifferentialRefraction]:
    """Shift a spectrum's wavelengths for atmospheric differential refraction.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The wavelengths the scale gives, in Angstroms.
    intensity : `numpy.ndarray`
        The spectrum's brightness at those samples. With
        `quantum_efficiency_curve`, pass the quantum-efficiency-corrected
        spectrum; without it, pass the raw counts. It sets the effective
        wavelength of the zero order.
    header : `Mapping`, optional
        The frame's header, read for the time.
    wcs : `astropy.wcs.WCS`, optional
        The frame's WCS.
    target_pixel_xy : `tuple` [`float`, `float`]
        The zero-order position `(x, y)`, in zero-based pixels.
    dispersion_image_angle_degrees : `float`
        The image direction in which wavelength increases along the trail,
        measured from the +x axis toward the +y axis, in degrees.
    site : `ObservatorySite`, optional
        The observatory. Without one, nothing is computed.
    conditions : `AtmosphericConditions`, optional
        The air's conditions. The standard atmosphere at the site's
        elevation is used when this is `None`.
    dispersion_angstrom_per_px : `Callable`
        Gives the Angstroms per pixel along the trail at an array of
        wavelengths.
    quantum_efficiency_curve : `QuantumEfficiencyCurve`, optional
        The camera's sensitivity curve.
    apply_correction : `bool`, optional
        When `False`, the record is made but the wavelengths stay as they
        are.

    Returns
    -------
    wavelength : `numpy.ndarray`
        The corrected wavelengths, in Angstroms. It is a copy of the input
        when nothing was applied.
    record : `DifferentialRefraction`
        What was computed and whether it was applied.
    """
    wavelength = np.asarray(wavelength_angstrom, dtype=float)
    effective = effective_zero_order_wavelength(wavelength, intensity, quantum_efficiency_curve)
    if effective is None:
        return wavelength.copy(), DifferentialRefraction(False, False, REASON_NO_SPECTRUM)
    geometry, reason = measure_refraction_geometry(
        header=header,
        wcs=wcs,
        target_pixel_xy=target_pixel_xy,
        dispersion_image_angle_degrees=dispersion_image_angle_degrees,
        site=site,
        conditions=conditions,
        effective_wavelength_angstrom=effective,
    )
    if geometry is None:
        return wavelength.copy(), DifferentialRefraction(False, False, reason)
    if not geometry.is_usable:
        record = _describe(
            geometry,
            dispersion_angstrom_per_px,
            is_applied=False,
            reason=f"{REASON_LOW_ALTITUDE} ({MINIMUM_ALTITUDE_DEGREES:.0f} degrees)",
        )
        return wavelength.copy(), record
    if not apply_correction:
        record = _describe(
            geometry, dispersion_angstrom_per_px, is_applied=False, reason="the correction is switched off"
        )
        return wavelength.copy(), record
    corrected = shift_wavelengths(wavelength, geometry, dispersion_angstrom_per_px)
    return corrected, _describe(geometry, dispersion_angstrom_per_px, is_applied=True, reason=None)
