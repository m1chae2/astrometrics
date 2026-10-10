"""Purpose: Tests for the cached target positions used by sky queries.

Description: Every Planetarium pan or zoom asks which library targets lie in
the view. Reading every target record for that took about 120 ms. The parsed
positions are now kept until the library database changes. These tests check
the cache gives the same answer as reading everything, is reused, and is
dropped when the database file changes.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from astropy.coordinates import SkyCoord

from wayfindinglib.tasks.planning_tasks import catalog_operations
from wayfindinglib.tasks.planning_tasks.catalog_operations import _targets_within_radius


def _astrometrics(library: Path, targets: list[SimpleNamespace]) -> tuple[SimpleNamespace, list[str]]:
    """Build a fake science library with the given targets.

    Returns
    -------
    astrometrics : `types.SimpleNamespace`
        A fake handle with `config` and `targets`.
    calls : `list` [`str`]
        One `"list"` entry per full read of the target records.
    """
    calls: list[str] = []
    by_id = {target.id: target for target in targets}

    def list_targets() -> list[SimpleNamespace]:
        """Count the full read and return every target.

        Returns
        -------
        targets : `list`
            All the fake targets.
        """
        calls.append("list")
        return list(targets)

    handle = SimpleNamespace(
        config=SimpleNamespace(get_library_path=lambda: library),
        targets=SimpleNamespace(list=list_targets, get=lambda target_id: by_id.get(target_id)),
    )
    return handle, calls


def _targets() -> list[SimpleNamespace]:
    """Build three targets: two near each other and one far away.

    Returns
    -------
    targets : `list`
        Fake targets with `id`, `ra` and `dec` strings.
    """
    return [
        SimpleNamespace(id="Near A", ra="10h 00m 00s", dec="20° 00′ 00″"),
        SimpleNamespace(id="Near B", ra="10h 04m 00s", dec="20° 30′ 00″"),
        SimpleNamespace(id="Far", ra="20h 00m 00s", dec="-40° 00′ 00″"),
    ]


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    """Start each test with an empty position cache."""
    catalog_operations._target_position_cache.clear()


def test_the_cache_gives_the_same_targets_and_skips_the_full_read(tmp_path: Path) -> None:
    """A second query does not read every target again."""
    (tmp_path / "astrometrics.db").write_text("x")
    handle, calls = _astrometrics(tmp_path, _targets())
    center = SkyCoord(ra=150.0, dec=20.0, unit="deg")

    first = _targets_within_radius(handle, center, 5.0)
    second = _targets_within_radius(handle, center, 5.0)

    assert [target.id for target in first] == ["Near A", "Near B"]
    assert [target.id for target in second] == ["Near A", "Near B"]
    assert calls == ["list"]


def test_a_database_change_drops_the_cache(tmp_path: Path) -> None:
    """A database write makes the next query read the records again."""
    database = tmp_path / "astrometrics.db"
    database.write_text("x")
    handle, calls = _astrometrics(tmp_path, _targets())
    center = SkyCoord(ra=150.0, dec=20.0, unit="deg")
    _targets_within_radius(handle, center, 5.0)

    os.utime(database, ns=(1, 1))
    _targets_within_radius(handle, center, 5.0)

    assert calls == ["list", "list"]


def test_without_a_database_file_nothing_is_cached(tmp_path: Path) -> None:
    """With no readable version, every query reads the records."""
    handle, calls = _astrometrics(tmp_path, _targets())
    center = SkyCoord(ra=150.0, dec=20.0, unit="deg")

    _targets_within_radius(handle, center, 5.0)
    _targets_within_radius(handle, center, 5.0)

    assert calls == ["list", "list"]


def test_targets_without_a_position_are_left_out(tmp_path: Path) -> None:
    """Blank and placeholder positions never match."""
    (tmp_path / "astrometrics.db").write_text("x")
    targets = [
        SimpleNamespace(id="Blank", ra="", dec=""),
        SimpleNamespace(id="Placeholder", ra="0h 0m 0s", dec="0° 0′ 0′′"),
        *_targets(),
    ]
    handle, _ = _astrometrics(tmp_path, targets)

    found = _targets_within_radius(handle, SkyCoord(ra=0.0, dec=0.0, unit="deg"), 180.0)

    assert [target.id for target in found] == ["Near A", "Near B", "Far"]
