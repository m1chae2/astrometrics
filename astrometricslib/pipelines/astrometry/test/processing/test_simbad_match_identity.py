"""Tests that a SIMBAD match is saved under the star's Gaia DR3 number.

A star that SIMBAD links to a Gaia DR3 source is saved as ``Gaia DR3
<number>``, so every run finds it under the same id however it was named.
The SIMBAD names stay as the display name and aliases, most familiar first.
A star with no Gaia entry keeps its SIMBAD main id.
"""

from astropy.table import Column, MaskedColumn, Table

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.astrometry.test.processing.test_star_identifier import (
    VEGA_DEC_DEG,
    VEGA_RA_DEG,
    _make_star_identifier,
)


def _match(main_id: str, ids: str) -> StellarObject:
    """Apply a one-row SIMBAD match with the given main id and name list.

    Returns
    -------
    stellar_object : `StellarObject`
        The star after the match was applied.
    """
    table = Table({
        "main_id": Column([main_id], dtype=object),
        "ids": Column([ids], dtype=object),
        "sp_type": MaskedColumn(["K3II"], mask=[False], dtype=object),
        "V": MaskedColumn([3.1], mask=[False]),
    })
    stellar_object = StellarObject()
    _make_star_identifier()._apply_simbad_match(stellar_object, table[0], VEGA_RA_DEG, VEGA_DEC_DEG)
    return stellar_object


def test_a_star_with_a_gaia_number_is_saved_under_it() -> None:
    """Check the id is the Gaia number and the SIMBAD names are kept."""
    star = _match(
        "* bet01 Cyg",
        "NAME Albireo|* bet01 Cyg|HD 183912|HIP 95947|2MASS J19304337+2757347|Gaia DR3 2026116260337482112",
    )

    assert star.id == "Gaia DR3 2026116260337482112"
    assert star.gaia_dr3_source_id == 2026116260337482112
    assert star.name == "Albireo"
    assert star.aliases == ["* bet01 Cyg", "HD 183912", "HIP 95947"]
    assert star.is_catalog_identified


def test_a_star_without_a_gaia_number_keeps_its_simbad_id() -> None:
    """Check a star Gaia does not list keeps the SIMBAD main id."""
    star = _match("* alf Lyr", "NAME Vega|* alf Lyr|HD 172167")

    assert star.id == "* alf Lyr"
    assert star.name == "Vega"
    assert star.aliases == ["HD 172167"]
