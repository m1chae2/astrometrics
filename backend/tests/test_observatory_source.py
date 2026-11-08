"""Purpose: Test how the backend reports and changes the observatory source.

Description: The backend builds no world. A launcher starts it and sets
``ASTROMETRICS_LAUNCHER``. These tests check what ``observatory:get_source``
reports (the launch identity, the source the launcher started it with and the
saved choice), what ``observatory:set_source`` writes, and that the backend
ends with exit code 75 so the launcher starts it again. The environment is a
plain dict in a temporary folder, so the real settings are never read, and no
process starts.
"""

from pathlib import Path

import pytest

from astrometricslib.drivers import LaunchIdentity
from astrometricslib.exceptions import ConfigurationError, InvalidArgumentError
from backend import observatory_source
from backend.public_interface import (
    ACTIVE_SOURCE_VARIABLE,
    LAUNCH_ERROR_VARIABLE,
    LAUNCH_NOTES_VARIABLE,
    LAUNCHER_VARIABLE,
    MISSING_PROGRAMS_VARIABLE,
    NO_TELESCOPE,
    REAL,
    RESTART_EXIT_CODE,
    SIMULATED,
    SourceChoice,
    read_choice,
    write_choice,
)
from backend.services.observatory.observatory_source_service import ObservatorySourceService

SIMULATOR_IDENTITY = LaunchIdentity(
    label="SIMULATOR", live_hardware=False, disposable_library=True, offline=True, locked_keys=("A.b",)
)


@pytest.fixture
def environment(tmp_path: Path) -> dict[str, str]:
    """Give an environment run by a launcher, with temporary settings.

    Returns
    -------
    environment : `dict` [`str`, `str`]
        ``XDG_CONFIG_HOME`` under `tmp_path` and ``ASTROMETRICS_LAUNCHER``.
    """
    return {"XDG_CONFIG_HOME": str(tmp_path / "config"), LAUNCHER_VARIABLE: "1"}


def _service(
    environment: dict[str, str],
    identity: LaunchIdentity | None = None,
    restarts: list[object] | None = None,
) -> ObservatorySourceService:
    """Build the source service with a recording restarter.

    Parameters
    ----------
    environment : `dict` [`str`, `str`]
        The environment.
    identity : `LaunchIdentity`, optional
        What the settings in use say. Default: no ``[Launch]`` section.
    restarts : `list`, optional
        Receives the shutdown hook each time the backend would end.

    Returns
    -------
    service : `ObservatorySourceService`
        The service.
    """
    log = restarts if restarts is not None else []
    return ObservatorySourceService(
        environment, restarter=lambda stop: log.append(stop), launch_reader=lambda: identity
    )


def test_a_launched_real_backend_reports_the_real_source(environment: dict[str, str]) -> None:
    """With no world and no launch section, the source is real."""
    state = _service(environment).get_source()

    assert state["source"] == REAL
    assert state["chosen"] == REAL
    assert state["launch"] is None
    assert state["label"] == ""
    assert state["restart_pending"] is False
    assert state["ready"] is True
    assert state["starting"] is False
    assert state["failed"] is False
    assert state["available"] is True
    assert state["commands"] is None


def test_get_source_reports_the_launch_identity_and_the_saved_choice(environment: dict[str, str]) -> None:
    """The launch identity, the source and the saved choice are reported."""
    environment[ACTIVE_SOURCE_VARIABLE] = SIMULATED
    write_choice(SourceChoice(REAL), environment)

    state = _service(environment, SIMULATOR_IDENTITY).get_source()

    assert state["source"] == SIMULATED
    assert state["chosen"] == REAL
    assert state["restart_pending"] is True
    assert state["label"] == "SIMULATOR"
    assert state["launch"] == SIMULATOR_IDENTITY.as_dict()


def test_a_failed_simulator_is_reported_with_its_reason(environment: dict[str, str]) -> None:
    """The offline world reports ``failed`` and the launcher's reason."""
    environment[ACTIVE_SOURCE_VARIABLE] = NO_TELESCOPE
    environment[LAUNCH_ERROR_VARIABLE] = "RuntimeError: no indiserver"
    write_choice(SourceChoice(SIMULATED), environment)

    state = _service(environment, SIMULATOR_IDENTITY).get_source()

    assert state["failed"] is True
    assert state["error"] == state["reason"] == "RuntimeError: no indiserver"
    assert state["restart_pending"] is False


def test_programs_the_launcher_found_missing_are_reported(environment: dict[str, str]) -> None:
    """The settings control learns which programs the simulator lacks."""
    environment[MISSING_PROGRAMS_VARIABLE] = "indiserver,phd2"
    service = _service(environment)

    state = service.get_source()

    assert state["missing_programs"] == ["indiserver", "phd2"]
    assert state["available"] is False
    assert "indiserver, phd2" in state["unavailable_reason"]
    with pytest.raises(ConfigurationError, match="indiserver"):
        service.set_source(SIMULATED)
    assert read_choice(environment).source == REAL


def test_set_source_writes_the_choice_and_asks_for_the_restart(environment: dict[str, str]) -> None:
    """A new source is saved and the backend is asked to end."""
    restarts: list[object] = []
    service = _service(environment, restarts=restarts)
    service.set_shutdown_hook(lambda: None)

    result = service.set_source(SIMULATED)

    assert result == {"restarting": True, "source": SIMULATED}
    assert read_choice(environment).source == SIMULATED
    assert len(restarts) == 1
    assert callable(restarts[0])


def test_choosing_the_source_in_force_does_not_restart(environment: dict[str, str]) -> None:
    """The same source again saves the choice but does not end the backend."""
    restarts: list[object] = []
    service = _service(environment, restarts=restarts)

    result = service.set_source(REAL)

    assert result == {"restarting": False, "source": REAL}
    assert restarts == []


def test_the_app_has_no_replay_source(environment: dict[str, str]) -> None:
    """Only real and simulated can be chosen."""
    with pytest.raises(InvalidArgumentError):
        _service(environment).set_source("replay")


def test_switching_is_refused_while_work_runs(environment: dict[str, str]) -> None:
    """A running job, stack or download blocks the change."""
    restarts: list[object] = []
    service = _service(environment, restarts=restarts)
    service.set_busy_probe(lambda: ["a stack of M 31"])

    with pytest.raises(ConfigurationError, match="a stack of M 31"):
        service.set_source(SIMULATED)

    assert read_choice(environment).source == REAL
    assert restarts == []


def test_a_backend_without_a_launcher_cannot_change_its_source(environment: dict[str, str]) -> None:
    """Nobody would start it again, so the change is refused."""
    del environment[LAUNCHER_VARIABLE]
    restarts: list[object] = []
    service = _service(environment, restarts=restarts)

    state = service.get_source()
    with pytest.raises(ConfigurationError, match="not started by the launcher"):
        service.set_source(SIMULATED)

    assert state["available"] is False
    assert "launcher" in state["unavailable_reason"]
    assert read_choice(environment).source == REAL
    assert restarts == []


def test_the_restart_stops_the_services_and_the_children_then_exits_75() -> None:
    """The backend stops its services, then exits with code 75."""
    events: list[object] = []

    thread = observatory_source.restart_backend(
        lambda: events.append("shutdown"), delay_seconds=0, exit_process=events.append
    )
    thread.join(timeout=30)

    assert events == ["shutdown", 75]
    assert RESTART_EXIT_CODE == 75


def test_a_failing_shutdown_does_not_stop_the_restart() -> None:
    """The exit code is still used when the shutdown hook raises."""
    events: list[object] = []

    def broken() -> None:
        """Raise, as a service that cannot stop would.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("stuck")

    thread = observatory_source.restart_backend(broken, delay_seconds=0, exit_process=events.append)
    thread.join(timeout=30)

    assert events == [75]


def test_the_backend_knows_whether_a_launcher_runs_it() -> None:
    """The launcher variable decides, and an empty value does not count."""
    assert observatory_source.launched_by_launcher({LAUNCHER_VARIABLE: "1"})
    assert not observatory_source.launched_by_launcher({})
    assert not observatory_source.launched_by_launcher({LAUNCHER_VARIABLE: ""})


def test_launch_notes_are_reported_and_never_block_a_source_change(environment: dict[str, str]) -> None:
    """A note such as a missing star catalog never blocks a change."""
    environment[LAUNCH_NOTES_VARIABLE] = "gsc (star catalog): stars are not drawn\n"
    service = _service(environment)

    state = service.get_source()

    assert state["notes"] == ["gsc (star catalog): stars are not drawn"]
    assert state["missing_programs"] == []
    assert state["available"] is True
