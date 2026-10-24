"""Purpose: Tests for the sky model and the observing values.

Description: Generates sky rates from known coefficients with the model's own
features and checks that the fit recovers them and reports a held-out error,
that too few nights give no model, and that the airmass, dew risk, pointing
error, guide join values and the polar-alignment fit behave as derived.
"""

import math

import pytest

from wayfindinglib.analytics.result_quality import observing_values as ov
from wayfindinglib.analytics.result_quality import sky_model as sm

LATITUDE, LONGITUDE, ELEVATION = 45.76, -110.74, 1400.0
# 2026-10-01 00:00 UTC.
START = 1790812800.0


def _times(nights: int = 8, per_night: int = 6) -> list[tuple[str, float]]:
    """Make observation times spread over a lunar cycle.

    Parameters
    ----------
    nights, per_night : `int`
        Number of nights, and frames per night.

    Returns
    -------
    rows : `list` [`tuple` [`str`, `float`]]
        Night id and epoch seconds. Nights are 3.7 days apart so that the Moon
        goes through its phases, and frames run from 03:00 to 08:00 UTC.
    """
    rows = []
    for night in range(nights):
        base = START + night * 3.7 * 86400.0 + 3 * 3600.0
        for frame in range(per_night):
            rows.append((f"n{night}", base + frame * 3600.0))
    return rows


def _samples(coefficients: tuple[float, float, float]) -> list[sm.SkyRateSample]:
    """Make sky-rate samples that follow the model exactly.

    Parameters
    ----------
    coefficients : `tuple` [`float`, `float`, `float`]
        c0, c1 and c2.

    Returns
    -------
    samples : `list` [`SkyRateSample`]
        The samples.
    """
    rows = _times()
    ra, dec = [300.0] * len(rows), [40.0] * len(rows)
    geometry = ov.sky_geometry([r[1] for r in rows], ra, dec, LATITUDE, LONGITUDE, ELEVATION)
    samples = []
    for (night, epoch), geo in zip(rows, geometry, strict=True):
        features = sm.sky_features(geo)
        if features is None:
            continue
        rate = sum(c * f for c, f in zip(coefficients, features, strict=True))
        samples.append(
            sm.SkyRateSample(
                frame_id=f"{night}-{epoch}",
                night_id=night,
                epoch_seconds=epoch,
                ra_deg=300.0,
                dec_deg=40.0,
                sky_rate_e_per_s_per_px=rate,
            )
        )
    return samples


def test_airmass_follows_kasten_young() -> None:
    """The airmass is 1 at the zenith, about 2 at 30 degrees.

    None below the horizon.
    """
    assert ov.airmass_from_altitude(90.0) == pytest.approx(1.0, abs=0.001)
    assert ov.airmass_from_altitude(30.0) == pytest.approx(1.99, abs=0.01)
    assert ov.airmass_from_altitude(0.0) is None
    assert ov.airmass_from_altitude(-5.0) is None


def test_sky_geometry_is_sensible() -> None:
    """Values are in range and the lists match the input length."""
    rows = _times(nights=2, per_night=2)
    geometry = ov.sky_geometry([r[1] for r in rows], [300.0] * 4, [40.0] * 4, LATITUDE, LONGITUDE, ELEVATION)
    assert len(geometry) == 4
    for item in geometry:
        assert 0.0 <= item.moon_illumination_fraction <= 1.0
        assert 0.0 <= item.moon_separation_deg <= 180.0
        assert -90.0 <= item.altitude_deg <= 90.0
    with pytest.raises(ValueError, match="same length"):
        ov.sky_geometry([1.0], [1.0, 2.0], [1.0], LATITUDE, LONGITUDE, ELEVATION)
    assert ov.sky_geometry([], [], [], LATITUDE, LONGITUDE, ELEVATION) == []


def test_sky_model_recovers_known_coefficients_and_reports_held_out_error() -> None:
    """A noiseless set gives back the coefficients and a tiny error."""
    truth = (0.4, 0.15, 0.3)
    outcome = sm.fit_sky_model(_samples(truth), "key", LATITUDE, LONGITUDE, ELEVATION)
    assert outcome.model is not None
    model = outcome.model
    assert model.model_derived
    assert model.constant == pytest.approx(truth[0], rel=0.05)
    assert model.held_out_relative_error < 0.05
    assert model.nights_used >= sm.MINIMUM_NIGHTS_FOR_SKY_MODEL


def test_prediction_without_the_moon_is_lower_when_the_moon_is_up() -> None:
    """The moonless rate never exceeds the full prediction."""
    outcome = sm.fit_sky_model(_samples((0.4, 0.15, 0.3)), "key", LATITUDE, LONGITUDE, ELEVATION)
    assert outcome.model is not None
    for sample in _samples((0.4, 0.15, 0.3))[:12]:
        prediction = sm.predict_sky_rate_at(
            outcome.model, sample.epoch_seconds, 300.0, 40.0, LATITUDE, LONGITUDE, ELEVATION
        )
        assert prediction.sky_rate_e_per_s_per_px >= prediction.moonless_sky_rate_e_per_s_per_px


def test_too_few_nights_give_no_model_and_a_reason() -> None:
    """Fewer than five nights gives None with the reason."""
    samples = [s for s in _samples((0.4, 0.15, 0.3)) if s.night_id in {"n0", "n1", "n2"}]
    outcome = sm.fit_sky_model(samples, "key", LATITUDE, LONGITUDE, ELEVATION)
    assert outcome.model is None
    assert "nights" in (outcome.reason or "")


def test_dew_risk_and_missing_data() -> None:
    """The spread decides risk.

    A missing value gives a reason and no number.
    """
    assert ov.dew_risk(10.0, 8.0).at_risk is True
    assert ov.dew_risk(10.0, 2.0).at_risk is False
    missing = ov.dew_risk(None, 2.0)
    assert missing.at_risk is None and missing.spread_c is None
    assert "ambient" in (missing.reason or "")


def test_pointing_error_is_the_separation() -> None:
    """One arcminute of declination is 60 arcseconds."""
    assert ov.pointing_error_arcsec(10.0, 20.0, 10.0, 20.0 + 1 / 60) == pytest.approx(60.0, rel=1e-3)


def test_observing_values_report_what_is_missing() -> None:
    """A frame with a position and a time gets geometry.

    The rest are reasons, not defaults.
    """
    frame = ov.FrameObservingInputs(
        frame_id="f",
        start_epoch_seconds=START + 4 * 3600,
        exposure_seconds=60.0,
        solved_ra_deg=300.0,
        solved_dec_deg=40.0,
    )
    values = ov.observing_values([frame], LATITUDE, LONGITUDE, ELEVATION)[0]
    assert values.geometry is not None
    assert values.guide_rms_arcsec is None
    assert set(values.insufficient) == {"pointing_error", "guide_rms", "dew_risk"}
    bare = ov.observing_values([ov.FrameObservingInputs(frame_id="g")], LATITUDE, LONGITUDE, ELEVATION)[0]
    assert bare.geometry is None and "geometry" in bare.insufficient


def _rotation_samples(
    altitude_error: float,
    east_west_error: float,
    span_hours: float,
    run: str = "r1",
    count: int = 12,
    dec: float = 40.0,
) -> list[ov.RotationSample]:
    """Make rotation samples from the forward model of a polar error.

    Parameters
    ----------
    altitude_error, east_west_error : `float`
        The two parts, in radians.
    span_hours : `float`
        Hours of tracking.
    run : `str`
        Guiding run id.
    count : `int`
        Number of samples.
    dec : `float`
        Declination.

    Returns
    -------
    samples : `list` [`RotationSample`]
        Samples with a run-specific zero point of 0.3 degrees.
    """
    from astropy.time import Time

    from wayfindinglib.astronomy.coordinate_transforms import earth_location, hour_angle_deg

    location = earth_location(LATITUDE, LONGITUDE, ELEVATION)
    out = []
    for index in range(count):
        epoch = START + 3 * 3600 + index * span_hours * 3600 / (count - 1)
        hour = math.radians(hour_angle_deg(300.0, location, Time(epoch, format="unix", scale="utc")))
        rotation = 0.3 + math.degrees(
            (altitude_error * math.sin(hour) - east_west_error * math.cos(hour)) / math.cos(math.radians(dec))
        )
        out.append(
            ov.RotationSample(
                guiding_run_id=run, epoch_seconds=epoch, rotation_deg=rotation, ra_deg=300.0, dec_deg=dec
            )
        )
    return out


@pytest.mark.parametrize("dec", [33.0, 60.0])
def test_polar_alignment_fit_recovers_the_error_over_a_long_span(dec: float) -> None:
    """Over 5 hours both parts are separated, at declination 33 and 60 degrees.

    The rotation rate goes as 1/cos(dec), so a model with cos(dec) in the
    numerator recovers the wrong size at these declinations. The total
    matches (exploratory).
    """
    altitude, east_west = math.radians(20 / 60), math.radians(30 / 60)
    samples = _rotation_samples(altitude, east_west, 5.0, dec=dec) + _rotation_samples(
        altitude, east_west, 4.0, run="r2", dec=dec
    )
    estimate = ov.polar_alignment_from_rotation(samples, LATITUDE, LONGITUDE, ELEVATION)
    assert estimate.exploratory
    assert estimate.kind == "fit"
    assert estimate.total_error_arcmin == pytest.approx(math.hypot(20, 30), rel=0.02)
    assert estimate.altitude_error_arcmin == pytest.approx(20, rel=0.05)
    assert estimate.azimuth_error_arcmin == pytest.approx(30 / math.cos(math.radians(LATITUDE)), rel=0.05)


@pytest.mark.parametrize("dec", [33.0, 60.0])
def test_polar_alignment_over_a_short_span_is_only_a_lower_bound(dec: float) -> None:
    """A one-hour run gives a lower bound that does not exceed the truth."""
    altitude, east_west = math.radians(20 / 60), math.radians(30 / 60)
    estimate = ov.polar_alignment_from_rotation(
        _rotation_samples(altitude, east_west, 1.0, dec=dec), LATITUDE, LONGITUDE, ELEVATION
    )
    assert estimate.kind == "lower_bound"
    assert 0.0 < estimate.total_error_arcmin <= math.hypot(20, 30) * 1.02


def test_polar_alignment_with_too_few_samples_says_insufficient() -> None:
    """Fewer than five samples in a run gives insufficient data."""
    estimate = ov.polar_alignment_from_rotation(
        _rotation_samples(0.01, 0.01, 3.0, count=4), LATITUDE, LONGITUDE, ELEVATION
    )
    assert estimate.kind == "insufficient_data"


def test_moon_phase_function_follows_krisciunas_and_schaefer() -> None:
    """The phase function is 1 at full Moon and about 0.09 at quarter phase."""
    assert sm.moon_phase_function(0.0) == pytest.approx(1.0)
    assert sm.moon_phase_function(90.0) == pytest.approx(0.091, abs=0.003)
    assert sm.moon_phase_function(90.0) > sm.moon_phase_function(150.0)
    assert sm.moon_phase_angle_deg(1.0) == pytest.approx(0.0)
    assert sm.moon_phase_angle_deg(0.5) == pytest.approx(90.0)


def test_moon_term_uses_the_moons_own_extinction_and_phase() -> None:
    """A low Moon is dimmer than a high one, a quarter Moon than full."""

    def term(altitude: float, illumination: float) -> float:
        """Give the Moon term for a Moon at an altitude and lit fraction.

        Returns
        -------
        term : `float`
            The third model feature.
        """
        geometry = ov.SkyGeometry(
            epoch_seconds=0.0,
            altitude_deg=60.0,
            airmass=ov.airmass_from_altitude(60.0),
            moon_altitude_deg=altitude,
            moon_separation_deg=60.0,
            moon_illumination_fraction=illumination,
        )
        features = sm.sky_features(geometry)
        assert features is not None
        return features[2]

    assert term(-5.0, 1.0) == pytest.approx(0.0)
    assert term(10.0, 1.0) < term(70.0, 1.0)
    assert term(70.0, 0.5) / term(70.0, 1.0) == pytest.approx(0.091, abs=0.003)
