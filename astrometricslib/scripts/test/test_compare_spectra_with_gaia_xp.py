"""Purpose: Tests for the script that compares stored spectra with Gaia XP.

Description: The script reads a target's stars from the catalog, compares each
stored response-corrected spectrum with its Gaia XP spectrum and prints a
table. These tests give it a catalog stand-in holding three stars made from a
Pickles template with a known tilt, a fake Gaia XP driver, and a star with no
Gaia id, and check the numbers in the table and the run summary. No catalog
database and no network are used.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject
from astrometricslib.scripts import compare_spectra_with_gaia_xp as script
from astrometricslib.test.synthetic.gaia_xp_spectra import (
    CAMERA_NAME,
    FakeGaiaXpDriver,
    make_calibrated_spectrum,
    make_xp_spectrum,
)

TILT_PERCENT_PER_1000_ANGSTROM = 4.0


def make_star(star_id: str, tilt: float, source_id: int | None) -> StellarObject:
    """Build a catalog star with a stored, response-corrected spectrum.

    Parameters
    ----------
    star_id : `str`
        The star's id.
    tilt : `float`
        The tilt put into the spectrum, in percent per 1000 Angstroms.
    source_id : `int` or `None`
        The star's stored Gaia DR3 source id.

    Returns
    -------
    star : `StellarObject`
        A star whose spectrum is a G2V template with that tilt.
    """
    wavelength, intensity = make_calibrated_spectrum("G2V", tilt_percent_per_1000_angstrom=tilt)
    return StellarObject(
        id=star_id,
        gaia_dr3_source_id=source_id,
        spectroscopy=SpectroscopyResult(
            wavelengths_angstrom=wavelength.tolist(),
            intensities=wavelength.tolist(),
            response_corrected_intensities=intensity.tolist(),
        ),
    )


class FakeCatalog:
    """A catalog stand-in holding a few stars.

    Parameters
    ----------
    stars : `list` [`StellarObject`]
        The stars it holds.
    """

    def __init__(self, stars: list[StellarObject]) -> None:
        """Keep the stars by id.

        Parameters
        ----------
        stars : `list` [`StellarObject`]
            The stars it holds.
        """
        self.stars = {star.id: star for star in stars}

    def list_star_summaries(
        self, target_id: str | None = None, limit: int | None = None
    ) -> list[SimpleNamespace]:
        """List every star as a summary with spectra.

        Returns
        -------
        summaries : `list` [`types.SimpleNamespace`]
            One summary per star.
        """
        return [SimpleNamespace(id=star_id, has_spectra=True) for star_id in self.stars]

    def get_by_ids(self, _collection: str, ids: list[str]) -> list[StellarObject]:
        """Give the stars with these ids.

        Returns
        -------
        stars : `list` [`StellarObject`]
            The stars.
        """
        return [self.stars[star_id] for star_id in ids]


def make_astrometrics(stars: list[StellarObject]) -> SimpleNamespace:
    """Build the part of `Astrometrics` the script uses.

    Returns
    -------
    astrometrics : `types.SimpleNamespace`
        With a catalog and a config that names the test camera.
    """
    return SimpleNamespace(
        catalog_access=FakeCatalog(stars),
        config=SimpleNamespace(get_primary_camera_name=lambda: CAMERA_NAME),
    )


def test_each_star_is_compared_and_the_report_has_the_band_table() -> None:
    """The report lists each star's tilt and the median ratio per band."""
    stars = [make_star(f"HD {index}", TILT_PERCENT_PER_1000_ANGSTROM, 1000 + index) for index in range(3)]
    driver = FakeGaiaXpDriver({1000 + index: make_xp_spectrum("G2V") for index in range(3)})

    results = script.compare_target(make_astrometrics(stars), "Test Target", driver)

    assert [star_id for star_id, _comparison in results] == ["HD 0", "HD 1", "HD 2"]
    for _star_id, comparison in results:
        assert comparison.status == "compared"
        assert comparison.slope_percent_per_1000_angstrom == pytest.approx(
            TILT_PERCENT_PER_1000_ANGSTROM, abs=0.5
        )
    report = script.format_report(results)
    assert "4200-5000" in report
    assert "7000-8000" in report
    assert "Run summary over 3 compared star(s)" in report
    assert "median ratio" in report
    assert "Gate gaia_xp_agreement: failed" in report


def test_a_star_that_cannot_be_compared_says_why_and_stays_out_of_the_summary() -> None:
    """A star with no Gaia id is listed with its reason and not counted."""
    stars = [make_star("HD 1", 0.0, 1001), make_star("HD 2", 0.0, None)]
    driver = FakeGaiaXpDriver({1001: make_xp_spectrum("G2V")})

    results = script.compare_target(make_astrometrics(stars), "Test Target", driver)
    report = script.format_report(results)

    assert "no Gaia DR3 source id is known for this star" in report
    assert "Run summary over 1 compared star(s)" in report
    assert driver.requested_ids == [1001]
    assert "Gate gaia_xp_agreement: not_checked" in report


def test_a_target_with_no_comparable_star_reports_that() -> None:
    """With nothing compared the report says so; the gate is not checked."""
    results = script.compare_target(
        make_astrometrics([make_star("HD 2", 0.0, None)]), "Test Target", FakeGaiaXpDriver()
    )

    report = script.format_report(results)

    assert "No star could be compared with a Gaia XP spectrum." in report
    assert "Gate gaia_xp_agreement: not_checked" in report


def test_a_star_with_no_stored_spectrum_is_left_out() -> None:
    """A star without wavelengths gives no result at all."""
    star = StellarObject(id="HD 3", gaia_dr3_source_id=1003)

    assert script.compare_star(star, FakeGaiaXpDriver(), None) is None
