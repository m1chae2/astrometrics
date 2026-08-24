"""Purpose: Unit tests for the StellarCatalog high-level interface.

Description: Verifies `save_all` passes the butler's required
`coordinate` argument (omitting it previously made every call raise
TypeError, including the backend's own save path), and that its
replace-all semantics cannot silently wipe the catalog when handed an
empty list.
"""

from unittest.mock import MagicMock

import pytest

from astrometricslib.api.stars import StellarCatalog
from astrometricslib.models.stellar_source import StellarObject


def _make_catalog() -> StellarCatalog:
    config = MagicMock()
    butler = MagicMock()
    return StellarCatalog(config=config, butler=butler)


class TestSaveAll:
    """Unit test suite for StellarCatalog.save_all."""

    def test_passes_required_coordinate_argument_to_butler(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify the butler receives all three required arguments.

        `AbstractButler.put(obj, dataset_type, coordinate)` requires
        `coordinate`; the previous two-argument call raised TypeError on
        every invocation.
        """
        catalog = _make_catalog()
        objects = [StellarObject(id="* alf Lyr", name="Vega")]

        result = catalog.save_all(objects)

        catalog.butler.put.assert_called_once_with(objects, "stellar_catalog", {})
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

        catalog.butler.put.assert_not_called()

    def test_empty_list_allowed_when_explicitly_requested(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Deliberately clearing the catalog remains possible."""
        catalog = _make_catalog()

        result = catalog.save_all([], allow_empty=True)

        catalog.butler.put.assert_called_once_with([], "stellar_catalog", {})
        assert result == "stellar catalog saved"
