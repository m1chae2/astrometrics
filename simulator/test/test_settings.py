"""Purpose: Tests for the simulated and offline observatory settings.

Description: The simulated observatory lets the app send commands, because
they reach only the simulated devices on this computer. The offline settings
keep commands off, because they exist so the app never reaches a telescope.
"""

import tomllib

from simulator.settings import OFFLINE_TELESCOPE_SETTINGS, SIMULATED_TELESCOPE_SETTINGS


def _telescope(settings_text: str) -> dict:
    """Read the telescope section of a settings text, ports filled in.

    Parameters
    ----------
    settings_text : `str`
        One of the settings texts.

    Returns
    -------
    telescope : `dict`
        The ``Observatory.Telescope`` section.
    """
    filled = settings_text.format(indi_port=7624, phd2_port=4400, pictures="/tmp/pictures")
    return tomllib.loads(filled)["Observatory.Telescope"]


def test_simulated_observatory_allows_commands() -> None:
    """The simulated observatory takes commands, and only on this computer."""
    telescope = _telescope(SIMULATED_TELESCOPE_SETTINGS)
    assert telescope["allow_commands"] == "true"
    assert telescope["hostname"] == "127.0.0.1"


def test_offline_settings_keep_commands_off() -> None:
    """The offline settings never allow commands."""
    assert _telescope(OFFLINE_TELESCOPE_SETTINGS)["allow_commands"] == "false"
