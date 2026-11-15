"""Purpose: Tests for setting up Siril and GraXpert on Windows.

Description: Downloads are replaced by small in-memory files and ``msiexec``
by a stand-in, so nothing reaches the network or installs anything. The tests
check that a matching zip is unpacked and its setting saved, that an
installer package is unpacked with ``msiexec /a`` into the folder, that a
file whose checksum does not match is refused and deleted, that a zip holding
a path outside its folder is refused, that Linux refuses, and that the setup
script keeps a program the user installed, moves its own older copy to the
pinned release, and goes on to the next program when one fails. The system
is a feature switch, not a system check: the tests patch ``on_windows`` to run
the Windows-only setup on any system.
"""

import contextlib
import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from astrometricslib._internal.drivers import program_setup
from astrometricslib.foundation.errors import ConfigurationError, ExternalServiceError
from astrometricslib.scripts import set_up_programs as setup_script


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


def _release_for(
    data: bytes, archive: str = "zip", program: str = "siril/bin/siril-cli.exe"
) -> program_setup.ProgramRelease:
    """Pin a fake release to some bytes.

    Parameters
    ----------
    data : `bytes`
        The file the fake server sends.
    archive : `str`, optional
        ``"zip"`` or ``"msi"``.
    program : `str`, optional
        The program inside the unpacked folder.

    Returns
    -------
    release : `program_setup.ProgramRelease`
        A release whose size and checksum match `data`.
    """
    return program_setup.ProgramRelease(
        name="Tool",
        version="9.9.9",
        url="https://example.invalid/tool",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        archive=archive,  # type: ignore[arg-type]
        program=program,
        section="Processing.Tool",
        key="tool_executable",
    )


class _Settings:
    """Stand-in settings that record what the setup saves."""

    def __init__(self, values: dict[str, dict[str, str]] | None = None) -> None:
        """Start with some settings."""
        self.values: dict[str, dict[str, str]] = values or {}
        self.saved = 0
        self.app_config = self

    def get_value(self, section: str, key: str, fallback: str = "") -> str:
        """Give one setting.

        Returns
        -------
        value : `str`
            The setting, or `fallback`.
        """
        return self.values.get(section, {}).get(key, fallback)

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

    def write_lock(self) -> contextlib.AbstractContextManager[None]:
        """Give a lock that does nothing.

        Returns
        -------
        lock : context manager
            A no-op context.
        """
        return contextlib.nullcontext()


@pytest.fixture
def windows_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pretend to be on Windows, with the data and cache folders in tmp_path.

    Returns
    -------
    data : `pathlib.Path`
        The data folder programs are set up in.
    """
    data = tmp_path / "data"
    cache = tmp_path / "cache"
    data.mkdir()
    cache.mkdir()
    monkeypatch.setattr(program_setup, "on_windows", lambda: True)
    monkeypatch.setattr(program_setup, "data_directory", lambda: str(data))
    monkeypatch.setattr(program_setup, "scratch_directory", lambda: str(cache))
    monkeypatch.setattr(program_setup, "require_online", lambda service: None)
    return data


def _serve(monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    """Make every download answer with `data`."""
    monkeypatch.setattr(program_setup.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(data))


def test_a_matching_zip_is_unpacked_and_used(windows_world: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The checked zip is unpacked and saved as the program's setting."""
    data = _zip_bytes({"siril/bin/siril-cli.exe": b"program", "siril/share/readme.txt": b"hello"})
    _serve(monkeypatch, data)
    settings = _Settings()
    program = program_setup.set_up_program(settings, _release_for(data))  # type: ignore[arg-type]
    assert program == windows_world / "Tool" / "9.9.9" / "siril" / "bin" / "siril-cli.exe"
    assert program.read_bytes() == b"program"
    assert settings.get_value("Processing.Tool", "tool_executable") == str(program)
    assert settings.saved == 1
    assert list((windows_world.parent / "cache").iterdir()) == []


def test_an_installer_package_is_unpacked_with_msiexec(
    windows_world: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An .msi is copied out with msiexec /a; nothing is installed."""
    data = b"an installer package"
    commands: list[str] = []

    def fake_msiexec(command: str, **_keywords: object) -> object:
        """Record the command and place the program as msiexec would.

        Returns
        -------
        completed : `subprocess.CompletedProcess`
            A finished command.
        """
        commands.append(command)
        target = command.split('TARGETDIR="', 1)[1].rstrip('"')
        Path(target, "GraXpert.exe").write_bytes(b"program")
        return program_setup.subprocess.CompletedProcess(command, 0)

    _serve(monkeypatch, data)
    monkeypatch.setattr(program_setup.subprocess, "run", fake_msiexec)
    settings = _Settings()
    release = _release_for(data, archive="msi", program="GraXpert.exe")
    program = program_setup.set_up_program(settings, release)  # type: ignore[arg-type]
    assert program == windows_world / "Tool" / "9.9.9" / "GraXpert.exe"
    assert commands[0].startswith('msiexec /a "') and " /qn " in commands[0]
    assert settings.get_value("Processing.Tool", "tool_executable") == str(program)


def test_a_failed_msiexec_leaves_nothing_behind(windows_world: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When msiexec fails, no half-made folder and no download are left."""
    data = b"an installer package"
    _serve(monkeypatch, data)
    monkeypatch.setattr(
        program_setup.subprocess,
        "run",
        lambda command, **_k: program_setup.subprocess.CompletedProcess(command, 1603),
    )
    with pytest.raises(ExternalServiceError, match="msiexec exited with 1603"):
        program_setup.set_up_program(_Settings(), _release_for(data, archive="msi"))  # type: ignore[arg-type]
    assert list((windows_world / "Tool").iterdir()) == []
    assert list((windows_world.parent / "cache").iterdir()) == []


def test_a_download_with_the_wrong_checksum_is_refused_and_deleted(
    windows_world: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that fails the pinned checksum is deleted, not unpacked."""
    good = _zip_bytes({"siril/bin/siril-cli.exe": b"program"})
    tampered = good[:-1] + bytes([good[-1] ^ 0xFF])
    _serve(monkeypatch, tampered)
    settings = _Settings()
    with pytest.raises(ExternalServiceError, match="does not match"):
        program_setup.set_up_program(settings, _release_for(good))  # type: ignore[arg-type]
    assert not (windows_world / "Tool").exists()
    assert list((windows_world.parent / "cache").iterdir()) == []
    assert settings.saved == 0


def test_a_zip_with_a_path_outside_its_folder_is_refused(
    windows_world: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A member like ../evil is refused before anything is unpacked."""
    data = _zip_bytes({"siril/bin/siril-cli.exe": b"program", "../evil.txt": b"x"})
    _serve(monkeypatch, data)
    with pytest.raises(ExternalServiceError, match="unsafe path"):
        program_setup.set_up_program(_Settings(), _release_for(data))  # type: ignore[arg-type]
    assert not (windows_world / "Tool" / "evil.txt").exists()
    assert not (windows_world / "Tool" / "9.9.9").exists()


def test_linux_does_not_set_up_programs(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Linux the programs come from the package manager."""
    monkeypatch.setattr(program_setup, "on_windows", lambda: False)
    with pytest.raises(ConfigurationError, match="only on Windows"):
        program_setup.set_up_program(_Settings(), program_setup.SIRIL_RELEASE)  # type: ignore[arg-type]


def test_the_pinned_releases_are_siril_and_graxpert_over_https() -> None:
    """Both pinned programs use https and name their settings."""
    names = [(release.name, release.section, release.key) for release in program_setup.PINNED_RELEASES]
    assert names == [
        ("Siril", "Processing.Siril", "siril_executable"),
        ("GraXpert", "Processing.GraXpert", "graxpert_executable"),
    ]
    assert all(release.url.startswith("https://") for release in program_setup.PINNED_RELEASES)


@pytest.fixture
def windows_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run the setup script as on Windows, path reader included.

    Returns
    -------
    data : `pathlib.Path`
        The data folder the script sets programs up in.
    """
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(setup_script, "on_windows", lambda: True)
    monkeypatch.setattr(program_setup, "data_directory", lambda: str(data))
    return data


def test_the_script_keeps_programs_the_settings_already_name(
    windows_script: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Someone with their own Siril and GraXpert keeps them."""
    own = tmp_path / "own_tools"
    own.mkdir()
    (own / "siril-cli.exe").write_bytes(b"")
    (own / "GraXpert.exe").write_bytes(b"")
    settings = _Settings({
        "Processing.Siril": {"siril_executable": str(own / "siril-cli.exe")},
        "Processing.GraXpert": {"graxpert_executable": str(own / "GraXpert.exe")},
    })
    monkeypatch.setattr(setup_script, "get_configuration", lambda: settings)

    def no_download(*_arguments: object, **_keywords: object) -> None:
        """Fail the test if the script tries to set a program up."""
        pytest.fail("the script set up a program although one is configured")

    monkeypatch.setattr(setup_script, "set_up_program", no_download)
    assert setup_script.main([]) == 0
    output = capsys.readouterr().out
    assert "Keeping the Siril" in output and "Keeping the GraXpert" in output


def test_the_script_moves_its_own_older_copy_to_the_pinned_release(
    windows_script: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An older copy this script made is set up again at the pinned version."""
    old_siril = windows_script / "Siril" / "1.0.0" / "siril" / "bin" / "siril-cli.exe"
    old_siril.parent.mkdir(parents=True)
    old_siril.write_bytes(b"")
    settings = _Settings({"Processing.Siril": {"siril_executable": str(old_siril)}})
    monkeypatch.setattr(setup_script, "get_configuration", lambda: settings)
    set_up: list[str] = []

    def record(config: object, release: program_setup.ProgramRelease, progress: object = None) -> Path:
        """Record which program is set up.

        Returns
        -------
        program : `pathlib.Path`
            A made-up program path.
        """
        set_up.append(release.name)
        return Path("program.exe")

    monkeypatch.setattr(setup_script, "set_up_program", record)
    assert setup_script.main([]) == 0
    assert set_up == ["Siril", "GraXpert"]
    assert "Keeping the Siril" not in capsys.readouterr().out


def test_the_script_goes_on_after_a_failure(
    windows_script: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed Siril setup does not stop GraXpert's; the exit code is 1."""
    monkeypatch.setattr(setup_script, "get_configuration", lambda: _Settings())
    set_up: list[str] = []

    def fail_siril(config: object, release: program_setup.ProgramRelease, progress: object = None) -> Path:
        """Fail for Siril, succeed for the others.

        Returns
        -------
        program : `pathlib.Path`
            A made-up program path.

        Raises
        ------
        ExternalServiceError
            For Siril.
        """
        if release.name == "Siril":
            raise ExternalServiceError("download failed")
        set_up.append(release.name)
        return Path("program.exe")

    monkeypatch.setattr(setup_script, "set_up_program", fail_siril)
    assert setup_script.main([]) == 1
    assert set_up == ["GraXpert"]
    assert "Siril setup failed" in capsys.readouterr().err


def test_the_script_does_nothing_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux needs no setup step for these programs."""
    monkeypatch.setattr(setup_script, "on_windows", lambda: False)
    assert setup_script.main([]) == 0
