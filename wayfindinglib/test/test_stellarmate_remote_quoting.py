"""Purpose: Test that remote shell strings quote every interpolated path.

Description: The StellarMate driver sends some commands to the telescope
computer as one shell string over ssh. A path with a space, a quote or a
shell symbol must reach the remote shell as one plain word, so it cannot add
a command. These tests replace the ssh call with a recorder and parse each
string the way a shell would.
"""

import shlex

import pytest

from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface

HOSTILE_PATH = "/home/pi/Pic tures; touch /tmp/pwned #"


@pytest.fixture
def driver(monkeypatch: pytest.MonkeyPatch) -> tuple[StellarMateInterface, list[str]]:
    """Make a driver whose ssh calls are recorded, not run.

    Returns
    -------
    driver, commands : `tuple` [`StellarMateInterface`, `list` [`str`]]
        The driver and the list that collects each remote shell string.
    """
    interface = StellarMateInterface("stellarmate", HOSTILE_PATH)
    commands: list[str] = []

    def fake_run(command_list: list[str]) -> str:
        """Record the remote shell string and answer with one folder.

        Returns
        -------
        output : `str`
            A listing with the folder ``M 31/`` (or a count of 0).
        """
        remote_string = command_list[-1]
        commands.append(remote_string)
        return "0" if "wc -l" in remote_string else "M 31/\n"

    monkeypatch.setattr(interface, "_run_command", fake_run)
    return interface, commands


def test_the_listing_of_target_folders_quotes_the_pictures_path(
    driver: tuple[StellarMateInterface, list[str]],
) -> None:
    """The ``ls`` path is one word, whatever characters it has."""
    interface, commands = driver

    interface.list_remote_targets()

    words = shlex.split(commands[0])
    assert words[:3] == ["ls", "-F", HOSTILE_PATH]


def test_find_commands_quote_a_folder_name_that_came_from_a_listing(
    driver: tuple[StellarMateInterface, list[str]],
) -> None:
    """A folder name with a quote cannot end the quoted path early."""
    interface, commands = driver
    interface._resolve_remote_folder_name = lambda folder_name: "it's; rm -rf ~"

    interface.list_remote_files("M 31")
    interface.list_remote_files_with_sizes("M 31")
    interface.get_remote_folder_count("M 31")

    expected_path = f"{HOSTILE_PATH}/it's; rm -rf ~"
    assert len(commands) == 3
    for command in commands:
        assert shlex.split(command.split("|")[0].split(" -type")[0])[:2] == ["find", expected_path]
