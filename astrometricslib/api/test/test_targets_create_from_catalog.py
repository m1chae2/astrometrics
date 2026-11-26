"""Purpose: Tests for `TargetCatalog.create(..., from_catalog=True)`.

Description: Checks that a catalog name makes a target with the position
SIMBAD gives, that a target already in the library is reused with no
lookup, and that an unknown name, an empty name and a moving body are
refused with `NotFoundError`. Uses a fake SIMBAD driver and a mock
storage, so nothing touches the network or real data.
"""

from unittest.mock import MagicMock

import pytest
from astropy.table import Table

from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib.api.targets import TargetCatalog
from astrometricslib.foundation.errors import NotFoundError
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared import target_records
from astrometricslib.test.synthetic.fake_simbad import FakeSimbad


def _simbad_knowing(positions: dict[str, tuple[float, float]]) -> FakeSimbad:
    """Build a SIMBAD stand-in that knows a fixed set of names.

    Parameters
    ----------
    positions : `dict` [`str`, `tuple` [`float`, `float`]]
        Right ascension and declination in degrees for each known name.

    Returns
    -------
    simbad : `FakeSimbad`
        The stand-in. A known name gets a one-row table with ``ra`` and
        ``dec``; any other name gets `None`.
    """

    def answer(name: str) -> Table | None:
        """Give the row for a known name.

        Parameters
        ----------
        name : `str`
            The name asked for.

        Returns
        -------
        table : `astropy.table.Table` or `None`
            A one-row table, or `None` for an unknown name.
        """
        if name not in positions:
            return None
        ra_deg, dec_deg = positions[name]
        return Table({"ra": [ra_deg], "dec": [dec_deg]})

    return FakeSimbad(objects=answer)


def _catalog(
    monkeypatch: pytest.MonkeyPatch, simbad: FakeSimbad, targets: list[Target] | None = None
) -> TargetCatalog:
    """Build a catalog over a mock storage with the fake SIMBAD driver.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to skip the frame scan and the save.
    simbad : `FakeSimbad`
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
    simbad = _simbad_knowing({"M 57": (283.396, 33.029)})
    catalog = _catalog(monkeypatch, simbad)

    target = catalog.create("M_57", from_catalog=True)

    assert target.id == "M 57"
    assert target.ra != "0h 0m 0s"
    assert target.dec != "0° 0′ 0′′"
    assert catalog.get("M 57") is target


def test_an_existing_target_is_reused_without_a_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A target already in the library is returned and SIMBAD is not asked."""
    existing = Target(id="M 57")
    simbad = _simbad_knowing({})
    catalog = _catalog(monkeypatch, simbad, [existing])

    assert catalog.create("M 57", from_catalog=True) is existing
    assert simbad.object_calls == []
    assert len(catalog._targets) == 1


def test_an_unknown_name_is_refused_and_nothing_is_created(monkeypatch: pytest.MonkeyPatch) -> None:
    """A name SIMBAD does not know raises NotFoundError."""
    catalog = _catalog(monkeypatch, _simbad_knowing({}))

    with pytest.raises(NotFoundError):
        catalog.create("NGC 9999", from_catalog=True)
    assert catalog._targets == []


@pytest.mark.parametrize("name", ["", "   ", "Jupiter"])
def test_an_empty_name_or_a_moving_body_is_refused(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """An empty name or a planet has no catalog position and is refused."""
    simbad = _simbad_knowing({"Jupiter": (10.0, 10.0)})
    catalog = _catalog(monkeypatch, simbad)

    with pytest.raises(NotFoundError):
        catalog.create(name, from_catalog=True)
    assert simbad.object_calls == []


def test_without_from_catalog_a_bare_target_is_made(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default keeps its old meaning: no lookup, no position."""
    simbad = _simbad_knowing({"M 57": (283.396, 33.029)})
    catalog = _catalog(monkeypatch, simbad)

    target = catalog.create("M 57")

    assert target.ra == "0h 0m 0s"
    assert simbad.object_calls == []
