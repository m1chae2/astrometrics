"""Purpose: Unit tests for `ObservationPlanning`.

Description: Verifies that plans are written and read back through one
storage path: packages check their target, a session built by hand with
`edit_queue` has the same structure as one placed automatically,
`get_plan` reads sessions back, and every `kind=` method refuses
arguments its kind does not use. Also checks that the sky engine is built
once even under concurrent first use, and that no planning source file
imports a device driver, so planning stays free of hardware.
"""

import pytest

from astrometricslib import AppConfiguration, InvalidArgumentError, NotFoundError, Target
from wayfindinglib.api.planning import ObservationPlanning
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.equipment_and_site.calibration import CalibrationAdvisory
from wayfindinglib.models.equipment_and_site.equipment import Telescope
from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
from wayfindinglib.models.planning.observation_package import ExposureRequest, FrameType, ObservationPackage
from wayfindinglib.models.planning.quality_advisory import TargetQualityAdvisory
from wayfindinglib.models.planning.sequence_plan import SequencePlan
from wayfindinglib.models.session.observation_session import ObservationSession, QueueRequest, StartTimeMode


class _FakeTargetCatalog:
    """Finds targets by id in a dictionary and keeps added ones."""

    def __init__(self, targets: list[Target]) -> None:
        """Hold the given targets.

        Parameters
        ----------
        targets : `list` [`Target`]
            The library's targets.
        """
        self._targets = {target.id: target for target in targets}

    def get(self, target_id: str, refresh: bool = False) -> Target | None:
        """Find one target by id.

        Parameters
        ----------
        target_id : `str`
            The id.
        refresh : `bool`, optional
            Ignored.

        Returns
        -------
        target : `Target` or `None`
            The target, or `None`.
        """
        return self._targets.get(target_id)

    def add(self, target: Target) -> None:
        """Keep one more target.

        Parameters
        ----------
        target : `Target`
            The target.
        """
        self._targets[target.id] = target

    def save(self) -> None:
        """Do nothing; the stand-in keeps everything in memory."""


class _FakeAstrometrics:
    """Holds only a target catalog stand-in."""

    def __init__(self, targets: list[Target]) -> None:
        """Build the target catalog stand-in.

        Parameters
        ----------
        targets : `list` [`Target`]
            The library's targets.
        """
        self.targets = _FakeTargetCatalog(targets)


def _target(target_id: str, ra: str = "09:55:33", dec: str = "+69:03:55") -> Target:
    """Build a library target.

    Parameters
    ----------
    target_id : `str`
        The id.
    ra, dec : `str`
        The position as text.

    Returns
    -------
    target : `Target`
        The target.
    """
    return Target(id=target_id, ra=ra, dec=dec)


@pytest.fixture
def isolated_butler(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> DiskButler:
    """Build a DiskButler backed by a fully isolated temporary database.

    Returns
    -------
    butler : `DiskButler`
        The constructed, isolated butler.
    """
    config_path = tmp_path / "astrometrics.config.toml"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return DiskButler(app_config=config)


def _planning(butler: DiskButler, *targets: Target) -> ObservationPlanning:
    """Build a planning API over the butler and a fake target catalog.

    Parameters
    ----------
    butler : `DiskButler`
        The isolated storage.
    *targets : `Target`
        The library's targets.

    Returns
    -------
    planning : `ObservationPlanning`
        The API under test.
    """
    return ObservationPlanning(butler.config, butler, astrometrics=_FakeAstrometrics(list(targets)))


def _site() -> SiteProfile:
    """Build a test site.

    Returns
    -------
    site : `SiteProfile`
        A site in Denver.
    """
    return SiteProfile(id="s1", name="Test Site", latitude_deg=39.7392, longitude_deg=-104.9903)


def _telescope() -> Telescope:
    """Build a test telescope.

    Returns
    -------
    telescope : `Telescope`
        A small refractor with no altitude limit.
    """
    return Telescope(id="t1", name="Test Scope", focal_length_mm=450.0, focal_ratio=6.0, min_altitude_deg=0.0)


def _package(planning: ObservationPlanning, target_id: str = "M 81") -> ObservationPackage:
    """Record a one-exposure package.

    Parameters
    ----------
    planning : `ObservationPlanning`
        The API under test.
    target_id : `str`, optional
        The package's target.

    Returns
    -------
    package : `ObservationPackage`
        The recorded package.
    """
    return planning.create_plan(
        "package",
        target=target_id,
        exposure_requests=[ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=60.0, count=1)],
    )


def test_a_package_is_recorded_for_a_library_target(isolated_butler: DiskButler) -> None:
    """A package names its target and can be read back by id."""
    planning = _planning(isolated_butler, _target("M 81"))
    package = _package(planning)
    assert isinstance(package, ObservationPackage)
    assert package.target_id == "M 81"
    assert isolated_butler.get("observation_package", {"id": package.id}) is not None


def test_a_package_for_an_unknown_target_is_refused(isolated_butler: DiskButler) -> None:
    """A target id that names no library target raises NotFoundError."""
    planning = _planning(isolated_butler)
    with pytest.raises(NotFoundError):
        _package(planning, "NOT-A-TARGET")


def test_create_plan_refuses_arguments_its_kind_does_not_use(isolated_butler: DiskButler) -> None:
    """A package does not take a night, and a sequence needs its items."""
    planning = _planning(isolated_butler, _target("M 81"))
    with pytest.raises(InvalidArgumentError, match="night_id"):
        planning.create_plan("package", target="M 81", exposure_requests=[], night_id="2026-08-10")
    with pytest.raises(InvalidArgumentError, match="plan_items"):
        planning.create_plan("sequence", target="M 81")
    with pytest.raises(InvalidArgumentError, match="kind"):
        planning.create_plan("weekly", target="M 81")


def test_a_sequence_plan_adds_up_its_items(isolated_butler: DiskButler) -> None:
    """A sequence plan gives each item's duration and the total."""
    planning = _planning(isolated_butler, _target("M 81"))
    plan = planning.create_plan(
        "sequence", target="M 81", plan_items=[{"count": 10, "exposure": 60, "filter": "Ha"}, {"count": 2}]
    )
    assert isinstance(plan, SequencePlan)
    assert plan.target_name == "M 81"
    assert plan.total_duration == pytest.approx(600.0)
    assert plan.items[1].filter == "L"


def test_a_hand_built_queue_matches_the_automatic_structure(isolated_butler: DiskButler) -> None:
    """edit_queue freezes the package into the queue entry and records it."""
    planning = _planning(isolated_butler, _target("M 81"))
    session = planning.create_plan(
        "empty_session", site_profile=_site(), telescope=_telescope(), camera_id="c1", night_id="2026-08-10"
    )
    package = _package(planning)

    updated = planning.edit_queue(session.id, add=[QueueRequest(package_id=package.id)])

    assert len(updated.queue) == 1
    entry = updated.queue[0]
    assert entry.observation_package_id == package.id
    assert entry.exposure_requests == package.exposure_requests
    assert entry.start_time_mode == StartTimeMode.SOONEST
    assert planning.get_plan(session.id).queue[0].id == entry.id


def test_edit_queue_reorders_and_refuses_a_wrong_entry_set(isolated_butler: DiskButler) -> None:
    """Order must name exactly the entries; then the queue follows it."""
    planning = _planning(isolated_butler, _target("M 81"))
    session = planning.create_plan(
        "empty_session", site_profile=_site(), telescope=_telescope(), camera_id="c1", night_id="2026-08-10"
    )
    package = _package(planning)
    filled = planning.edit_queue(session.id, add=[{"package_id": package.id}, {"package_id": package.id}])
    first, second = (entry.id for entry in filled.queue)

    with pytest.raises(InvalidArgumentError, match="order"):
        planning.edit_queue(session.id, order=["nonexistent-entry-id"])
    reordered = planning.edit_queue(session.id, order=[second, first])
    assert [entry.id for entry in reordered.queue] == [second, first]


def test_edit_queue_needs_a_known_session_and_a_change(isolated_butler: DiskButler) -> None:
    """An unknown session is not found; asking for no change is refused."""
    planning = _planning(isolated_butler, _target("M 81"))
    with pytest.raises(NotFoundError):
        planning.edit_queue("does-not-exist", order=[])
    with pytest.raises(InvalidArgumentError):
        planning.edit_queue("does-not-exist")


def test_a_scheduled_session_is_placed_and_listed(isolated_butler: DiskButler) -> None:
    """Automatic placement records a session that get_plan lists."""
    planning = _planning(isolated_butler, _target("M 81"), _target("M 13", ra="16:41:41", dec="+36:27:35"))
    package = _package(planning)

    session = planning.create_plan(
        "scheduled_session",
        requests=[QueueRequest(package_id=package.id)],
        site_profile=_site(),
        telescope=_telescope(),
        camera_id="c1",
        night_id="2026-08-10",
    )

    assert isinstance(session, ObservationSession)
    assert session.status.value == "PLANNED"
    summaries = planning.get_plan()
    assert [summary.id for summary in summaries] == [session.id]
    assert str(summaries[0].night_date) == "2026-08-10"


def test_a_bad_night_id_is_refused(isolated_butler: DiskButler) -> None:
    """night_id must be a YYYY-MM-DD date."""
    planning = _planning(isolated_butler)
    with pytest.raises(InvalidArgumentError, match="night_id"):
        planning.create_plan(
            "empty_session", site_profile=_site(), telescope=_telescope(), camera_id="c1", night_id="tonight"
        )


def test_get_advisory_answers_each_kind_and_refuses_the_wrong_arguments(isolated_butler: DiskButler) -> None:
    """Quality takes a target; calibration takes a camera and frame type."""
    planning = _planning(isolated_butler, _target("M 81"))
    assert isinstance(planning.get_advisory("quality", target="M 81"), TargetQualityAdvisory)
    calibration = planning.get_advisory("calibration", camera_id="c1", frame_type=FrameType.DARK)
    assert isinstance(calibration, CalibrationAdvisory)
    assert calibration.existing_count == 0
    with pytest.raises(InvalidArgumentError, match="camera_id"):
        planning.get_advisory("quality", target="M 81", camera_id="c1")
    with pytest.raises(NotFoundError):
        planning.get_advisory("quality", target="NOT-A-TARGET")


def test_create_mosaic_records_one_package_per_panel(isolated_butler: DiskButler) -> None:
    """Panels from calculate_panels become targets and recorded packages."""
    planning = _planning(isolated_butler, _target("NGC 7000", ra="20h 58m 47s", dec="+44° 19′ 48″"))
    panels = planning.calculate_panels("20h 58m 47s", "+44 19 48", 1, 2, 10.0)
    plan = planning.create_mosaic(
        "NGC 7000",
        [panel.model_dump() for panel in panels],
        exposure_requests=[ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=60.0, count=1)],
    )
    assert plan.target_ids == ["NGC 7000_P1_1", "NGC 7000_P1_2"]
    for package in plan.packages:
        assert isolated_butler.get("observation_package", {"id": package.id}) is not None
    with pytest.raises(InvalidArgumentError, match="exposure_requests"):
        planning.create_mosaic("NGC 7000", panels)


def test_deep_catalog_status_refuses_estimate_settings_without_the_estimate(
    isolated_butler: DiskButler,
) -> None:
    """The status is read from disk; estimate settings need the estimate."""
    planning = _planning(isolated_butler)
    status = planning.deep_catalog_status()
    assert status.installed is False
    assert status.estimate is None
    with pytest.raises(InvalidArgumentError, match="healpix_level"):
        planning.deep_catalog_status(healpix_level=3)


def test_get_visibility_refuses_span_arguments_for_one_moment(isolated_butler: DiskButler) -> None:
    """step_minutes and include=["samples"] need an end_time."""
    planning = _planning(isolated_butler)
    point = {"id": "P", "ra_deg": 10.0, "dec_deg": 20.0}
    with pytest.raises(InvalidArgumentError, match="end_time"):
        planning.get_visibility([point], time="2026-10-03T04:00:00Z", step_minutes=10)
    with pytest.raises(InvalidArgumentError, match="include"):
        planning.get_visibility([point], include=["sun"])
    report = planning.get_visibility([point], time="2026-10-03T04:00:00Z", include=["meridian"])
    assert report.objects[0].id == "P"
    assert report.objects[0].meridian is not None
    assert report.time == "2026-10-03T04:00:00Z"


def test_sky_browsing_methods_delegate_to_the_sky_engine(mocker: object, isolated_butler: DiskButler) -> None:
    """Sky-browsing methods hand their arguments to the sky engine."""
    planning = ObservationPlanning(isolated_butler.config, isolated_butler)
    fake_sky = mocker.Mock()
    mocker.patch.object(
        type(planning), "_sky_engine", new_callable=mocker.PropertyMock, return_value=fake_sky
    )

    planning.resolve_target_coordinates("M 81")
    fake_sky.resolve_target_coordinates.assert_called_once_with("M 81")

    collect = mocker.patch("wayfindinglib.tasks.planning_tasks.sky_sources.collect_sky_sources")
    planning.get_sources(1.0, 2.0, 3.0, include=["online"], limiting_magnitude=8.0)
    collect.assert_called_once_with(
        fake_sky,
        1.0,
        2.0,
        3.0,
        include_stars=False,
        include_online=True,
        limiting_magnitude=8.0,
        include_stars_without_catalog_magnitude=True,
    )
    with pytest.raises(InvalidArgumentError, match="only apply"):
        planning.get_sources(1.0, 2.0, 3.0, include=[], limiting_magnitude=8.0)

    planning.get_library_star_summaries(1.0, 2.0, 3.0)
    fake_sky.get_library_star_summaries.assert_called_once_with(1.0, 2.0, 3.0, None)

    planning.get_constellation_lines()
    fake_sky.get_constellation_lines.assert_called_once()


def test_the_sky_engine_is_lazily_constructed_once(isolated_butler: DiskButler) -> None:
    """The sky engine is built on first use and then reused."""
    planning = ObservationPlanning(isolated_butler.config, isolated_butler)
    assert planning._ObservationPlanning__sky_engine is None
    sky_engine = planning._sky_engine
    assert planning._sky_engine is sky_engine


def test_sky_engine_is_constructed_once_under_concurrent_first_access(
    isolated_butler: DiskButler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simultaneous first requests share one `Sky` instead of one each.

    Regression test: the Planetarium fires several queries at once on mount,
    and an unguarded lazy init let every one of them construct its own `Sky`
    -- each loading a full copy of the star catalog (tens of seconds and
    gigabytes each on a 270k-star library).
    """
    import threading
    import time

    import wayfindinglib.sky

    construction_count = 0
    count_lock = threading.Lock()

    class SlowFakeSky:
        """A sky engine that takes a while to build and counts builds."""

        def __init__(self, config: object = None, astrometrics: object = None) -> None:
            """Count one build and wait so other threads arrive meanwhile."""
            nonlocal construction_count
            with count_lock:
                construction_count += 1
            time.sleep(0.2)  # long enough that every thread arrives mid-construction

    monkeypatch.setattr(wayfindinglib.sky, "Sky", SlowFakeSky)

    planning = ObservationPlanning(isolated_butler.config, isolated_butler)
    thread_count = 8
    start_together = threading.Barrier(thread_count)
    engines = []

    def request_engine() -> None:
        """Wait for every thread, then ask for the engine."""
        start_together.wait()
        engines.append(planning._sky_engine)

    threads = [threading.Thread(target=request_engine) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert construction_count == 1
    assert len(engines) == thread_count
    assert all(engine is engines[0] for engine in engines)


def test_planning_module_tree_imports_no_device_driver() -> None:
    """No planning task or planning API source imports the INDI device layer.

    A static check of the source rather than a runtime `sys.modules`
    snapshot: other tests in the same pytest session legitimately import
    INDI drivers (`control` needs them), so checking at runtime would
    depend on test order and would not test what matters -- that the
    planning code itself never reaches for a device driver.
    """
    import pathlib

    planning_root = pathlib.Path(__file__).resolve().parents[2] / "tasks" / "planning_tasks"
    api_file = pathlib.Path(__file__).resolve().parents[1] / "planning.py"
    forbidden_substrings = ("wayfindinglib.drivers.indi", "import PyIndi", "from PyIndi")

    offending_files = []
    for source_file in [*planning_root.glob("*.py"), api_file]:
        text = source_file.read_text()
        if any(needle in text for needle in forbidden_substrings):
            offending_files.append(source_file.name)

    assert offending_files == []


def test_a_package_can_be_built_from_sequence_items(isolated_butler: DiskButler) -> None:
    """Sequence items become exposure requests; one source is needed."""
    planning = _planning(isolated_butler, _target("M 81"))

    package = planning.create_plan(
        "package",
        target="M 81",
        plan_items=[
            {"count": 3, "exposure": 60, "filter": "L"},
            {"count": 2, "exposure": 30, "filter": "ha"},
        ],
    )

    assert [
        (request.count, request.exposure_sec, request.filter.value) for request in package.exposure_requests
    ] == [
        (3, 60.0, "Luminance"),
        (2, 30.0, "Ha"),
    ]
    assert package.exposure_requests[0].frame_type == FrameType.LIGHT
    with pytest.raises(InvalidArgumentError, match="exactly one"):
        planning.create_plan("package", target="M 81")
    with pytest.raises(InvalidArgumentError, match="Unknown filter"):
        planning.create_plan(
            "package", target="M 81", plan_items=[{"count": 1, "exposure": 1, "filter": "Z"}]
        )


def test_an_empty_session_defaults_to_the_site_and_active_equipment(
    isolated_butler: DiskButler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no equipment given, the site and the active rig are used."""
    from types import SimpleNamespace

    from astrometricslib import ConfigurationError
    from wayfindinglib.data_access import equipment_catalog_reader

    planning = _planning(isolated_butler)
    catalog = SimpleNamespace(active_telescope=_telescope, active_camera=lambda: SimpleNamespace(id="c1"))
    monkeypatch.setattr(equipment_catalog_reader, "get_equipment_catalog", lambda config: catalog)

    session = planning.create_plan("empty_session", night_id="2026-08-10")

    assert (session.telescope_id, session.camera_id, session.site_profile_id) == ("t1", "c1", "default")
    empty = SimpleNamespace(active_telescope=lambda: None, active_camera=lambda: None)
    monkeypatch.setattr(equipment_catalog_reader, "get_equipment_catalog", lambda config: empty)
    with pytest.raises(ConfigurationError):
        planning.create_plan("empty_session", night_id="2026-08-10")


def test_edit_queue_removes_entries_and_refuses_unknown_ones(isolated_butler: DiskButler) -> None:
    """Removal takes entries out; an unknown entry is not found."""
    planning = _planning(isolated_butler, _target("M 81"))
    session = planning.create_plan(
        "empty_session", site_profile=_site(), telescope=_telescope(), camera_id="c1", night_id="2026-08-10"
    )
    package = _package(planning)
    filled = planning.edit_queue(session.id, add=[{"package_id": package.id}, {"package_id": package.id}])
    first, second = (entry.id for entry in filled.queue)

    remaining = planning.edit_queue(session.id, remove=[first])

    assert [entry.id for entry in remaining.queue] == [second]
    with pytest.raises(NotFoundError):
        planning.edit_queue(session.id, remove=[first])
