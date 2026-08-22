"""Purpose: Unit tests for mosaic panel set generation.

Description: Verifies generate_mosaic_packages produces one correctly
coordinated package per panel, each with a distinct panel sub-target,
using a fake Astrometrics high-level interface rather than the real
science library.
"""

from wayfindinglib.models.equipment_and_site.equipment import Camera, EquipmentConfiguration, Telescope
from wayfindinglib.models.planning.mosaic import MosaicGridConfig
from wayfindinglib.models.planning.observation_package import ExposureRequest, FrameType
from wayfindinglib.tasks.planning_tasks.mosaic_tasks import generate_mosaic_packages


class _FakeTarget:
    def __init__(self, target_id, ra="09:55:33", dec="+69:03:55"):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.id = target_id
        self.ra = ra
        self.dec = dec


class _FakeTargetRegistry:
    def __init__(self, astrometrics: _FakeAstrometrics):  # ruff: ignore[missing-return-type-special-method]
        self._astrometrics = astrometrics

    def get(self, target_id):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return self._astrometrics._targets.get(target_id)

    def create(self, target_id):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        target = _FakeTarget(target_id)
        self._astrometrics._targets[target_id] = target
        return target

    def add(self, target):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        self._astrometrics._targets[target.id] = target

    def save(self):  # ruff: ignore[missing-return-type-private-function]
        self._astrometrics.saved = True


class _FakeAstrometrics:
    def __init__(self, parent: _FakeTarget):  # ruff: ignore[missing-return-type-special-method]
        self._targets = {parent.id: parent}
        self.saved = False
        self.targets = _FakeTargetRegistry(self)


def _equipment():  # ruff: ignore[missing-return-type-private-function]
    telescope = Telescope(id="t1", name="Test Scope", focal_length_mm=450.0, focal_ratio=6.0)
    camera = Camera(id="c1", name="Test Cam", pixel_size_um=3.76, sensor_width_px=3008, sensor_height_px=3008)
    return EquipmentConfiguration(telescope=telescope, camera=camera)


def test_generates_one_package_per_panel_with_distinct_targets():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a 2x2 grid produces four packages with distinct targets."""
    parent = _FakeTarget("NGC 7000")
    astrometrics = _FakeAstrometrics(parent)
    grid_config = MosaicGridConfig(rows=2, cols=2, overlap_percent=10.0)
    exposure_requests = [ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=300.0, count=10)]

    packages = generate_mosaic_packages(
        astrometrics, "NGC 7000", grid_config, exposure_requests, _equipment()
    )

    assert len(packages) == 4
    target_ids = {p.target_id for p in packages}
    assert len(target_ids) == 4  # every panel got a distinct sub-target
    assert astrometrics.saved is True
    for package in packages:
        assert package.exposure_requests == exposure_requests


def test_panel_coordinates_spread_around_parent_center():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify panels offset from the parent center in a symmetric pattern."""
    parent = _FakeTarget("NGC 7000")
    astrometrics = _FakeAstrometrics(parent)
    grid_config = MosaicGridConfig(rows=1, cols=2, overlap_percent=0.0)
    exposure_requests = [ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=300.0, count=1)]

    packages = generate_mosaic_packages(
        astrometrics, "NGC 7000", grid_config, exposure_requests, _equipment()
    )

    assert len(packages) == 2
    ra_values = sorted(astrometrics.targets.get(p.target_id).ra for p in packages)
    # Two distinct panel RAs should be assigned, straddling the parent center.
    assert ra_values[0] != ra_values[1]


def test_raises_for_unknown_parent_target():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a ValueError is raised when the parent does not resolve."""
    import pytest

    astrometrics = _FakeAstrometrics(_FakeTarget("known"))
    with pytest.raises(ValueError):
        generate_mosaic_packages(
            astrometrics,
            "does-not-exist",
            MosaicGridConfig(rows=1, cols=1),
            [ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=300.0, count=1)],
            _equipment(),
        )
