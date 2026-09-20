"""Purpose: Unit tests for the atmospheric mask.

Description: Earth's air absorbs light at fixed wavelengths, which makes
dark bands that belong to the air and not to the star. The classifier
leaves those wavelengths out of its comparison. These tests check that the
mask covers the right wavelengths, that a dip inside a band no longer
counts against a star, that a dip outside the bands still does, and that
the switch turns the whole stage off.
"""

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter1d

from astrometricslib.pipelines.spectroscopy.atmospheric_mask import (
    ATMOSPHERIC_BANDS_ANGSTROM,
    atmospheric_band_mask,
    mask_atmospheric_bands,
)
from astrometricslib.pipelines.spectroscopy.spectral_classifier import (
    _get_reference_templates,
    classify_spectral_type,
)

OXYGEN_A_BAND_CENTER_ANGSTROM = 7600.0
OXYGEN_A_BAND_DIP_SIGMA_ANGSTROM = 25.0


def _observed_a0v_spectrum(dip_center_angstrom: float | None) -> tuple[np.ndarray, np.ndarray]:
    """Build a spectrum of an A0V star, optionally with one dark dip.

    The dip is 15% deep. It stands for either the air's oxygen A band (when
    centered on it) or a real feature of the star (elsewhere).

    Returns
    -------
    wavelength_angstrom, intensity : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        The reference A0V spectrum blurred like the instrument would blur it,
        from 4200 to 8000 A, with the dip if one was asked for.
    """
    wavelength, flux = _get_reference_templates()["A0V"]
    blurred = gaussian_filter1d(flux, 43.0 / 2.355 / float(np.median(np.diff(wavelength))))
    keep = (wavelength >= 4200.0) & (wavelength <= 8000.0)
    wavelength, blurred = wavelength[keep], blurred[keep]
    if dip_center_angstrom is not None:
        dip = 0.15 * np.exp(
            -0.5 * ((wavelength - dip_center_angstrom) / OXYGEN_A_BAND_DIP_SIGMA_ANGSTROM) ** 2
        )
        blurred = blurred * (1.0 - dip)
    return wavelength, blurred


def test_band_mask_marks_inside_and_leaves_outside() -> None:
    """Wavelengths inside a band are `True`, everything else `False`."""
    name, start, end = ATMOSPHERIC_BANDS_ANGSTROM[1]
    assert name == "oxygen A band"

    is_in_band = atmospheric_band_mask(np.array([start - 1.0, start, (start + end) / 2, end, end + 1.0]))

    assert is_in_band.tolist() == [False, True, True, True, False]


def test_band_mask_covers_every_band_and_keeps_the_input_shape() -> None:
    """Each band's middle is masked, and the shape is kept."""
    middles = np.array([[(start + end) / 2 for _name, start, end in ATMOSPHERIC_BANDS_ANGSTROM]])

    is_in_band = atmospheric_band_mask(middles)

    assert is_in_band.shape == middles.shape
    assert is_in_band.all()


def test_band_mask_does_not_touch_the_balmer_lines() -> None:
    """None of the named absorption features lie inside an atmospheric band."""
    balmer_and_named_features = np.array([3950.0, 4102.0, 4300.0, 4340.0, 4861.0, 5175.0, 5893.0, 6563.0])

    assert not atmospheric_band_mask(balmer_and_named_features).any()


def test_masking_blanks_the_bands_and_leaves_the_input_alone() -> None:
    """Brightness in a band becomes NaN in a copy; the input is unchanged."""
    wavelength = np.array([6000.0, 6900.0, 7600.0, 8200.0, 9000.0])
    intensity = np.array([1.0, 2.0, 3.0, 4.0, 5.0])

    masked = mask_atmospheric_bands(wavelength, intensity)

    assert np.isnan(masked[[1, 2, 3]]).all()
    assert masked[[0, 4]].tolist() == [1.0, 5.0]
    assert intensity.tolist() == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_a_dip_from_the_air_no_longer_counts_against_the_star() -> None:
    """A 15% dip on the oxygen A band is ignored when bands are excluded."""
    wavelength, observed = _observed_a0v_spectrum(OXYGEN_A_BAND_CENTER_ANGSTROM)

    counted = classify_spectral_type(wavelength, observed, exclude_atmospheric_bands=False)
    ignored = classify_spectral_type(wavelength, observed, exclude_atmospheric_bands=True)

    assert ignored["rms"] < 0.01
    assert counted["rms"] > 3 * ignored["rms"]
    assert ignored["spectral_type"] == "A0V"


def test_a_dip_outside_the_bands_still_counts() -> None:
    """A dip that is not in a band belongs to the star and must still count.

    The dip is put near H-beta, away from the middle of the spectrum. The
    classifier divides each spectrum by its own median before comparing, and
    a dip right at the median's wavelength would shift that divisor.
    """
    wavelength, observed = _observed_a0v_spectrum(4800.0)

    counted = classify_spectral_type(wavelength, observed, exclude_atmospheric_bands=False)
    ignored = classify_spectral_type(wavelength, observed, exclude_atmospheric_bands=True)

    assert ignored["rms"] == pytest.approx(counted["rms"], rel=0.15)
    assert ignored["rms"] > 0.02


def test_leaving_bands_out_does_not_move_the_score_of_a_dip_mid_spectrum() -> None:
    """The score must not change just because band samples were removed.

    This is the regression behind the switch to a best-fit scale. The
    spectrum falls toward the red, so its median sits near 6100 A. A dip at
    that wavelength used to change the median, and leaving band samples out
    moved the median again, so the same dip scored 0.047 with the bands in
    and 0.019 with them out. On real A stars the same effect flipped the
    best type from A0V to B9V.
    """
    wavelength, observed = _observed_a0v_spectrum(6000.0)

    counted = classify_spectral_type(wavelength, observed, exclude_atmospheric_bands=False)
    ignored = classify_spectral_type(wavelength, observed, exclude_atmospheric_bands=True)

    assert ignored["rms"] == pytest.approx(counted["rms"], rel=0.1)
    assert ignored["spectral_type"] == counted["spectral_type"] == "A0V"


def test_excluding_is_the_default() -> None:
    """The classifier leaves the atmospheric bands out unless told not to."""
    wavelength, observed = _observed_a0v_spectrum(OXYGEN_A_BAND_CENTER_ANGSTROM)

    default = classify_spectral_type(wavelength, observed)
    explicit = classify_spectral_type(wavelength, observed, exclude_atmospheric_bands=True)

    assert default["rms"] == pytest.approx(explicit["rms"])


def test_a_clean_spectrum_is_unchanged_by_the_mask() -> None:
    """With no atmospheric dip, leaving the bands out changes little."""
    wavelength, observed = _observed_a0v_spectrum(None)

    counted = classify_spectral_type(wavelength, observed, exclude_atmospheric_bands=False)
    ignored = classify_spectral_type(wavelength, observed, exclude_atmospheric_bands=True)

    assert counted["spectral_type"] == ignored["spectral_type"] == "A0V"
    assert counted["rms"] < 0.01
    assert ignored["rms"] < 0.01
