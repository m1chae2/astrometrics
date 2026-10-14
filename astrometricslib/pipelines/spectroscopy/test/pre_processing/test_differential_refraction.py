"""Purpose: Unit tests for the atmospheric differential refraction correction.

Description: Uses a fixed site, time and target so the answers are known.
Checks the parallactic angle against the textbook formula; that a dispersion
lying along the vertical gets the full shift along it and none across it, a
dispersion at 90 degrees the reverse, and a dispersion pointing away from
the zenith the opposite sign; that an image direction other than +x is
carried through the WCS; that a line displaced by a known refraction amount
goes back to within 1 Angstrom; the effective wavelength of the zero order;
the local dispersion; the mid-exposure time; and every reason the correction
is skipped.
"""

import math

import numpy as np
import pytest
from astropy import units as astropy_units
from astropy.coordinates import AltAz, EarthLocation, SkyCoord
from astropy.time import Time
from astropy.wcs import WCS

from astrometricslib.foundation.observatory_site import ObservatorySite
from astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_refraction import (
    AtmosphericConditions,
    differential_refraction_arcsec,
    standard_atmosphere,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.differential_refraction import (
    REASON_NO_SITE,
    REASON_NO_TIME,
    REASON_NO_WCS,
    DifferentialRefraction,
    RefractionGeometry,
    _wrap_degrees,
    correct_differential_refraction,
    effective_zero_order_wavelength,
    local_dispersion_angstrom_per_px,
    measure_refraction_geometry,
    mid_exposure_time,
    parallactic_angle_degrees,
    shift_wavelengths,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.optics_physics import (
    calculate_pixel_offset,
    calculate_wavelength,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.quantum_efficiency_correction import (
    QuantumEfficiencyCurve,
)

# The setup of the instrument: Star Analyser 200, 16.49 mm from an ASI533MM
# Pro sensor with 3.76 micrometer pixels.
LINES_PER_MM = 200.0
GRATING_DISTANCE_MM = 16.49
PIXEL_SIZE_UM = 3.76
PIXEL_SCALE_ARCSEC = 1.915

# A fixed site, time and sky. At this time the local apparent sidereal time
# is about 13h39m. A target three hours west of the meridian at declination
# +10 degrees stands at an altitude of about 40 degrees (airmass 1.5).
SITE = ObservatorySite(latitude_deg=40.0, longitude_deg=-105.0, elevation_m=1600.0)
EXPOSURE_START = "2025-06-01T03:59:00"
EXPOSURE_SECONDS = 120.0
MID_EXPOSURE = Time("2025-06-01T04:00:00", scale="utc")
CONDITIONS = AtmosphericConditions(845.6, 5.25, 0.0, "test")
EFFECTIVE_WAVELENGTH = 5800.0
TARGET_PIXEL = (60.0, 128.0)
HEADER = {"DATE-OBS": EXPOSURE_START, "EXPTIME": EXPOSURE_SECONDS}


def _dispersion(wavelength_angstrom: np.ndarray) -> np.ndarray:
    """Give the Angstroms per pixel of the Star Analyser 200 setup.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The wavelengths, in Angstroms.

    Returns
    -------
    dispersion : `numpy.ndarray`
        The Angstroms per pixel at each wavelength.
    """
    return local_dispersion_angstrom_per_px(
        wavelength_angstrom, GRATING_DISTANCE_MM, LINES_PER_MM, PIXEL_SIZE_UM
    )


def _target(hour_angle_hours: float, declination_degrees: float) -> SkyCoord:
    """Place a star at an hour angle on the fixed night.

    Parameters
    ----------
    hour_angle_hours : `float`
        The hour angle in hours. Positive is west of the meridian.
    declination_degrees : `float`
        The declination, in degrees.

    Returns
    -------
    target : `astropy.coordinates.SkyCoord`
        The star.
    """
    location = EarthLocation.from_geodetic(
        SITE.longitude_deg * astropy_units.deg,
        SITE.latitude_deg * astropy_units.deg,
        SITE.elevation_m * astropy_units.m,
    )
    sidereal = MID_EXPOSURE.sidereal_time("apparent", longitude=location.lon).deg
    return SkyCoord(
        ra=((sidereal - 15.0 * hour_angle_hours) % 360.0) * astropy_units.deg,
        dec=declination_degrees * astropy_units.deg,
    )


def _wcs_with_x_axis_at(target: SkyCoord, position_angle_degrees: float) -> WCS:
    """Build a WCS whose +x image axis points at a sky position angle.

    The +y axis points 90 degrees further around the compass, so an image
    direction at angle `a` from +x toward +y has the position angle
    `position_angle_degrees + a`.

    Parameters
    ----------
    target : `astropy.coordinates.SkyCoord`
        The sky position of the target pixel.
    position_angle_degrees : `float`
        The position angle of +x, in degrees from north through east.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        A tangent-plane WCS with `PIXEL_SCALE_ARCSEC` pixels.
    """
    angle = math.radians(position_angle_degrees)
    scale = PIXEL_SCALE_ARCSEC / 3600.0
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [target.ra.deg, target.dec.deg]
    # FITS pixels count from 1, so the zero-based target pixel is one less.
    wcs.wcs.crpix = [TARGET_PIXEL[0] + 1.0, TARGET_PIXEL[1] + 1.0]
    wcs.wcs.cd = scale * np.array([[math.sin(angle), math.cos(angle)], [math.cos(angle), -math.sin(angle)]])
    return wcs


def _geometry(
    angle_from_zenith_degrees: float,
    image_angle_degrees: float = 0.0,
    hour_angle_hours: float = 3.0,
) -> RefractionGeometry:
    """Measure the geometry with the dispersion at a set angle from the zenith.

    Parameters
    ----------
    angle_from_zenith_degrees : `float`
        The position angle of the dispersion minus the parallactic angle.
    image_angle_degrees : `float`, optional
        The dispersion direction on the image, from +x toward +y.
    hour_angle_hours : `float`, optional
        The target's hour angle.

    Returns
    -------
    geometry : `RefractionGeometry`
        The geometry.
    """
    target = _target(hour_angle_hours, 10.0)
    _, zenith_angle = parallactic_angle_degrees(target, MID_EXPOSURE, SITE)
    wcs = _wcs_with_x_axis_at(target, zenith_angle + angle_from_zenith_degrees - image_angle_degrees)
    geometry, reason = measure_refraction_geometry(
        header=HEADER,
        wcs=wcs,
        target_pixel_xy=TARGET_PIXEL,
        dispersion_image_angle_degrees=image_angle_degrees,
        site=SITE,
        conditions=CONDITIONS,
        effective_wavelength_angstrom=EFFECTIVE_WAVELENGTH,
    )
    assert reason is None
    assert geometry is not None
    return geometry


def test_the_parallactic_angle_matches_the_textbook_formula() -> None:
    """Astropy's zenith direction agrees with the spherical-trig formula."""
    latitude = math.radians(SITE.latitude_deg)
    location = EarthLocation.from_geodetic(
        SITE.longitude_deg * astropy_units.deg,
        SITE.latitude_deg * astropy_units.deg,
        SITE.elevation_m * astropy_units.m,
    )
    frame = AltAz(obstime=MID_EXPOSURE, location=location)
    for altitude_degrees, azimuth_degrees in (
        (40.0, 250.0),
        (40.0, 110.0),
        (60.0, 180.0),
        (60.0, 0.5),
        (35.0, 300.0),
    ):
        target = SkyCoord(
            alt=altitude_degrees * astropy_units.deg, az=azimuth_degrees * astropy_units.deg, frame=frame
        ).icrs
        altitude, angle = parallactic_angle_degrees(SkyCoord(target.ra, target.dec), MID_EXPOSURE, SITE)
        alt = math.radians(altitude_degrees)
        azimuth = math.radians(azimuth_degrees)
        sin_dec = math.sin(latitude) * math.sin(alt) + math.cos(latitude) * math.cos(alt) * math.cos(azimuth)
        cos_dec = math.sqrt(1.0 - sin_dec**2)
        # The sides of the parallactic triangle give sin(q) and cos(q). The
        # azimuth runs from north through east, so a star in the east (azimuth
        # 90 degrees) has the zenith to its west and a negative angle.
        expected = math.degrees(
            math.atan2(
                -math.sin(azimuth) * math.cos(latitude) / cos_dec,
                (math.sin(latitude) - sin_dec * math.sin(alt)) / (cos_dec * math.cos(alt)),
            )
        )
        assert altitude == pytest.approx(altitude_degrees, abs=0.05)
        # The ICRS north differs from the north of date by a small angle.
        assert _wrap_degrees(angle - expected) == pytest.approx(0.0, abs=0.2)


def test_the_altitude_of_the_fixed_target_is_about_forty_degrees() -> None:
    """The fixed test target is at airmass about 1.5."""
    altitude, _ = parallactic_angle_degrees(_target(3.0, 10.0), MID_EXPOSURE, SITE)

    assert altitude == pytest.approx(40.3, abs=0.1)


def test_dispersion_along_the_vertical_gives_the_full_shift_along_and_none_across() -> None:
    """With the red end toward the zenith, all of the refraction is along."""
    geometry = _geometry(0.0)
    wavelengths = np.array([4200.0, 5800.0, 8000.0])

    along, across = geometry.displacement_arcsec(wavelengths)

    full = differential_refraction_arcsec(
        wavelengths, EFFECTIVE_WAVELENGTH, geometry.zenith_distance_degrees, CONDITIONS
    )
    assert geometry.parallactic_to_dispersion_angle_degrees == pytest.approx(0.0, abs=0.01)
    np.testing.assert_allclose(along, full, atol=2e-3)
    np.testing.assert_allclose(across, 0.0, atol=2e-3)
    assert along[0] > 0.5
    assert along[2] < 0.0


def test_dispersion_at_ninety_degrees_to_the_vertical_gives_the_reverse() -> None:
    """Along the horizon, all of the refraction is across the dispersion."""
    wavelengths = np.array([4200.0, 5800.0, 8000.0])
    for angle in (90.0, -90.0):
        geometry = _geometry(angle)

        along, across = geometry.displacement_arcsec(wavelengths)

        full = differential_refraction_arcsec(
            wavelengths, EFFECTIVE_WAVELENGTH, geometry.zenith_distance_degrees, CONDITIONS
        )
        assert abs(geometry.parallactic_to_dispersion_angle_degrees) == pytest.approx(90.0, abs=0.01)
        np.testing.assert_allclose(along, 0.0, atol=2e-3)
        # The across axis points at theta + 90 degrees. The zenith lies at
        # theta - 90 degrees when the angle is +90, so the sign is negative.
        np.testing.assert_allclose(across, -math.copysign(1.0, angle) * full, atol=2e-3)


def test_dispersion_pointing_away_from_the_zenith_flips_the_sign() -> None:
    """With the red end toward the horizon, the along shift changes sign."""
    toward = _geometry(0.0)
    away = _geometry(180.0)
    wavelengths = np.array([4200.0, 8000.0])

    along_toward, _ = toward.displacement_arcsec(wavelengths)
    along_away, across_away = away.displacement_arcsec(wavelengths)

    np.testing.assert_allclose(along_away, -along_toward, atol=2e-3)
    np.testing.assert_allclose(across_away, 0.0, atol=2e-3)


def test_a_dispersion_at_another_image_angle_is_carried_through_the_wcs() -> None:
    """A tilted trail on the image still lines up with the vertical."""
    wavelengths = np.array([4200.0, 8000.0])
    tilted = _geometry(0.0, image_angle_degrees=-37.0)
    plain = _geometry(0.0)

    np.testing.assert_allclose(
        tilted.displacement_arcsec(wavelengths)[0], plain.displacement_arcsec(wavelengths)[0], atol=2e-3
    )
    assert tilted.pixel_scale_arcsec == pytest.approx(PIXEL_SCALE_ARCSEC, rel=1e-3)


def test_an_intermediate_angle_splits_the_shift_by_cosine_and_sine() -> None:
    """At 30 degrees from the vertical, the parts are cos(30) and sin(30)."""
    geometry = _geometry(30.0)
    wavelength = np.array([4200.0])

    along, across = geometry.displacement_arcsec(wavelength)

    full = differential_refraction_arcsec(
        wavelength, EFFECTIVE_WAVELENGTH, geometry.zenith_distance_degrees, CONDITIONS
    )
    assert along[0] == pytest.approx(full[0] * math.cos(math.radians(30.0)), abs=2e-3)
    assert across[0] == pytest.approx(-full[0] * math.sin(math.radians(30.0)), abs=2e-3)


def test_the_spread_between_4200_and_8000_angstrom_is_under_one_pixel_at_airmass_1_5() -> None:
    """At the 40 degree target the spread is about 0.8 pixel."""
    geometry = _geometry(0.0)

    along, _ = geometry.displacement_arcsec(np.array([4200.0, 8000.0]))

    assert abs(along[0] - along[1]) / PIXEL_SCALE_ARCSEC == pytest.approx(0.8, abs=0.15)


def test_shifting_a_line_displaced_by_a_known_amount_puts_it_back_within_one_angstrom() -> None:
    """A displaced absorption line goes back to its true position."""
    geometry = _geometry(0.0)
    labels = np.arange(4000.0, 8000.0, 11.2)
    true_line = 4340.0
    sigma = 12.0
    # The line's light lands where the displacement puts it, so the scale
    # labels it with a different wavelength.
    known_shift_px = (
        float(
            differential_refraction_arcsec(
                true_line, EFFECTIVE_WAVELENGTH, geometry.zenith_distance_degrees, CONDITIONS
            )
        )
        / PIXEL_SCALE_ARCSEC
    )
    labelled_line = true_line + known_shift_px * float(_dispersion(np.array([true_line]))[0])
    depth = 0.6 * np.exp(-0.5 * ((labels - labelled_line) / sigma) ** 2)

    corrected = shift_wavelengths(labels, geometry, _dispersion)

    def centroid(wavelengths: np.ndarray) -> float:
        """Find the center of the dip.

        Parameters
        ----------
        wavelengths : `numpy.ndarray`
            The wavelength of each sample, in Angstroms.

        Returns
        -------
        center : `float`
            The depth-weighted mean wavelength.
        """
        return float((wavelengths * depth).sum() / depth.sum())

    assert abs(centroid(labels) - true_line) > 3.0
    assert centroid(corrected) == pytest.approx(true_line, abs=1.0)


def test_the_pipeline_entry_point_corrects_a_displaced_line_and_records_it() -> None:
    """The whole correction, from header to record, fixes a line."""
    target = _target(3.0, 10.0)
    _, zenith_angle = parallactic_angle_degrees(target, MID_EXPOSURE, SITE)
    wcs = _wcs_with_x_axis_at(target, zenith_angle)
    labels = np.arange(4000.0, 8000.0, 11.2)
    continuum = np.ones_like(labels)
    effective = effective_zero_order_wavelength(labels, continuum)
    assert effective is not None
    probe_geometry, _ = measure_refraction_geometry(
        header=HEADER,
        wcs=wcs,
        target_pixel_xy=TARGET_PIXEL,
        dispersion_image_angle_degrees=0.0,
        site=SITE,
        conditions=CONDITIONS,
        effective_wavelength_angstrom=effective,
    )
    assert probe_geometry is not None
    true_line = 4861.0
    known_shift_px = (
        float(
            differential_refraction_arcsec(
                true_line, effective, probe_geometry.zenith_distance_degrees, CONDITIONS
            )
        )
        / PIXEL_SCALE_ARCSEC
    )
    labelled_line = true_line + known_shift_px * float(_dispersion(np.array([true_line]))[0])
    depth = 0.6 * np.exp(-0.5 * ((labels - labelled_line) / 12.0) ** 2)

    corrected, record = correct_differential_refraction(
        labels,
        continuum,
        header=HEADER,
        wcs=wcs,
        target_pixel_xy=TARGET_PIXEL,
        dispersion_image_angle_degrees=0.0,
        site=SITE,
        conditions=CONDITIONS,
        dispersion_angstrom_per_px=_dispersion,
    )

    assert float((corrected * depth).sum() / depth.sum()) == pytest.approx(true_line, abs=1.0)
    assert record.is_computed is True
    assert record.is_applied is True
    assert record.reason is None
    assert record.altitude_degrees == pytest.approx(40.3, abs=0.1)
    assert record.effective_wavelength_angstrom == pytest.approx(effective)
    assert record.atmosphere_source == "test"
    assert record.along_dispersion_arcsec_at_4200 is not None
    assert record.along_dispersion_arcsec_at_8000 is not None
    assert record.along_dispersion_arcsec_at_4200 > 0.0 > record.along_dispersion_arcsec_at_8000
    assert record.along_dispersion_angstrom_at_4200 is not None
    assert record.along_dispersion_angstrom_at_8000 is not None
    assert record.along_dispersion_span_angstrom == pytest.approx(
        abs(record.along_dispersion_angstrom_at_4200 - record.along_dispersion_angstrom_at_8000)
    )
    assert record.across_dispersion_span_px == pytest.approx(0.0, abs=0.01)
    assert set(record.as_dict()) == set(DifferentialRefraction.__dataclass_fields__)


def test_switching_the_correction_off_keeps_the_wavelengths_but_records_the_numbers() -> None:
    """With `apply_correction=False` the record is made and nothing moves."""
    target = _target(3.0, 10.0)
    _, zenith_angle = parallactic_angle_degrees(target, MID_EXPOSURE, SITE)
    labels = np.arange(4000.0, 8000.0, 11.2)

    corrected, record = correct_differential_refraction(
        labels,
        np.ones_like(labels),
        header=HEADER,
        wcs=_wcs_with_x_axis_at(target, zenith_angle),
        target_pixel_xy=TARGET_PIXEL,
        dispersion_image_angle_degrees=0.0,
        site=SITE,
        conditions=CONDITIONS,
        dispersion_angstrom_per_px=_dispersion,
        apply_correction=False,
    )

    np.testing.assert_array_equal(corrected, labels)
    assert record.is_computed is True
    assert record.is_applied is False
    assert record.along_dispersion_span_angstrom is not None


def test_a_target_below_twenty_degrees_is_not_corrected() -> None:
    """At 18 degrees the record keeps the altitude and skips the rest."""
    target = _target(5.0, 10.0)
    _, zenith_angle = parallactic_angle_degrees(target, MID_EXPOSURE, SITE)
    labels = np.arange(4000.0, 8000.0, 11.2)

    corrected, record = correct_differential_refraction(
        labels,
        np.ones_like(labels),
        header=HEADER,
        wcs=_wcs_with_x_axis_at(target, zenith_angle),
        target_pixel_xy=TARGET_PIXEL,
        dispersion_image_angle_degrees=0.0,
        site=SITE,
        conditions=CONDITIONS,
        dispersion_angstrom_per_px=_dispersion,
    )

    np.testing.assert_array_equal(corrected, labels)
    assert record.is_computed is False
    assert record.is_applied is False
    assert record.altitude_degrees == pytest.approx(18.1, abs=0.2)
    assert record.reason is not None
    assert "minimum altitude" in record.reason
    assert record.along_dispersion_span_angstrom is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"site": None}, REASON_NO_SITE),
        ({"wcs": None}, REASON_NO_WCS),
        ({"header": {"EXPTIME": 60.0}}, REASON_NO_TIME),
        ({"header": {"DATE-OBS": "2025-06-01", "EXPTIME": 60.0}}, REASON_NO_TIME),
        ({"header": None}, REASON_NO_TIME),
    ],
)
def test_a_missing_input_skips_the_correction_and_says_why(changes: dict[str, object], reason: str) -> None:
    """Without a site, a WCS or a time of day nothing is computed."""
    target = _target(3.0, 10.0)
    labels = np.arange(4000.0, 8000.0, 11.2)
    inputs: dict[str, object] = {
        "header": HEADER,
        "wcs": _wcs_with_x_axis_at(target, 0.0),
        "site": SITE,
    }
    inputs.update(changes)

    corrected, record = correct_differential_refraction(
        labels,
        np.ones_like(labels),
        target_pixel_xy=TARGET_PIXEL,
        dispersion_image_angle_degrees=0.0,
        conditions=CONDITIONS,
        dispersion_angstrom_per_px=_dispersion,
        **inputs,
    )

    np.testing.assert_array_equal(corrected, labels)
    assert record == DifferentialRefraction(False, False, reason)


def test_a_spectrum_with_no_positive_samples_is_not_corrected() -> None:
    """An all-zero spectrum has no effective wavelength."""
    labels = np.arange(4000.0, 8000.0, 11.2)

    corrected, record = correct_differential_refraction(
        labels,
        np.zeros_like(labels),
        header=HEADER,
        wcs=_wcs_with_x_axis_at(_target(3.0, 10.0), 0.0),
        target_pixel_xy=TARGET_PIXEL,
        dispersion_image_angle_degrees=0.0,
        site=SITE,
        conditions=CONDITIONS,
        dispersion_angstrom_per_px=_dispersion,
    )

    np.testing.assert_array_equal(corrected, labels)
    assert record.is_computed is False


def test_without_given_conditions_the_standard_atmosphere_at_the_site_is_used() -> None:
    """With no conditions given, the record names the standard air."""
    target = _target(3.0, 10.0)
    labels = np.arange(4000.0, 8000.0, 11.2)

    _, record = correct_differential_refraction(
        labels,
        np.ones_like(labels),
        header=HEADER,
        wcs=_wcs_with_x_axis_at(target, 0.0),
        target_pixel_xy=TARGET_PIXEL,
        dispersion_image_angle_degrees=0.0,
        site=SITE,
        conditions=None,
        dispersion_angstrom_per_px=_dispersion,
    )

    assert record.atmosphere_source == "standard atmosphere scaled to the site elevation"
    assert record.pressure_hpa == pytest.approx(standard_atmosphere(SITE.elevation_m).pressure_hpa)


def test_the_effective_wavelength_of_a_flat_spectrum_is_its_mean() -> None:
    """With equal weights the effective wavelength is the middle."""
    wavelength = np.linspace(4000.0, 8000.0, 401)

    assert effective_zero_order_wavelength(wavelength, np.ones_like(wavelength)) == pytest.approx(6000.0)


def test_the_effective_wavelength_follows_the_bright_end_of_the_spectrum() -> None:
    """A spectrum rising to the red has a red effective wavelength."""
    wavelength = np.linspace(4000.0, 8000.0, 401)

    blue = effective_zero_order_wavelength(wavelength, (8000.0 - wavelength) + 1.0)
    red = effective_zero_order_wavelength(wavelength, (wavelength - 4000.0) + 1.0)

    assert blue is not None
    assert red is not None
    assert blue < 6000.0 < red


def test_the_effective_wavelength_weights_by_the_cameras_sensitivity() -> None:
    """A QE-corrected spectrum with the curve equals the raw counts alone."""
    wavelength = np.linspace(4000.0, 8000.0, 401)
    curve = QuantumEfficiencyCurve(
        wavelength_nm=np.array([300.0, 500.0, 700.0, 1000.0]),
        quantum_efficiency_fraction=np.array([0.3, 0.9, 0.6, 0.1]),
    )
    star = 1.0 + (wavelength - 4000.0) / 4000.0
    sensitivity = np.interp(wavelength / 10.0, curve.wavelength_nm, curve.quantum_efficiency_fraction)

    with_curve = effective_zero_order_wavelength(wavelength, star, curve)
    from_counts = effective_zero_order_wavelength(wavelength, star * sensitivity)
    without_curve = effective_zero_order_wavelength(wavelength, star)

    assert with_curve == pytest.approx(from_counts)
    assert with_curve != pytest.approx(without_curve, abs=10.0)


def test_negative_and_missing_samples_count_as_zero_weight() -> None:
    """NaN and negative noise give the same answer as zeros."""
    wavelength = np.linspace(4000.0, 8000.0, 401)
    noisy = np.ones_like(wavelength)
    noisy[:50] = -5.0
    noisy[60:70] = np.nan
    zeroed = np.ones_like(wavelength)
    zeroed[:50] = 0.0
    zeroed[60:70] = 0.0

    assert effective_zero_order_wavelength(wavelength, noisy) == pytest.approx(
        effective_zero_order_wavelength(wavelength, zeroed)
    )


def test_the_local_dispersion_is_the_derivative_of_the_grating_equation() -> None:
    """It agrees with a numerical derivative of the pipeline's own scale."""
    wavelength = np.array([4200.0, 5000.0, 6563.0, 8000.0])
    offset = calculate_pixel_offset(wavelength / 10.0, GRATING_DISTANCE_MM, LINES_PER_MM, PIXEL_SIZE_UM)
    step = 1.0e-3
    numerical = (
        10.0
        * (
            calculate_wavelength(offset + step, GRATING_DISTANCE_MM, LINES_PER_MM, PIXEL_SIZE_UM)
            - calculate_wavelength(offset - step, GRATING_DISTANCE_MM, LINES_PER_MM, PIXEL_SIZE_UM)
        )
        / (2.0 * step)
    )

    np.testing.assert_allclose(_dispersion(wavelength), numerical, rtol=1e-6)
    assert float(_dispersion(np.array([5000.0]))[0]) == pytest.approx(11.4, abs=0.2)


def test_the_mid_exposure_time_is_the_start_plus_half_the_exposure() -> None:
    """DATE-OBS plus half of EXPTIME."""
    middle = mid_exposure_time({"DATE-OBS": "2025-06-01T03:59:00", "EXPTIME": 120.0})

    assert middle is not None
    assert middle.isot == "2025-06-01T04:00:00.000"


def test_the_mid_exposure_time_uses_date_end_when_it_is_present() -> None:
    """DATE-END gives the true middle of a stack."""
    middle = mid_exposure_time({
        "DATE-OBS": "2025-06-01T03:00:00",
        "DATE-END": "2025-06-01T05:00:00",
        "EXPTIME": 600.0,
    })

    assert middle is not None
    assert middle.isot == "2025-06-01T04:00:00.000"


def test_the_mid_exposure_time_without_an_exposure_is_the_start() -> None:
    """With neither DATE-END nor EXPTIME the start time is used."""
    middle = mid_exposure_time({"DATE-OBS": "2025-06-01T04:00:00"})

    assert middle is not None
    assert middle.isot == "2025-06-01T04:00:00.000"


@pytest.mark.parametrize("header", [None, {}, {"DATE-OBS": "2025-06-01"}, {"DATE-OBS": "not a date at all"}])
def test_an_unusable_date_gives_no_mid_exposure_time(header: dict[str, str] | None) -> None:
    """A missing, date-only or unreadable DATE-OBS gives `None`."""
    assert mid_exposure_time(header) is None


@pytest.mark.parametrize(
    ("angle", "expected"),
    [
        (0.0, 0.0),
        (190.0, -170.0),
        (-190.0, 170.0),
        (180.0, 180.0),
        (-180.0, 180.0),
        (540.0, 180.0),
        (45.0, 45.0),
    ],
)
def test_angles_wrap_to_minus_180_through_180(angle: float, expected: float) -> None:
    """The wrap puts 180 in the range and -180 out."""
    assert _wrap_degrees(angle) == pytest.approx(expected)
