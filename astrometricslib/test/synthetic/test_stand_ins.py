"""Purpose: Tests of the shared test stand-ins: sky map, fake SIMBAD, stars.

Description: Checks that `tan_sky_map` puts its reference pixel at the given
sky position with the given pixel size, that `FakeSimbad` gives its set
answers, records each question and fails when told to, and that
`add_gaussian_star` draws the same star as a drawing over every pixel.
"""

import numpy as np
import pytest
from astropy.table import Table

from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.test.synthetic.fake_simbad import FakeSimbad
from astrometricslib.test.synthetic.point_stars import add_gaussian_star
from astrometricslib.test.synthetic.sky_map import tan_sky_map


def test_the_reference_pixel_lands_on_the_center() -> None:
    """The reference pixel (counted from 1) maps to the given sky position."""
    wcs = tan_sky_map(250.0, 36.0, (51.0, 51.0), degrees_per_pixel=2.0 / 3600.0)

    ra, dec = wcs.wcs_pix2world([[50.0, 50.0]], 0)[0]

    assert ra == pytest.approx(250.0)
    assert dec == pytest.approx(36.0)
    assert list(wcs.wcs.cdelt) == pytest.approx([-2.0 / 3600.0, 2.0 / 3600.0])


def test_a_cd_matrix_is_used_as_given() -> None:
    """A full CD matrix replaces the plain pixel size."""
    wcs = tan_sky_map(10.0, 20.0, (5.0, 5.0), cd=[[0.0, 1e-4], [1e-4, 0.0]])

    assert wcs.wcs.has_cd()
    assert wcs.wcs.cd.tolist() == [[0.0, 1e-4], [1e-4, 0.0]]


@pytest.mark.parametrize("arguments", [{}, {"degrees_per_pixel": 1e-4, "cd": [[1e-4, 0.0], [0.0, 1e-4]]}])
def test_the_scale_must_be_given_exactly_once(arguments: dict) -> None:
    """Neither or both of the pixel size and the CD matrix is refused."""
    with pytest.raises(ValueError, match="exactly one"):
        tan_sky_map(0.0, 0.0, (1.0, 1.0), **arguments)


def test_the_fake_gives_its_answers_and_records_the_questions() -> None:
    """Fixed and computed answers come back, and every question is kept."""
    simbad = FakeSimbad(objects=lambda name: Table({"main_id": [name]}), tap=None)

    assert len(simbad.query_region("center", "0.1d")) == 0
    assert simbad.query_object("Vega")["main_id"][0] == "Vega"
    assert simbad.query_tap("SELECT 1") is None
    assert simbad.region_calls == [("center", "0.1d")]
    assert simbad.object_calls == ["Vega"]
    assert simbad.tap_calls == ["SELECT 1"]
    assert simbad.call_count == 3


def test_a_set_failure_is_raised_after_the_question_is_recorded() -> None:
    """With a failure set, every question raises it and still counts."""
    simbad = FakeSimbad()
    simbad.failure = ExternalServiceError("down")

    with pytest.raises(ExternalServiceError):
        simbad.query_object("Vega")

    assert simbad.call_count == 1


@pytest.mark.parametrize(
    ("x", "y", "sigma_px"),
    [(150.3, 200.7, 2.0), (2.2, 3.6, 1.5), (397.8, 0.4, 4.0), (-5.0, 120.0, 2.5)],
)
def test_a_windowed_star_matches_a_star_drawn_over_every_pixel(x: float, y: float, sigma_px: float) -> None:
    """The square drawing gives the same float32 image, also at the edges.

    The last case has its center off the image, so only part of the star
    lands on it.
    """
    rows, columns = np.mgrid[0:300, 0:400]
    sky = np.random.default_rng(1).normal(100.0, 3.0, (300, 400))
    expected = sky + 2000.0 * np.exp(-((columns - x) ** 2 + (rows - y) ** 2) / (2 * sigma_px**2))
    drawn = sky.copy()

    add_gaussian_star(drawn, x, y, 2000.0, sigma_px)

    np.testing.assert_array_equal(drawn.astype(np.float32), expected.astype(np.float32))
