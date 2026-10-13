"""Tests that the star identifier keeps the object types SIMBAD gives a star.

A match must record every object type, because SIMBAD's single main type can
hide a variable star (Algol's is "SB*"). When a result carries only the main
type, only a main type that is itself a variable type may be kept; anything
else is recorded as unknown so it cannot read as "not listed as variable".
"""

from astropy.table import Column, MaskedColumn, Table

from astrometricslib.models.known_variability import KnownVariability
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.astrometry.test.processing.test_star_identifier import (
    VEGA_DEC_DEG,
    VEGA_RA_DEG,
    _make_star_identifier,
)


def match_star(**extra_columns: object) -> StellarObject:
    """Apply a one-row SIMBAD match with the given extra columns.

    Parameters
    ----------
    **extra_columns
        Columns to add to the row, such as ``otype`` or ``alltypes.otypes``.

    Returns
    -------
    stellar_object : `StellarObject`
        The star after the SIMBAD match was applied.
    """
    columns: dict[str, object] = {
        "main_id": Column(["* bet Per"], dtype=object),
        "ids": Column(["NAME Algol|* bet Per"], dtype=object),
        "sp_type": MaskedColumn(["B8V"], mask=[False], dtype=object),
        "V": MaskedColumn([2.1], mask=[False]),
    }
    columns.update(extra_columns)
    stellar_object = StellarObject()
    _make_star_identifier()._apply_simbad_match(stellar_object, Table(columns)[0], VEGA_RA_DEG, VEGA_DEC_DEG)
    return stellar_object


def test_the_full_type_list_is_recorded() -> None:
    """The all-types column is stored as SIMBAD gave it."""
    star = match_star(**{
        "otype": Column(["SB*"], dtype=object),
        "alltypes.otypes": Column(["*|**|EB*|SB*|V*"], dtype=object),
    })

    assert star.simbad_object_types == "*|**|EB*|SB*|V*"
    assert star.known_variability is KnownVariability.KNOWN_VARIABLE


def test_a_main_type_alone_that_is_not_variable_is_recorded_as_unknown() -> None:
    """Algol's main type "SB*" must not turn into "not listed as variable"."""
    star = match_star(otype=Column(["SB*"], dtype=object))

    assert star.simbad_object_types == ""
    assert star.known_variability is KnownVariability.UNKNOWN


def test_a_main_type_alone_that_is_variable_is_kept() -> None:
    """A variable main type is positive evidence even without the full list."""
    star = match_star(otype=Column(["EB*"], dtype=object))

    assert star.simbad_object_types == "EB*"
    assert star.known_variability is KnownVariability.KNOWN_VARIABLE


def test_a_masked_all_types_value_falls_back_to_the_main_type() -> None:
    """A masked all-types value is treated as missing."""
    star = match_star(**{
        "otype": Column(["EB*"], dtype=object),
        "alltypes.otypes": MaskedColumn(["x"], mask=[True], dtype=object),
    })

    assert star.simbad_object_types == "EB*"


def test_a_result_with_no_type_column_is_unknown() -> None:
    """No type column at all gives unknown."""
    star = match_star()

    assert star.simbad_object_types == ""
    assert star.known_variability is KnownVariability.UNKNOWN
