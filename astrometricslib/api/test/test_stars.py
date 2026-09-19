"""Purpose: Unit tests for the StellarCatalog high-level interface.

Description: Verifies `save_all` passes the catalog_access's required
`coordinate` argument (omitting it previously made every call raise
TypeError, including the backend's own save path), and that its
replace-all semantics cannot silently wipe the catalog when handed an
empty list.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from astrometricslib.api.stars import StellarCatalog
from astrometricslib.models.stellar_source import PhotometryResult, StellarObject


def _make_catalog() -> StellarCatalog:
    config = MagicMock()
    catalog_access = MagicMock()
    return StellarCatalog(config=config, catalog_access=catalog_access)


class TestSaveAll:
    """Unit test suite for StellarCatalog.save_all."""

    def test_passes_required_coordinate_argument_to_catalog_access(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify the catalog_access receives all three required arguments.

        `AbstractCatalogAccess.put(obj, dataset_type, coordinate)` requires
        `coordinate`; the previous two-argument call raised TypeError on
        every invocation.
        """
        catalog = _make_catalog()
        objects = [StellarObject(id="* alf Lyr", name="Vega")]

        result = catalog.save_all(objects)

        catalog.catalog_access.put.assert_called_once_with(objects, "stellar_catalog", {})
        assert result == "stellar catalog saved"

    def test_empty_list_is_refused_by_default(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """An empty list must not silently delete the whole catalog.

        `put_all` runs an unconditional DELETE when given an empty
        list, so a caller that simply had not loaded the catalog yet
        would otherwise destroy every row.
        """
        catalog = _make_catalog()

        with pytest.raises(ValueError, match="empty list"):
            catalog.save_all([])

        catalog.catalog_access.put.assert_not_called()

    def test_empty_list_allowed_when_explicitly_requested(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Deliberately clearing the catalog remains possible."""
        catalog = _make_catalog()

        result = catalog.save_all([], allow_empty=True)

        catalog.catalog_access.put.assert_called_once_with([], "stellar_catalog", {})
        assert result == "stellar catalog saved"


class TestAnalyzePeriodicity:
    """Unit test suite for StellarCatalog.analyze_periodicity."""

    def test_saves_a_periodogram_when_there_are_enough_points(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify 20 measurements produce a periodogram that is saved."""
        catalog = _make_catalog()
        start = datetime(2026, 1, 1, tzinfo=UTC)
        star = StellarObject(
            id="Gaia DR3 1",
            photometry=PhotometryResult(
                timestamps=[start + timedelta(minutes=10 * index) for index in range(20)],
                fluxes_detrended=[1.0 + 0.1 * (index % 4) for index in range(20)],
            ),
        )
        catalog.get_object = MagicMock(return_value=star)
        catalog.update = MagicMock(return_value=star)

        result = catalog.analyze_periodicity("Gaia DR3 1")

        assert result.photometry.periodogram is not None
        object_id, updates = catalog.update.call_args.args
        assert object_id == "Gaia DR3 1"
        assert updates["photometry"].periodogram is not None

    def test_returns_a_star_with_too_few_points_without_saving(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify a star with 3 measurements is returned unchanged."""
        catalog = _make_catalog()
        star = StellarObject(
            id="Gaia DR3 1",
            photometry=PhotometryResult(
                timestamps=[datetime(2026, 1, 1, tzinfo=UTC)] * 3, fluxes_detrended=[1.0, 1.1, 1.0]
            ),
        )
        catalog.get_object = MagicMock(return_value=star)
        catalog.update = MagicMock()

        assert catalog.analyze_periodicity("Gaia DR3 1") is star
        catalog.update.assert_not_called()

    def test_returns_none_for_an_unknown_star(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify an id that is not in the catalog gives None."""
        catalog = _make_catalog()
        catalog.get_object = MagicMock(return_value=None)

        assert catalog.analyze_periodicity("missing") is None
