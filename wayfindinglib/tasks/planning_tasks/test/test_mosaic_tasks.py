"""Purpose: Unit tests for mosaic panels and mosaic creation.

Description: Verifies `calculate_panels` spreads panels symmetrically
around the center using the equipment's field of view, and that
`create_mosaic` adds one distinct panel target per panel and, when asked,
one package each. A small in-memory stand-in replaces the science
library's target catalog.
"""

import pytest

from astrometricslib import InvalidArgumentError, Target
from wayfindinglib.models.equipment_and_site.equipment import Camera, EquipmentConfiguration, Telescope
from wayfindinglib.models.planning.observation_package import ExposureRequest, FrameType
from wayfindinglib.tasks.planning_tasks.mosaic_tasks import calculate_panels, create_mosaic


class _FakeTargetCatalog:
    """Keeps added targets in a dictionary and remembers a save."""

    def __init__(self) -> None:
        """Start with no targets and nothing saved."""
        self.targets: dict[str, Target] = {}
        self.saved = False

    def add(self, target: Target) -> None:
        """Keep one target.

        Parameters
        ----------
        target : `Target`
            The target to keep.
        """
        self.targets[target.id] = target

    def save(self) -> None:
        """Remember that the catalog was saved."""
        self.saved = True


class _FakeAstrometrics:
    """Holds only the target catalog stand-in."""

    def __init__(self) -> None:
        """Build the target catalog stand-in."""
        self.targets = _FakeTargetCatalog()


def _equipment() -> EquipmentConfiguration:
    """Build a small refractor and camera pair.

    Returns
    -------
    equipment : `EquipmentConfiguration`
        A 450 mm telescope with a square sensor.
    """
    telescope = Telescope(id="t1", name="Test Scope", focal_length_mm=450.0, focal_ratio=6.0)
    camera = Camera(id="c1", name="Test Cam", pixel_size_um=3.76, sensor_width_px=3008, sensor_height_px=3008)
    return EquipmentConfiguration(telescope=telescope, camera=camera)


def _parent() -> Target:
    """Build the parent target the mosaic covers.

    Returns
    -------
    target : `Target`
        NGC 7000 with a camera and telescope recorded.
    """
    parent = Target(id="NGC 7000", commonName="North America", ra="20h 58m 47s", dec="+44° 19′ 48″")
    parent.main_camera = "ASI2600"
    parent.main_scope = "Apertura 75Q"
    return parent


def test_panels_straddle_the_center_using_the_equipment_field_of_view() -> None:
    """A 1x2 grid puts one panel each side of the center, one field apart."""
    equipment = _equipment()
    panels = calculate_panels(None, "180", "0", 1, 2, 0.0, equipment)
    assert [panel.panel_id for panel in panels] == ["P1_1", "P1_2"]
    separation = panels[1].ra_deg - panels[0].ra_deg
    assert separation == pytest.approx(equipment.fov_width_deg, rel=1e-6)
    assert (panels[0].ra_deg + panels[1].ra_deg) / 2 == pytest.approx(180.0)


def test_a_bad_grid_is_refused() -> None:
    """Zero rows or a 100 percent overlap cannot make a grid."""
    with pytest.raises(InvalidArgumentError):
        calculate_panels(None, "180", "0", 0, 2, 0.0, _equipment())
    with pytest.raises(InvalidArgumentError):
        calculate_panels(None, "180", "0", 1, 2, 100.0, _equipment())


def test_create_mosaic_adds_one_target_and_package_per_panel() -> None:
    """A 2x2 grid gives four distinct panel targets and four packages."""
    astrometrics = _FakeAstrometrics()
    panels = calculate_panels(None, "20h 58m 47s", "+44 19 48", 2, 2, 10.0, _equipment())
    exposure_requests = [ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=300.0, count=10)]

    plan = create_mosaic(astrometrics, _parent(), panels, exposure_requests, None, packages=True)

    assert len(set(plan.target_ids)) == 4
    assert plan.target_ids[0] == "NGC 7000_P1_1"
    assert astrometrics.targets.saved is True
    assert [package.target_id for package in plan.packages] == plan.target_ids
    assert all(package.exposure_requests == exposure_requests for package in plan.packages)
    panel_target = astrometrics.targets.targets["NGC 7000_P1_1"]
    assert panel_target.main_camera == "ASI2600"
    assert panel_target.ra == panels[0].ra_str


def test_create_mosaic_can_skip_the_packages() -> None:
    """With packages=False only the panel targets are made."""
    astrometrics = _FakeAstrometrics()
    panels = calculate_panels(None, "180", "0", 1, 2, 0.0, _equipment())
    plan = create_mosaic(astrometrics, _parent(), panels, None, None, packages=False)
    assert plan.packages == []
    assert len(plan.target_ids) == 2
