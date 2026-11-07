"""Purpose: Test the backend's use of the launch identity.

Description: The ``[Launch]`` section of the settings the backend starts
with says what kind of run this is (see
`astrometricslib.foundation.launch_identity`). These tests check that
``observatory:get_source`` reports it, that a save cannot change a locked
key, and that the offline switch makes the phone link unavailable. The
simulator's own settings are tested in `simulator.test`. The real settings and
library are never read.
"""

from pathlib import Path

import pytest

from astrometricslib.drivers import LaunchIdentity
from astrometricslib.exceptions import ConfigurationError
from backend.services.infrastructure.settings_service import SettingsService
from backend.services.observatory.observatory_source_service import ObservatorySourceService

LOCKED = LaunchIdentity(
    label="NIGHT",
    live_hardware=True,
    locked_keys=("Observatory.Telescope.hostname", "Observatory.Telescope.indi_port"),
)


def _service(identity: LaunchIdentity | None) -> ObservatorySourceService:
    """Build the source service with a chosen launch identity.

    Parameters
    ----------
    identity : `LaunchIdentity` or `None`
        What the settings in use say.

    Returns
    -------
    service : `ObservatorySourceService`
        A service for the real telescope, with no restarter.
    """
    return ObservatorySourceService(
        environ={"XDG_CONFIG_HOME": "/nonexistent"},
        restarter=lambda _stop: None,
        launch_reader=lambda: identity,
    )


def test_get_source_reports_the_launch_identity() -> None:
    """``launch`` carries the five keys of the identity."""
    state = _service(LOCKED).get_source()

    assert state["launch"] == {
        "label": "NIGHT",
        "live_hardware": True,
        "disposable_library": False,
        "offline": False,
        "locked_keys": ["Observatory.Telescope.hostname", "Observatory.Telescope.indi_port"],
    }
    assert state["source"] == "real"


def test_get_source_reports_no_launch_for_an_ordinary_run() -> None:
    """Without a ``[Launch]`` section, ``launch`` is `None`."""
    assert _service(None).get_source()["launch"] is None


def test_get_source_reads_the_settings_in_use_by_default() -> None:
    """The default reader asks the shared settings object."""
    state = ObservatorySourceService(environ={"XDG_CONFIG_HOME": "/nonexistent"}).get_source()

    assert state["launch"] is None  # the test settings have no [Launch] section


class _Config:
    """Settings that remember what is saved and know a few current values."""

    def __init__(self) -> None:
        """Start with a telescope host and port."""
        self.values = {("Observatory.Telescope", "hostname"): "stellarmate.local",
                       ("Observatory.Telescope", "indi_port"): "7624",
                       ("Observatory.Telescope", "focal_length_mm"): "405"}  # fmt: skip
        self.saved: list[dict] = []

    def get_value(self, section: str, key: str, fallback: object = None) -> object:
        """Give a current value.

        Returns
        -------
        value : `object`
            The value, or `fallback`.
        """
        return self.values.get((section, key), fallback)

    def update_config(self, config: dict) -> None:
        """Remember the save."""
        self.saved.append(config)


def test_a_save_that_changes_a_locked_key_is_refused() -> None:
    """A changed hostname is refused with the key named."""
    config = _Config()
    service = SettingsService(config, launch_reader=lambda: LOCKED)

    with pytest.raises(ConfigurationError, match=r"Observatory\.Telescope\.hostname") as refusal:
        service.save_config({"Observatory.Telescope": {"hostname": "other.local", "focal_length_mm": "500"}})

    assert refusal.value.details["locked_keys"] == ["Observatory.Telescope.hostname"]
    assert config.saved == []


def test_a_save_that_sends_a_locked_value_back_unchanged_is_allowed() -> None:
    """The settings page sends everything, so an equal value is no change."""
    config = _Config()
    service = SettingsService(config, launch_reader=lambda: LOCKED)

    assert service.save_config({
        "Observatory.Telescope": {"hostname": "stellarmate.local", "indi_port": 7624, "focal_length_mm": 500}
    })

    assert len(config.saved) == 1


def test_unlocked_keys_are_free_without_an_identity() -> None:
    """With no identity, or no lock on the key, any save goes through."""
    config = _Config()

    assert SettingsService(config).save_config({"Observatory.Telescope": {"hostname": "x"}})
    assert SettingsService(config, launch_reader=lambda: LOCKED).save_config({
        "Observatory.Telescope": {"focal_length_mm": "1"}
    })
    assert len(config.saved) == 2


def test_the_launch_section_itself_cannot_be_changed_but_can_be_echoed() -> None:
    """A save may send ``[Launch]`` back as it is, and may not alter it."""
    config = _Config()
    service = SettingsService(config, launch_reader=lambda: LOCKED)

    assert service.save_config({"Launch": LOCKED.as_dict()})
    with pytest.raises(ConfigurationError, match="Launch"):
        service.save_config({"Launch": {**LOCKED.as_dict(), "offline": True}})
    with pytest.raises(ConfigurationError, match="Launch"):
        service.save_config({"Launch": {"locked_keys": []}})
    assert len(config.saved) == 1


@pytest.fixture
def offline_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the offline switch on and forbid starting any program.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the switch and the program starters.
    """

    def forbidden(*_args: object, **_kwargs: object) -> None:
        """Fail the test: an offline phone link started a program."""
        pytest.fail("an offline phone link started a program")

    for module in (
        "backend.services.infrastructure.handoff_service",
        "backend.services.infrastructure.phone_share",
    ):
        monkeypatch.setattr(f"{module}.network_is_offline", lambda: True)
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/fake")


def test_the_phone_link_is_unavailable_when_offline(offline_launch: None, tmp_path: Path) -> None:
    """Phone share and handoff say "unavailable" and start no program."""
    from backend.services.infrastructure import phone_share
    from backend.services.infrastructure.handoff_service import HandoffService

    service = HandoffService()
    picture = tmp_path / "a.png"
    picture.write_bytes(b"x")

    assert phone_share.connected_gsconnect_devices() == []
    with pytest.raises(ConfigurationError, match="sends nothing off"):
        phone_share.send_file_to_phone(str(picture))
    assert service.list_paired_devices() == []
    with pytest.raises(ConfigurationError, match="sends nothing off"):
        service.beam_to_device("M 31", "Planetarium")
    with pytest.raises(ConfigurationError, match="sends nothing off"):
        service.share_file_to_device(str(picture))
    result = service.send_device_alert("Title", "Body")
    assert result["channels"] == ["websocket"]
