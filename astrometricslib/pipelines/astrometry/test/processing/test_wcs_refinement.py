"""Purpose: Tests for refitting a plate solution to the Gaia stars of a field.

Description: Against Gaia DR3 on 2026-10-10, the plate solution of Alnath was
2.7 arcseconds (1.4 pixels) off and NGC 2403's 0.6 arcsecond off, while the
astrometry gate passed both because it only compared the solver with itself.
The refit takes the solution and the detected stars' pixel positions, matches
them to Gaia, fits a new tangent-plane solution with SIP distortion and keeps
it only when it fits better. These tests build a field whose true solution is
known, give the refit a worse one, and check that it comes back to the truth
within a fraction of a pixel, that it ignores wrong matches, and that it
refuses when there is too little to fit. No network is used.
"""

from unittest.mock import MagicMock

import astropy.units as u
import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy.table import Table
from astropy.wcs import WCS, Sip

from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.pipelines.astrometry.processing import star_identifier as star_identifier_module
from astrometricslib.pipelines.astrometry.processing import wcs_refinement
from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier

CENTRE_RA_DEG = 250.445
CENTRE_DEC_DEG = 36.157
PIXEL_SCALE_ARCSEC = 1.92
IMAGE_SHAPE = (3008, 3008)


def make_wcs(
    ra_deg: float,
    dec_deg: float,
    scale_arcsec: float = PIXEL_SCALE_ARCSEC,
    rotation_deg: float = 0.0,
    with_sip: bool = True,
) -> WCS:
    """Build a tangent-plane solution for the test image.

    Parameters
    ----------
    ra_deg, dec_deg : `float`
        The sky position of the reference pixel.
    scale_arcsec : `float`, optional
        The pixel size.
    rotation_deg : `float`, optional
        The rotation of the pixel grid.
    with_sip : `bool`, optional
        Whether to add a small second-order distortion (a few tenths of an
        arcsecond at the corners).

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        The solution.
    """
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [1504.5, 1504.5]
    wcs.wcs.crval = [ra_deg, dec_deg]
    cosine, sine = np.cos(np.radians(rotation_deg)), np.sin(np.radians(rotation_deg))
    wcs.wcs.cd = np.array([[-cosine, sine], [sine, cosine]]) * scale_arcsec / 3600.0
    wcs.wcs.ctype = ["RA---TAN-SIP", "DEC--TAN-SIP"] if with_sip else ["RA---TAN", "DEC--TAN"]
    if with_sip:
        a = np.zeros((3, 3))
        b = np.zeros((3, 3))
        a[2, 0], a[1, 1], b[0, 2] = 2e-7, 1e-7, -1.5e-7
        wcs.sip = Sip(a, b, None, None, wcs.wcs.crpix)
    return wcs


def make_field(star_count: int = 600, wrong_fraction: float = 0.15) -> tuple[np.ndarray, SkyCoord, WCS]:
    """Build detected-star positions, the Gaia stars and the true solution.

    Parameters
    ----------
    star_count : `int`, optional
        How many stars.
    wrong_fraction : `float`, optional
        The share of detections that sit up to a few pixels from their real
        position (blends and bad centroids).

    Returns
    -------
    measured : `numpy.ndarray`
        The detected centroids: the true pixels plus 0.1 pixel of noise, and
        `wrong_fraction` of them moved by about 3 pixels.
    gaia : `astropy.coordinates.SkyCoord`
        The Gaia positions of the stars, from the true solution.
    truth : `astropy.wcs.WCS`
        The true solution.
    """
    generator = np.random.default_rng(3)
    truth = make_wcs(CENTRE_RA_DEG, CENTRE_DEC_DEG)
    true_pixels = generator.uniform(30, IMAGE_SHAPE[0] - 30, (star_count, 2))
    sky = truth.all_pix2world(true_pixels, 0)
    gaia = SkyCoord(sky[:, 0] * u.deg, sky[:, 1] * u.deg)
    measured = true_pixels + generator.normal(0, 0.1, true_pixels.shape)
    wrong = generator.random(star_count) < wrong_fraction
    measured[wrong] += generator.normal(0, 3, (int(wrong.sum()), 2))
    return measured, gaia, truth


def alnath_like_solution() -> WCS:
    """Build a worse solution: the error found on Alnath's stack.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        A solution 2.7 arcseconds from the truth, 0.03 percent off in scale,
        slightly rotated and with no distortion terms.
    """
    return make_wcs(
        CENTRE_RA_DEG + 0.81 / 3600.0 / np.cos(np.radians(CENTRE_DEC_DEG)),
        CENTRE_DEC_DEG - 2.6 / 3600.0,
        scale_arcsec=PIXEL_SCALE_ARCSEC * 1.0003,
        rotation_deg=0.01,
        with_sip=False,
    )


def error_against_truth_arcsec(wcs: WCS, truth: WCS) -> np.ndarray:
    """Measure how far a solution is from the truth over the field.

    Parameters
    ----------
    wcs, truth : `astropy.wcs.WCS`
        The solution to judge and the true one.

    Returns
    -------
    errors : `numpy.ndarray`
        The distance in arcseconds at 2,000 random pixels.
    """
    pixels = np.random.default_rng(9).uniform(100, IMAGE_SHAPE[0] - 100, (2000, 2))
    one = wcs.all_pix2world(pixels, 0)
    two = truth.all_pix2world(pixels, 0)
    return (
        SkyCoord(one[:, 0] * u.deg, one[:, 1] * u.deg)
        .separation(SkyCoord(two[:, 0] * u.deg, two[:, 1] * u.deg))
        .arcsec
    )


def test_a_solution_off_by_a_pixel_and_a_half_is_brought_back_to_the_truth() -> None:
    """A 2.7 arcsecond error falls under 0.1, despite 15% wrong detections."""
    measured, gaia, truth = make_field()
    solver = alnath_like_solution()
    assert np.median(error_against_truth_arcsec(solver, truth)) > 2.0

    refinement = wcs_refinement.refine_wcs_with_gaia(solver, measured, gaia, IMAGE_SHAPE)

    assert refinement.applied
    assert refinement.star_count > 400
    errors = error_against_truth_arcsec(refinement.wcs, truth)
    assert np.median(errors) < 0.05
    assert errors.max() < 0.15
    assert refinement.rms_before_arcsec > 2.0
    assert refinement.rms_after_arcsec < 0.4


def test_the_refit_reports_the_distance_it_moved_the_field() -> None:
    """The shift is the largest move over the image: about the error."""
    measured, gaia, _ = make_field()

    refinement = wcs_refinement.refine_wcs_with_gaia(alnath_like_solution(), measured, gaia, IMAGE_SHAPE)

    assert 2.0 < refinement.shift_arcsec < 6.0


def test_too_few_detected_stars_leave_the_solution_alone() -> None:
    """Below `MINIMUM_MATCHED_STARS` no fit is made and the reason is given."""
    measured, gaia, _ = make_field()
    solver = alnath_like_solution()

    refinement = wcs_refinement.refine_wcs_with_gaia(
        solver, measured[: wcs_refinement.MINIMUM_MATCHED_STARS - 1], gaia, IMAGE_SHAPE
    )

    assert not refinement.applied
    assert refinement.wcs is solver
    assert refinement.star_count == 0
    assert "too few" in refinement.reason


def test_gaia_stars_that_are_not_the_detected_stars_leave_the_solution_alone() -> None:
    """With no matches there is nothing to fit, so the solution is kept."""
    measured, _, _ = make_field()
    far = SkyCoord(
        (CENTRE_RA_DEG + 0.5 + np.arange(200) * 1e-3) * u.deg, np.full(200, CENTRE_DEC_DEG + 0.5) * u.deg
    )
    solver = alnath_like_solution()

    refinement = wcs_refinement.refine_wcs_with_gaia(solver, measured, far, IMAGE_SHAPE)

    assert not refinement.applied
    assert refinement.wcs is solver
    assert "matched Gaia" in refinement.reason


def test_a_solution_that_is_already_the_truth_is_not_made_worse() -> None:
    """The kept solution fits Gaia no worse than the given one."""
    measured, gaia, truth = make_field(wrong_fraction=0.0)

    refinement = wcs_refinement.refine_wcs_with_gaia(truth, measured, gaia, IMAGE_SHAPE)

    assert refinement.rms_after_arcsec <= refinement.rms_before_arcsec
    assert np.median(error_against_truth_arcsec(refinement.wcs, truth)) < 0.05


def test_two_solutions_far_apart_are_measured_as_far_apart() -> None:
    """The grid shift of the Alnath-like solution is its 2.7 arcsec error."""
    shift = wcs_refinement._grid_shift_arcsec(
        make_wcs(CENTRE_RA_DEG, CENTRE_DEC_DEG), alnath_like_solution(), IMAGE_SHAPE
    )

    assert 2.5 < shift < 6.0


def make_identifier() -> StarIdentifier:
    """Build an identifier with fake drivers, as the other tests do.

    Returns
    -------
    identifier : `StarIdentifier`
        An identifier that never reaches a network service.
    """
    config = MagicMock()
    config.get_maximum_identified_stars.return_value = None
    config.get_value.return_value = None
    return StarIdentifier(config=config, drivers=Drivers(plate_solve=MagicMock(), simbad=MagicMock()))


def gaia_table_for(gaia: SkyCoord) -> Table:
    """Build the table a Gaia query returns for these stars.

    Parameters
    ----------
    gaia : `astropy.coordinates.SkyCoord`
        The star positions.

    Returns
    -------
    table : `astropy.table.Table`
        Columns as `gaia_cone_driver` returns them, with no proper motion.
    """
    count = len(gaia)
    return Table({
        "source_id": [str(index) for index in range(count)],
        "ra": gaia.ra.deg,
        "dec": gaia.dec.deg,
        "phot_g_mean_mag": np.linspace(9.0, 16.0, count),
        "designation": [f"Gaia DR3 {index}" for index in range(count)],
        "pmra": np.zeros(count),
        "pmdec": np.zeros(count),
    })


def test_the_identifier_refits_the_solution_and_records_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """With Gaia stars for the field, the refit replaces the solution."""
    measured, gaia, truth = make_field()
    identifier = make_identifier()
    monkeypatch.setattr(
        StarIdentifier,
        "_query_gaia_region",
        staticmethod(lambda *a, **k: (gaia_table_for(gaia), gaia)),
    )
    sources = [{"x_centroid": x, "y_centroid": y} for x, y in measured]

    refined = identifier._refine_wcs_against_gaia(alnath_like_solution(), sources, *IMAGE_SHAPE)

    assert np.median(error_against_truth_arcsec(refined, truth)) < 0.05
    assert star_identifier_module.FLAG_WCS_REFINED in identifier.astrometry_flags
    assert identifier.wcs_refinement is not None and identifier.wcs_refinement.applied


def test_a_failed_gaia_download_keeps_the_solver_solution(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no Gaia stars the solution is returned as it came, with a flag."""
    identifier = make_identifier()
    monkeypatch.setattr(StarIdentifier, "_query_gaia_region", staticmethod(lambda *a, **k: (None, None)))
    solver = alnath_like_solution()

    result = identifier._refine_wcs_against_gaia(
        solver, [{"x_centroid": 10.0, "y_centroid": 10.0}], *IMAGE_SHAPE
    )

    assert result is solver
    assert star_identifier_module.FLAG_WCS_REFINEMENT_SKIPPED in identifier.astrometry_flags
    assert identifier.wcs_refinement is None


def test_the_refinement_names_the_stars_the_final_fit_used() -> None:
    """The indices point into the given positions and number `star_count`."""
    measured, gaia, _ = make_field()
    refinement = wcs_refinement.refine_wcs_with_gaia(
        alnath_like_solution(), np.array(measured), gaia, IMAGE_SHAPE
    )
    assert len(refinement.fit_star_indices) == refinement.star_count > 0
    assert max(refinement.fit_star_indices) < len(measured)
    assert len(set(refinement.fit_star_indices)) == refinement.star_count


def _refit_with_peaks(
    monkeypatch: pytest.MonkeyPatch, peak_of: object, noise: float | None, binned: bool = False
) -> tuple[StarIdentifier, list]:
    """Run the identifier's refit on a field whose sources carry peaks.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the Gaia query.
    peak_of : `Callable`
        Gives a source's peak from its index.
    noise : `float` or `None`
        The detector's last background noise.
    binned : `bool`, optional
        Whether the detection ran on a binned color frame.

    Returns
    -------
    identifier : `StarIdentifier`
        The identifier after the refit.
    measured : `list`
        The source centroids given to the refit.
    """
    measured, gaia, _ = make_field()
    identifier = make_identifier()
    identifier.detector.last_background_noise = noise
    identifier._detected_on_binned_copy = binned
    monkeypatch.setattr(
        StarIdentifier, "_query_gaia_region", staticmethod(lambda *a, **k: (gaia_table_for(gaia), gaia))
    )
    sources = [
        {"x_centroid": x, "y_centroid": y, "peak": peak_of(index)} for index, (x, y) in enumerate(measured)
    ]
    identifier._refine_wcs_against_gaia(alnath_like_solution(), sources, *IMAGE_SHAPE)
    return identifier, measured


def test_the_identifier_keeps_the_peak_over_noise_of_the_fit_stars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each fit star's peak is divided by the detector noise, in fit order."""
    identifier, _ = _refit_with_peaks(monkeypatch, lambda index: 100.0 + index, noise=4.0)
    refinement = identifier.wcs_refinement
    assert refinement is not None and refinement.star_count > 0
    assert identifier.gaia_fit_signal_reason == ""
    expected = tuple((100.0 + index) / 4.0 for index in refinement.fit_star_indices)
    assert identifier.gaia_fit_peak_over_noise == pytest.approx(expected)


@pytest.mark.parametrize(
    ("peak", "noise", "binned", "words"),
    [
        (50.0, 4.0, True, "binned copy"),
        (50.0, None, False, "no background noise"),
        (50.0, 0.0, False, "no background noise"),
        (float("nan"), 4.0, False, "positive peak"),
        (-5.0, 4.0, False, "positive peak"),
    ],
)
def test_the_identifier_gives_no_signal_when_the_peaks_or_noise_cannot_be_trusted(
    monkeypatch: pytest.MonkeyPatch, peak: float, noise: float | None, binned: bool, words: str
) -> None:
    """A binned frame, no noise or a bad peak leaves the list empty."""
    identifier, _ = _refit_with_peaks(monkeypatch, lambda index: peak, noise=noise, binned=binned)
    assert identifier.gaia_fit_peak_over_noise == ()
    assert words in identifier.gaia_fit_signal_reason
