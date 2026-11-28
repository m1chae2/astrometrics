"""Purpose: Tests for measuring a plate solution against Gaia stars.

Description: The plate solver's own fit says how well it agrees with itself.
The Gaia match asks the same question from the outside: it matches the
detected stars to Gaia stars at the date of the image, throws out wrong
matches, and reports the root mean square (RMS) distance of the stars that are
left. These tests build a field whose true solution is known, give the match
the true solution and a shifted one, and check the RMS, that wrong matches are
clipped, that the solution is never changed and that too little to match gives
a reason. No network is used.
"""

from unittest.mock import MagicMock

import astropy.units as u
import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy.table import Table
from astropy.wcs import WCS, Sip

from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib.pipelines.astrometry.processing import gaia_match, plate_solution_steps
from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier
from astrometricslib.pipelines.astrometry.test.gaia_field_fakes import use_field_stars

CENTRE_RA_DEG = 250.445
CENTRE_DEC_DEG = 36.157
PIXEL_SCALE_ARCSEC = 1.92
IMAGE_SHAPE = (3008, 3008)
CENTROID_NOISE_PIXELS = 0.1


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


def shifted_solution(shift_arcsec: float) -> WCS:
    """Build the true solution moved north by a constant.

    Parameters
    ----------
    shift_arcsec : `float`
        How far to move the reference point, in arcseconds.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        The solution a solver that was off by a constant would return.
    """
    return make_wcs(CENTRE_RA_DEG, CENTRE_DEC_DEG + shift_arcsec / 3600.0)


def test_the_true_solution_sits_at_the_centroid_noise_from_gaia() -> None:
    """The RMS is the 2-D centroid noise; wrong matches are clipped."""
    measured, gaia, truth = make_field()
    match = gaia_match.measure_gaia_match(truth, measured, gaia)

    expected = np.sqrt(2.0) * CENTROID_NOISE_PIXELS * PIXEL_SCALE_ARCSEC
    assert match.rms_arcsec == pytest.approx(expected, rel=0.25)
    assert match.reason == ""
    # The 15% of detections that are wrong by about 3 pixels do not count.
    assert 0.6 * len(measured) < match.star_count < 0.9 * len(measured)


def test_a_constant_shift_adds_to_the_rms_in_quadrature() -> None:
    """A solution 1 arcsec off adds 1 arcsec in quadrature."""
    measured, gaia, _ = make_field()
    on_truth = gaia_match.measure_gaia_match(make_wcs(CENTRE_RA_DEG, CENTRE_DEC_DEG), measured, gaia)
    shifted = gaia_match.measure_gaia_match(shifted_solution(1.0), measured, gaia)

    assert on_truth.rms_arcsec is not None and shifted.rms_arcsec is not None
    assert shifted.rms_arcsec == pytest.approx(np.hypot(on_truth.rms_arcsec, 1.0), rel=0.1)


def test_the_match_never_changes_the_solution_it_measures() -> None:
    """The WCS given comes back untouched: the check is a measurement only."""
    measured, gaia, _ = make_field()
    solution = shifted_solution(1.0)
    before = solution.to_header(relax=True).tostring()

    gaia_match.measure_gaia_match(solution, measured, gaia)

    assert solution.to_header(relax=True).tostring() == before


def test_the_match_names_the_stars_it_kept() -> None:
    """The indices point into the given positions and number `star_count`."""
    measured, gaia, truth = make_field()
    match = gaia_match.measure_gaia_match(truth, measured, gaia)

    assert len(match.star_indices) == match.star_count > 0
    assert max(match.star_indices) < len(measured)
    assert len(set(match.star_indices)) == match.star_count


def test_too_few_detected_stars_give_a_reason_and_no_rms() -> None:
    """With fewer stars than the minimum there is nothing to measure."""
    measured, gaia, truth = make_field()
    match = gaia_match.measure_gaia_match(truth, measured[:10], gaia)

    assert match.rms_arcsec is None and match.star_count == 0
    assert "too few" in match.reason


def test_gaia_stars_that_are_not_the_detected_stars_give_a_reason() -> None:
    """Gaia stars a degree away match nothing, so no RMS is given."""
    measured, gaia, truth = make_field()
    far = SkyCoord((gaia.ra.deg + 1.0) * u.deg, gaia.dec)

    match = gaia_match.measure_gaia_match(truth, measured, far)

    assert match.rms_arcsec is None
    assert "stars matched Gaia" in match.reason


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


def test_the_identifier_measures_the_solution_and_leaves_it_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """With Gaia stars, the identifier keeps the match and changes nothing."""
    measured, gaia, _ = make_field()
    identifier = make_identifier()
    use_field_stars(monkeypatch, gaia_table_for(gaia), gaia)
    sources = [{"x_centroid": x, "y_centroid": y} for x, y in measured]
    solution = shifted_solution(1.0)

    identifier._measure_wcs_against_gaia(solution, sources, *IMAGE_SHAPE)

    assert identifier.gaia_match is not None and identifier.gaia_match.rms_arcsec is not None
    assert identifier.gaia_match.rms_arcsec > 0.9
    assert plate_solution_steps.FLAG_GAIA_MATCH_SKIPPED not in identifier.astrometry_flags
    assert identifier.gaia_match_note == "measured"


def test_a_failed_gaia_download_records_the_flag_and_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no Gaia stars no RMS is measured, and the flag says so."""
    identifier = make_identifier()
    use_field_stars(monkeypatch)

    identifier._measure_wcs_against_gaia(
        shifted_solution(1.0), [{"x_centroid": 10.0, "y_centroid": 10.0}], *IMAGE_SHAPE
    )

    assert plate_solution_steps.FLAG_GAIA_MATCH_SKIPPED in identifier.astrometry_flags
    assert identifier.gaia_match is None


def _match_with_peaks(
    monkeypatch: pytest.MonkeyPatch, peak_of: object, noise: float | None, binned: bool = False
) -> StarIdentifier:
    """Run the identifier's Gaia match on a field whose sources carry peaks.

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
        The identifier after the match.
    """
    measured, gaia, _ = make_field()
    identifier = make_identifier()
    identifier.detector.last_background_noise = noise
    identifier._detected_on_binned_copy = binned
    use_field_stars(monkeypatch, gaia_table_for(gaia), gaia)
    sources = [
        {"x_centroid": x, "y_centroid": y, "peak": peak_of(index)} for index, (x, y) in enumerate(measured)
    ]
    identifier._measure_wcs_against_gaia(shifted_solution(0.5), sources, *IMAGE_SHAPE)
    return identifier


def test_the_identifier_keeps_the_peak_over_noise_of_the_matched_stars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each kept star's peak is divided by the noise, in match order."""
    identifier = _match_with_peaks(monkeypatch, lambda index: 100.0 + index, noise=4.0)
    match = identifier.gaia_match
    assert match is not None and match.star_count > 0
    assert identifier.gaia_match_signal_reason == ""
    expected = tuple((100.0 + index) / 4.0 for index in match.star_indices)
    assert identifier.gaia_match_peak_over_noise == pytest.approx(expected)


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
    identifier = _match_with_peaks(monkeypatch, lambda index: peak, noise=noise, binned=binned)
    assert identifier.gaia_match_peak_over_noise == ()
    assert words in identifier.gaia_match_signal_reason
