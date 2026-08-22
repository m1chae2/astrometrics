"""Shared pytest fixtures and test-session bootstrapping for backend tests.

Configures an isolated temporary library directory, patches
``AppConfiguration`` to use it, and stubs out ``astroquery`` before any
backend module is imported, so the test suite never touches the real
astrometrics library index or the network.
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
test_logs_path = TEST_TEMP_DIR / "logs"
test_targets_path = test_library_path / "targets"
test_calibration_path = test_library_path / "calibration"

test_library_path.mkdir(parents=True, exist_ok=True)
test_frames_path.mkdir(parents=True, exist_ok=True)
test_logs_path.mkdir(parents=True, exist_ok=True)
test_targets_path.mkdir(parents=True, exist_ok=True)
test_calibration_path.mkdir(parents=True, exist_ok=True)

test_config_path = TEST_TEMP_DIR / "astrometrics.config"
test_config_path.write_text(f"""[Image Library]
path = {test_library_path}
frames_path = {test_frames_path}
""")

# 4. Patch AppConfiguration so it always uses this temporary directory
from astrometricslib import AppConfiguration

# Monkeypatch the class methods directly
AppConfiguration._find_config_file = lambda self: test_config_path
AppConfiguration.get_project_root = lambda self: TEST_TEMP_DIR

# Also mock astroquery to avoid external calls, just like tests/conftest.py did
from unittest.mock import MagicMock

mock_astroquery = MagicMock()
sys.modules["astroquery"] = mock_astroquery
sys.modules["astroquery.simbad"] = mock_astroquery.simbad
sys.modules["astroquery.astrometry_net"] = mock_astroquery.astrometry_net

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Ensure the environment remains setup during the session."""
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


@pytest.fixture
def client():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """FastAPI test client fixture.

    Returns
    -------
    client : `~fastapi.testclient.TestClient`
        A `TestClient` wrapping the high-level interface FastAPI `app`.
    """
    from backend.main_backend import app

    return TestClient(app)


@pytest.fixture
def mock_container(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Fixture to mock the DI container and its services.

    Returns
    -------
    container : `Container`
        The module-level singleton `Container` instance.
    """
    from backend.container import container

    return container
