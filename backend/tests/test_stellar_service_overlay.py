"""Purpose: Unit tests for StellarService.get_astrometry_overlay_stars.

Description: Verifies that get_astrometry_overlay_stars retrieves star pixel
centroids and labels for astrometry visualization overlays, excludes cluster
pseudo-objects and detection stubs, sorts catalog-identified stars first,
and respects the limit.
"""

from unittest.mock import MagicMock

import pytest

from astrometricslib import StellarObject
from backend.services.data.stellar_service import StellarService


def _create_mock_stellar_service(
    stellar_objects: list[StellarObject] | None = None,
) -> StellarService:
    """Build a StellarService backed by mock astrometrics and wayfinder.

    Parameters
    ----------
    stellar_objects : `list[StellarObject]`, optional
        Stellar objects returned by the mock star catalog.

    Returns
    -------
    service : `StellarService`
        Configured StellarService instance.
    """
    mock_astrometrics = MagicMock()
    mock_astrometrics.stars.list_objects.return_value = stellar_objects or []
    mock_astrometrics.targets.list.return_value = []

    mock_wayfinder = MagicMock()
    mock_wayfinder.planning.get_sources.return_value = []

    return StellarService(config=MagicMock(), astrometrics=mock_astrometrics, wayfinder=mock_wayfinder)


def test_get_astrometry_overlay_stars_returns_empty_when_target_id_is_empty() -> None:
    """Verify empty target ID returns an empty list immediately."""
    service = _create_mock_stellar_service([])
    result = service.get_astrometry_overlay_stars(target_id="")
    assert result == []


def test_get_astrometry_overlay_stars_filters_by_target_id() -> None:
    """Verify only stars belonging to the requested target are returned."""
    star_target_a = StellarObject(
        id="STAR_A",
        name="Star A",
        target_ids=["M 13"],
        star_data={"x_centroid": 100.5, "y_centroid": 200.5},
        is_catalog_identified=True,
    )
    star_target_b = StellarObject(
        id="STAR_B",
        name="Star B",
        target_ids=["NGC 7000"],
        star_data={"x_centroid": 300.0, "y_centroid": 400.0},
        is_catalog_identified=True,
    )

    service = _create_mock_stellar_service([star_target_a, star_target_b])
    results = service.get_astrometry_overlay_stars(target_id="M 13")

    assert len(results) == 1
    assert results[0]["id"] == "STAR_A"
    assert results[0]["name"] == "Star A"
    assert results[0]["x"] == pytest.approx(100.5)
    assert results[0]["y"] == pytest.approx(200.5)
    assert results[0]["isCatalogIdentified"] is True


def test_get_astrometry_overlay_stars_excludes_clusters_and_stubs() -> None:
    """Verify Cluster pseudo-stars, detections, and Star_ IDs are excluded."""
    cluster_obj = StellarObject(
        id="M_13_Cluster",
        name="M 13 Cluster",
        target_ids=["M 13"],
        stellar_spectral_type="Cluster",
        star_data={"x_centroid": 500.0, "y_centroid": 500.0},
    )
    stub_detection = StellarObject(
        id="M 13:2026-01-14:0:0:Star_42",
        name="Detection 42",
        target_ids=["M 13"],
        star_data={"x_centroid": 120.0, "y_centroid": 240.0},
    )
    generic_star_prefix = StellarObject(
        id="Star_99",
        name="Star 99",
        target_ids=["M 13"],
        star_data={"x_centroid": 150.0, "y_centroid": 250.0},
    )
    valid_star = StellarObject(
        id="FIELD_J249.7726+35.4880",
        name="FIELD_J249.7726+35.4880",
        target_ids=["M 13"],
        star_data={"x_centroid": 51.4, "y_centroid": 1.2},
    )

    service = _create_mock_stellar_service([cluster_obj, stub_detection, generic_star_prefix, valid_star])
    results = service.get_astrometry_overlay_stars(target_id="M 13")

    assert len(results) == 1
    assert results[0]["id"] == "FIELD_J249.7726+35.4880"


def test_get_astrometry_overlay_stars_prioritizes_catalog_identified_and_respects_limit() -> None:
    """Verify catalog-identified stars sort first and the limit is applied."""
    stars = [
        StellarObject(
            id=f"FIELD_J_{i}",
            name=f"Field Star {i}",
            target_ids=["M 13"],
            star_data={"x_centroid": float(i * 10), "y_centroid": float(i * 10)},
            is_catalog_identified=False,
            magnitude=10.0 + i,
        )
        for i in range(10)
    ]
    catalog_star = StellarObject(
        id="TYC_1234_567_1",
        name="HD 149757",
        target_ids=["M 13"],
        star_data={"x_centroid": 800.0, "y_centroid": 600.0},
        stellar_spectral_type="O9.5V",
        is_catalog_identified=True,
        magnitude=2.56,
    )
    stars.append(catalog_star)

    service = _create_mock_stellar_service(stars)
    results = service.get_astrometry_overlay_stars(target_id="M 13", limit=3)

    assert len(results) == 3
    # The catalog-identified star must be first
    assert results[0]["id"] == "TYC_1234_567_1"
    assert results[0]["name"] == "HD 149757"
    assert results[0]["spectralType"] == "O9.5V"
    assert results[0]["isCatalogIdentified"] is True


def test_get_astrometry_overlay_stars_matches_normalized_target_id() -> None:
    """Verify target ID with underscore matches target ID with space."""
    star = StellarObject(
        id="HD_150998",
        name="HD 150998",
        target_ids=["M 13"],
        star_data={"x_centroid": 123.4, "y_centroid": 567.8},
        is_catalog_identified=True,
    )
    service = _create_mock_stellar_service([star])
    # Query with underscore
    results = service.get_astrometry_overlay_stars(target_id="M_13")
    assert len(results) == 1
    assert results[0]["id"] == "HD_150998"
    assert results[0]["x"] == pytest.approx(123.4)


def test_get_astrometry_overlay_stars_projects_celestial_coords_with_wcs() -> None:
    """Verify celestial coordinates project using WCS coordinates."""
    star = StellarObject(
        id="HD_150998",
        name="HD 150998",
        target_ids=["M 13"],
        right_ascension=250.767,
        declination=36.509,
        stellar_spectral_type="K2",
        is_catalog_identified=True,
    )
    mock_astrometrics = MagicMock()
    mock_astrometrics.stars.list_objects.return_value = [star]

    mock_target = MagicMock()
    mock_target.stacked_image = None
    mock_target.processed_image = None
    mock_astrometrics.targets.get.return_value = mock_target

    service = StellarService(config=MagicMock(), astrometrics=mock_astrometrics, wayfinder=MagicMock())

    results = service.get_astrometry_overlay_stars(target_id="M 13")
    # Since no WCS is loaded from file, stars without x/y are skipped
    assert results == []
