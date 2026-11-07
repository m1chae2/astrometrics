"""Purpose: Test the launch identity and the offline switch it carries.

Description: The ``[Launch]`` section of the settings file says what kind of
run this is. These tests check that the one reader parses it in every
spelling a settings file or the settings page can give, that a file without
the section is an ordinary run, and that `require_online` and
`require_local_host` refuse exactly when ``offline = true``.
"""

from pathlib import Path

import pytest

from astrometricslib.drivers import (
    LaunchIdentity,
    OfflineModeError,
    is_loopback_host,
    launch_identity_from_settings,
    library_folders,
    network_is_offline,
    read_launch_identity,
    require_local_host,
    require_online,
)
from astrometricslib.foundation import offline
from astrometricslib.foundation.config import _TomlSectionedConfig

FULL_SECTION = """
[Launch]
label = "SIMULATOR"
live_hardware = false
disposable_library = true
offline = true
locked_keys = ["Observatory.Telescope.hostname", "Image Library.path"]
"""


def _identity_from_text(text: str) -> LaunchIdentity | None:
    """Read the identity from settings text through the settings object.

    Parameters
    ----------
    text : `str`
        A settings file's contents.

    Returns
    -------
    identity : `LaunchIdentity` or `None`
        What `AppConfiguration.get_launch_identity` would give.
    """
    from astrometricslib.foundation.config import AppConfiguration

    config = AppConfiguration.__new__(AppConfiguration)
    config.app_config = _TomlSectionedConfig()
    config.app_config.read_string(text)
    return config.get_launch_identity()


def test_a_settings_file_without_the_section_is_an_ordinary_run() -> None:
    """No ``[Launch]`` section gives no identity."""
    assert _identity_from_text('["Image Library"]\npath = "x"\n') is None
    assert launch_identity_from_settings({}) is None


def test_a_full_section_is_read_through_the_settings_object() -> None:
    """Every key of the section reaches the identity."""
    identity = _identity_from_text(FULL_SECTION)

    assert identity == LaunchIdentity(
        label="SIMULATOR",
        live_hardware=False,
        disposable_library=True,
        offline=True,
        locked_keys=("Observatory.Telescope.hostname", "Image Library.path"),
    )
    assert identity.is_locked("Observatory.Telescope", "hostname")
    assert identity.is_locked("Image Library", "path")
    assert not identity.is_locked("Observatory.Telescope", "indi_port")


def test_the_description_is_plain_json_values() -> None:
    """``as_dict`` is the shape ``observatory:get_source`` reports."""
    identity = _identity_from_text(FULL_SECTION)

    assert identity.as_dict() == {
        "label": "SIMULATOR",
        "live_hardware": False,
        "disposable_library": True,
        "offline": True,
        "locked_keys": ["Observatory.Telescope.hostname", "Image Library.path"],
    }


def test_missing_keys_take_the_safe_defaults() -> None:
    """A bare section assumes live hardware, a kept library, a network."""
    identity = _identity_from_text("[Launch]\n")

    assert identity == LaunchIdentity()
    assert identity.live_hardware is True
    assert identity.disposable_library is False
    assert identity.offline is False
    assert identity.locked_keys == ()


def test_text_values_and_comma_lists_are_read_like_the_settings_page_writes_them() -> None:
    """The settings page saves everything as text, so text is accepted too."""
    identity = launch_identity_from_settings({
        "Launch": {
            "live_hardware": "False",
            "offline": "true",
            "disposable_library": "yes",
            "locked_keys": "Image Library.path, Observatory.Guiding.phd2_port",
        }
    })

    assert identity.live_hardware is False
    assert identity.offline is True
    assert identity.disposable_library is True
    assert identity.locked_keys == ("Image Library.path", "Observatory.Guiding.phd2_port")


def test_the_identity_is_read_from_a_file_and_a_bad_file_gives_none(tmp_path: Path) -> None:
    """A missing or unparsable file is an ordinary run."""
    good = tmp_path / "good.toml"
    good.write_text(FULL_SECTION, encoding="utf-8")
    bad = tmp_path / "bad.toml"
    bad.write_text("[Launch\nlabel =", encoding="utf-8")

    assert read_launch_identity(good).label == "SIMULATOR"
    assert read_launch_identity(bad) is None
    assert read_launch_identity(tmp_path / "missing.toml") is None


def test_library_folders_reads_any_settings_file_without_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``library_folders`` follows the named file, not the environment."""
    monkeypatch.setenv("ASTROMETRICS_CONFIG_PATH", str(tmp_path / "other.toml"))
    config = tmp_path / "mine.toml"
    config.write_text(
        f'["Image Library"]\npath = "{tmp_path / "lib"}"\nstacks_path = "{tmp_path / "stacks"}"\n'
        f'["Wayfinding Library"]\npath = "{tmp_path / "way"}"\n',
        encoding="utf-8",
    )

    folders = library_folders(config)

    assert folders.config_file == config
    assert folders.library == tmp_path / "lib"
    assert folders.frames == tmp_path / "lib" / "frames"
    assert folders.stacks == tmp_path / "stacks"
    assert folders.wayfinding == tmp_path / "way"


def test_library_folders_gives_defaults_for_a_missing_file(tmp_path: Path) -> None:
    """A missing settings file names the default folders."""
    folders = library_folders(tmp_path / "none.toml", default_data_home=tmp_path / "share")

    assert folders.library == tmp_path / "share" / "astrometrics"
    assert folders.frames == folders.library / "frames"
    assert folders.wayfinding == tmp_path / "share" / "astrometrics" / "wayfinding"


class _Configuration:
    """A settings object that only knows its launch identity."""

    def __init__(self, identity: LaunchIdentity | None) -> None:
        """Keep the identity.

        Parameters
        ----------
        identity : `LaunchIdentity` or `None`
            What `get_launch_identity` gives.
        """
        self._identity = identity

    def get_launch_identity(self) -> LaunchIdentity | None:
        """Give the identity.

        Returns
        -------
        identity : `LaunchIdentity` or `None`
            The identity the test chose.
        """
        return self._identity


@pytest.fixture
def launch(monkeypatch: pytest.MonkeyPatch) -> object:
    """Let a test choose the launch identity the offline switch sees.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the settings object the switch reads.

    Returns
    -------
    choose : `~collections.abc.Callable`
        Call it with a `LaunchIdentity` (or `None`).
    """

    def choose(identity: LaunchIdentity | None) -> None:
        """Make the offline switch read `identity`."""
        monkeypatch.setattr(offline, "get_configuration", lambda: _Configuration(identity))

    return choose


def test_an_ordinary_run_is_online(launch: object) -> None:
    """Without ``offline = true``, nothing is refused."""
    launch(None)
    assert not network_is_offline()
    require_online("SIMBAD")
    require_local_host("stellarmate.local", "The telescope computer")

    launch(LaunchIdentity(label="X"))
    assert not network_is_offline()
    require_online("SIMBAD")


def test_an_offline_launch_refuses_the_internet_with_a_connection_error(launch: object) -> None:
    """The refusal is a `ConnectionError`: callers see "unreachable"."""
    launch(LaunchIdentity(offline=True))

    with pytest.raises(OfflineModeError, match="SIMBAD") as refusal:
        require_online("SIMBAD")

    assert isinstance(refusal.value, ConnectionError)
    assert isinstance(refusal.value, OSError)


def test_an_offline_launch_keeps_this_computer_reachable(launch: object) -> None:
    """Loopback names stay allowed, because the stand-ins listen there."""
    launch(LaunchIdentity(offline=True))

    for host in ("localhost", "127.0.0.1", "127.1.2.3", "::1", "[::1]", "indi.localhost"):
        require_local_host(host, "The INDI server")
    for host in ("stellarmate.local", "192.168.1.20", "10.0.0.5", "example.com", ""):
        with pytest.raises(OfflineModeError, match="INDI"):
            require_local_host(host, "The INDI server")


def test_loopback_detection_looks_nothing_up() -> None:
    """A name is judged by its spelling, because a lookup goes out."""
    assert is_loopback_host("LOCALHOST")
    assert not is_loopback_host("localhost.example.com")
