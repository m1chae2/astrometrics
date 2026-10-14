"""Purpose: Tests for the spectral focus-sweep script.

Description: The script reads spectral frames of one star taken at several
focuser positions, measures the trail width in wavelength bands, fits a
parabola of width against focuser position for each band, and names the
position that gives the sharpest spectrum at 5500 A. These tests check the
parabola fit on exact data, the handling of data that cannot be fitted, and the
whole script on synthetic frames, written as FITS files with a fake
``FOCUSPOS`` card, whose trail width depends quadratically on the focuser
position and whose best position changes with wavelength.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.foundation.errors import ProcessingError
from astrometricslib.models.measured_line_spread import MeasuredLineSpread
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import FWHM_PER_SIGMA
from astrometricslib.pipelines.spectroscopy.test.pre_processing.test_measured_line_spread import (
    build_line_spread_pipeline,
)
from astrometricslib.pipelines.spectroscopy.test.test_result_diagnostics import (
    FRAME_SHAPE,
    ZERO_ORDER_XY,
)
from astrometricslib.scripts.spectral_focus_sweep import (
    FOCUS_TARGET_ANGSTROM,
    FrameProfile,
    analyse_sweep,
    fit_focus_curve,
    format_report,
    measure_frame,
    read_focuser_position,
    run_focus_sweep,
)
from astrometricslib.test.synthetic import make_spectral_frame

# The fake focuser positions of the sweep, and the shape of the width.
POSITIONS = (960, 980, 1000, 1020, 1040)
MINIMUM_SIGMA_PX = 1.8
CURVATURE_PX_PER_STEP_SQUARED = 0.0006
# The best focuser position is 1000 at 5500 A and moves by this many steps per
# Angstrom: 985 at 4500 A and 1015 at 6500 A, a red end that focuses
# farther out.
BEST_POSITION_SLOPE_STEPS_PER_ANGSTROM = 0.015
BEST_POSITION_AT_5500 = 1000.0
# How far the recovered positions may sit from the injected ones, in steps. The
# generator's straight-line wavelengths differ from the instrument's grating
# equation by under 1 percent, which moves the best position by about 1 step.
POSITION_TOLERANCE_STEPS = 3.0


def true_best_position(wavelength_angstrom: float) -> float:
    """Give the injected best focuser position at a wavelength.

    Parameters
    ----------
    wavelength_angstrom : `float`
        The wavelength, in Angstroms.

    Returns
    -------
    position : `float`
        The focuser position where the trail is narrowest at that wavelength.
    """
    return BEST_POSITION_AT_5500 + BEST_POSITION_SLOPE_STEPS_PER_ANGSTROM * (wavelength_angstrom - 5500.0)


def true_sigma(position: float, wavelength_angstrom: float) -> float:
    """Give the injected trail sigma at a focuser position and wavelength.

    Parameters
    ----------
    position : `float`
        The focuser position.
    wavelength_angstrom : `float`
        The wavelength, in Angstroms.

    Returns
    -------
    sigma_px : `float`
        The sigma, in pixels: a floor plus a parabola in the distance from
        that wavelength's best position.
    """
    offset = position - true_best_position(wavelength_angstrom)
    return MINIMUM_SIGMA_PX + CURVATURE_PX_PER_STEP_SQUARED * offset**2


def write_sweep(directory: Path) -> list[str]:
    """Write one synthetic frame for each focuser position.

    Parameters
    ----------
    directory : `pathlib.Path`
        The folder to write into.

    Returns
    -------
    paths : `list` [`str`]
        The frames' paths, in a shuffled order (the script must not depend
        on the order).
    """
    paths = []
    for position in POSITIONS:
        nodes = tuple((float(w), true_sigma(position, float(w))) for w in range(3000, 9500, 100))
        frame = make_spectral_frame(
            zero_order_xy=ZERO_ORDER_XY,
            angle_deg=2.0,
            dispersion_a_per_px=11.4,
            trail_length_px=800,
            lines=(),
            shape=FRAME_SHAPE,
            trace_sigma_by_wavelength=nodes,
            seed=position,
        )
        path = directory / f"sweep_{position}.fits"
        header = fits.Header()
        header["FOCUSPOS"] = position
        fits.PrimaryHDU(frame.image.astype(np.float32), header=header).writeto(path)
        paths.append(str(path))
    return [paths[index] for index in (3, 0, 4, 2, 1)]


def profile_from(position: float, fwhm_px: list[float], centres: list[float]) -> FrameProfile:
    """Build a frame profile from given band widths.

    Parameters
    ----------
    position : `float`
        The focuser position.
    fwhm_px : `list` [`float`]
        The FWHM of each band, in pixels.
    centres : `list` [`float`]
        The band centres, in Angstroms.

    Returns
    -------
    frame : `FrameProfile`
        The profile, with 11.4 Angstroms per pixel.
    """
    profile = MeasuredLineSpread(
        wavelength_angstrom=centres,
        fwhm_angstrom=[value * 11.4 for value in fwhm_px],
        fwhm_px=fwhm_px,
        scatter_px=[0.0] * len(centres),
        sample_count=[30] * len(centres),
    )
    return FrameProfile(path=f"frame_{position}.fits", focuser_position=position, profile=profile)


def test_the_fit_finds_the_vertex_of_an_exact_parabola() -> None:
    """Exact parabola data give its vertex, its minimum and its values."""
    positions = np.array([100.0, 110.0, 120.0, 130.0, 140.0])
    fwhm = 3.0 + 0.002 * (positions - 123.0) ** 2

    fit = fit_focus_curve(positions, fwhm)

    assert fit is not None
    assert fit.best_position == pytest.approx(123.0)
    assert fit.minimum_fwhm_px == pytest.approx(3.0)
    assert fit.is_inside_sweep is True
    assert fit.fwhm_at(100.0) == pytest.approx(3.0 + 0.002 * 23.0**2)


def test_the_fit_reports_a_minimum_outside_the_sweep() -> None:
    """A vertex beyond the sampled positions is marked as an extrapolation."""
    positions = np.array([100.0, 110.0, 120.0])
    fwhm = 3.0 + 0.002 * (positions - 150.0) ** 2

    fit = fit_focus_curve(positions, fwhm)

    assert fit is not None
    assert fit.best_position == pytest.approx(150.0)
    assert fit.is_inside_sweep is False


def test_the_fit_works_with_large_position_numbers() -> None:
    """Focuser positions near 30000 lose no accuracy."""
    positions = np.array([29900.0, 29950.0, 30000.0, 30050.0, 30100.0])
    fwhm = 2.5 + 1e-5 * (positions - 29987.0) ** 2

    fit = fit_focus_curve(positions, fwhm)

    assert fit is not None
    assert fit.best_position == pytest.approx(29987.0, abs=1e-6)


@pytest.mark.parametrize(
    ("positions", "fwhm"),
    [
        ([100.0, 110.0], [3.0, 3.2]),
        ([100.0, 100.0, 100.0, 110.0], [3.0, 3.1, 3.2, 3.3]),
        ([100.0, 110.0, 120.0, 130.0], [2.0, 3.0, 3.0, 2.0]),
        ([100.0, 110.0, 120.0], [5.0, 4.0, 3.0]),
        ([100.0, 110.0, np.nan], [3.0, 2.0, 3.0]),
    ],
)
def test_data_without_a_minimum_gives_no_fit(positions: list[float], fwhm: list[float]) -> None:
    """Too few positions, a hill, a straight line or NaN data give `None`."""
    assert fit_focus_curve(positions, fwhm) is None


def test_a_valley_shaped_set_of_points_gives_a_fit() -> None:
    """Four points forming a valley fit a parabola, vertex in the middle."""
    fit = fit_focus_curve([100.0, 110.0, 120.0, 130.0], [3.0, 2.0, 2.0, 3.0])

    assert fit is not None
    assert fit.best_position == pytest.approx(115.0)


def test_the_analysis_picks_the_position_for_5500_and_reports_the_ends() -> None:
    """Profiles built from known parabolas give the known recommendation."""
    centres = [4400.0, 4800.0, 5200.0, 5600.0, 6000.0, 6400.0, 6800.0]
    frames = [
        profile_from(
            position,
            [FWHM_PER_SIGMA * true_sigma(position, centre) for centre in centres],
            centres,
        )
        for position in POSITIONS
    ]

    result = analyse_sweep(frames)

    assert result.recommended_position == pytest.approx(true_best_position(5500.0), abs=0.5)
    assert result.band_fits[4400.0].best_position == pytest.approx(true_best_position(4400.0), abs=1e-6)
    assert result.band_fits[6800.0].best_position == pytest.approx(true_best_position(6800.0), abs=1e-6)
    for wavelength in (4500.0, 6500.0):
        expected = FWHM_PER_SIGMA * true_sigma(result.recommended_position, wavelength)
        assert result.check_fwhm_px[wavelength] == pytest.approx(expected, rel=0.03)
    # The red end is wider than the blue end at the chosen position, or the
    # opposite, but the two differ because the best positions differ.
    assert result.probe_fits[6500.0].best_position > result.probe_fits[4500.0].best_position


def test_the_report_lists_frames_bands_and_the_recommendation() -> None:
    """The text names the recommended position and the end FWHM values."""
    centres = [4400.0, 4800.0, 5200.0, 5600.0, 6000.0, 6400.0, 6800.0]
    frames = [
        profile_from(position, [FWHM_PER_SIGMA * true_sigma(position, c) for c in centres], centres)
        for position in POSITIONS
    ]

    report = format_report(frames, analyse_sweep(frames))

    assert "Focus position that minimizes the FWHM at 5500 A: 1000.0" in report
    assert "FWHM at 4500 A" in report
    assert "FWHM at 6500 A" in report
    assert "FOCUSPOS     960.0" in report


def test_the_whole_script_recovers_the_best_focus_from_synthetic_frames(tmp_path: Path) -> None:
    """Five FITS frames with FOCUSPOS give the injected best focus."""
    paths = write_sweep(tmp_path)
    pipeline = build_line_spread_pipeline()

    frames, result = run_focus_sweep(paths, pipeline, star_position=ZERO_ORDER_XY)

    assert sorted(frame.focuser_position for frame in frames) == [float(p) for p in POSITIONS]
    assert result.recommended_position == pytest.approx(
        true_best_position(FOCUS_TARGET_ANGSTROM), abs=POSITION_TOLERANCE_STEPS
    )
    assert result.probe_fits[4500.0].best_position == pytest.approx(
        true_best_position(4500.0), abs=POSITION_TOLERANCE_STEPS
    )
    assert result.probe_fits[6500.0].best_position == pytest.approx(
        true_best_position(6500.0), abs=POSITION_TOLERANCE_STEPS
    )
    # At the recommended position the ends are off their own best by the
    # injected amount.
    for wavelength in (4500.0, 6500.0):
        expected = FWHM_PER_SIGMA * true_sigma(result.recommended_position, wavelength)
        assert result.check_fwhm_px[wavelength] == pytest.approx(expected, rel=0.08)
    # Each band's best position grows with wavelength.
    best = [fit.best_position for fit in result.band_fits.values() if fit is not None]
    assert len(best) >= 8
    assert np.all(np.diff(best) > 0)


def test_the_focuser_position_is_read_from_the_focuspos_card(tmp_path: Path) -> None:
    """The header card is read as a number, and a missing card is an error."""
    with_card = tmp_path / "with.fits"
    without_card = tmp_path / "without.fits"
    header = fits.Header()
    header["FOCUSPOS"] = 29936
    fits.PrimaryHDU(np.zeros((8, 8), dtype=np.float32), header=header).writeto(with_card)
    fits.PrimaryHDU(np.zeros((8, 8), dtype=np.float32)).writeto(without_card)

    assert read_focuser_position(str(with_card)) == pytest.approx(29936.0)
    with pytest.raises(ProcessingError, match="FOCUSPOS"):
        read_focuser_position(str(without_card))


def test_fewer_than_three_frames_are_refused(tmp_path: Path) -> None:
    """A parabola needs three positions, so two frames are an error."""
    paths = write_sweep(tmp_path)[:2]

    with pytest.raises(ProcessingError, match="at least 3"):
        run_focus_sweep(paths, build_line_spread_pipeline(), star_position=ZERO_ORDER_XY)


def test_a_frame_with_no_trail_width_is_refused(tmp_path: Path) -> None:
    """A frame without a star at the given position cannot give a profile."""
    path = tmp_path / "blank.fits"
    header = fits.Header()
    header["FOCUSPOS"] = 1000
    fits.PrimaryHDU(np.full(FRAME_SHAPE, 150.0, dtype=np.float32), header=header).writeto(path)

    with pytest.raises(ProcessingError):
        measure_frame(str(path), build_line_spread_pipeline(), ZERO_ORDER_XY)
