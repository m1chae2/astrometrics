"""Setup tools for running automated tests.

This file creates a safe, temporary environment for tests to run in,
so they don't accidentally overwrite your real astronomy data. It also
fakes (mocks) connections to online astronomy databases so tests can run
offline and quickly.
"""

import os
import sys
import tempfile
from pathlib import Path

# 1. Set testing flag immediately so any module loading later sees it
os.environ["ASTROMETRICS_TESTING"] = "1"

# 2. Configure Matplotlib to use the headless Agg backend to avoid
# Tkinter warnings
import matplotlib

matplotlib.use("Agg")

# 2. Setup a global temporary directory for tests
_test_tmp_dir = tempfile.TemporaryDirectory()
TEST_TEMP_DIR = Path(_test_tmp_dir.name)

# 3. Create isolated library and frames directories
test_library_path = TEST_TEMP_DIR / "libraryIndex"
test_frames_path = test_library_path / "frames"
test_targets_path = test_library_path / "targets"
test_calibration_path = test_library_path / "calibration"

test_library_path.mkdir(parents=True, exist_ok=True)
test_frames_path.mkdir(parents=True, exist_ok=True)
test_targets_path.mkdir(parents=True, exist_ok=True)
test_calibration_path.mkdir(parents=True, exist_ok=True)

test_config_path = TEST_TEMP_DIR / "astrometrics.config"
test_config_path.write_text(f"""[Image Library]
path = {test_library_path}
frames_path = {test_frames_path}
""")
os.environ["ASTROMETRICS_CONFIG"] = str(test_config_path)

# Populate synthetic test data for CI/CD environment
import astropy.io.fits as fits
import numpy as np


def _seed_synthetic_test_library():  # ruff: ignore[missing-return-type-private-function]
    """Create fake images and a fake database for testing.

    Raises
    ------
    RuntimeError
        If it accidentally points to your real database instead of the
        safe temporary one.
    """
    m81_dir = test_frames_path / "lights" / "M 81" / "ZWO ASI 533MM Pro"
    vega_dir = test_frames_path / "lights" / "Vega"
    m13_dir = test_frames_path / "lights" / "M 13"

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
    from astrometricslib.drivers.disk_interface import save_target
    from astrometricslib.utilities.config_loader import AppConfiguration

    app_config = AppConfiguration()
    resolved_library_path = Path(str(app_config.get_library_path())).resolve()
    if TEST_TEMP_DIR not in resolved_library_path.parents and resolved_library_path != TEST_TEMP_DIR:
        raise RuntimeError(
            "Refusing to seed synthetic test data: resolved library path "
            f"'{resolved_library_path}' is outside the sandboxed test directory "
            f"'{TEST_TEMP_DIR}'. This would overwrite the real production "
            "astrometrics.db. Check AppConfiguration._find_config_file patching."
        )

    t_m81 = Target(id="M 81", right_ascension="09:55:33", declination="+69:03:55", frames=frame_records)
    t_vega = Target(id="Vega", right_ascension="18:36:56", declination="+38:47:01", stacked_image=vega_fits)
    t_m13 = Target(id="M 13", right_ascension="16:41:41", declination="+36:27:35", stacked_image=m13_fits)

    save_target(app_config=app_config, target=t_m81)
    save_target(app_config=app_config, target=t_vega)
    save_target(app_config=app_config, target=t_m13)


# 4. Patch AppConfiguration so it always uses this temporary directory,
# BEFORE seeding the synthetic library below -- otherwise seeding would
# resolve the real production config/database and write synthetic test
# data into it.
from astrometricslib.utilities import config_loader
from astrometricslib.utilities.config_loader import AppConfiguration

AppConfiguration._find_config_file = lambda self: test_config_path
AppConfiguration.get_project_root = lambda self: TEST_TEMP_DIR

_seed_synthetic_test_library()

# 5. Mock astroquery to avoid external calls
from unittest.mock import MagicMock

mock_astroquery = MagicMock()
sys.modules["astroquery"] = mock_astroquery
sys.modules["astroquery.simbad"] = mock_astroquery.simbad
sys.modules["astroquery.astrometry_net"] = mock_astroquery.astrometry_net
sys.modules["astroquery.imcce"] = mock_astroquery.imcce
sys.modules["astroquery.gaia"] = mock_astroquery.gaia

import pytest


def pytest_configure(config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Register custom markers to stop pytest from printing warnings."""
    config.addinivalue_line("markers", "slow: marks tests as slow subprocess integration tests")


@pytest.fixture(scope="session", autouse=True)
def isolate_config_singleton():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Create a temporary settings object for the tests.

    This makes sure tests don't accidentally change the real settings
    used by the main program. It puts the original settings back when
    the tests are done.

    Yields
    ------
    sandbox_config : `AppConfiguration`
        The temporary settings object tests should use.
    """
    original_instance = getattr(config_loader, "_instance", None)

    sandbox_config = AppConfiguration()
    config_loader._instance = sandbox_config

    yield sandbox_config

    config_loader._instance = original_instance


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Create the safe testing folder and delete it when tests are finished."""
    yield
    # Cleanup temp directory when test session ends. SQLite connections
    # can lazily create -wal/-shm files after the last query, which
    # occasionally races shutil.rmtree's directory walk and raises
    # ENOTEMPTY; retry once after a short pause to absorb that.
    import time

    try:
        _test_tmp_dir.cleanup()
    except OSError:
        time.sleep(0.5)
        _test_tmp_dir.cleanup()
