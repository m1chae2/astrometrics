# ruff: file-ignore[module-import-not-at-top-of-file]
"""Root test configuration and session-wide test isolation for Astrometrics.

This is the only place that makes the temporary library, database folders and
settings file the tests use. Every test suite (astrometricslib,
wayfindinglib, and backend) shares them, so no test can reach the real
settings or data. The other `conftest.py` files add only what their own suite
needs on top of them.

A test that changes settings should not change the shared settings file. It
asks for the `config_in_tmp_path` fixture, which gives it a settings file of
its own. A guard checks that every settings object a test creates reads a
file inside the shared test folder or the test's own folder.
"""

import collections.abc
import os
import sys
import tempfile
import types
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

# 5. Mock astroquery to avoid external network calls
mock_astroquery = MagicMock()
sys.modules.setdefault("astroquery", mock_astroquery)
sys.modules.setdefault("astroquery.simbad", mock_astroquery.simbad)
sys.modules.setdefault("astroquery.astrometry_net", mock_astroquery.astrometry_net)

from astrometricslib import AppConfiguration
from astrometricslib.foundation import config as config_loader  # ruff: ignore[banned-api]


@pytest.fixture(scope="session")
def test_directories() -> types.SimpleNamespace:
    """Name the shared temporary folders the tests work in.

    Returns
    -------
    directories : `types.SimpleNamespace`
        ``temp`` (the top folder), ``library``, ``frames``, ``wayfinding``,
        ``logs``, ``targets``, ``calibration`` and ``config`` (the shared
        settings file).
    """
    return types.SimpleNamespace(
        temp=TEST_TEMP_DIR,
        library=test_library_path,
        frames=test_frames_path,
        wayfinding=test_wayfinding_path,
        logs=test_logs_path,
        targets=test_targets_path,
        calibration=test_calibration_path,
        config=test_config_path,
    )


@pytest.fixture(scope="session")
def shipped_camera_sections() -> str:
    """Give the camera sections of the shipped settings template.

    Returns
    -------
    sections_text : `str`
        See `_shipped_camera_sections_toml`.
    """
    return _shipped_camera_sections_toml()


@pytest.fixture(scope="session", autouse=True)
def shared_test_settings_file() -> collections.abc.Generator[None]:
    """Point every settings object at the shared test settings file.

    Replaces the settings file lookup and the project root for the whole
    session, and puts the originals back at the end. Done here and not when
    this file is imported, so the change is visible, undoable and does not
    depend on import order.

    Yields
    ------
    None
        Control, while the tests run.
    """
    patcher = pytest.MonkeyPatch()
    patcher.setattr(AppConfiguration, "_find_config_file", lambda self: test_config_path)
    patcher.setattr(AppConfiguration, "get_project_root", lambda self: TEST_TEMP_DIR)
    try:
        yield
    finally:
        patcher.undo()


@pytest.fixture(scope="session", autouse=True)
def isolate_config_singleton(
    shared_test_settings_file: None,
) -> collections.abc.Generator[AppConfiguration]:
    """Make the shared settings object read the shared test settings file.

    This makes sure tests don't accidentally change the real settings used by
    the main program. It puts the original settings object back when the
    tests are done.

    Parameters
    ----------
    shared_test_settings_file : `None`
        Makes sure the settings file lookup is patched first.

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


@pytest.fixture(autouse=True)
def settings_files_stay_in_test_folders(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Fail a test whose settings object reads a file outside the test folders.

    A settings object that reads the real settings file would change what the
    test sees depending on the machine, and a save could overwrite it. The
    allowed places are the shared test folder and the folders pytest makes for
    tests (``tmp_path``).
    """
    allowed_folders = (TEST_TEMP_DIR.resolve(), tmp_path_factory.getbasetemp().resolve())
    real_load_configuration = AppConfiguration.load_configuration

    def load_configuration_inside_test_folders(self: AppConfiguration) -> None:
        """Check which file would be read, and only then load the settings."""
        config_file = Path(self._find_config_file()).resolve()
        assert any(folder in config_file.parents for folder in allowed_folders), (
            f"A test made a settings object that reads '{config_file}', outside the test folders "
            f"{[str(folder) for folder in allowed_folders]}. Use the config_in_tmp_path fixture."
        )
        real_load_configuration(self)

    monkeypatch.setattr(AppConfiguration, "load_configuration", load_configuration_inside_test_folders)


@pytest.fixture(autouse=True)
def shared_settings_file_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> collections.abc.Generator[None]:
    """Keep every test's settings changes out of the shared settings file.

    Every test shares one settings file. A test that saves a change to it (for
    example with ``update_config`` on a settings object that reads it) changes
    what every later test sees, so a suite can pass in one order and fail in
    another. Two things stop that:

    - A settings object that would save into the shared file saves into a
      file in the test's own folder instead.
    - If the shared file changes anyway, by some other route, it is put back
      as it was and the test fails with a pointer to the fix, so one such test
      does not hide the others.

    Parameters
    ----------
    tmp_path : `Path`
        The test's own folder.
    monkeypatch : `pytest.MonkeyPatch`
        Used to replace the save for the length of the test.

    Yields
    ------
    None
        Control, while the test runs.
    """
    real_save_configuration = AppConfiguration.save_configuration

    def save_outside_the_shared_file(self: AppConfiguration) -> None:
        """Save to the test's own folder, not over the shared file."""
        target = Path(self.config_file_path or self._find_config_file())
        if target.resolve() == test_config_path.resolve():
            self.config_file_path = tmp_path / f"saved_{id(self)}.config.toml"
        real_save_configuration(self)

    monkeypatch.setattr(AppConfiguration, "save_configuration", save_outside_the_shared_file)
    text_before = test_config_path.read_text(encoding="utf-8")
    yield
    text_after = test_config_path.read_text(encoding="utf-8")
    if text_after != text_before:
        test_config_path.write_text(text_before, encoding="utf-8")
        pytest.fail(
            "This test changed the shared test settings file, which every other test also reads. "
            "Use the config_in_tmp_path fixture to give the test a settings file of its own.",
            pytrace=False,
        )


@pytest.fixture
def config_in_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfiguration:
    """Give a test a settings object with a settings file of its own.

    The file lies in the test's own folder, with an image library and a
    wayfinding library inside that folder. The test can change
    and save settings without touching the shared file or any other test.

    Returns
    -------
    configuration : `AppConfiguration`
        A new settings object reading the test's own settings file.
    """
    library_path = tmp_path / "library"
    library_path.mkdir()
    wayfinding_path = tmp_path / "wayfinding_library"
    wayfinding_path.mkdir()
    own_config_path = tmp_path / "astrometrics.config.toml"
    own_config_path.write_text(
        f'["Image Library"]\npath = "{library_path}"\n\n'
        f'["Wayfinding Library"]\npath = "{wayfinding_path}"\n\n' + _shipped_camera_sections_toml(),
        encoding="utf-8",
    )
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: own_config_path)
    return AppConfiguration()


@pytest.fixture(scope="session", autouse=True)
def setup_root_test_environment() -> collections.abc.Generator[None]:
    """Create the safe testing folder and delete it when tests are finished.

    SQLite connections can lazily create -wal/-shm files after the last
    query, which occasionally races the folder removal and raises ENOTEMPTY,
    so the removal is tried again once after a short pause.
    """
    yield
    import time

    try:
        _test_tmp_dir.cleanup()
    except OSError:
        time.sleep(0.5)
        _test_tmp_dir.cleanup()
