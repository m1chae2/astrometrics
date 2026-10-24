"""Purpose: Unit tests for `ObservationPlanning`.

Description: Verifies that plans are written and read back through one
storage path: packages check their target, a session built by hand with
`edit_queue` has the same structure as one placed automatically,
`get_plan` reads sessions back, and every `kind=` method refuses
arguments its kind does not use. Also checks that the sky engine is built
once even under concurrent first use, and that using planning in a
fresh process loads no hardware driver and no Control module, so planning
stays free of hardware.
"""

from types import SimpleNamespace

import pytest

from astrometricslib import AppConfiguration, InvalidArgumentError, NotFoundError, Target
from wayfindinglib.api.planning import ObservationPlanning
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.equipment_and_site.calibration import CalibrationAdvisory
from wayfindinglib.models.equipment_and_site.equipment import Telescope
from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
from wayfindinglib.models.planning.observation_package import (
    DitherConfig,
    ExposureRequest,
    FrameType,
    ObservationPackage,
)
from wayfindinglib.models.planning.quality_advisory import TargetQualityAdvisory
from wayfindinglib.models.planning.sequence_plan import SequencePlan
from wayfindinglib.models.planning.sequence_summary import SequenceSummary
from wayfindinglib.models.session.observation_session import (
    ObservationSession,
    PlanRevisionConflictError,
    QueueRequest,
    StartTimeMode,
)


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
        self.quality = SimpleNamespace(capture_settings=dict)


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

    updated = planning.edit_queue(session.id, add=[QueueRequest(package_id=package.id)]).session

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
    filled = planning.edit_queue(
        session.id, add=[{"package_id": package.id}, {"package_id": package.id}]
    ).session
    first, second = (entry.id for entry in filled.queue)

    with pytest.raises(InvalidArgumentError, match="order"):
        planning.edit_queue(session.id, order=["nonexistent-entry-id"])
    reordered = planning.edit_queue(session.id, order=[second, first]).session
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
    """Simultaneous first requests share one `SkyEngine` instead of one each.

    Regression test: the Planetarium fires several queries at once on mount,
    and an unguarded lazy init let every one of them construct its own
    `SkyEngine` -- each loading a full copy of the star catalog (tens of
    seconds and gigabytes each on a 270k-star library).
    """
    import threading
    import time

    import wayfindinglib.tasks.planning_tasks.sky_engine as sky_engine_module

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

    monkeypatch.setattr(sky_engine_module, "SkyEngine", SlowFakeSky)

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
    """Using Planning loads no hardware driver and no Control module.

    "Planning is hardware-free" (`Wayfinding_Library_Architecture.md`
    Sections 4.4 and 7.1).
    The check runs in a fresh Python process, because other tests in this
    pytest session import the INDI drivers on purpose. The child imports
    `wayfindinglib.api.planning`, reads the equipment catalog (which
    checks each device's protocol name) and builds an empty session for
    the active telescope and camera. Then no module of the hardware
    drivers, the Control tasks or the Control API may be in `sys.modules`.
    The import-linter contracts in ``pyproject.toml`` check the same rule
    statically.
    """
    from wayfindinglib.test.import_probe import (
        CONTROL_PACKAGES,
        modules_within,
        wayfindinglib_modules_loaded_by,
    )

    loaded = wayfindinglib_modules_loaded_by(
        """
        from wayfindinglib.api.planning import ObservationPlanning
        from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog
        from wayfindinglib.drivers.butler import DiskButler

        assert get_equipment_catalog(config).active_telescope().mount_protocol == "indi"
        planning = ObservationPlanning(config, DiskButler(app_config=config))
        session = planning.create_plan("empty_session", night_id="2026-08-10")
        assert (session.telescope_id, session.camera_id) == ("Short", "Main A")
        """
    )

    assert "wayfindinglib.api.planning" in loaded
    assert modules_within(loaded, CONTROL_PACKAGES) == []


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
    filled = planning.edit_queue(
        session.id, add=[{"package_id": package.id}, {"package_id": package.id}]
    ).session
    first, second = (entry.id for entry in filled.queue)

    remaining = planning.edit_queue(session.id, remove=[first]).session

    assert [entry.id for entry in remaining.queue] == [second]
    with pytest.raises(NotFoundError):
        planning.edit_queue(session.id, remove=[first])


def _session(planning: ObservationPlanning) -> ObservationSession:
    """Record an empty night plan for camera ``c1``.

    Parameters
    ----------
    planning : `ObservationPlanning`
        The API under test.

    Returns
    -------
    session : `ObservationSession`
        The recorded, empty session at revision 0.
    """
    return planning.create_plan(
        "empty_session", site_profile=_site(), telescope=_telescope(), camera_id="c1", night_id="2026-08-10"
    )


def _measured_profile() -> SimpleNamespace:
    """Build a camera profile that measured one setting.

    Returns
    -------
    profile : `SimpleNamespace`
        Gain setting 100, offset 10, binning 1x1 measured.
    """
    return SimpleNamespace(
        camera_name="c1",
        is_generic_fallback=False,
        sensor_noise=[SimpleNamespace(gain_setting="100", offset="10", binning="1x1")],
    )


def test_each_plan_write_adds_one_to_the_revision(isolated_butler: DiskButler) -> None:
    """A write returns the new revision, the changes and the session."""
    planning = _planning(isolated_butler, _target("M 81"))
    session = _session(planning)
    package = _package(planning)
    assert session.revision == 0

    change = planning.edit_queue(session.id, add=[{"package_id": package.id}], expected_revision=0)

    assert (change.previous_revision, change.revision) == (0, 1)
    assert [item.kind for item in change.changes] == ["queue_added"]
    assert planning.get_plan(session.id).revision == 1


def test_a_stale_revision_is_refused_and_names_the_current_one(isolated_butler: DiskButler) -> None:
    """A second window that read revision 0 cannot overwrite revision 1."""
    planning = _planning(isolated_butler, _target("M 81"))
    session = _session(planning)
    package = _package(planning)
    planning.edit_queue(session.id, add=[{"package_id": package.id}], expected_revision=0)

    with pytest.raises(PlanRevisionConflictError) as caught:
        planning.edit_queue(session.id, add=[{"package_id": package.id}], expected_revision=0)

    assert caught.value.details["current_revision"] == 1
    assert len(planning.get_plan(session.id).queue) == 1


def test_the_database_write_refuses_a_revision_that_moved(isolated_butler: DiskButler) -> None:
    """A second write from the same revision stores nothing."""
    planning = _planning(isolated_butler, _target("M 81"))
    session = _session(planning)
    first = session.model_copy(update={"revision": 1, "status_detail": "first"})
    second = session.model_copy(update={"revision": 1, "status_detail": "second"})

    assert isolated_butler.put_session_if_revision(first, 0)
    assert not isolated_butler.put_session_if_revision(second, 0)
    assert planning.get_plan(session.id).status_detail == "first"
    assert not isolated_butler.put_session_if_revision(first.model_copy(update={"id": "nope"}), 0)


def test_a_session_stored_before_the_night_plan_fields_still_loads(isolated_butler: DiskButler) -> None:
    """Old stored JSON lacks the checklist, framing and revision fields."""
    import json
    import sqlite3

    planning = _planning(isolated_butler, _target("M 81"))
    session = _session(planning)
    package = _package(planning)
    planning.edit_queue(session.id, add=[{"package_id": package.id}])
    database = isolated_butler.library_path / "wayfinding.db"
    connection = sqlite3.connect(database)
    try:
        selects = {
            "wayfinding_observation_sessions": "SELECT id, data_json FROM wayfinding_observation_sessions",
            "observation_packages": "SELECT id, data_json FROM observation_packages",
        }
        updates = {
            "wayfinding_observation_sessions": (
                "UPDATE wayfinding_observation_sessions SET data_json = ? WHERE id = ?"
            ),
            "observation_packages": "UPDATE observation_packages SET data_json = ? WHERE id = ?",
        }
        for table, select in selects.items():
            for row_id, text in connection.execute(select).fetchall():
                data = json.loads(text)
                for key in ("checklist", "framing", "revision"):
                    data.pop(key, None)
                requests = [
                    request for entry in data.get("queue", []) for request in entry["exposureRequests"]
                ]
                requests.extend(data.get("exposureRequests", []))
                for request in requests:
                    for key in ("gainSetting", "offset", "binning"):
                        request.pop(key, None)
                connection.execute(updates[table], (json.dumps(data), row_id))
        connection.commit()
    finally:
        connection.close()

    loaded = planning.get_plan(session.id)
    assert (loaded.checklist, loaded.framing, loaded.revision) == ([], None, 0)
    assert loaded.queue[0].exposure_requests[0].gain_setting is None
    change = planning.edit_queue(session.id, add=[{"package_id": package.id}])
    assert change.revision == 1


def test_exact_duplicates_merge_and_conflicting_ones_are_refused(isolated_butler: DiskButler) -> None:
    """Identical requests add counts; a delay-only difference conflicts."""
    planning = _planning(isolated_butler, _target("M 81"))
    session = _session(planning)
    package = _package(planning)
    light = {"frame_type": "LIGHT", "exposure_sec": 60.0, "count": 5}

    change = planning.edit_queue(
        session.id, add=[{"package_id": package.id, "exposure_requests": [light, dict(light, count=3)]}]
    )
    assert [request.count for request in change.session.queue[0].exposure_requests] == [8]
    with pytest.raises(InvalidArgumentError, match="delays"):
        planning.edit_queue(
            session.id,
            add=[{"package_id": package.id, "exposure_requests": [light, dict(light, delay_sec=2.0)]}],
        )


def test_a_measured_camera_refuses_an_unmeasured_setting(
    isolated_butler: DiskButler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gain setting the profile never measured is refused."""
    import astrometricslib

    monkeypatch.setattr(
        astrometricslib, "resolve_camera_profile", lambda name, config=None: _measured_profile()
    )
    planning = _planning(isolated_butler, _target("M 81"))
    session = _session(planning)
    package = _package(planning)
    good = {"frame_type": "LIGHT", "exposure_sec": 60.0, "count": 1, "gain_setting": 100, "offset": 10}
    bad = dict(good, gain_setting=250)

    assert (
        planning.edit_queue(
            session.id, add=[{"package_id": package.id, "exposure_requests": [good]}]
        ).setting_notes
        == []
    )
    with pytest.raises(InvalidArgumentError, match="gain setting 100"):
        planning.edit_queue(session.id, add=[{"package_id": package.id, "exposure_requests": [bad]}])
    assert len(planning.get_plan(session.id).queue) == 1


def test_an_unprofiled_camera_accepts_and_marks_the_setting(isolated_butler: DiskButler) -> None:
    """Camera c1 has no profile, so any setting is accepted with a note."""
    planning = _planning(isolated_butler, _target("M 81"))
    session = _session(planning)
    package = _package(planning)
    request = {"frame_type": "LIGHT", "exposure_sec": 60.0, "count": 1, "gain_setting": 321}

    change = planning.edit_queue(session.id, add=[{"package_id": package.id, "exposure_requests": [request]}])

    assert change.session.queue[0].exposure_requests[0].gain_setting == 321
    assert any("setting not measured" in note for note in change.setting_notes)


def test_set_exposures_changes_a_pending_entry_only(isolated_butler: DiskButler) -> None:
    """New exposures replace a pending entry's; an unknown entry fails."""
    planning = _planning(isolated_butler, _target("M 81"))
    session = _session(planning)
    package = _package(planning)
    entry_id = planning.edit_queue(session.id, add=[{"package_id": package.id}]).session.queue[0].id

    change = planning.edit_queue(
        session.id, set_exposures={entry_id: [{"frame_type": "LIGHT", "exposure_sec": 30.0, "count": 4}]}
    )

    assert change.session.queue[0].exposure_requests[0].exposure_sec == pytest.approx(30.0)
    with pytest.raises(NotFoundError):
        planning.edit_queue(session.id, set_exposures={"missing": []})


def test_checklist_items_are_added_once_and_ticked(isolated_butler: DiskButler) -> None:
    """A rule and setting are added once; ticks keep their history."""
    planning = _planning(isolated_butler, _target("M 81"))
    session = _session(planning)
    item = {"rule": "refocus_when_temperature_drifts"}

    first = planning.edit_queue(session.id, add_checklist=[item])
    again = planning.edit_queue(session.id, add_checklist=[item])
    item_id = first.session.checklist[0].item_id
    assert [change.kind for change in again.changes] == ["checklist_already_present"]
    assert len(again.session.checklist) == 1

    ticked = planning.tick_checklist_item(
        session.id, item_id, expected_revision=again.revision, note="did it"
    )
    retick = planning.tick_checklist_item(session.id, item_id)

    assert ticked.revision == again.revision + 1
    assert ticked.session.checklist[0].ticked_at is not None
    assert len(retick.session.checklist[0].tick_history) == 2
    assert retick.session.checklist[0].note == "did it"
    with pytest.raises(NotFoundError):
        planning.tick_checklist_item(session.id, "no-such-item")
    with pytest.raises(PlanRevisionConflictError):
        planning.tick_checklist_item(session.id, item_id, expected_revision=0)


def test_framing_is_stored_and_the_sequence_summary_follows_ekos_order(isolated_butler: DiskButler) -> None:
    """Framing round trips; summary rows follow the Ekos Capture order."""
    planning = _planning(isolated_butler, _target("M 81"))
    session = _session(planning)
    package = planning.create_plan(
        "package",
        target="M 81",
        exposure_requests=[
            ExposureRequest(
                frame_type=FrameType.LIGHT,
                exposure_sec=60.0,
                count=12,
                gain_setting=100,
                offset=10,
                binning=1,
            )
        ],
        dither_config=DitherConfig(enabled=True, every_n_frames=4, pixels=5.0),
    )
    panels = planning.calculate_panels("10:00:00", "+30:00:00", 1, 2, 10.0, rotation_deg=45.0)
    change = planning.edit_queue(
        session.id,
        add=[{"package_id": package.id}],
        framing={"center_ra_deg": 150.0, "center_dec_deg": 30.0, "rotation_deg": 45.0, "panels": panels},
    )

    stored = planning.get_plan(session.id)
    assert stored.framing.rotation_deg == pytest.approx(45.0)
    assert len(stored.framing.panels) == 2
    summary = SequenceSummary.from_session(change.session)
    assert [field.name for field in summary.rows[0].fields] == [
        "filter",
        "exposure_seconds",
        "count",
        "binning",
        "gain_setting",
        "offset",
    ]
    assert [field.value for field in summary.rows[0].fields][1:] == [60.0, 12, 1, 100, 10]
    assert summary.revision == stored.revision
    assert summary.dither_rows[0].size_pixels == pytest.approx(5.0)
    assert "Guide" in summary.dither_rows[0].size_ekos_screen
    assert "Capture" in summary.dither_rows[0].frequency_ekos_screen
