"""Purpose: Unit tests for Astrometrics API High-Level Interface.

Description: Verifies overall Target access and creation via the high-level
Astrometrics high-level interface.
"""

from astrometricslib import Astrometrics
from astrometricslib.tasks.target_tasks.pipeline_tasks import analyze_target
from astrometricslib.utilities.config_loader import AppConfiguration


def test_astrometrics_target_access(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the interface retrieves targets from the target directory.

    Targets are created on disk if they do not already exist there.
    """
    # Setup mock config
    config = AppConfiguration()
    library_path = tmp_path / "library"
    library_path.mkdir()
    (library_path / "targets").mkdir()
    original_path = config.get_value("Image Library", "path", fallback="./libraryIndex")
    config.update_config({"Image Library": {"path": str(library_path)}})

    try:
        astrometrics = Astrometrics(app_config=config)

        # Verify initial state
        assert len(astrometrics.targets.list()) == 0

        # Manually create a target file to verify astrometrics reading
        target_json = library_path / "targets" / "Vega.json"
        with open(target_json, "w") as f:
            f.write('{"id": "Vega", "right_ascension": "05 00 00", "declination": "+45 00 00"}')

        targets = astrometrics.targets.list()
        assert len(targets) == 1
        assert targets[0].id == "Vega"
    finally:
        config.update_config({"Image Library": {"path": original_path}})


def test_astrometry_pulls_solved_coordinates_when_unpopulated(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verifies target RA/Dec are backfilled from the WCS center when unset.

    If the target's RA and Dec are unpopulated or zero, they should be
    updated from the WCS center during astrometry analysis.
    """
    from unittest.mock import MagicMock, patch

    config = AppConfiguration()
    library_path = tmp_path / "library"
    library_path.mkdir()
    (library_path / "targets").mkdir()
    original_path = config.get_value("Image Library", "path", fallback="./libraryIndex")
    config.update_config({"Image Library": {"path": str(library_path)}})

    try:
        astrometrics = Astrometrics(app_config=config)
        target = astrometrics.targets.create("TestTarget")
        # Ensure coordinates are unpopulated
        target.ra = "0h 0m 0s"
        target.dec = "0° 0′ 0′′"
        target.stacked_image = "dummy.fits"

        # Mock AstrometryPipeline
        mock_pipeline_class = MagicMock()
        mock_pipeline_inst = MagicMock()
        mock_pipeline_class.return_value = mock_pipeline_inst

        mock_context = MagicMock()
        mock_wcs = MagicMock()
        # Mock crval: RA=180.0 deg, Dec=-45.0 deg
        mock_wcs.wcs.crval = [180.0, -45.0]
        mock_context.wcs = mock_wcs
        mock_context.stellar_objects = []
        mock_pipeline_inst.process.return_value = mock_context

        with patch(
            "astrometricslib.tasks.stellar_tasks.astrometry_tasks.astrometry_pipeline.AstrometryPipeline",
            mock_pipeline_class,
        ):
            analyze_target(target, type="astrometry")

        # Verify that coordinates were pulled from WCS
        # 180 deg RA -> 12h, -45 deg Dec -> -45d
        assert "12" in target.ra
        assert "-45" in target.dec
    finally:
        config.update_config({"Image Library": {"path": original_path}})


def test_plot_fits_star_field_handles_none_image_data():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify plot_fits_star_field handles None image_data gracefully."""
    import matplotlib.pyplot as plt

    from astrometricslib.visualization import plot_fits_star_field

    ax = plot_fits_star_field(image_data=None, stellar_objects=[])
    assert ax is not None
    plt.close(ax.figure)
