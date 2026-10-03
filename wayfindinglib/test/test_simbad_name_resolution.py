"""Purpose: Unit tests for resolving a name through SIMBAD.

Description: Covers the column-name and field handling that made name
lookups fail for clusters and nebulae such as M 52. With astroquery 0.4.x
SIMBAD names its columns in lower case, and requesting the V magnitude
makes SIMBAD return only objects that have one.
"""

import sys
import types
from typing import Any, ClassVar

import pytest
from astropy.table import Table

from astrometricslib import StellarObject, Target
from wayfindinglib.drivers.catalog.simbad_catalog_driver import (
    format_target_coordinates,
    read_simbad_field,
)
from wayfindinglib.tasks.planning_tasks import resolution_operations


class _FakeSimbadClient:
    """A SIMBAD client that behaves like astroquery 0.4.x."""

    requested_fields: ClassVar[list[tuple[str, ...]]] = []

    def __init__(self) -> None:
        """Start with no extra columns requested."""
        self._fields: tuple[str, ...] = ()

    def add_votable_fields(self, *fields: str) -> None:
        """Record the extra columns, as astroquery does."""
        self._fields = fields
        _FakeSimbadClient.requested_fields.append(fields)

    def query_object(self, name: str) -> Table | None:
        """Return the row for a known name.

        Returns
        -------
        table : `astropy.table.Table` or `None`
            A one-row table, or an empty one when `V` was requested for an
            object with no V magnitude, as the real service does.
        """
        known = {
            "M 52": ("M  52", 351.2, 61.59, "OpC", "", None),
            "Vega": ("* alf Lyr", 279.23, 38.78, "Star", "A0Va", 0.03),
            "* alf Lyr": ("* alf Lyr", 279.23, 38.78, "Star", "A0Va", 0.03),
        }
        if name not in known:
            return None
        main_id, ra, dec, otype, sp_type, magnitude = known[name]
        if "V" in self._fields and magnitude is None:
            return Table(
                names=["main_id", "ra", "dec", "otype", "sp_type", "V"],
                dtype=[str, float, float, str, str, float],
            )
        columns: dict[str, list[Any]] = {
            "main_id": [main_id],
            "ra": [ra],
            "dec": [dec],
            "otype": [otype],
            "sp_type": [sp_type],
        }
        if "V" in self._fields:
            columns["V"] = [magnitude]
        return Table(columns)


@pytest.fixture
def fake_simbad(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace astroquery's SIMBAD client with the fake."""
    _FakeSimbadClient.requested_fields = []
    module = types.ModuleType("astroquery.simbad")
    module.Simbad = _FakeSimbadClient
    monkeypatch.setitem(sys.modules, "astroquery.simbad", module)


def _sky_with_empty_library() -> Any:
    """Build a stand-in sky whose library has no targets or stars.

    Returns
    -------
    sky : `Any`
        An object with the attributes the resolver reads.
    """
    astrometrics = types.SimpleNamespace(
        targets=types.SimpleNamespace(list=lambda: []),
        stars=types.SimpleNamespace(find_all_by_id_or_name=lambda name: []),
    )
    return types.SimpleNamespace(_astrometrics=astrometrics)


def test_a_cluster_without_a_v_magnitude_resolves_as_a_target(fake_simbad: None) -> None:
    """M 52 has no V magnitude in SIMBAD and must still resolve."""
    resolved = resolution_operations.resolve_target_coordinates(_sky_with_empty_library(), "M 52")

    assert isinstance(resolved, Target)
    assert resolved.id == "M  52"
    assert ("V",) not in _FakeSimbadClient.requested_fields[:1]
    assert "otype" in _FakeSimbadClient.requested_fields[0]


def test_a_star_resolves_with_its_spectral_type_and_magnitude(fake_simbad: None) -> None:
    """A star is read from lower-case columns and gets its V magnitude."""
    resolved = resolution_operations.resolve_target_coordinates(_sky_with_empty_library(), "Vega")

    assert isinstance(resolved, StellarObject)
    assert resolved.spectral_type == "A0Va"
    assert resolved.magnitude == pytest.approx(0.03)


def test_a_target_from_simbad_stores_coordinates_as_text_not_bare_degrees(fake_simbad: None) -> None:
    """A bare ``351.2`` would read as hours, so the text form is used."""
    resolved = resolution_operations.resolve_target_coordinates(_sky_with_empty_library(), "M 52")

    assert resolved.ra.endswith("s")
    assert "h" in resolved.ra
    assert resolved.dec.startswith("+61")


def test_read_simbad_field_ignores_letter_case_and_masked_values() -> None:
    """Column names read in either case, and empty cells read as missing."""
    upper = Table({"MAIN_ID": ["x"]})[0]
    lower = Table({"main_id": ["x"]})[0]
    masked = Table({"V": [1.0]}, masked=True)
    masked["V"].mask = [True]

    assert read_simbad_field(upper, "main_id") == "x"
    assert read_simbad_field(lower, "MAIN_ID") == "x"
    assert read_simbad_field(masked[0], "V", default=None) is None
    assert read_simbad_field(lower, "absent", default="d") == "d"


def test_format_target_coordinates_writes_hours_and_signed_degrees() -> None:
    """Degrees convert to the text form a `Target` stores."""
    assert format_target_coordinates(351.2024, 61.594) == ("23h 24m 48.58s", "+61° 35′ 38.4′′")
    assert format_target_coordinates(10.68, -25.29)[1].startswith("-25")
