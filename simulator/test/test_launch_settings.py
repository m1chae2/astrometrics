"""Purpose: Test that the simulator's settings carry their launch identity.

Description: The simulated world and the offline world each write a
``[Launch]`` section (see `simulator.settings`) that the backend reports
and respects. Nothing starts and the real settings are never read.
"""

import tomllib
from pathlib import Path

from astrometricslib.drivers import launch_identity_from_settings
from simulator.settings import (
    OFFLINE_LABEL,
    SIMULATED_LABEL,
    SIMULATED_LOCKED_KEYS,
    launch_section_text,
    offline_config_text,
    simulator_config_text,
    with_launch_section,
)


def test_the_simulator_world_carries_its_launch_identity(tmp_path: Path) -> None:
    """The simulator's settings say SIMULATOR, no hardware, offline."""
    settings = tomllib.loads(simulator_config_text(tmp_path, 4001, 4002))
    identity = launch_identity_from_settings(settings)

    assert identity.label == SIMULATED_LABEL == "SIMULATOR"
    assert identity.live_hardware is False
    assert identity.disposable_library is True
    assert identity.offline is True
    assert set(identity.locked_keys) == set(SIMULATED_LOCKED_KEYS)
    for name in (
        "Observatory.Telescope.hostname",
        "Observatory.Telescope.indi_port",
        "Observatory.Telescope.remote_pictures_path",
        "Observatory.Telescope.allow_commands",
        "Observatory.Guiding.phd2_port",
        "Observatory.Guiding.phd2_location",
        "Image Library.path",
    ):
        assert name in identity.locked_keys


def test_the_offline_world_carries_its_launch_identity(tmp_path: Path) -> None:
    """The world used when the simulator failed says SIMULATOR FAILED."""
    identity = launch_identity_from_settings(tomllib.loads(offline_config_text(tmp_path)))

    assert identity.label == OFFLINE_LABEL == "SIMULATOR FAILED"
    assert identity.live_hardware is False
    assert identity.offline is True


def test_an_existing_launch_section_is_replaced_not_doubled() -> None:
    """Writing the identity into settings that have one leaves exactly one."""
    old = '[Launch]\nlabel = "OLD"\n\n["Image Library"]\npath = "x"\n'
    launch = launch_section_text("SIMULATOR", False, True, True, ["Image Library.path"])

    text = with_launch_section(old, launch)

    assert text.count("[Launch]") == 1
    assert tomllib.loads(text)["Launch"]["label"] == "SIMULATOR"
    assert tomllib.loads(text)["Image Library"]["path"] == "x"
    assert tomllib.loads(with_launch_section('["Image Library"]\npath = "x"\n', launch))["Launch"]["offline"]


def test_a_launch_header_with_a_trailing_comment_is_replaced_too() -> None:
    """A comment after the header must not leave the old section behind."""
    old = '[Launch]  # written by hand\noffline = false\n\n["Image Library"]\npath = "x"\n'
    launch = launch_section_text("SIMULATOR", False, True, True, [])

    text = with_launch_section(old, launch)

    assert text.count("[Launch]") == 1
    assert tomllib.loads(text)["Launch"]["offline"] is True
