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
    is_loopback_host,
    launch_identity_from_settings,
    library_folders,
    network_is_offline,
    read_launch_identity,
    require_local_host,
    require_online,
)
from astrometricslib.exceptions import OfflineModeError
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
    """A missing file, or one with no launch header, is an ordinary run."""
    good = tmp_path / "good.toml"
    good.write_text(FULL_SECTION, encoding="utf-8")
    bad = tmp_path / "bad.toml"
    bad.write_text("[Image Library\nlabel =", encoding="utf-8")

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


@pytest.mark.parametrize("text", ["false", "False", " NO ", "0", "off", "Off"])
def test_offline_is_false_only_for_clearly_false_values(text: str) -> None:
    """The clearly false spellings leave the run online."""
    assert launch_identity_from_settings({"Launch": {"offline": text}}).offline is False
    assert launch_identity_from_settings({"Launch": {"offline": False}}).offline is False


@pytest.mark.parametrize("value", ["true", "yes", "1", "on", "maybe", "flase", "", "ture", 1, ["x"]])
def test_any_other_offline_value_counts_as_offline(value: object) -> None:
    """A typo or an odd value keeps the run closed (fail closed)."""
    assert launch_identity_from_settings({"Launch": {"offline": value}}).offline is True


def test_a_launch_section_that_is_not_a_table_counts_as_offline() -> None:
    """An unreadable section is offline; no section is online."""
    assert launch_identity_from_settings({"Launch": "oops"}).offline is True
    assert launch_identity_from_settings({"Launch": {"locked_keys": 5}}).offline is True
    assert launch_identity_from_settings({}) is None


def test_a_file_that_cannot_be_parsed_but_has_a_launch_header_counts_as_offline(tmp_path: Path) -> None:
    """The file is unreadable, but its ``[Launch]`` header is still found."""
    broken = tmp_path / "broken.toml"
    broken.write_text('[Launch]  # the launcher\noffline = tru\nlabel = "SIM', encoding="utf-8")

    assert read_launch_identity(broken).offline is True


def test_the_settings_object_reads_a_doubtful_offline_value_as_offline() -> None:
    """`AppConfiguration.get_launch_identity` fails closed too."""
    assert _identity_from_text("[Launch]\noffline = 'ture'\n").offline is True
    assert _identity_from_text("[Launch]\noffline = 'off'\n").offline is False


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
    monkeypatch.delenv(offline.OFFLINE_VARIABLE, raising=False)

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


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " 1 "])
def test_the_offline_variable_makes_the_launch_offline(
    launch: object, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """``ASTROMETRICS_OFFLINE`` closes the network even for an ordinary run."""
    launch(None)
    monkeypatch.setenv(offline.OFFLINE_VARIABLE, value)

    assert network_is_offline()
    with pytest.raises(OfflineModeError):
        require_online("SIMBAD")


@pytest.mark.parametrize("value", ["", "0", "false"])
def test_other_offline_variable_values_change_nothing(
    launch: object, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Only ``1`` and ``true`` close the network through the variable."""
    launch(None)
    monkeypatch.setenv(offline.OFFLINE_VARIABLE, value)

    assert not network_is_offline()


def test_loopback_detection_handles_mapped_bracketed_and_zoned_addresses() -> None:
    """IPv4-mapped loopback is loopback; a mapped outside address is not."""
    for host in ("::ffff:127.0.0.1", "[::ffff:127.0.0.1]", "::1%lo", "[::1]", "127.0.0.2"):
        assert is_loopback_host(host)
    for host in ("::ffff:10.0.0.1", "::ffff:192.168.1.1", "2001:db8::1", "8.8.8.8"):
        assert not is_loopback_host(host)


def test_loopback_detection_looks_nothing_up() -> None:
    """A name is judged by its spelling, because a lookup goes out."""
    assert is_loopback_host("LOCALHOST")
    assert not is_loopback_host("localhost.example.com")
