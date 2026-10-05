"""Purpose: Unit tests for saving spectroscopy stars and flagging shaky ones.

Description: `record_and_flag_spectroscopy_stars` is the one place both
the single-image pipeline and the per-session batch worker now go
through to reconcile, save, and flag their extracted stars -- these
tests confirm it actually does both halves (drops an unresolved
placeholder and saves the rest, then flags a shaky classification among
the saved stars), on a throwaway catalog, the same way
`test_star_recording_identified_reconciliation.py` tests
`record_pipeline_stars` itself.
"""

from pathlib import Path

import pytest

from astrometricslib.drivers.catalog_access import CatalogAccess
from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject
from astrometricslib.pipelines.spectroscopy.post_processing.assess_output_quality import (
    build_spectral_classification_concerns,
)
from astrometricslib.pipelines.spectroscopy.record_and_flag_spectroscopy_stars import (
    record_and_flag_spectroscopy_stars,
)


@pytest.fixture
def catalog_access(tmp_path: Path) -> CatalogAccess:
    """Give each test its own empty catalog inside the test's temporary folder.

    Returns
    -------
    catalog_access : `CatalogAccess`
        Catalog access over a library that holds no stars.
    """
    from astrometricslib.foundation.config import AppConfiguration

    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    return CatalogAccess(config)


def test_an_unresolved_star_is_dropped_and_the_rest_are_saved(catalog_access: CatalogAccess) -> None:
    """An unresolved placeholder is dropped; a real star is saved and kept."""
    unresolved = StellarObject(id="Star_1")
    identified = StellarObject(id="HD 1", right_ascension=10.0, declination=20.0)

    saved, breakdown, _concerns = record_and_flag_spectroscopy_stars(
        [unresolved, identified], catalog_access=catalog_access, target_id="TestTarget"
    )

    assert [star.id for star in saved] == ["HD 1"]
    assert breakdown is not None
    assert breakdown.unresolved == 1
    assert breakdown.catalog_matched + breakdown.position_only == 1
    stored = catalog_access.get_by_ids("stellar_catalog", ["HD 1"])
    assert len(stored) == 1
    assert "Star_1" not in [row.id for row in catalog_access.get_by_ids("stellar_catalog", ["Star_1"])]


def test_concerns_are_built_from_the_saved_stars_not_the_raw_input(catalog_access: CatalogAccess) -> None:
    """A shaky classification among the saved stars is flagged.

    Confirms the concerns come from calling
    `build_spectral_classification_concerns` on the same list that got
    saved (after reconciliation), not on whatever was passed in --
    exercised here by including an unresolved star that must not appear
    in the concerns list either, since it never gets saved at all.
    """
    unresolved = StellarObject(id="Star_2")
    low_confidence = StellarObject(
        id="HD 2",
        right_ascension=30.0,
        declination=40.0,
        spectroscopy=SpectroscopyResult(
            self_determined_spectral_type="O5V",
            self_determined_spectral_type_confidence=0.33,
            self_determined_spectral_type_candidates=[
                {"spectral_type": "O5V", "probability": 0.65, "correlation": 0.33},
                {"spectral_type": "B0V", "probability": 0.34, "correlation": 0.32},
            ],
        ),
    )

    saved, _breakdown, concerns = record_and_flag_spectroscopy_stars(
        [unresolved, low_confidence], catalog_access=catalog_access, target_id="TestTarget"
    )

    assert concerns == build_spectral_classification_concerns(saved)
    assert {concern["star_id"] for concern in concerns} == {"HD 2"}
