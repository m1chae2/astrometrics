"""Purpose: Tests for the PHD2 location settings.

Description: `get_phd2_location` defaults to the observatory computer when the
remote-transfer driver is StellarMate and to this computer otherwise, an
explicit value wins, and a bad value is refused.
"""

from unittest.mock import MagicMock

import pytest

from astrometricslib.foundation.config import AppConfiguration


def _configuration(values: dict[tuple[str, str], str]) -> AppConfiguration:
    """Build a configuration object that answers from `values`.

    Returns
    -------
    configuration : `AppConfiguration`
        An object whose settings come from `values`.
    """
    configuration = AppConfiguration.__new__(AppConfiguration)
    app_config = MagicMock()
    app_config.get.side_effect = lambda section, key, fallback=None: values.get((section, key), fallback)
    configuration.app_config = app_config
    return configuration


def test_default_is_observatory_for_the_stellarmate_driver() -> None:
    """With no setting, the default driver puts PHD2 on the observatory."""
    assert _configuration({}).get_phd2_location() == "observatory"


def test_default_is_local_for_another_driver() -> None:
    """A different remote-transfer driver defaults to local PHD2."""
    configuration = _configuration({("Observatory.RemoteTransfer", "driver"): "other"})
    assert configuration.get_phd2_location() == "local"


def test_explicit_value_wins_and_bad_value_is_refused() -> None:
    """An explicit setting overrides the default; nonsense raises."""
    assert _configuration({("Observatory.Guiding", "phd2_location"): "local"}).get_phd2_location() == "local"
    with pytest.raises(ValueError, match="phd2_location"):
        _configuration({("Observatory.Guiding", "phd2_location"): "moon"}).get_phd2_location()


def test_remote_port_defaults_to_4400_and_can_be_overridden() -> None:
    """The remote PHD2 port is 4400 unless the setting says otherwise."""
    assert _configuration({}).get_phd2_remote_port() == 4400
    assert _configuration({("Observatory.Guiding", "phd2_port"): "4401"}).get_phd2_remote_port() == 4401


def _auto_download_configuration(values: dict[tuple[str, str], str]) -> AppConfiguration:
    """Build a configuration whose ``getboolean`` answers from `values`.

    Returns
    -------
    configuration : `AppConfiguration`
        An object whose settings come from `values`.
    """
    configuration = _configuration(values)
    configuration.app_config.getboolean.side_effect = lambda section, key, fallback=None: (
        str(values[section, key]).lower() in ("true", "1", "yes", "on")
        if (section, key) in values
        else fallback
    )
    return configuration


def test_auto_download_defaults_on_for_stellarmate_and_off_otherwise() -> None:
    """With no setting, the automatic download follows the transfer driver."""
    assert _auto_download_configuration({}).get_auto_download_frames() is True
    other = _auto_download_configuration({("Observatory.RemoteTransfer", "driver"): "other"})
    assert other.get_auto_download_frames() is False


def test_auto_download_explicit_value_wins() -> None:
    """An explicit ``auto_download`` setting overrides the driver default."""
    off = _auto_download_configuration({("Observatory.RemoteTransfer", "auto_download"): "false"})
    assert off.get_auto_download_frames() is False
    on = _auto_download_configuration({
        ("Observatory.RemoteTransfer", "driver"): "other",
        ("Observatory.RemoteTransfer", "auto_download"): "true",
    })
    assert on.get_auto_download_frames() is True
