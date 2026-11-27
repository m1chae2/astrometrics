"""Purpose: Test `astrometrics.observatories.configurations`.

Description: Builds a real `Astrometrics` over a temporary library and
settings file with one setup, then checks that the first call seeds the
setup's configuration and matches the stored frames to it, that `scan`
adds a configuration for new settings, that `create`, `get`, `query` and
`update` work through the public object, that each method names its owner,
and that `targets.frames.query(target, configuration=...)` keeps only that
configuration's frames.
"""

import inspect
from datetime import datetime
from pathlib import Path

import pytest

from astrometricslib import Astrometrics, ObservatoryConfiguration
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.target import FrameRecord, Target

SETTINGS = {
    "Observatory.Optics": {"available": "Refractor"},
    "Observatory.Optic.Refractor": {"focal_length_mm": "400", "focal_ratio": "5"},
    "Observatory.Setups": {"available": "Mono on Refractor", "active": "Mono on Refractor"},
    "Observatory.Setup.Mono on Refractor": {"camera": "Mono Camera", "optic": "Refractor"},
    "Observatory.Camera": {"models": "Mono Camera, Color Camera", "default_primary_camera": "Mono Camera"},
    "Observatory.Camera.Mono Camera": {
        "pixel_size_um": "3.76",
        "sensor_width_px": "300",
        "sensor_height_px": "200",
    },
    "Observatory.Camera.Color Camera": {
        "pixel_size_um": "3.9",
        "sensor_width_px": "600",
        "sensor_height_px": "400",
    },
}
"""One setup: a mono camera on a refractor; the color camera is in none."""


@pytest.fixture
def astrometrics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Astrometrics:
    """Build the library over a temporary settings file and library folder.

    The library holds one target with a frame of each camera at 400 mm.

    Returns
    -------
    astrometrics : `Astrometrics`
        The library.
    """
    config_path = tmp_path / "astrometrics.config.toml"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    monkeypatch.setenv("ASTROMETRICS_CONFIG_PATH", str(config_path))
    config = AppConfiguration()
    (tmp_path / "library").mkdir()
    config.update_config({**SETTINGS, "Image Library": {"path": str(tmp_path / "library")}})
    library = Astrometrics(config)
    target = Target(id="M13")
    timestamp = datetime(2026, 10, 3, 22, 0).timestamp()
    for camera in ("Mono Camera", "Color Camera"):
        frame = FrameRecord(path=f"/no/such/{camera}.fits", camera=camera, timestamp=timestamp)
        frame.focal_length_mm = 400.0
        target.frames.append(frame)
    library.targets.add(target)
    return library


def test_the_first_call_seeds_and_matches_the_frames(astrometrics: Astrometrics) -> None:
    """The setup and the other frames get configurations; frames are saved."""
    configurations = astrometrics.observatories.configurations
    page = configurations.query()
    assert sorted(item.name for item in page) == ["Color Camera@400mm", "Mono on Refractor"]
    mono = next(item for item in page if item.name == "Mono on Refractor")
    assert configurations.get(mono.id) == mono
    assert configurations.get("no-such-id") is None
    stored = Astrometrics(astrometrics.config).targets.get("M13")
    assert {frame.camera: frame.configuration_id is not None for frame in stored.frames} == {
        "Mono Camera": True,
        "Color Camera": True,
    }
    assert configurations.scan().total == 0


def test_frame_query_keeps_one_configurations_frames(astrometrics: Astrometrics) -> None:
    """Choosing by configuration keeps only the frames of that equipment."""
    mono = next(item for item in astrometrics.observatories.configurations.query(text="mono on"))
    chosen = astrometrics.targets.frames.query(astrometrics.targets.get("M13"), configuration=mono)
    assert [frame.camera for frame in chosen] == ["Mono Camera"]


def test_create_and_update_go_through_the_public_object(astrometrics: Astrometrics) -> None:
    """A new configuration is stored, renamed and found by its camera."""
    configurations = astrometrics.observatories.configurations
    mono = next(item for item in configurations.query(text="mono on"))
    created = configurations.create(mono.model_copy(update={"id": "", "name": "", "notes": "new focuser"}))
    assert isinstance(created, ObservatoryConfiguration) and created.id != mono.id
    renamed = configurations.update(created, name="Refractor with new focuser")
    assert configurations.get(created.id).name == "Refractor with new focuser"
    assert renamed.revision == 2
    assert configurations.query(camera=mono.camera).total == 2


def test_scan_adds_new_settings(astrometrics: Astrometrics) -> None:
    """A setup added to the settings gets a configuration on the next scan."""
    configurations = astrometrics.observatories.configurations
    configurations.query()
    astrometrics.config.update_config({
        "Observatory.Setups": {"available": "Mono on Refractor, Color on Refractor"},
        "Observatory.Setup.Color on Refractor": {"camera": "Color Camera", "optic": "Refractor"},
    })
    # The frames' configuration already covers the color camera at 400 mm.
    assert configurations.scan().total == 0
    astrometrics.config.update_config({
        "Observatory.Optics": {"available": "Refractor, Lens"},
        "Observatory.Optic.Lens": {"focal_length_mm": "135"},
        "Observatory.Setups": {"available": "Mono on Refractor, Mono on Lens"},
        "Observatory.Setup.Mono on Lens": {"camera": "Mono Camera", "optic": "Lens"},
    })
    assert [item.name for item in configurations.scan()] == ["Mono on Lens"]


def test_every_method_names_its_owner(astrometrics: Astrometrics) -> None:
    """Each public method is a thin wrapper that names its owner function."""
    configurations = astrometrics.observatories.configurations
    for name in ("get", "query", "create", "update", "scan"):
        assert "Owner: astrometricslib.pipelines.shared.equipment." in inspect.getdoc(
            getattr(configurations, name)
        )
