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


def _grid_offsets_deg(
    panels: list, center_ra_deg: float, center_dec_deg: float
) -> dict[str, tuple[float, float]]:
    """Give each panel's offset from the grid center, in degrees.

    Parameters
    ----------
    panels : `list` [`MosaicPanel`]
        The panels.
    center_ra_deg, center_dec_deg : `float`
        The grid center.

    Returns
    -------
    offsets : `dict` [`str`, `tuple` [`float`, `float`]]
        (east, north) offsets by panel id. East is scaled by cos(dec).
    """
    import math

    return {
        panel.panel_id: (
            (panel.ra_deg - center_ra_deg) * math.cos(math.radians(panel.dec_deg)),
            panel.dec_deg - center_dec_deg,
        )
        for panel in panels
    }


def test_zero_rotation_leaves_the_grid_unchanged() -> None:
    """A rotation of 0 gives the same panels as leaving the rotation out."""
    plain = calculate_panels(object(), "10:00:00", "+30:00:00", 2, 3, 10.0, _equipment())
    turned = calculate_panels(object(), "10:00:00", "+30:00:00", 2, 3, 10.0, _equipment(), rotation_deg=0.0)
    assert [(p.ra_deg, p.dec_deg) for p in plain] == [(p.ra_deg, p.dec_deg) for p in turned]


def test_ninety_degrees_swaps_the_grid_axes() -> None:
    """At 90 degrees the columns run along declination, not right ascension."""
    equipment = _equipment()
    plain = calculate_panels(object(), "10:00:00", "+0:00:00", 1, 3, 0.0, equipment)
    turned = calculate_panels(object(), "10:00:00", "+0:00:00", 1, 3, 0.0, equipment, rotation_deg=90.0)
    plain_offsets = _grid_offsets_deg(plain, 150.0, 0.0)
    turned_offsets = _grid_offsets_deg(turned, 150.0, 0.0)
    # A single row of three columns: east-west before, north-south after.
    assert plain_offsets["P1_3"][0] > 0.5 and abs(plain_offsets["P1_3"][1]) < 1e-6
    assert abs(turned_offsets["P1_3"][0]) < 1e-6
    assert turned_offsets["P1_3"][1] == pytest.approx(-plain_offsets["P1_3"][0], abs=1e-6)
    assert all(panel.rotation_deg == pytest.approx(90.0) for panel in turned)


def test_rotated_panel_centers_round_trip() -> None:
    """Turning the offsets back by the rotation recovers the plain grid."""
    import math

    equipment = _equipment()
    plain = calculate_panels(object(), "10:00:00", "+20:00:00", 3, 3, 20.0, equipment)
    turned = calculate_panels(object(), "10:00:00", "+20:00:00", 3, 3, 20.0, equipment, rotation_deg=37.0)
    plain_offsets = _grid_offsets_deg(plain, 150.0, 20.0)
    turned_offsets = _grid_offsets_deg(turned, 150.0, 20.0)
    angle = math.radians(37.0)
    for panel_id, (east, north) in turned_offsets.items():
        recovered_column = east * math.cos(angle) - north * math.sin(angle)
        recovered_row = east * math.sin(angle) + north * math.cos(angle)
        # The east scale differs slightly with dec; allow a small tolerance.
        assert recovered_column == pytest.approx(plain_offsets[panel_id][0], abs=0.05)
        assert recovered_row == pytest.approx(plain_offsets[panel_id][1], abs=0.05)


def test_a_non_finite_rotation_is_refused() -> None:
    """A rotation that is not a number raises InvalidArgumentError."""
    with pytest.raises(InvalidArgumentError, match="rotation_deg"):
        calculate_panels(
            object(), "10:00:00", "+30:00:00", 1, 1, 0.0, _equipment(), rotation_deg=float("nan")
        )
