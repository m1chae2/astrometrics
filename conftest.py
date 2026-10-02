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

# 3. Configure Astropy to use bundled earth orientation tables, not downloads
from astropy.utils import iers

iers.conf.auto_download = False
iers.conf.auto_max_age = None

# 3. Setup a global temporary directory for tests
_test_tmp_dir = tempfile.TemporaryDirectory()
TEST_TEMP_DIR = Path(_test_tmp_dir.name)

# 4. Create isolated library, frames, wayfinding, and logs directories
test_library_path = TEST_TEMP_DIR / "library"
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


def _shipped_camera_sections_toml() -> str:
    """Read the real camera sections out of the shipped config template.

    Keeps the test fixture's camera catalog (aliases, record names,
    quantum efficiency curves) identical to the shipped one without
    duplicating it by hand.

    Returns
    -------
    sections_text : `str`
        Every ``[Observatory.Camera.<name>]`` section's TOML text,
        concatenated.
    """
    import tomlkit

    template_path = Path(__file__).parent / "astrometricslib" / "astrometrics.config.example.toml"
    document = tomlkit.parse(template_path.read_text(encoding="utf-8"))
    blocks = []
    for section_name, table in document.items():
        if section_name.startswith("Observatory.Camera.") and "clip_ceiling_adu" in table:
            blocks.append(f'["{section_name}"]\n{tomlkit.dumps(table)}')
    return "\n".join(blocks)


test_config_path = TEST_TEMP_DIR / "astrometrics.config.toml"
test_config_path.write_text(
    f'["Image Library"]\n'
    f'path = "{test_library_path}"\n'
    f'frames_path = "{test_frames_path}"\n\n'
    f'["Wayfinding Library"]\n'
    f'path = "{test_wayfinding_path}"\n\n' + _shipped_camera_sections_toml()
)
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
