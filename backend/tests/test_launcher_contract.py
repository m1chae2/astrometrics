"""Purpose: Test what the backend does when a launcher runs it as a child.

Description: Under ``ASTROMETRICS_LAUNCHER`` the launcher
(`simulator.launch`) owns the simulated world. The backend must not build,
start, command-by-hook or remove anything of its own, and a change of source
must end the process with the restart code. No simulator starts here: the
environment is a plain dict in a temporary folder.
"""

from pathlib import Path

import pytest

from backend import observatory_source as source
from backend.services.observatory.observatory_source_service import ObservatorySourceService


class _MustNotBuild:
    """A simulator class that fails the test if it is ever built."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Fail.

        Raises
        ------
        AssertionError
            Always.
        """
        raise AssertionError("The backend built a simulator under the launcher.")


@pytest.fixture
def launched(tmp_path: Path) -> dict[str, str]:
    """Give the environment a launcher sets for a simulated world.

    Returns
    -------
    environment : `dict` [`str`, `str`]
        The launcher variable, the simulated source and the world's root,
        and the real settings folders under `tmp_path`.
    """
    return {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        source.LAUNCHER_VARIABLE: "1",
        source.ACTIVE_SOURCE_VARIABLE: source.SIMULATED,
        source.ACTIVE_ROOT_VARIABLE: str(tmp_path / "world"),
        source.ACTIVE_PORTS_VARIABLE: "5001,5002",
        "ASTROMETRICS_CONFIG": str(tmp_path / "world" / "astrometrics.config.toml"),
    }


def test_no_simulator_object_is_rebuilt_under_the_launcher(launched: dict[str, str]) -> None:
    """With the ports in the environment the backend gets no simulator."""
    assert source.active_observatory(launched) is None
    del launched[source.LAUNCHER_VARIABLE]
    assert source.active_observatory(launched) is not None


def test_the_launch_environment_builds_nothing_under_the_launcher(
    launched: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backend's own launch builds nothing under the launcher."""
    monkeypatch.setattr(source, "SimulatedObservatory", _MustNotBuild)
    source.write_choice(source.SourceChoice(source.SIMULATED), launched)
    before = dict(launched)
    assert source.apply_launch_environment(launched) == source.SIMULATED
    assert launched == before
    unpinned = {key: value for key, value in launched.items() if key.startswith("XDG") or "LAUNCHER" in key}
    before = dict(unpinned)
    assert source.apply_launch_environment(unpinned) == source.REAL
    assert unpinned == before


def test_the_source_service_does_nothing_with_a_world_under_the_launcher(launched: dict[str, str]) -> None:
    """Start, stop and remove touch no simulator; the source shows."""
    service = ObservatorySourceService(
        source.active_observatory(launched), launched, launch_reader=lambda: None
    )
    service.start()
    service.stop()
    service.remove_library_copy()
    assert service.is_settled()
    description = service.get_source()
    assert description["source"] == source.SIMULATED
    assert description["label"] == "SIMULATOR"
    assert description["starting"] is False
    assert description["commands"] is None


def test_the_offline_world_reports_its_reason_under_the_launcher(launched: dict[str, str]) -> None:
    """The launcher passes the failure reason in the environment."""
    launched[source.ACTIVE_SOURCE_VARIABLE] = source.OFFLINE
    launched[source.LAUNCH_ERROR_VARIABLE] = "SimulationUnavailableError: no indiserver"
    service = ObservatorySourceService(None, launched, launch_reader=lambda: None)
    description = service.get_source()
    assert description["label"] == "SIMULATOR FAILED"
    assert description["failed"] is True
    assert description["reason"] == "SimulationUnavailableError: no indiserver"


def test_a_change_of_source_asks_the_launcher_to_restart(launched: dict[str, str]) -> None:
    """The choice is saved and the restart function is called."""
    restarts: list[object] = []
    service = ObservatorySourceService(
        None, launched, restarter=lambda shutdown: restarts.append(shutdown), launch_reader=lambda: None
    )
    result = service.set_source(source.REAL)
    assert result == {"restarting": True, "source": source.REAL}
    assert len(restarts) == 1
    assert source.read_choice(launched).source == source.REAL
