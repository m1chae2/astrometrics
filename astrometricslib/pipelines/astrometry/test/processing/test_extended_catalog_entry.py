"""Purpose: Tests for the extended-object test on a SIMBAD row.

Description: A ring nebula such as M 57 is a wide, faint glow. Its catalog
entry must not be forced onto a nearby star, so the identifier needs to know
that the entry is extended. A planetary nebula, globular cluster and galaxy
are extended; a star is not; a table with no type column gives no answer.
"""

import pytest
from astropy.table import Table

from astrometricslib.pipelines.astrometry.processing.extended_catalog_entry import (
    is_extended_catalog_entry,
)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("PN", True),
        (" GlC ", True),
        ("G", True),
        ("AGN", True),
        ("LIN", True),
        ("HII", True),
        ("OpC", True),
        ("*", False),
        ("**", False),
        ("V*", False),
        ("None", False),
    ],
)
def test_type_codes_are_sorted_into_extended_and_star(code: str, expected: bool) -> None:
    """Extended codes are true, star codes are false, spaces are ignored."""
    table = Table({"main_id": ["a"], "otype": [code]})
    assert is_extended_catalog_entry(table, 0) is expected


def test_a_table_without_a_type_column_is_not_extended() -> None:
    """With no type column nothing is known, so the entry counts as a star."""
    assert is_extended_catalog_entry(Table({"main_id": ["a"]}), 0) is False
