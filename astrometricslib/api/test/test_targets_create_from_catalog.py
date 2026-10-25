"""Purpose: Tests for `TargetCatalog.create(..., from_catalog=True)`.

Description: Checks that a catalog name makes a target with the position
SIMBAD gives, that a target already in the library is reused with no
lookup, and that an unknown name, an empty name and a moving body are
refused with `NotFoundError`. Uses a fake SIMBAD driver and a mock
storage, so nothing touches the network or real data.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from astropy.table import Table

from astrometricslib.api.targets import TargetCatalog
from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.drivers.interfaces.simbad_driver import SimbadDriver
from astrometricslib.foundation.errors import NotFoundError
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared import target_records


class _FakeSimbad(SimbadDriver):
    """A SIMBAD client that knows a fixed set of names.

    Attributes
    ----------
    positions : `dict` [`str`, `tuple` [`float`, `float`]]
        Right ascension and declination in degrees for each known name.
    object_calls : `list` [`str`]
        The names asked for.
    """

    def __init__(self, positions: dict[str, tuple[float, float]]) -> None:
        """Store the known names.

        Parameters
        ----------
        positions : `dict` [`str`, `tuple` [`float`, `float`]]
            Right ascension and declination in degrees for each known name.
        """
        self.positions = positions
        self.object_calls: list[str] = []

    def query_object(self, name: str, *, votable_fields: tuple[str, ...] = ()) -> Table | None:
        """Answer with one row for a known name, else `None`.

        Parameters
        ----------
        name : `str`
            The name asked for.
        votable_fields : `tuple` [`str`], optional
            Ignored.

        Returns
        -------
        table : `astropy.table.Table` or `None`
            A one-row table with ``ra`` and ``dec``, or `None`.
        """
        self.object_calls.append(name)
        if name not in self.positions:
            return None
        ra_deg, dec_deg = self.positions[name]
        return Table({"ra": [ra_deg], "dec": [dec_deg]})

    def query_region(self, *args: Any, **kwargs: Any) -> Table:
        """Not used by these tests.

        Returns
        -------
        table : `astropy.table.Table`
            An empty table.
        """
        return Table()

    def query_tap(self, *args: Any, **kwargs: Any) -> Table:
        """Not used by these tests.

        Returns
        -------
        table : `astropy.table.Table`
            An empty table.
        """
        return Table()


def _catalog(
    monkeypatch: pytest.MonkeyPatch, simbad: _FakeSimbad, targets: list[Target] | None = None
) -> TargetCatalog:
    """Build a catalog over a mock storage with the fake SIMBAD driver.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to skip the frame scan and the save.
    simbad : `_FakeSimbad`
        The SIMBAD stand-in.
    targets : `list` [`Target`], optional
        Targets the library already holds.

    Returns
    -------
    catalog : `TargetCatalog`
        The catalog.
    """
    monkeypatch.setattr(target_records, "reindex_frames", lambda target: None)
    monkeypatch.setattr(target_records, "save_targets", lambda api: None)
    storage = MagicMock()
    storage.get.return_value = list(targets or [])
    return TargetCatalog(MagicMock(), storage, drivers=Drivers(simbad=simbad))


def test_a_catalog_name_creates_a_target_with_its_position(monkeypatch: pytest.MonkeyPatch) -> None:
    """The new target holds the position SIMBAD gave."""
    simbad = _FakeSimbad({"M 57": (283.396, 33.029)})
    catalog = _catalog(monkeypatch, simbad)

    target = catalog.create("M_57", from_catalog=True)

    assert target.id == "M 57"
    assert target.ra != "0h 0m 0s"
    assert target.dec != "0° 0′ 0′′"
    assert catalog.get("M 57") is target


def test_an_existing_target_is_reused_without_a_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A target already in the library is returned and SIMBAD is not asked."""
    existing = Target(id="M 57")
    simbad = _FakeSimbad({})
    catalog = _catalog(monkeypatch, simbad, [existing])

    assert catalog.create("M 57", from_catalog=True) is existing
    assert simbad.object_calls == []
    assert len(catalog._targets) == 1


def test_an_unknown_name_is_refused_and_nothing_is_created(monkeypatch: pytest.MonkeyPatch) -> None:
    """A name SIMBAD does not know raises NotFoundError."""
    catalog = _catalog(monkeypatch, _FakeSimbad({}))

    with pytest.raises(NotFoundError):
        catalog.create("NGC 9999", from_catalog=True)
    assert catalog._targets == []


@pytest.mark.parametrize("name", ["", "   ", "Jupiter"])
def test_an_empty_name_or_a_moving_body_is_refused(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """An empty name or a planet has no catalog position and is refused."""
    simbad = _FakeSimbad({"Jupiter": (10.0, 10.0)})
    catalog = _catalog(monkeypatch, simbad)

    with pytest.raises(NotFoundError):
        catalog.create(name, from_catalog=True)
    assert simbad.object_calls == []


def test_without_from_catalog_a_bare_target_is_made(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default keeps its old meaning: no lookup, no position."""
    simbad = _FakeSimbad({"M 57": (283.396, 33.029)})
    catalog = _catalog(monkeypatch, simbad)

    target = catalog.create("M 57")

    assert target.ra == "0h 0m 0s"
    assert simbad.object_calls == []
