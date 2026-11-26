"""Purpose: Tests for filling a star's SIMBAD types from stored SIMBAD answers.

Description: A stored SIMBAD row is the star when it lies within the
variable-star match radius (`variable_star_catalogs.MATCH_RADIUS_ARCSEC`),
which follows the run's plate scale. These tests use a stand-in for the
stored SIMBAD answers, so no network is needed.
"""

from typing import Any

from astropy.table import Table

from astrometricslib.foundation.parameter_context import parameter_context, record_basis
from astrometricslib.foundation.run_bases import REFERENCE_SETUP_BASES
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.tuned_parameter import RunBasis
from astrometricslib.pipelines.shared.stored_variability import fill_known_variability

# A stored SIMBAD row 4 arcseconds north of the star.
STAR_RA, STAR_DEC = 30.0, 40.0
ROW_OFFSET_ARCSEC = 4.0


class _StoredSimbad:
    """Stand in for the SIMBAD driver's stored answers."""

    def stored_region(self, *_args: Any, **_kwargs: Any) -> Table:
        """Give one stored row of an eclipsing binary, 4 arcseconds away.

        Returns
        -------
        table : `astropy.table.Table`
            The row.
        """
        return Table({"ra": [STAR_RA], "dec": [STAR_DEC + ROW_OFFSET_ARCSEC / 3600.0], "otypes": ["EB*|*"]})


def _filled_types() -> str:
    """Fill one star from the stored answers and give its SIMBAD types.

    Returns
    -------
    types : `str`
        The types the star got; empty when the row was not taken as the star.
    """
    star = StellarObject(id="Star", ra=STAR_RA, declination=STAR_DEC)
    fill_known_variability([star], _StoredSimbad())
    return star.simbad_object_types or ""


def test_the_stored_simbad_match_follows_the_plate_scale(monkeypatch: Any) -> None:
    """The row is too far at the reference scale and the star at twice it."""
    from astrometricslib.pipelines.shared import stored_variability

    monkeypatch.setattr(stored_variability, "_fill_vsx_and_gaia", lambda *_args: None)
    with parameter_context():
        assert _filled_types() == ""
        record_basis(
            RunBasis.PLATE_SCALE_ARCSEC_PER_PX,
            2.0 * REFERENCE_SETUP_BASES[RunBasis.PLATE_SCALE_ARCSEC_PER_PX],
        )
        assert _filled_types() == "EB*|*"
