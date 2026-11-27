"""Purpose: Tests for the equipment catalog built from the observatory setups.

Description: When the config lists setups (issue #175), the catalog has one
telescope per optic, takes the active telescope and camera from the active
setup, and reads each telescope's safety limits from the mount section of
its setup. Making an observatory configuration active makes the setup that
pairs its camera and optic active, and saved limits go to the mount section.
Checked against a real
`AppConfiguration` backed by a temporary config file.
"""

from pathlib import Path

import pytest

from astrometricslib import AppConfiguration, Camera, ObservatoryConfiguration, Telescope
from astrometricslib.exceptions import InvalidArgumentError
from wayfindinglib.data_access.equipment_catalog_reader import (
    get_equipment_catalog,
    list_telescopes,
)
from wayfindinglib.data_access.equipment_catalog_writer import build_telescope_limits_update
from wayfindinglib.tasks.control_tasks.equipment_activation import set_active_configuration

SETUPS = {
    "Observatory.Optics": {"available": "Refractor, Camera Lens"},
    "Observatory.Optic.Refractor": {"focal_length_mm": "400", "focal_ratio": "5"},
    "Observatory.Optic.Camera Lens": {"focal_length_mm": "300", "focal_ratio": "5.6"},
    "Observatory.Setups": {
        "available": "Mono on Refractor, Color on Refractor, Color on Lens",
        "active": "Mono on Refractor",
    },
    "Observatory.Setup.Mono on Refractor": {
        "camera": "Mono Camera",
        "optic": "Refractor",
        "mount": "Mount A",
    },
    "Observatory.Setup.Color on Refractor": {
        "camera": "Color Camera",
        "optic": "Refractor",
        "mount": "Mount A",
    },
    "Observatory.Setup.Color on Lens": {"camera": "Color Camera", "optic": "Camera Lens"},
    "Observatory.Mount.Mount A": {"min_altitude_deg": "25", "flip_hour_angle_deg": "3"},
    "Observatory.Camera": {"models": "Mono Camera, Color Camera", "default_primary_camera": "Mono Camera"},
    "Observatory.Camera.Mono Camera": {
        "pixel_size_um": "3.76",
        "sensor_width_px": "3000",
        "sensor_height_px": "3000",
    },
    "Observatory.Camera.Color Camera": {
        "pixel_size_um": "3.9",
        "sensor_width_px": "6000",
        "sensor_height_px": "4000",
    },
}


@pytest.fixture
def app_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfiguration:
    """Build a real AppConfiguration with setups, backed by an isolated file.

    Returns
    -------
    config : `AppConfiguration`
        The configuration, holding `SETUPS`.
    """
    config_path = tmp_path / "astrometrics.config.toml"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    monkeypatch.setenv("ASTROMETRICS_CONFIG_PATH", str(config_path))
    config = AppConfiguration()
    config.update_config(SETUPS)
    return config


def test_there_is_one_telescope_per_optic(app_config: AppConfiguration) -> None:
    """The optics, not the old telescope list, give the telescopes."""
    telescopes = {telescope.id: telescope for telescope in list_telescopes(app_config)}
    assert set(telescopes) == {"Refractor", "Camera Lens"}
    assert telescopes["Refractor"].focal_length_mm == pytest.approx(400.0)


def test_limits_come_from_the_mount_of_the_setup(app_config: AppConfiguration) -> None:
    """The refractor's setup names Mount A, whose section holds its limits."""
    telescopes = {telescope.id: telescope for telescope in list_telescopes(app_config)}
    assert telescopes["Refractor"].min_altitude_deg == pytest.approx(25.0)
    assert telescopes["Refractor"].flip_hour_angle_deg == pytest.approx(3.0)
    assert app_config.get_telescope_limits_section("Refractor") == "Observatory.Mount.Mount A"
    assert app_config.get_telescope_limits_section("Camera Lens") != "Observatory.Mount.Mount A"


def test_the_active_setup_gives_the_active_telescope_and_camera(app_config: AppConfiguration) -> None:
    """The catalog's active entries are the active setup's optic and camera."""
    catalog = get_equipment_catalog(app_config)
    assert (catalog.active_telescope_id, catalog.active_camera_id) == ("Refractor", "Mono Camera")


def _configuration(camera_name: str, optic_name: str, focal_length_mm: float) -> ObservatoryConfiguration:
    """Build a stored-looking configuration of one camera on one optic.

    Parameters
    ----------
    camera_name : `str`
        The camera.
    optic_name : `str`
        The optic.
    focal_length_mm : `float`
        The optic's focal length.

    Returns
    -------
    configuration : `ObservatoryConfiguration`
        The configuration, with an id.
    """
    return ObservatoryConfiguration(
        id=f"{camera_name} on {optic_name}",
        telescope=Telescope(id=optic_name, name=optic_name, focal_length_mm=focal_length_mm),
        camera=Camera(
            id=camera_name, name=camera_name, pixel_size_um=3.9, sensor_width_px=10, sensor_height_px=10
        ),
    )


def test_a_configuration_makes_its_setup_active(app_config: AppConfiguration) -> None:
    """The setup pairing its camera and optic becomes the active setup."""
    set_active_configuration(app_config, _configuration("Color Camera", "Refractor", 400.0))
    assert app_config.get_observatory_setups().active_setup_name == "Color on Refractor"
    assert app_config.get_value("Observatory.Camera", "default_primary_camera") == "Color Camera"
    set_active_configuration(app_config, _configuration("Color Camera", "Camera Lens", 300.0))
    assert app_config.get_observatory_setups().active_setup_name == "Color on Lens"


def test_a_pairing_no_setup_lists_is_refused(app_config: AppConfiguration) -> None:
    """The mono camera is never used on the lens, so that is refused."""
    with pytest.raises(InvalidArgumentError, match="No setup"):
        set_active_configuration(app_config, _configuration("Mono Camera", "Camera Lens", 300.0))
    assert app_config.get_observatory_setups().active_setup_name == "Mono on Refractor"


def test_saved_limits_go_to_the_mount_section(app_config: AppConfiguration) -> None:
    """A limits change for the refractor is written to Mount A's section."""
    update = build_telescope_limits_update(app_config, "Refractor", {"min_altitude_deg": 30.0})
    assert list(update) == ["Observatory.Mount.Mount A"]
    assert update["Observatory.Mount.Mount A"]["min_altitude_deg"] == "30.0"
