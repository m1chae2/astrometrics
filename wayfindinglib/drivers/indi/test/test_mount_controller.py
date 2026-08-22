"""Purpose: Unit tests for MountController's altitude envelope resolution.

Description: Verifies `validate_altitude_limits` prefers the active
per-rig `Telescope`'s configured altitude envelope over the global
`[Observatory.Constraints]` section when one is configured, falls back
to the global section when none is, and respects a per-rig
`altitude_limits_enabled=False` override -- per
`Wayfinding_Library_Architecture.md` §2.5.2's "Documented Safety
Fallback" invariant. Uses a real, isolated `AppConfiguration` (not a
mock) against a fake INDI telescope device, matching this codebase's
established real-config testing discipline.

An observer at the north pole (latitude 90 deg) sees every target at
an altitude exactly equal to its declination, for any RA and any
time -- this gives a deterministic, closed-form expected altitude
without needing to independently reimplement the Alt/Az transform in
the test.
"""

import pytest

from wayfindinglib.drivers.indi.mount_controller import MountController
from wayfindinglib.exceptions import AstrometryHardwareError


class _FakeNumberElement:
    def __init__(self, value):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.value = value


class _FakeTelescope:
    """A fake INDI telescope device reporting a fixed GEOGRAPHIC_COORD."""

    def __init__(self, latitude_deg: float, longitude_deg: float, elevation_m: float):  # ruff: ignore[missing-return-type-special-method]
        self._geographic_coord = [
            _FakeNumberElement(latitude_deg),
            _FakeNumberElement(longitude_deg),
            _FakeNumberElement(elevation_m),
        ]

    def getNumber(self, name):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        if name == "GEOGRAPHIC_COORD":
            return self._geographic_coord
        return None


class _FakeClient:
    def __init__(self, config):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.config = config


@pytest.fixture
def app_config(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Build a real AppConfiguration backed by an isolated temp config file.

    Returns
    -------
    config : `AppConfiguration`
        A fresh, isolated configuration instance.
    """
    from astrometricslib import AppConfiguration

    config_path = tmp_path / "astrometrics.config"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    return AppConfiguration()


def _north_pole_telescope() -> _FakeTelescope:
    """Build a fake telescope at the north pole.

    There, altitude always equals declination, for any RA and time.

    Returns
    -------
    telescope : `_FakeTelescope`
        A fake telescope reporting a north-pole GEOGRAPHIC_COORD.
    """
    return _FakeTelescope(latitude_deg=90.0, longitude_deg=0.0, elevation_m=0.0)


def test_falls_back_to_global_constraint_when_no_rig_configured(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the global constraint governs when no per-rig telescope."""
    controller = MountController(_FakeClient(app_config))
    telescope = _north_pole_telescope()

    # Global defaults are 0-90 deg; dec=30 is within range and must not raise.
    controller.validate_altitude_limits(telescope, ra=6.0, dec=30.0)


def test_per_rig_envelope_overrides_global_constraint(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a configured per-rig Telescope envelope beats the global one."""
    app_config.update_config({
        "Observatory.Telescope": {"models": "Rig A"},
        "Observatory.Telescope.Rig A": {
            "focal_length_mm": "450.0",
            "focal_ratio": "6.0",
            "min_altitude_deg": "50.0",
            "max_altitude_deg": "90.0",
            "active_telescope": "Rig A",
        },
    })
    controller = MountController(_FakeClient(app_config))
    telescope = _north_pole_telescope()

    # dec=30 would pass the global 0-90 default, but fails the per-rig 50-90
    # envelope -- proving the per-rig value, not the global one, governed.
    with pytest.raises(AstrometryHardwareError, match="outside safe operating"):
        controller.validate_altitude_limits(telescope, ra=6.0, dec=30.0)


def test_per_rig_limits_disabled_skips_validation_entirely(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify altitude_limits_enabled=False on the active rig skips checks."""
    app_config.update_config({
        "Observatory.Telescope": {"models": "Rig A"},
        "Observatory.Telescope.Rig A": {
            "focal_length_mm": "450.0",
            "focal_ratio": "6.0",
            "min_altitude_deg": "50.0",
            "max_altitude_deg": "90.0",
            "altitude_limits_enabled": "false",
            "active_telescope": "Rig A",
        },
    })
    controller = MountController(_FakeClient(app_config))
    telescope = _north_pole_telescope()

    # dec=-45 is well outside every envelope above -- disabled limits must
    # skip the check entirely rather than reject it.
    controller.validate_altitude_limits(telescope, ra=6.0, dec=-45.0)


def test_resolve_altitude_envelope_returns_global_fallback_tuple(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify _resolve_altitude_envelope's return value in the fallback."""
    controller = MountController(_FakeClient(app_config))
    min_altitude, max_altitude, limits_enabled = controller._resolve_altitude_envelope()
    assert min_altitude == pytest.approx(0.0)
    assert max_altitude == pytest.approx(90.0)
    assert limits_enabled is True


def test_resolve_altitude_envelope_returns_per_rig_tuple(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify _resolve_altitude_envelope's return value when rig active."""
    app_config.update_config({
        "Observatory.Telescope": {"models": "Rig A"},
        "Observatory.Telescope.Rig A": {
            "focal_length_mm": "450.0",
            "focal_ratio": "6.0",
            "min_altitude_deg": "25.0",
            "max_altitude_deg": "80.0",
            "active_telescope": "Rig A",
        },
    })
    controller = MountController(_FakeClient(app_config))
    min_altitude, max_altitude, limits_enabled = controller._resolve_altitude_envelope()
    assert min_altitude == pytest.approx(25.0)
    assert max_altitude == pytest.approx(80.0)
    assert limits_enabled is True


def test_hour_angle_limits_disabled_with_no_active_rig(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify hour-angle limiting has no global fallback, unlike altitude."""
    controller = MountController(_FakeClient(app_config))
    max_hour_angle_hours, limits_enabled = controller._resolve_hour_angle_envelope()
    assert max_hour_angle_hours == pytest.approx(0.0)
    assert limits_enabled is False

    # With limits disabled, even a wildly out-of-envelope RA must not raise.
    controller.validate_hour_angle_limits(_north_pole_telescope(), ra=0.0)


def test_resolve_hour_angle_envelope_returns_per_rig_tuple(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify _resolve_hour_angle_envelope's return value when rig active."""
    app_config.update_config({
        "Observatory.Telescope": {"models": "Rig A"},
        "Observatory.Telescope.Rig A": {
            "focal_length_mm": "450.0",
            "focal_ratio": "6.0",
            "hour_angle_limits_enabled": "true",
            "max_hour_angle_hours": "4.5",
            "active_telescope": "Rig A",
        },
    })
    controller = MountController(_FakeClient(app_config))
    max_hour_angle_hours, limits_enabled = controller._resolve_hour_angle_envelope()
    assert max_hour_angle_hours == pytest.approx(4.5)
    assert limits_enabled is True


def test_validate_hour_angle_limits_rejects_ra_far_from_meridian(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a target far from the meridian is rejected when limits are on."""
    from astropy.time import Time

    app_config.update_config({
        "Observatory.Telescope": {"models": "Rig A"},
        "Observatory.Telescope.Rig A": {
            "focal_length_mm": "450.0",
            "focal_ratio": "6.0",
            "hour_angle_limits_enabled": "true",
            "max_hour_angle_hours": "2.0",
            "active_telescope": "Rig A",
        },
    })
    controller = MountController(_FakeClient(app_config))
    telescope = _north_pole_telescope()

    local_sidereal_time = Time.now().sidereal_time("mean", longitude=0.0).hour
    # 8 hours from the meridian is well outside the +/-2h envelope,
    # regardless of exactly what LST is right now.
    far_ra = (local_sidereal_time - 8.0) % 24.0

    with pytest.raises(AstrometryHardwareError, match="outside safe operating"):
        controller.validate_hour_angle_limits(telescope, ra=far_ra)


def test_validate_hour_angle_limits_accepts_ra_near_meridian(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a target near the meridian is accepted when limits are on."""
    from astropy.time import Time

    app_config.update_config({
        "Observatory.Telescope": {"models": "Rig A"},
        "Observatory.Telescope.Rig A": {
            "focal_length_mm": "450.0",
            "focal_ratio": "6.0",
            "hour_angle_limits_enabled": "true",
            "max_hour_angle_hours": "2.0",
            "active_telescope": "Rig A",
        },
    })
    controller = MountController(_FakeClient(app_config))
    telescope = _north_pole_telescope()

    local_sidereal_time = Time.now().sidereal_time("mean", longitude=0.0).hour
    near_ra = local_sidereal_time % 24.0

    controller.validate_hour_angle_limits(telescope, ra=near_ra)
