"""Purpose: Unit tests for observatory_session_recorder's _ensure_session.

Description: Verifies an existing session is returned unchanged, a
missing session is created from the active telescope/camera and a
seeded site profile when both are configured, and a missing
telescope/camera raises rather than fabricating a session with
invented equipment.
"""

from datetime import date

import pytest

from documentation.notebooks.wayfinding.execution.scripts.observatory_session_recorder import (
    _ensure_session,
)
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.equipment_and_site.equipment import Camera, Telescope
from wayfindinglib.models.session.observation_session import ObservationSession, SessionStatus


class _FakeControl:
    def __init__(self, telescope=None, camera=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self._telescope = telescope
        self._camera = camera

    def active_telescope(self):  # ruff: ignore[missing-return-type-private-function]
        return self._telescope

    def active_camera(self):  # ruff: ignore[missing-return-type-private-function]
        return self._camera


class _FakeExecution:
    def __init__(self, butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self._butler = butler


class _FakeWayfinder:
    def __init__(self, config, butler, telescope=None, camera=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.config = config
        self.control = _FakeControl(telescope, camera)
        self.execution = _FakeExecution(butler)


@pytest.fixture
def butler(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Build a DiskButler backed by an isolated temporary database.

    Returns
    -------
    butler : `DiskButler`
        A fresh, isolated butler instance.
    """
    from astrometricslib import AppConfiguration

    config_path = tmp_path / "astrometrics.config"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return DiskButler(app_config=config)


def test_returns_existing_session_unchanged(butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an already-persisted session is returned as-is."""
    existing = ObservationSession(
        id="session-1",
        night_date=date(2026, 8, 5),
        status=SessionStatus.RUNNING,
        site_profile_id="site-1",
        telescope_id="scope-1",
        camera_id="cam-1",
    )
    butler.put(existing, "observation_session", {"session_id": "session-1"})
    wayfinder = _FakeWayfinder(butler.config, butler)

    result = _ensure_session(wayfinder, "session-1")

    assert result.id == "session-1"
    assert result.status == SessionStatus.RUNNING


def test_creates_minimal_session_from_active_equipment(butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a missing session is created from the active telescope/camera."""
    telescope = Telescope(id="scope-1", name="Rig A", focal_length_mm=450.0, focal_ratio=6.0)
    camera = Camera(id="cam-1", name="CamA", pixel_size_um=3.76, sensor_width_px=6248, sensor_height_px=4176)
    wayfinder = _FakeWayfinder(butler.config, butler, telescope=telescope, camera=camera)

    result = _ensure_session(wayfinder, "new-session")

    assert result.id == "new-session"
    assert result.telescope_id == "scope-1"
    assert result.camera_id == "cam-1"
    assert result.site_profile_id

    reloaded = butler.get("observation_session", {"session_id": "new-session"})
    assert reloaded is not None


def test_raises_without_active_telescope_or_camera(butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a missing session with no active equipment raises."""
    wayfinder = _FakeWayfinder(butler.config, butler, telescope=None, camera=None)

    with pytest.raises(RuntimeError, match="No existing session"):
        _ensure_session(wayfinder, "new-session")
