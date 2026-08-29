# ruff: file-ignore[module-import-not-at-top-of-file]
"""Root test configuration and session-wide test isolation for Astrometrics.

Ensures that all test suites across the repository (astrometricslib,
wayfindinglib, and backend) run against an isolated temporary configuration
and data directory, preventing tests from mutating real configuration files.
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

# 2. Configure Matplotlib to use the headless Agg backend
import matplotlib

matplotlib.use("Agg")

# 3. Setup a global temporary directory for tests
_test_tmp_dir = tempfile.TemporaryDirectory()
TEST_TEMP_DIR = Path(_test_tmp_dir.name)

# 4. Create isolated library, frames, wayfinding, and logs directories
test_library_path = TEST_TEMP_DIR / "libraryIndex"
test_frames_path = test_library_path / "frames"
test_wayfinding_path = TEST_TEMP_DIR / "wayfinding_library"
test_logs_path = TEST_TEMP_DIR / "logs"
test_targets_path = test_library_path / "targets"
test_calibration_path = test_library_path / "calibration"

test_library_path.mkdir(parents=True, exist_ok=True)
test_frames_path.mkdir(parents=True, exist_ok=True)
test_wayfinding_path.mkdir(parents=True, exist_ok=True)
test_logs_path.mkdir(parents=True, exist_ok=True)
test_targets_path.mkdir(parents=True, exist_ok=True)
test_calibration_path.mkdir(parents=True, exist_ok=True)

test_config_path = TEST_TEMP_DIR / "astrometrics.config"
test_config_path.write_text(f"""[Image Library]
path = {test_library_path}
frames_path = {test_frames_path}

[Wayfinding Library]
path = {test_wayfinding_path}
""")
os.environ["ASTROMETRICS_CONFIG"] = str(test_config_path)
os.environ["ASTROMETRICS_CONFIG_PATH"] = str(test_config_path)

# 5. Patch AppConfiguration so it always uses this temporary directory
from astrometricslib import AppConfiguration

AppConfiguration._find_config_file = lambda self: test_config_path
AppConfiguration.get_project_root = lambda self: TEST_TEMP_DIR

# 6. Mock astroquery to avoid external network calls
mock_astroquery = MagicMock()
sys.modules.setdefault("astroquery", mock_astroquery)
sys.modules.setdefault("astroquery.simbad", mock_astroquery.simbad)
sys.modules.setdefault("astroquery.astrometry_net", mock_astroquery.astrometry_net)


@pytest.fixture(scope="session", autouse=True)
def isolate_root_config_singleton() -> collections.abc.Generator[AppConfiguration]:
    """Create a temporary settings object for the tests.

    This makes sure tests don't accidentally change the real settings
    used by the main program. It puts the original settings back when
    the tests are done.

    Yields
    ------
    sandbox_config : `AppConfiguration`
        The temporary settings object tests should use.
    """
    yield AppConfiguration()


@pytest.fixture(scope="session", autouse=True)
def setup_root_test_environment() -> collections.abc.Generator[None]:
    """Create the safe testing folder and delete it when tests are finished."""
    yield
    import time

    try:
        _test_tmp_dir.cleanup()
    except OSError:
        time.sleep(0.5)
        _test_tmp_dir.cleanup()
