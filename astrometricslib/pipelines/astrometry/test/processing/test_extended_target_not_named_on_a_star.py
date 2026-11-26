"""Purpose: Tests that an extended target's catalog entry is not put on a star.

Description: The Ring Nebula (M 57, SIMBAD type "PN") is a faint glow about 45
by 30 pixels across, not a bright point. When the frame has no plate solution,
the identifier names the star at the frame centre from the SIMBAD entry that
matches the target's name. SIMBAD gives M 57 a spectral type, so the star
filter kept it as a "star", and the nebula's name was stamped on a field star
(or, with no bright detection, the run warned that the entry "is not
applied"). The identifier should drop extended types before matching and let
the extended-source hint carry the target.
"""

import logging

import pytest
from astropy.table import Column

from astrometricslib.pipelines.astrometry.test.processing.test_center_star_naming import (
    HINT_DEC,
    HINT_RA,
    _identifier,
    _name_table,
    _run,
    _simbad_table,
)


def _ring_nebula_table() -> object:
    """Build a SIMBAD result whose target entry is a planetary nebula.

    Returns
    -------
    table : `astropy.table.Table`
        One row, "M  57", of type "PN", 78 arcseconds from the hint.
    """
    table = _simbad_table([("M  57", HINT_RA, HINT_DEC + 78 / 3600, None)])
    table["otype"][0] = "PN"
    # SIMBAD gives M 57 a spectral type, which let `_filter_stellar_rows`
    # keep it as if it were a star.
    table["sp_type"] = Column(["B"], dtype=object)
    table["ids"] = Column(["M  57|NGC  6720|PN G063.1+13.9"], dtype=object)
    return table


def test_a_ring_nebula_entry_is_not_forced_onto_a_nearby_star(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The detection at the frame centre keeps its own name."""
    identifier = _identifier(
        monkeypatch, _ring_nebula_table(), _name_table("M  57", HINT_RA, HINT_DEC + 78 / 3600)
    )

    with caplog.at_level(logging.INFO):
        star = _run(identifier, "M 57")

    assert star.name == "Star 1"
    assert not star.id
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
