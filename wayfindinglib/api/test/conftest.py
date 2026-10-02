"""Purpose: Shared fixtures for the facade tests.

Description: Builds an isolated `ObservatoryControl` with one complete rig
(a telescope, an imaging camera, a guide scope and a guide camera), backed by
a temporary configuration, Butler and log database.
"""

from pathlib import Path

import pytest

from astrometricslib import AppConfiguration
from wayfindinglib.api.control_registry import ObservatoryControl
from wayfindinglib.api.test.capture_night_helpers import Library
from wayfindinglib.drivers.butler import DiskButler


@pytest.fixture
def control(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ObservatoryControl:
    """Build an isolated `ObservatoryControl` with one complete rig.

    Returns
    -------
    control : `ObservatoryControl`
        Backed by a temporary configuration, Butler and log database.
    """
    config_path = tmp_path / "astrometrics.config.toml"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    (tmp_path / "science_library").mkdir()
    config.update_config({
        "Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")},
        "Image Library": {"path": str(tmp_path / "science_library")},
        "Observatory.Telescope": {"models": "Short", "active_telescope": "Short"},
        "Observatory.Telescope.Short": {"focal_length_mm": "400.0", "focal_ratio": "5.0"},
        "Observatory.GuideScope": {"models": "Guide 120", "active_guide_scope": "Guide 120"},
        "Observatory.GuideScope.Guide 120": {"focal_length_mm": "120.0", "aperture_mm": "30"},
        "Observatory.Camera": {
            "models": "Main A, Guider",
            "default_primary_camera": "Main A",
            "default_guide_camera": "Guider",
        },
        "Observatory.Camera.Main A": {
            "pixel_size_μm": "3.76",
            "sensor_width_px": "3008",
            "sensor_height_px": "3008",
        },
        "Observatory.Camera.Guider": {
            "pixel_size_μm": "3.75",
            "sensor_width_px": "1280",
            "sensor_height_px": "960",
        },
    })
    return ObservatoryControl(config=config, butler=DiskButler(app_config=config))


@pytest.fixture
def library(control: ObservatoryControl, monkeypatch: pytest.MonkeyPatch) -> Library:
    """Replace the science library's frame records with a synthetic library.

    Returns
    -------
    library : `Library`
        Fill its `frames` and `verdicts` before analysing a night.
    """
    synthetic = Library()
    monkeypatch.setattr(
        ObservatoryControl,
        "_capture_library",
        lambda self, telescope_name, camera_name, cache: (synthetic.frames, synthetic.verdicts),
    )
    return synthetic
