"""Purpose: Unit tests for StellarService.get_astrometry_overlay_stars.

Description: The library places a target's stars on its image
(`StellarCatalog.query(detail="overlay")`; its rules are tested in
`astrometricslib/api/test/test_star_query_display.py`). These tests check
what the backend adds: the request reaches the library with the right
arguments, the answer comes back with the app's camelCase keys, and a repeat
request is answered from memory until the database changes.
"""

import os
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrometricslib import OverlayStar, StarQueryResult
from backend.services.data.stellar_service import StellarService


def test_an_empty_target_id_returns_no_stars() -> None:
    """An empty target id is answered at once, without asking the library."""
    astrometrics = MagicMock()
    service = StellarService(config=MagicMock(), astrometrics=astrometrics, wayfinder=MagicMock())

    assert service.get_astrometry_overlay_stars(target_id="") == []
    astrometrics.stars.query.assert_not_called()


def test_the_overlay_comes_from_the_library_with_camel_case_keys(tmp_path: Path) -> None:
    """The library's overlay stars reach the app under the keys it reads."""
    astrometrics = MagicMock()
    astrometrics.targets.get.return_value = None
    astrometrics.stars.query.return_value = StarQueryResult(
        detail="overlay",
        overlay=[
            OverlayStar(
                id="HD 1",
                name="HD 1",
                x=10.0,
                y=20.0,
                spectral_type="K2",
                is_catalog_identified=True,
                reference_width=60,
                reference_height=40,
                radius_px=2.5,
            )
        ],
    )
    config = SimpleNamespace(get_library_path=lambda: str(tmp_path))
    service = StellarService(config=config, astrometrics=astrometrics, wayfinder=MagicMock())

    results = service.get_astrometry_overlay_stars(target_id="M 13", limit=3)

    astrometrics.stars.query.assert_called_once_with(target_id="M 13", detail="overlay", limit=3)
    assert results == [
        {
            "id": "HD 1",
            "name": "HD 1",
            "x": 10.0,
            "y": 20.0,
            "spectralType": "K2",
            "isCatalogIdentified": True,
            "referenceWidth": 60,
            "referenceHeight": 40,
            "radiusPx": 2.5,
        }
    ]


def _service_with_counting_overlay(library_path: Path) -> tuple[StellarService, list[str]]:
    """Build a service whose overlay work is replaced by a counter.

    Returns
    -------
    service : `StellarService`
        A service with no real catalog behind it.
    calls : `list` [`str`]
        One entry is added each time the overlay is really computed.
    """
    service = StellarService.__new__(StellarService)
    service.config = SimpleNamespace(get_library_path=lambda: library_path)
    service._overlay_cache = OrderedDict()
    service.astrometrics = SimpleNamespace(
        targets=SimpleNamespace(
            get=lambda target_id: SimpleNamespace(
                stacking=SimpleNamespace(stacked_image=None, processed_image=None)
            )
        )
    )
    calls: list[str] = []

    def compute(target_id: str, limit: int = 35) -> list[dict]:
        """Count the call and return one star.

        Returns
        -------
        stars : `list` [`dict`]
            A single fake overlay star.
        """
        calls.append(target_id)
        return [{"id": "S1", "x": 1.0}]

    service._compute_overlay = compute
    return service, calls


def test_the_overlay_is_reused_until_the_database_changes(tmp_path: Path) -> None:
    """A repeat request is answered from memory until a write."""
    database = tmp_path / "astrometrics.db"
    database.write_text("a")
    service, calls = _service_with_counting_overlay(tmp_path)

    first = service.get_astrometry_overlay_stars("M 51")
    second = service.get_astrometry_overlay_stars("M 51")
    assert calls == ["M 51"]
    assert first == second

    second[0]["x"] = 99.0
    assert service.get_astrometry_overlay_stars("M 51")[0]["x"] == pytest.approx(1.0)

    os.utime(database, ns=(1, 1))
    service.get_astrometry_overlay_stars("M 51")
    assert calls == ["M 51", "M 51"]
