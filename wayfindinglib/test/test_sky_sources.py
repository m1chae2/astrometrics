"""Tests for the Planetarium's sky sources (`planning_tasks/sky_sources`).

`ObservationPlanning.get_sources` turns library targets, library stars and
online catalog objects into `SkySource` records, and applies the map's
magnitude limits. These tests run the stars through a real star catalog over
an empty temporary library, with a stand-in for the region search that finds
targets and SIMBAD objects.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import (
    AppConfiguration,
    CatalogAccess,
    FrameRecord,
    StellarCatalog,
    StellarObject,
    Target,
)
from wayfindinglib.tasks.planning_tasks import resolution_operations, sky_sources


def _sky(tmp_path: Path, stars: list[StellarObject], targets: list[Target]) -> SimpleNamespace:
    """Build a stand-in sky engine over a real star catalog.

    Returns
    -------
    sky : `types.SimpleNamespace`
        Holds ``_astrometrics`` with a real ``stars`` catalog and a
        ``targets`` list.
    """
    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    catalog = StellarCatalog(config, CatalogAccess(config))
    if stars:
        catalog.catalog_access.put(stars, "stellar_catalog", {})
    astrometrics = SimpleNamespace(stars=catalog, targets=SimpleNamespace(list=lambda: targets))
    return SimpleNamespace(_astrometrics=astrometrics)


def _found(monkeypatch: pytest.MonkeyPatch, items: list[Any]) -> None:
    """Make the region search find the given targets and online objects.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        The test's patcher.
    items : `list`
        What the region search returns.
    """
    monkeypatch.setattr(resolution_operations, "get_sources", lambda *args, **kwargs: list(items))


def _ids(sources: list[Any]) -> list[str]:
    """List the ids of some sources.

    Returns
    -------
    ids : `list` [`str`]
        The ids, in order.
    """
    return [source.id for source in sources]


def test_single_frame_detections_and_unplaced_stars_are_not_drawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Solved detections and stars without a position are left out."""
    sky = _sky(
        tmp_path,
        [
            StellarObject(id="Polaris", ra=37.95, dec=89.26, magnitude=2.0),
            StellarObject(id="M 81:2026-01-14:0:0:Star_60", ra=37.9, dec=89.2),
            StellarObject(id="Unplaced", ra=0.0, dec=89.0),
        ],
        [],
    )
    _found(monkeypatch, [])

    sources = sky_sources.collect_sky_sources(sky, 37.95, 89.26, 2.5, True, False, None, True)

    assert _ids(sources) == ["Polaris"]


def test_magnitude_limits_thin_faint_and_uncataloged_stars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The limit drops faint catalog stars; the flag drops uncataloged ones."""
    sky = _sky(
        tmp_path,
        [
            StellarObject(id="Bright", ra=10.0, dec=10.0, magnitude=3.0),
            StellarObject(id="Faint", ra=10.1, dec=10.1, magnitude=14.0),
            StellarObject(id="NoMagnitude", ra=10.3, dec=10.3),
            StellarObject(id="Instrumental", ra=10.4, dec=10.4, magnitude=-14.9),
        ],
        [],
    )
    _found(monkeypatch, [])

    def drawn(limit: float | None, uncataloged: bool) -> list[str]:
        """List the stars drawn with these limits.

        Returns
        -------
        ids : `list` [`str`]
            The ids, sorted.
        """
        return sorted(
            _ids(sky_sources.collect_sky_sources(sky, 10.0, 10.0, 5.0, True, False, limit, uncataloged))
        )

    assert drawn(None, True) == ["Bright", "Faint", "Instrumental", "NoMagnitude"]
    assert drawn(6.0, True) == ["Bright", "Instrumental", "NoMagnitude"]
    assert drawn(None, False) == ["Bright", "Faint"]
    assert drawn(6.0, False) == ["Bright"]


def test_a_star_source_uses_the_planetarium_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A star's source dumps to the camelCase keys the Planetarium reads."""
    star = StellarObject(
        id="2MASS J1",
        ra=250.76,
        dec=36.72,
        spectral_type="K2",
        magnitude=9.5,
        photometry={"timestamps": ["2026-01-01T00:00:00Z"], "magnitudes": [0.5]},
    )
    sky = _sky(tmp_path, [star], [])
    _found(monkeypatch, [])

    (source,) = sky_sources.collect_sky_sources(sky, 250.76, 36.72, 2.5, True, False, None, True)

    assert source.model_dump(by_alias=True) == {
        "id": "2MASS J1",
        "ra": 250.76,
        "dec": 36.72,
        "name": "2MASS J1",
        "commonName": "2MASS J1",
        "spectralType": "K2",
        "magnitude": 9.5,
        "hasSpectra": False,
        "hasPhotometry": True,
        "type": "star",
        "global": False,
        "catalogSource": None,
        "stackedImage": None,
        "fieldOfView": None,
    }


def test_a_target_without_a_stack_shows_its_longest_light_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The longest LIGHT frame stands in for a missing stack, not a dark."""
    target = Target(
        id="M 57",
        common_name="Ring Nebula",
        ra="18h 53m 35s",
        dec="33° 1′ 45″",
        frames=[
            FrameRecord(path="/short.fits", exposure="30", role="LIGHT"),
            FrameRecord(path="/long.fits", exposure="120", role="LIGHT"),
            FrameRecord(path="/dark.fits", exposure="600", role="DARK"),
        ],
    )
    sky = _sky(tmp_path, [], [target])
    _found(monkeypatch, [target])

    (source,) = sky_sources.collect_sky_sources(sky, 283.4, 33.0, 2.0, False, False, None, True)

    assert source.type == "target"
    assert source.name == "Ring Nebula"
    assert source.stacked_image == "/long.fits"
    assert source.has_photometry is True
    assert source.is_global is False
    assert source.ra == pytest.approx(283.396, abs=0.01)


def test_online_objects_are_global_and_never_repeat_library_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SIMBAD objects come last, marked global, without library duplicates."""
    library_star = StellarObject(id="Vega", ra=279.23, dec=38.78, magnitude=0.03)
    sky = _sky(tmp_path, [library_star], [])
    _found(
        monkeypatch,
        [
            StellarObject(id="Vega", ra=279.23, dec=38.78, magnitude=0.03),
            StellarObject(id="HD 172167 B", ra=279.24, dec=38.79, magnitude=9.5),
            StellarObject(id="Too faint", ra=279.25, dec=38.77, magnitude=15.0),
        ],
    )

    sources = sky_sources.collect_sky_sources(sky, 279.23, 38.78, 1.0, True, True, 12.0, True)

    assert _ids(sources) == ["Vega", "HD 172167 B"]
    assert [source.is_global for source in sources] == [False, True]


def test_online_catalog_sources_name_their_driver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each online catalog star records which driver found it."""
    sky = _sky(tmp_path, [], [])
    monkeypatch.setattr(
        resolution_operations,
        "get_online_catalog_sources",
        lambda *args: [("deep_stars", StellarObject(id="Gaia 1", ra=1.0, dec=2.0, magnitude=12.1))],
    )

    (source,) = sky_sources.online_catalog_sources(sky, 1.0, 2.0, 0.5, ["deep_stars"], 13.0)

    assert source.catalog_source == "deep_stars"
    assert source.is_global is True
    assert source.model_dump(by_alias=True)["catalogSource"] == "deep_stars"
