"""Purpose: Test the flux uncertainties attached to each flux (review item S8).

Description: The CCD equation (the noise budget of a sensor) gives the
1-sigma uncertainty of a star's flux from its own counts, the sky, the read
noise, and the gain. These tests check the equation term by term against
hand-worked numbers, check that the error scales as the square root of the
flux when the sky is noise free, recover the scatter of noisy synthetic
frames to within 10 percent, check that the worker reports errors in ADU per
second, and check where the gain and read noise come from (the camera
profile, then the FITS header, then an assumption).
"""

import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.camera_profile import (
    CameraProfile,
    ProvenancedValue,
    SensorSettingNoise,
    ValueProvenance,
)
from astrometricslib.pipelines.photometry.pre_processing.detector_noise import (
    DetectorNoise,
    resolve_detector_noise,
)
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import (
    _process_single_frame_worker,
    aperture_flux_error_adu,
    measure_aperture_photometry,
)
from astrometricslib.test.synthetic import SyntheticStar, make_photometry_fits, make_photometry_frame

SATURATION_ADU = 65535.0
FRAME_SHAPE = (80, 80)


def _profile(**changes: object) -> CameraProfile:
    """Build a camera profile for the gain tests.

    Parameters
    ----------
    **changes
        Fields of `CameraProfile` to set, such as ``sensor_noise``.

    Returns
    -------
    profile : `CameraProfile`
        A profile with the required fields filled in.
    """
    assumed = ValueProvenance(kind="assumed", source="test")
    return CameraProfile(
        camera_name="Test camera",
        clip_ceiling_adu=ProvenancedValue(value=65535.0, provenance=assumed),
        saturation_threshold_adu=ProvenancedValue(value=65000.0, provenance=assumed),
        **changes,
    )


def _measured(value: float) -> ProvenancedValue:
    """Wrap a number as a measured profile value.

    Returns
    -------
    value : `ProvenancedValue`
        The number with a ``measured`` provenance.
    """
    return ProvenancedValue(value=value, provenance=ValueProvenance(kind="measured", source="test"))


def test_the_ccd_equation_matches_a_hand_worked_example() -> None:
    """Each term adds what the equation says it should.

    The star has 1000 ADU, the sky 10 ADU per pixel, the gain is 2 e-/ADU and
    the read noise 5 e-. The aperture has 50 pixels and the sky ring 300.

    * Star: 1000 x 2 = 2000 e-.
    * Aperture sky and read noise: 50 x (20 + 25) = 2250 e-.
    * Sky level: (50**2 / 300) x (20 + 25) = 375 e-.
    * Dark current of 3 e- per pixel: 50 x 3 = 150 e- in the aperture, and
      (50**2 / 300) x 3 = 25 e- in the sky level, because each ring pixel
      carries the same dark noise.
    """
    noise = DetectorNoise(
        gain_e_per_adu=2.0, read_noise_e=5.0, gain_is_assumed=False, read_noise_is_assumed=False
    )

    without_dark = aperture_flux_error_adu(1000.0, 10.0, 50.0, 300.0, noise)
    with_dark = aperture_flux_error_adu(1000.0, 10.0, 50.0, 300.0, noise, dark_current_e_per_pixel=3.0)

    assert without_dark == pytest.approx(math.sqrt(2000.0 + 2250.0 + 375.0) / 2.0)
    assert with_dark == pytest.approx(math.sqrt(2000.0 + 2250.0 + 375.0 + 150.0 + 25.0) / 2.0)


def test_with_unit_gain_and_no_noise_the_error_is_the_square_root_of_the_counts() -> None:
    """A 10000 ADU star on a dark, noise-free sky has an error of 100 ADU."""
    error = aperture_flux_error_adu(10000.0, 0.0, 50.0, 300.0, DetectorNoise())

    assert error == pytest.approx(100.0)


def test_a_negative_flux_or_sky_does_not_make_the_variance_negative() -> None:
    """A faint star or the sky can read below zero; the error stays real."""
    error = aperture_flux_error_adu(-50.0, -3.0, 50.0, 300.0, DetectorNoise(read_noise_e=4.0))

    assert error == pytest.approx(math.sqrt(50.0 * 16.0 + (50.0**2 / 300.0) * 16.0))


def test_the_error_is_zero_for_a_star_too_near_the_edge_to_measure() -> None:
    """The out-of-frame measurement reports zero flux and zero error."""
    frame = make_photometry_frame([SyntheticStar(5.0, 5.0, 5000.0)], shape=FRAME_SHAPE)

    measurement = measure_aperture_photometry(frame, 5.0, 5.0, saturation_threshold_adu=SATURATION_ADU)

    assert measurement.net_flux_adu == pytest.approx(0.0, abs=1e-12)
    assert measurement.flux_error_adu == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("gain", [1.0, 2.5])
def test_the_error_grows_as_the_square_root_of_the_flux_on_a_noise_free_sky(gain: float) -> None:
    """Across three brightness levels, error squared over flux is a constant.

    The sky is zero and the frame has no added noise, so only the star's own
    counts make the error. With no read noise, the error is
    ``sqrt(flux / gain)``. The three stars differ by factors of 10 and 100,
    so the errors differ by factors of sqrt(10) and 10.
    """
    fluxes = []
    errors = []
    for total_adu in (1000.0, 10000.0, 100000.0):
        frame = make_photometry_frame(
            [SyntheticStar(40.0, 40.0, total_adu, 3.0)], shape=FRAME_SHAPE, sky_adu=0.0, add_noise=False
        )
        measurement = measure_aperture_photometry(
            frame,
            40.0,
            40.0,
            saturation_threshold_adu=SATURATION_ADU,
            noise=DetectorNoise(gain_e_per_adu=gain, gain_is_assumed=False),
        )
        fluxes.append(measurement.net_flux_adu)
        errors.append(measurement.flux_error_adu)

    assert errors[1] / errors[0] == pytest.approx(math.sqrt(fluxes[1] / fluxes[0]), rel=1e-6)
    assert errors[2] / errors[0] == pytest.approx(math.sqrt(fluxes[2] / fluxes[0]), rel=1e-6)
    for flux, error in zip(fluxes, errors, strict=True):
        assert error**2 / flux == pytest.approx(1.0 / gain, rel=1e-6)


def test_the_predicted_error_matches_the_scatter_of_noisy_frames_within_ten_percent() -> None:
    """The measured scatter of an injected star equals the predicted error.

    The same star is drawn on 400 frames with independent Poisson and read
    noise (gain 2 e-/ADU, read noise 6 e-, sky 100 ADU). The scatter of the
    measured flux across frames is compared with the mean error the CCD
    equation predicted for each frame.
    """
    gain, read_noise_e = 2.0, 6.0
    noise = DetectorNoise(
        gain_e_per_adu=gain, read_noise_e=read_noise_e, gain_is_assumed=False, read_noise_is_assumed=False
    )
    star = SyntheticStar(40.3, 39.6, 20000.0, 3.0)
    fluxes, errors = [], []
    for seed in range(400):
        frame = make_photometry_frame(
            [star],
            shape=FRAME_SHAPE,
            sky_adu=100.0,
            read_noise_adu=read_noise_e / gain,
            gain_e_per_adu=gain,
            seed=seed,
        )
        measurement = measure_aperture_photometry(
            frame, star.x, star.y, saturation_threshold_adu=SATURATION_ADU, noise=noise
        )
        fluxes.append(measurement.net_flux_adu)
        errors.append(measurement.flux_error_adu)

    measured_scatter = float(np.std(fluxes, ddof=1))
    predicted_error = float(np.mean(errors))

    assert measured_scatter == pytest.approx(predicted_error, rel=0.10)


def _write_frame(path: Path, exposure_s: float) -> None:
    """Write one noisy frame with a single star.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write the file.
    exposure_s : `float`
        The ``EXPTIME`` card.
    """
    make_photometry_fits(
        path,
        [SyntheticStar(40.0, 40.0, 20000.0, 3.0)],
        date_obs="2026-05-24T04:58:30.570",
        exptime_s=exposure_s,
        shape=FRAME_SHAPE,
        seed=1,
    )


def test_the_worker_reports_flux_and_error_in_adu_per_second(tmp_path: Path) -> None:
    """The worker divides both the flux and its error by the exposure time.

    It also returns the exposure time it used, so the BJD_TDB step can find
    the middle of the exposure.
    """
    path = tmp_path / "frame.fits"
    _write_frame(path, exposure_s=30.0)
    with fits.open(path, memmap=False) as handle:
        data = handle[0].data.astype(float)
    direct = measure_aperture_photometry(data, 40.0, 40.0, saturation_threshold_adu=SATURATION_ADU)

    _, result = _process_single_frame_worker((
        str(path),
        [("star0", 40.0, 40.0)],
        [],
        SATURATION_ADU,
        DetectorNoise(),
    ))

    assert isinstance(result, tuple)
    flux, _is_saturated, error = result[1]["star0"]
    assert result[0] == datetime(2026, 5, 24, 4, 58, 30, 570000)
    assert result[7] == pytest.approx(30.0)
    # The worker centres on the star's own centroid, so allow a small
    # difference from the aperture placed at the true position.
    assert flux == pytest.approx(direct.net_flux_adu / 30.0, rel=0.01)
    assert error == pytest.approx(direct.flux_error_adu / 30.0, rel=0.01)


# The setting the gain tests measure at: gain 0, offset 10, no binning.
SETTING_HEADER = {"GAIN": 0.0, "OFFSET": 10, "XBINNING": 1, "YBINNING": 1}


def _profile_with_noise_at_gain_zero(**changes: object) -> CameraProfile:
    """Build a profile with gain 1.1 and read noise 3.2 at one setting.

    Returns
    -------
    profile : `CameraProfile`
        A profile whose only noise entry is for gain 0, offset 10, 1x1.
    """
    entry = SensorSettingNoise(
        gain_setting="0",
        offset="10",
        binning="1x1",
        gain_e_per_adu=_measured(1.1),
        read_noise_e=_measured(3.2),
        **changes,
    )
    return _profile(sensor_noise=(entry,))


def test_the_profile_gain_comes_first_then_egain_and_gain_is_never_used() -> None:
    """The gain comes from the profile, then ``EGAIN``, never ``GAIN``.

    The ``GAIN`` card of ZWO cameras is a setting index, not electrons per
    ADU, so a header with only ``GAIN`` leaves the gain assumed.
    """
    profile = _profile_with_noise_at_gain_zero()
    bare = _profile()
    header = fits.Header({"EGAIN": 0.8, "RDNOISE": 2.0, **SETTING_HEADER})

    from_profile = resolve_detector_noise(profile, header)
    from_header = resolve_detector_noise(bare, header)
    only_gain_card = resolve_detector_noise(bare, fits.Header({"GAIN": 120.0}))
    nothing = resolve_detector_noise(bare, None)

    assert (from_profile.gain_e_per_adu, from_profile.read_noise_e) == (1.1, 3.2)
    assert not from_profile.gain_is_assumed
    assert not from_profile.read_noise_is_assumed
    assert from_profile.profile_gap_reason is None
    assert (from_header.gain_e_per_adu, from_header.read_noise_e) == (0.8, 2.0)
    assert not from_header.gain_is_assumed
    assert from_header.profile_gap_reason is not None
    assert (only_gain_card.gain_e_per_adu, only_gain_card.gain_is_assumed) == (1.0, True)
    assert nothing.gain_is_assumed
    assert nothing.read_noise_is_assumed


def test_a_frame_at_another_setting_gets_nothing_from_the_profile() -> None:
    """Gain and read noise are used only at the setting they were recorded at.

    A frame at gain 100 must not borrow the gain-0 entry, and the result says
    why. A header ``EGAIN`` may still fill the gap.
    """
    profile = _profile_with_noise_at_gain_zero()
    other_gain = {**SETTING_HEADER, "GAIN": 100.0}
    other_offset = {**SETTING_HEADER, "OFFSET": 30}
    other_binning = {**SETTING_HEADER, "XBINNING": 2, "YBINNING": 2}

    for header in (other_gain, other_offset, other_binning):
        noise = resolve_detector_noise(profile, header)
        assert noise.gain_is_assumed and noise.read_noise_is_assumed
        assert noise.gain_e_per_adu == pytest.approx(1.0)
        assert "no gain and read noise entry" in (noise.profile_gap_reason or "")
    with_egain = resolve_detector_noise(profile, {**other_gain, "EGAIN": 0.9})
    assert with_egain.gain_e_per_adu == pytest.approx(0.9)
    assert not with_egain.gain_is_assumed


def test_the_setting_can_be_given_by_a_frame_record_instead_of_a_header() -> None:
    """A frame record's gain, offset and binning pick the entry too."""
    profile = _profile_with_noise_at_gain_zero()

    noise = resolve_detector_noise(profile, None, gain_setting="0.0", offset="10", binning=1)

    assert (noise.gain_e_per_adu, noise.read_noise_e) == (1.1, 3.2)


def test_an_unmigrated_profile_gives_the_migration_reason() -> None:
    """Old single values are not applied; the reason names the migration."""
    profile = _profile(has_unkeyed_noise_values=True)

    noise = resolve_detector_noise(profile, SETTING_HEADER)

    assert noise.gain_is_assumed and noise.read_noise_is_assumed
    assert noise.profile_gap_reason == (
        "camera profile has no setting-keyed gain and read noise; run the profile migration"
    )


@pytest.mark.parametrize("bad_value", [0.0, -1.0, float("nan"), "text"])
def test_an_unusable_egain_card_leaves_the_gain_assumed(bad_value: object) -> None:
    """Zero, negative, NaN and non-numeric ``EGAIN`` values are not used."""
    noise = resolve_detector_noise(_profile(), {"EGAIN": bad_value})

    assert noise.gain_is_assumed
    assert noise.gain_e_per_adu == pytest.approx(1.0)


def test_existing_profiles_without_gain_still_load() -> None:
    """A profile with no noise entries is valid."""
    profile = _profile()

    assert profile.sensor_noise == ()
    assert profile.has_unkeyed_noise_values is False
