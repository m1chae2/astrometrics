"""Purpose: Tests for setting up Siril's portable version on Windows.

Description: The download is replaced by a small in-memory zip, so nothing
reaches the network. The tests check that a matching file is unpacked and the
setting saved, that a file whose checksum does not match is refused and
deleted, that a zip holding a path outside its folder is refused, that Linux
refuses to set Siril up, and that the setup script keeps a Siril the settings
already name. The system is chosen by patching ``on_windows``.
"""

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from astrometricslib._internal.drivers import siril_setup
from astrometricslib.foundation.errors import ConfigurationError, ExternalServiceError
from astrometricslib.scripts import set_up_siril as setup_script


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    """Build a zip file in memory.

    Parameters
    ----------
    members : `dict` [`str`, `bytes`]
        Name inside the zip -> content.

    Returns
    -------
    data : `bytes`
        The zip file.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zip_file:
        for name, content in members.items():
            zip_file.writestr(name, content)
    return buffer.getvalue()


def _release_for(data: bytes) -> siril_setup.SirilRelease:
    """Pin a fake release to some zip bytes.

    Parameters
    ----------
    data : `bytes`
        The zip the fake server sends.

    Returns
    -------
    release : `siril_setup.SirilRelease`
        A release whose size and checksum match `data`.
    """
    return siril_setup.SirilRelease(
        version="9.9.9",
        url="https://example.invalid/siril.zip",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        program="siril/bin/siril-cli.exe",
    )


class _Settings:
    """Stand-in settings that record what the setup saves."""

    def __init__(self, siril_executable: str = "siril-cli") -> None:
        """Start with a siril_executable setting."""
        self.values: dict[str, dict[str, str]] = {"Processing.Siril": {"siril_executable": siril_executable}}
        self.saved = 0
        self.app_config = self

    def get_siril_executable(self) -> str:
        """Give the setting.

        Returns
        -------
        setting : `str`
            The siril_executable value.
        """
        return self.values["Processing.Siril"]["siril_executable"]

    def __contains__(self, section: str) -> bool:
        """Say whether a section exists.

        Returns
        -------
        present : `bool`
            `True` if it does.
        """
        return section in self.values

    def add_section(self, section: str) -> None:
        """Add an empty section."""
        self.values[section] = {}

    def set(self, section: str, key: str, value: str) -> None:
        """Set one value."""
        self.values[section][key] = value

    def save_configuration(self) -> None:
        """Count a save."""
        self.saved += 1

    def write_lock(self) -> object:
        """Give a lock that does nothing.

        Returns
        -------
        lock : context manager
            A no-op context.
        """
        import contextlib

        return contextlib.nullcontext()


@pytest.fixture
def windows_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pretend to be on Windows, with the data and cache folders in tmp_path.

    Returns
    -------
    data : `pathlib.Path`
        The data folder Siril is set up in.
    """
    data = tmp_path / "data"
    cache = tmp_path / "cache"
    data.mkdir()
    cache.mkdir()
    monkeypatch.setattr(siril_setup, "on_windows", lambda: True)
    monkeypatch.setattr(siril_setup, "data_directory", lambda: str(data))
    monkeypatch.setattr(siril_setup, "scratch_directory", lambda: str(cache))
    monkeypatch.setattr(siril_setup, "require_online", lambda service: None)
    return data


def _serve(monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    """Make every download answer with `data`."""
    monkeypatch.setattr(siril_setup.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(data))


def test_a_matching_download_is_unpacked_and_used(
    windows_world: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The checked zip is unpacked and saved as siril_executable."""
    data = _zip_bytes({"siril/bin/siril-cli.exe": b"program", "siril/share/readme.txt": b"hello"})
    release = _release_for(data)
    _serve(monkeypatch, data)
    settings = _Settings()
    program = siril_setup.set_up_siril(settings, release=release)  # type: ignore[arg-type]
    assert program == windows_world / "Siril" / "9.9.9" / "siril" / "bin" / "siril-cli.exe"
    assert program.read_bytes() == b"program"
    assert settings.get_siril_executable() == str(program)
    assert settings.saved == 1
    assert list((windows_world.parent / "cache").iterdir()) == []


def test_a_download_with_the_wrong_checksum_is_refused_and_deleted(
    windows_world: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that fails the pinned checksum is deleted, not unpacked."""
    good = _zip_bytes({"siril/bin/siril-cli.exe": b"program"})
    release = _release_for(good)
    tampered = good[:-1] + bytes([good[-1] ^ 0xFF])
    _serve(monkeypatch, tampered)
    settings = _Settings()
    with pytest.raises(ExternalServiceError, match="does not match"):
        siril_setup.set_up_siril(settings, release=release)  # type: ignore[arg-type]
    assert not (windows_world / "Siril").exists()
    assert list((windows_world.parent / "cache").iterdir()) == []
    assert settings.saved == 0


def test_a_zip_with_a_path_outside_its_folder_is_refused(
    windows_world: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A member like ../evil is refused before anything is unpacked."""
    data = _zip_bytes({"siril/bin/siril-cli.exe": b"program", "../evil.txt": b"x"})
    _serve(monkeypatch, data)
    with pytest.raises(ExternalServiceError, match="unsafe path"):
        siril_setup.set_up_siril(_Settings(), release=_release_for(data))  # type: ignore[arg-type]
    assert not (windows_world / "evil.txt").exists()
    assert not (windows_world / "Siril" / "9.9.9").exists()


def test_linux_does_not_set_up_siril(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Linux Siril comes from the package manager."""
    monkeypatch.setattr(siril_setup, "on_windows", lambda: False)
    with pytest.raises(ConfigurationError, match="only on Windows"):
        siril_setup.set_up_siril(_Settings())  # type: ignore[arg-type]


def test_the_script_keeps_a_siril_the_settings_already_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Someone with their own Siril keeps it; nothing is downloaded."""
    own_siril = tmp_path / "My Siril" / "siril-cli.exe"
    own_siril.parent.mkdir()
    own_siril.write_bytes(b"")
    monkeypatch.setattr(setup_script, "on_windows", lambda: True)
    monkeypatch.setattr(setup_script, "get_configuration", lambda: _Settings(str(own_siril)))
    monkeypatch.setattr(setup_script, "installed_siril_program", lambda: None)

    def no_download(*_arguments: object, **_keywords: object) -> None:
        """Fail the test if the script tries to set Siril up."""
        pytest.fail("the script set up Siril although one is configured")

    monkeypatch.setattr(setup_script, "set_up_siril", no_download)
    assert setup_script.main([]) == 0
    assert "Keeping the Siril" in capsys.readouterr().out


def test_the_script_does_nothing_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux needs no setup step for Siril."""
    monkeypatch.setattr(setup_script, "on_windows", lambda: False)
    assert setup_script.main([]) == 0
