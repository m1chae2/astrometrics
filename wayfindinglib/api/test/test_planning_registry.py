"""Purpose: Unit tests for the ObservationPlanning astrometrics.

Description: Verifies package authoring persists and validates targets,
manual queue authoring writes the same structure the automated path
does, reorder_queue rejects a mismatched entry set, and
plan_observation_session runs end to end against a fake astrometrics
astrometrics with the driver layer entirely absent from the import graph --
confirming "Planning Is Hardware-Free".
"""

from datetime import date

import pytest

from wayfindinglib.api.planning_registry import ObservationPlanning
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.equipment_and_site.equipment import Telescope
from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
from wayfindinglib.models.planning.observation_package import ExposureRequest, FrameType
from wayfindinglib.models.session.observation_session import StartTimeMode


class _FakeTarget:
    def __init__(self, target_id, ra="09:55:33", dec="+69:03:55"):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.id = target_id
        self.ra = ra
        self.dec = dec


class _FakeTargetRegistry:
    def __init__(self, targets):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self._targets = targets

    def get(self, target_id):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return self._targets.get(target_id)


class _FakeAstrometrics:
    def __init__(self, targets):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self._targets = {t.id: t for t in targets}
        self.targets = _FakeTargetRegistry(self._targets)


@pytest.fixture
def isolated_butler(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Build a DiskButler backed by a fully isolated temporary database.

    Returns
    -------
    butler : `DiskButler`
        The constructed, isolated butler.
    """
    from astrometricslib import AppConfiguration

    config_path = tmp_path / "astrometrics.config"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return DiskButler(app_config=config)


def _site():  # ruff: ignore[missing-return-type-private-function]
    return SiteProfile(id="s1", name="Test Site", latitude_deg=39.7392, longitude_deg=-104.9903)


def _telescope():  # ruff: ignore[missing-return-type-private-function]
    return Telescope(id="t1", name="Test Scope", focal_length_mm=450.0, focal_ratio=6.0, min_altitude_deg=0.0)


def test_create_observation_package_validates_and_persists(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify package creation validates the target and persists the result."""
    planning = ObservationPlanning(butler=isolated_butler)
    astrometrics = _FakeAstrometrics([_FakeTarget("M 81")])

    package = planning.create_observation_package(
        astrometrics, "M 81", [ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=300.0, count=10)]
    )

    loaded = isolated_butler.get("observation_package", {"id": package.id})
    assert loaded is not None
    assert loaded.target_id == "M 81"


def test_create_observation_package_rejects_unknown_target(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify package creation raises when the target does not resolve."""
    planning = ObservationPlanning(butler=isolated_butler)
    astrometrics = _FakeAstrometrics([])
    with pytest.raises(ValueError):
        planning.create_observation_package(
            astrometrics,
            "does-not-exist",
            [ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=300.0, count=1)],
        )


def test_manual_queue_authoring_matches_automated_structure(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify create_empty_session/add_to_queue matches the automated shape."""
    planning = ObservationPlanning(butler=isolated_butler)
    astrometrics = _FakeAstrometrics([_FakeTarget("M 81")])

    session = planning.create_empty_session(_site(), _telescope(), "c1", date(2026, 8, 10))
    assert session.queue == []

    package = planning.create_observation_package(
        astrometrics, "M 81", [ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=300.0, count=5)]
    )
    updated = planning.add_to_queue(session.id, package, StartTimeMode.SOONEST)

    assert len(updated.queue) == 1
    entry = updated.queue[0]
    assert entry.observation_package_id == package.id
    assert entry.exposure_requests[0].exposure_sec == pytest.approx(300.0)
    assert entry.status.value == "PENDING"


def test_add_to_queue_raises_for_unknown_session(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify add_to_queue raises when the session does not resolve."""
    planning = ObservationPlanning(butler=isolated_butler)
    package = planning.create_observation_package(
        _FakeAstrometrics([_FakeTarget("M 81")]),
        "M 81",
        [ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=300.0, count=1)],
    )
    with pytest.raises(ValueError):
        planning.add_to_queue("does-not-exist", package, StartTimeMode.SOONEST)


def test_reorder_queue_rejects_mismatched_entry_set(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify reorder_queue rejects an entry_ids list mismatching the queue."""
    planning = ObservationPlanning(butler=isolated_butler)
    astrometrics = _FakeAstrometrics([_FakeTarget("M 81")])
    session = planning.create_empty_session(_site(), _telescope(), "c1", date(2026, 8, 10))
    package = planning.create_observation_package(
        astrometrics, "M 81", [ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=300.0, count=1)]
    )
    planning.add_to_queue(session.id, package, StartTimeMode.SOONEST)

    with pytest.raises(ValueError):
        planning.reorder_queue(session.id, ["nonexistent-entry-id"])


def test_plan_observation_session_runs_successfully(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a full session plans successfully end to end."""
    planning = ObservationPlanning(butler=isolated_butler)
    astrometrics = _FakeAstrometrics([
        _FakeTarget("M 81"),
        _FakeTarget("M 13", ra="16:41:41", dec="+36:27:35"),
    ])
    package = planning.create_observation_package(
        astrometrics, "M 81", [ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=60.0, count=1)]
    )

    session = planning.plan_observation_session(
        astrometrics,
        [(package, StartTimeMode.SOONEST, None)],
        _site(),
        _telescope(),
        "c1",
        date(2026, 8, 10),
    )

    assert session.status.value == "PLANNED"


def test_sky_browsing_methods_delegate_to_the_sky_engine(mocker, isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify sky-browsing methods delegate to the composed Sky engine."""
    planning = ObservationPlanning(butler=isolated_butler)
    fake_sky = mocker.Mock(latitude=1.0, longitude=2.0, elevation=3.0, meridian_flip_delay_min=4.0)
    mocker.patch.object(
        type(planning), "_sky_engine", new_callable=mocker.PropertyMock, return_value=fake_sky
    )

    assert planning.site_latitude_deg == pytest.approx(1.0)
    assert planning.site_longitude_deg == pytest.approx(2.0)
    assert planning.site_elevation_m == pytest.approx(3.0)
    assert planning.meridian_flip_delay_min == pytest.approx(4.0)

    planning.resolve_target_coordinates("M 81")
    fake_sky.resolve_target_coordinates.assert_called_once_with("M 81")

    planning.get_sources(1.0, 2.0, 3.0, include_catalog=True)
    fake_sky.get_sources.assert_called_once_with(1.0, 2.0, 3.0, True)

    planning.get_visibility(["obj"], time_input="now")
    fake_sky.get_visibility.assert_called_once_with(["obj"], "now")

    planning.get_constellation_lines()
    fake_sky.get_constellation_lines.assert_called_once()


def test_mosaic_and_sequence_methods_delegate_to_the_observation_engine(mocker, isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify legacy mosaic/sequence methods delegate to the engine."""
    planning = ObservationPlanning(butler=isolated_butler)
    fake_observation = mocker.Mock()
    mocker.patch.object(
        type(planning),
        "_observation_engine",
        new_callable=mocker.PropertyMock,
        return_value=fake_observation,
    )

    planning.calculate_panels("10:00:00", "+20:00:00", 2, 2, 10.0)
    fake_observation.calculate_panels.assert_called_once_with("10:00:00", "+20:00:00", 2, 2, 10.0)

    planning.create_mosaic_targets("M 81", {}, [], {}, {})
    fake_observation.create_mosaic_targets.assert_called_once_with("M 81", {}, [], {}, {})

    planning.create_sequence_plan("M 81", [])
    fake_observation.create_sequence_plan.assert_called_once_with("M 81", [])


def test_sky_and_observation_engines_are_lazily_constructed_once(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the composed engines are built lazily and cached across calls."""
    planning = ObservationPlanning(butler=isolated_butler)
    assert planning._ObservationPlanning__sky_engine is None
    assert planning._ObservationPlanning__observation_engine is None

    sky_engine = planning._sky_engine
    observation_engine = planning._observation_engine

    assert planning._sky_engine is sky_engine
    assert planning._observation_engine is observation_engine


def test_planning_module_tree_imports_no_device_driver():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify no planning_tasks/api source file imports the INDI device layer.

    A static source-level check of "Planning Is Hardware-Free"
    (`Wayfinding_Library_Architecture.md` §2.3.4), rather than a runtime
    `sys.modules` snapshot: other tests in the same pytest session
    legitimately import INDI drivers (Observatory Control needs to), so
    asserting nothing-has-been-imported-yet at runtime is inherently
    order-dependent and does not actually test the invariant that
    matters -- that this module tree's own code never reaches for a
    device driver, regardless of what else has run in the process.
    """
    import pathlib

    planning_root = pathlib.Path(__file__).resolve().parents[2] / "tasks" / "planning_tasks"
    api_file = pathlib.Path(__file__).resolve().parents[1] / "planning_registry.py"
    forbidden_substrings = ("wayfindinglib.drivers.indi", "import PyIndi", "from PyIndi")

    offending_files = []
    for source_file in [*planning_root.glob("*.py"), api_file]:
        text = source_file.read_text()
        if any(needle in text for needle in forbidden_substrings):
            offending_files.append(source_file.name)

    assert offending_files == []
