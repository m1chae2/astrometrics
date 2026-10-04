"""Setup tools for running the astrometricslib tests.

The temporary library, database and settings file the tests use are made once
for the whole repository, in the root `conftest.py`. This file adds only what
the astrometricslib tests need on top of them: a small synthetic library
(fake images and a fake database), fake connections to online astronomy
databases so tests run offline, and a stop on starting Siril for previews.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Set the testing flag immediately so any module loading later sees it
os.environ["ASTROMETRICS_TESTING"] = "1"

# Use the headless Agg backend for Matplotlib, to avoid Tkinter warnings
import matplotlib

matplotlib.use("Agg")

# Populate synthetic test data for CI/CD environment
import astropy.io.fits as fits
import numpy as np


def _seed_synthetic_test_library(directories: SimpleNamespace) -> None:
    """Create fake images and a fake database for testing.

    Parameters
    ----------
    directories : `types.SimpleNamespace`
        The shared test folders (see the root `conftest.py`).

    Raises
    ------
    RuntimeError
        If it accidentally points to your real database instead of the
        safe temporary one.
    """
    m81_dir = directories.frames / "lights" / "M 81" / "ZWO ASI 533MM Pro"
    vega_dir = directories.frames / "lights" / "Vega"
    m13_dir = directories.frames / "lights" / "M 13"

    m81_dir.mkdir(parents=True, exist_ok=True)
    vega_dir.mkdir(parents=True, exist_ok=True)
    m13_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create synthetic FITS images with headers
    arr = np.zeros((100, 100), dtype=np.float32)
    # Add synthetic star peak
    arr[50, 50] = 5000.0

    frame_records = []
    for i in range(3):
        frame_path = str(m81_dir / f"M81_Light_00{i + 1}.fits")
        hdu = fits.PrimaryHDU(arr)
        hdu.header["OBJECT"] = "M 81"
        hdu.header["FILTER"] = "L"
        hdu.header["INSTRUME"] = "ZWO ASI 533MM Pro"
        hdu.header["EXPOSURE"] = 30.0
        hdu.header["DATE-OBS"] = f"2026-05-01T00:0{i}:00"
        hdu.writeto(frame_path, overwrite=True)

        from astrometricslib import FrameRecord

        frame_records.append(
            FrameRecord(
                path=frame_path,
                filter="Luminance",
                role="LIGHT",
                camera="ZWO ASI 533MM Pro",
                date=f"2026-05-01 00:0{i}:00",
                timestamp=1777507200.0 + i * 60,
                exposure="30.0",
            )
        )

    vega_fits = str(vega_dir / "Vega_Stacked.fits")
    vega_hdu = fits.PrimaryHDU(arr)
    vega_hdu.header["OBJECT"] = "Vega"
    vega_hdu.writeto(vega_fits, overwrite=True)

    m13_fits = str(m13_dir / "M_13_Stacked.fits")
    m13_hdu = fits.PrimaryHDU(arr)
    m13_hdu.header["OBJECT"] = "M 13"
    m13_hdu.writeto(m13_fits, overwrite=True)

    # 2. Seed SQLite database
    from astrometricslib import Target
    from astrometricslib.drivers.local_database import save_target
    from astrometricslib.utilities.config_loader import AppConfiguration

    app_config = AppConfiguration()
    resolved_library_path = Path(str(app_config.get_library_path())).resolve()
    if directories.temp not in resolved_library_path.parents and resolved_library_path != directories.temp:
        raise RuntimeError(
            "Refusing to seed synthetic test data: resolved library path "
            f"'{resolved_library_path}' is outside the sandboxed test directory "
            f"'{directories.temp}'. This would overwrite the real production "
            "astrometrics.db. Check AppConfiguration._find_config_file patching."
        )

    t_m81 = Target(id="M 81", right_ascension="09:55:33", declination="+69:03:55", frames=frame_records)
    t_vega = Target(id="Vega", right_ascension="18:36:56", declination="+38:47:01", stacked_image=vega_fits)
    t_m13 = Target(id="M 13", right_ascension="16:41:41", declination="+36:27:35", stacked_image=m13_fits)

    save_target(app_config=app_config, target=t_m81)
    save_target(app_config=app_config, target=t_vega)
    save_target(app_config=app_config, target=t_m13)


# 5. Mock astroquery to avoid external calls
from unittest.mock import MagicMock

mock_astroquery = MagicMock()
sys.modules["astroquery"] = mock_astroquery
sys.modules["astroquery.simbad"] = mock_astroquery.simbad
sys.modules["astroquery.astrometry_net"] = mock_astroquery.astrometry_net
sys.modules["astroquery.imcce"] = mock_astroquery.imcce
sys.modules["astroquery.gaia"] = mock_astroquery.gaia


def pytest_configure(config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Register custom markers to stop pytest from printing warnings."""
    config.addinivalue_line("markers", "slow: marks tests as slow subprocess integration tests")


@pytest.fixture(scope="session", autouse=True)
def synthetic_test_library(test_directories: SimpleNamespace, isolate_config_singleton: object) -> None:
    """Fill the shared test library with fake images and a fake database.

    Runs once per session, after the root `conftest.py` has pointed every
    settings object at the shared test settings file, so nothing is written
    to the real library.

    Parameters
    ----------
    test_directories : `types.SimpleNamespace`
        The shared test folders.
    isolate_config_singleton : `AppConfiguration`
        Makes sure the shared settings object exists and is the test one.
    """
    _seed_synthetic_test_library(test_directories)


@pytest.fixture(autouse=True)
def no_siril_stack_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop tests from starting Siril or GraXpert to draw a preview picture.

    Stacking writes a picture of each finished stack by running GraXpert and
    Siril. Tests that stack with a fake driver must not launch the real
    programs. Tests of the preview code replace these stand-ins with their
    own.
    """
    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.post_processing.stack_preview.run_preview_script",
        lambda directory, commands, siril_executable: False,
    )
    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.post_processing.stack_preview.flatten_background",
        lambda graxpert_executable, input_path, output_stem: False,
    )
    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.post_processing.stack_preview.denoise_with_cosmic_clarity",
        lambda executable, input_path, output_path, strength: False,
    )
