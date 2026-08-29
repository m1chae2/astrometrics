# ruff: file-ignore[module-import-not-at-top-of-file]
"""Setup tools for running automated wayfindinglib tests.

This file creates a safe, isolated temporary environment for wayfindinglib
tests to run in, ensuring they never modify the live astrometrics.config or
touch production databases.
"""

import collections.abc
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 1. Set testing flag immediately so any module loading later sees it
os.environ["ASTROMETRICS_TESTING"] = "1"

# 2. Setup a global temporary directory for tests
_test_tmp_dir = tempfile.TemporaryDirectory()
TEST_TEMP_DIR = Path(_test_tmp_dir.name)

# 3. Create isolated library, wayfinding library, and log directories
test_library_path = TEST_TEMP_DIR / "libraryIndex"
test_frames_path = test_library_path / "frames"
test_wayfinding_path = TEST_TEMP_DIR / "wayfinding_library"
test_logs_path = TEST_TEMP_DIR / "logs"

test_library_path.mkdir(parents=True, exist_ok=True)
test_frames_path.mkdir(parents=True, exist_ok=True)
test_wayfinding_path.mkdir(parents=True, exist_ok=True)
test_logs_path.mkdir(parents=True, exist_ok=True)

test_config_path = TEST_TEMP_DIR / "astrometrics.config"
test_config_path.write_text(f"""[Image Library]
path = {test_library_path}
frames_path = {test_frames_path}

[Wayfinding Library]
path = {test_wayfinding_path}
""")
os.environ["ASTROMETRICS_CONFIG"] = str(test_config_path)
os.environ["ASTROMETRICS_CONFIG_PATH"] = str(test_config_path)

# 4. Patch AppConfiguration so it always uses this temporary directory
from astrometricslib import AppConfiguration

AppConfiguration._find_config_file = lambda self: test_config_path
AppConfiguration.get_project_root = lambda self: TEST_TEMP_DIR

# 5. Mock astroquery if imported
mock_astroquery = MagicMock()
sys.modules.setdefault("astroquery", mock_astroquery)
sys.modules.setdefault("astroquery.simbad", mock_astroquery.simbad)
sys.modules.setdefault("astroquery.astrometry_net", mock_astroquery.astrometry_net)


@pytest.fixture(scope="session", autouse=True)
def isolate_wayfinding_config_singleton() -> collections.abc.Generator[AppConfiguration]:
    """Create a temporary settings object for wayfindinglib tests.

    Ensures tests don't mutate live settings or configuration on disk.

    Yields
    ------
    sandbox_config : `AppConfiguration`
        The temporary settings object tests should use.
    """
    yield AppConfiguration()


@pytest.fixture(scope="session", autouse=True)
def setup_wayfinding_test_environment() -> collections.abc.Generator[None]:
    """Clean up the safe testing folder when tests are finished."""
    yield
    import time

    try:
        _test_tmp_dir.cleanup()
    except OSError:
        time.sleep(0.5)
        _test_tmp_dir.cleanup()
