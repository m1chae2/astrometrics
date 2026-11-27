"""Purpose: Unit tests for `ObservationPlanning` and its four objects.

Description: Verifies that plans are written and read back through one
storage path: packages take a library target, a session built by hand with
`sessions.add` has the same structure as one placed automatically, and
`sessions.get` reads a night's session back. Each change checks the revision
the session object carries. Also checks the sky answers (visibility, mosaic
panels, constellation lines), that the sky engine is built once even under
concurrent first use, and that using planning in a fresh process loads no
hardware driver and no Control module, so planning stays free of hardware.
"""

from types import SimpleNamespace

import pytest

from astrometricslib import AppConfiguration, ObservatoryConfiguration, StellarObject, Target, Telescope
from astrometricslib._internal.results import RestoreReport
from astrometricslib.arguments import Page
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from astrometricslib.models.observatory_configuration import Camera
from wayfindinglib.api.planning import ObservationPlanning
from wayfindinglib.api.review import ObservationReview
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.planning.observation_package import (
    DitherConfig,
    ExposureRequest,
    ObservationPackage,
)
from wayfindinglib.models.planning.sequence_summary import SequenceSummary
from wayfindinglib.models.planning.target_report import TargetDatasetReport
from wayfindinglib.models.session.observation_session import (
    ChecklistItem,
    Framing,
    ObservationSession,
    PlanRevisionConflictError,
    QueueRequest,
    StartTimeMode,
)
from wayfindinglib.test.camera_profile_support import NO_CAMERAS, no_camera_profiles_calibration


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
        self.catalog_names = {"NGC 7331"}
        self.created: list[str] = []
        self.frames = SimpleNamespace(get_set_aside=lambda _target: ())

    def get(
        self, target_id: str | Target, refresh: bool | str = False, *, required: bool = False
    ) -> Target | None:
        """Find one target by id, as the library does.

        Parameters
        ----------
        target_id : `str` or `Target`
            The id, or a target, which is given back as it is.
        refresh : `bool` or `str`, optional
            Ignored.
        required : `bool`, optional
            Raise `NotFoundError` instead of giving `None`.

        Returns
        -------
        target : `Target` or `None`
            The target, or `None`.

        Raises
        ------
        NotFoundError
            If no target has the id and ``required`` is set.
        """
        if isinstance(target_id, Target):
            return target_id
        found = self._targets.get(target_id)
        if found is None and required:
            raise NotFoundError(f"No target with id {target_id!r} in the library.")
        return found

    def query(self, **_kwargs: object) -> SimpleNamespace:
        """Give every target, as ``query(include_empty=True)`` does.

        Returns
        -------
        answer : `SimpleNamespace`
            The library's targets in ``items``.
        """
        return SimpleNamespace(items=tuple(self._targets.values()))

    def add(self, target: Target) -> None:
        """Keep one more target.

        Parameters
        ----------
        target : `Target`
            The target.
        """
        self._targets[target.id] = target

    def create(self, target_id: str, from_catalog: bool = False) -> Target:
        """Stand in for catalog-based creation.

        Parameters
        ----------
        target_id : `str`
            The catalog name.
        from_catalog : `bool`, optional
            Must be `True`.

        Returns
        -------
        target : `Target`
            The new target, with a position, when `catalog_names` lists
            the name.

        Raises
        ------
        NotFoundError
            If the name is not in `catalog_names`.
        """
        assert from_catalog
        self.created.append(target_id)
        if target_id not in self.catalog_names:
            raise NotFoundError(f"The catalog does not know {target_id!r}.")
        target = Target(id=target_id, ra="01h 02m 03s", dec="+10° 20′ 30″")
        self._targets[target.id] = target
        return target

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
        self.quality = SimpleNamespace(
            records=SimpleNamespace(
                query=lambda **_arguments: Page.of([]),
                get_capture_settings=lambda ids=None: [],
                get_metrics=lambda checkpoint=None: [],
                get_calibration_identities=lambda target=None, **_arguments: [],
            )
        )
        self.stars = SimpleNamespace(get=lambda object_id, **_: None)
        self.processing = SimpleNamespace(
            restore_excluded_frames=lambda target, apply=False: RestoreReport(
                targetId=getattr(target, "id", target), applied=False
            ),
        )
        self.calibration = SimpleNamespace(
            query=lambda kind=None, **_arguments: [],
            get_profile=no_camera_profiles_calibration().get_profile,
        )
        self.config = NO_CAMERAS


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


def _review(butler: DiskButler, *targets: Target) -> ObservationReview:
    """Build a review API over the butler and a fake target catalog.

    Parameters
    ----------
    butler : `DiskButler`
        The isolated storage.
    *targets : `Target`
        The library's targets.

    Returns
    -------
    review : `ObservationReview`
        The API under test.
    """
    from wayfindinglib.api.control import ObservatoryControl

    control = ObservatoryControl(
        butler.config, butler=butler, astrometrics=_FakeAstrometrics(list(targets)), simulated=True
    )
    return ObservationReview(control)


def _configuration() -> ObservatoryConfiguration:
    """Build a test observatory configuration.

    Returns
    -------
    configuration : `ObservatoryConfiguration`
        Telescope ``t1`` (a small refractor with no altitude limit) and
        camera ``c1``.
    """
    return ObservatoryConfiguration(
        id="rig",
        name="Test rig",
        telescope=Telescope(
            id="t1", name="Test Scope", focal_length_mm=450.0, focal_ratio=6.0, min_altitude_deg=0.0
        ),
        camera=Camera(id="c1", name="c1", pixel_size_um=3.76, sensor_width_px=3008, sensor_height_px=3008),
    )


def _rows(**fields: object) -> list[ExposureRequest]:
    """Build one light exposure row.

    Parameters
    ----------
    **fields : `object`
        Fields to set besides a 60 s, one-frame light.

    Returns
    -------
    rows : `list` [`ExposureRequest`]
        The one row.
    """
    return [ExposureRequest.model_validate({"frame_type": "LIGHT", "exposure_s": 60.0, "count": 1, **fields})]


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
    target = planning._context.astrometrics.targets.get(target_id, required=True)
    return planning.packages.create(target, _rows())


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
    return planning.sessions.create("2026-08-10", _configuration())


def _point(ra_deg: float = 10.0, dec_deg: float = 20.0) -> StellarObject:
    """Build a point on the sky with a known position.

    Parameters
    ----------
    ra_deg, dec_deg : `float`, optional
        The position, ICRS degrees.

    Returns
    -------
    point : `StellarObject`
        A star named ``P`` at that position.
    """
    return StellarObject(id="P", name="P", ra=ra_deg, dec=dec_deg)


def test_a_package_is_recorded_for_a_library_target(isolated_butler: DiskButler) -> None:
    """A package names its target and can be read back by id."""
    planning = _planning(isolated_butler, _target("M 81"))
    package = _package(planning)
    assert isinstance(package, ObservationPackage)
    assert package.target_id == "M 81"
    assert planning.packages.get(package.id) == package
    assert planning.packages.get("no-such-package") is None
    with pytest.raises(NotFoundError):
        planning.packages.get("no-such-package", required=True)


def test_create_package_takes_a_target_object_and_checks_its_rows(isolated_butler: DiskButler) -> None:
    """A name is refused, and so is a bad value; no rows is allowed."""
    planning = _planning(isolated_butler, _target("M 81"))
    with pytest.raises(InvalidArgumentError, match="Target"):
        planning.packages.create("M 81")
    target = planning._context.astrometrics.targets.get("M 81")
    with pytest.raises(InvalidArgumentError, match="not valid"):
        planning.packages.create(target, minimum_altitude_deg=120.0)
    assert planning.packages.create(target).exposure_requests == []


def test_a_hand_built_queue_matches_the_automatic_structure(isolated_butler: DiskButler) -> None:
    """sessions.add freezes the package into the queue entry and records it."""
    planning = _planning(isolated_butler, _target("M 81"))
    session = _session(planning)
    package = _package(planning)

    updated = planning.sessions.add(session, [package])

    assert len(updated.queue) == 1
    entry = updated.queue[0]
    assert entry.observation_package_id == package.id
    assert entry.exposure_requests == package.exposure_requests
    assert entry.start_time_mode == StartTimeMode.SOONEST
    assert planning.sessions.get("2026-08-10").queue[0].id == entry.id


def test_update_reorders_and_refuses_a_wrong_entry_set(isolated_butler: DiskButler) -> None:
    """Order must name exactly the entries; then the queue follows it."""
    planning = _planning(isolated_butler, _target("M 81"))
    package = _package(planning)
    filled = planning.sessions.add(_session(planning), [package, QueueRequest(package_id=package.id)])
    first, second = filled.queue

    with pytest.raises(InvalidArgumentError, match="order"):
        planning.sessions.update(filled, order=[first])
    reordered = planning.sessions.update(filled, order=[second, first])
    assert [entry.id for entry in reordered.queue] == [second.id, first.id]


def test_a_change_needs_a_stored_session(isolated_butler: DiskButler) -> None:
    """A session that is not stored is not found."""
    planning = _planning(isolated_butler, _target("M 81"))
    session = _session(planning).model_copy(update={"id": "does-not-exist"})
    with pytest.raises(NotFoundError):
        planning.sessions.update(session, order=[])


def test_a_scheduled_session_is_placed_and_listed(isolated_butler: DiskButler) -> None:
    """Automatic placement records a session that sessions.query lists."""
    planning = _planning(isolated_butler, _target("M 81"), _target("M 13", ra="16:41:41", dec="+36:27:35"))
    package = _package(planning)

    session = planning.sessions.create("2026-08-10", _configuration(), packages=[package])

    assert isinstance(session, ObservationSession)
    assert session.status.value == "PLANNED"
    sessions = planning.sessions.query()
    assert [listed.id for listed in sessions] == [session.id]
    assert str(sessions[0].night_date) == "2026-08-10"
    assert planning.sessions.query(status="COMPLETED").total == 0
    assert planning.sessions.query(since="2026-08-11T00:00:00Z").total == 0


def test_a_bad_night_is_refused(isolated_butler: DiskButler) -> None:
    """A night must be a YYYY-MM-DD date."""
    planning = _planning(isolated_butler)
    with pytest.raises(InvalidArgumentError, match="night"):
        planning.sessions.create("tonight", _configuration())
    with pytest.raises(InvalidArgumentError, match="night"):
        planning.sessions.get("tonight")
    assert planning.sessions.get("2026-08-10") is None
    with pytest.raises(NotFoundError):
        planning.sessions.get("2026-08-10", required=True)


def test_a_quality_advisory_answers_and_refuses_the_wrong_arguments(isolated_butler: DiskButler) -> None:
    """The quality report takes a target; a wrong kind or object is refused."""
    review = _review(isolated_butler, _target("M 81"))
    report = review.targets.get_report(_target("M 81"))
    assert isinstance(report, TargetDatasetReport)
    assert report.target_id == "M 81"
    assert report.needs_attention.warning_count == 0
    assert report.starting_point is not None
    with pytest.raises(NotFoundError):
        review.targets.get_result(StellarObject(id="no-such-star"), target=_target("M 81"))


def test_tonight_advice_reads_a_time_and_refuses_bad_text(isolated_butler: DiskButler) -> None:
    """Tonight gives a ranking; text that is not a time is refused."""
    from wayfindinglib.models.planning.tonight_ranking import TonightRanking

    planning = _planning(isolated_butler, _target("M 81"))
    assert isinstance(planning.sky.get_tonight_advice(time="2026-10-24T04:00:00Z"), TonightRanking)
    with pytest.raises(InvalidArgumentError):
        planning.sky.get_tonight_advice(time="not a time")


def test_a_mosaic_center_is_read_as_degrees_not_hours(isolated_butler: DiskButler) -> None:
    """A right ascension of 10.684 degrees is not read as 10.684 hours."""
    planning = _planning(isolated_butler)
    (panel,) = planning.sky.get_panels(_point(10.684, 41.269))
    assert panel.ra_deg == pytest.approx(10.684, abs=1e-6)
    assert panel.dec_deg == pytest.approx(41.269, abs=1e-6)
    with pytest.raises(InvalidArgumentError, match="overlap_pct"):
        planning.sky.get_panels(_point(), 2, 2, overlap_pct=100.0)
    with pytest.raises(InvalidArgumentError, match="a StellarObject or a SkyPosition"):
        planning.sky.get_panels("M 31")
    from astrometricslib import SkyPosition

    (position_panel,) = planning.sky.get_panels(SkyPosition(ra_deg=10.684, dec_deg=41.269))
    assert position_panel.ra_deg == pytest.approx(10.684, abs=1e-6)


def test_create_mosaic_records_one_package_per_panel(isolated_butler: DiskButler) -> None:
    """Panels from get_panels become targets and recorded packages."""
    parent = _target("NGC 7000", ra="20h 58m 47s", dec="+44° 19′ 48″")
    planning = _planning(isolated_butler, parent)
    panels = planning.sky.get_panels(parent, 1, 2, overlap_pct=10.0)
    plan = planning.packages.create_mosaic(parent, panels, _rows())
    assert plan.target_ids == ["NGC 7000_P1_1", "NGC 7000_P1_2"]
    for package in plan.packages:
        assert planning.packages.get(package.id) is not None
    assert planning.packages.create_mosaic(parent, panels).packages == []
    with pytest.raises(InvalidArgumentError, match="dither_config"):
        planning.packages.create_mosaic(parent, panels, dither_config=DitherConfig())


def test_the_deep_catalog_status_is_read_from_disk(isolated_butler: DiskButler) -> None:
    """With nothing downloaded, the catalog is not installed."""
    planning = _planning(isolated_butler)
    status = planning.catalogs.get_deep_status()
    assert status.installed is False
    assert status.estimate is None


def test_get_visibility_refuses_span_arguments_for_one_moment(isolated_butler: DiskButler) -> None:
    """step_min and include=["samples"] need an until; names are refused."""
    planning = _planning(isolated_butler)
    with pytest.raises(InvalidArgumentError, match="until"):
        planning.sky.get_visibility([_point()], time="2026-10-03T04:00:00Z", step_min=10)
    with pytest.raises(InvalidArgumentError, match="include"):
        planning.sky.get_visibility([_point()], include=["sun"])
    with pytest.raises(InvalidArgumentError, match="resolve"):
        planning.sky.get_visibility(["M 31"])
    report = planning.sky.get_visibility([_point()], time="2026-10-03T04:00:00Z", include=["meridian"])
    assert report.objects[0].id == "P"
    assert report.objects[0].meridian is not None
    assert report.time == "2026-10-03T04:00:00Z"


def test_the_constellation_lines_come_from_the_bundled_file(isolated_butler: DiskButler) -> None:
    """The bundled stick figures load: hundreds of segments, not none."""
    planning = _planning(isolated_butler)
    lines = planning.sky.get_constellation_lines()
    assert len(lines) > 500
    assert {"Ori", "UMa"} <= {line.constellation for line in lines}


def test_sky_browsing_methods_delegate_to_the_sky_engine(mocker: object, isolated_butler: DiskButler) -> None:
    """Sky-browsing methods hand their arguments to the sky engine."""
    planning = ObservationPlanning(isolated_butler.config, isolated_butler)
    fake_sky = mocker.Mock()
    planning._context._sky_engine = fake_sky

    fake_sky.get_constellation_lines.return_value = [
        {"constellation": "Ori", "ra1": 1.0, "dec1": 2.0, "ra2": 3.0, "dec2": 4.0}
    ]
    (segment,) = planning.sky.get_constellation_lines()
    assert (segment.constellation, segment.ra2) == ("Ori", 3.0)
    fake_sky.list_catalog_driver_metadata.return_value = [
        {"driver_name": "deep_stars", "display_name": "Gaia deep stars", "maximum_query_radius_deg": 2.0}
    ]
    (driver,) = planning.catalogs.query()
    assert driver.model_dump() == {
        "driver_name": "deep_stars",
        "display_name": "Gaia deep stars",
        "maximum_query_radius_deg": 2.0,
    }


def test_the_sky_engine_is_lazily_constructed_once(isolated_butler: DiskButler) -> None:
    """The sky engine is built on first use and then reused."""
    planning = ObservationPlanning(isolated_butler.config, isolated_butler)
    assert planning._context._sky_engine is None
    sky_engine = planning._context.sky_engine
    assert planning._context.sky_engine is sky_engine


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
        engines.append(planning._context.sky_engine)

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
        session = planning.sessions.create("2026-08-10")
        assert (session.telescope_id, session.camera_id) == ("Short", "Main A")
        """
    )

    assert "wayfindinglib.api.planning" in loaded
    assert modules_within(loaded, CONTROL_PACKAGES) == []


def test_an_empty_session_defaults_to_the_site_and_active_equipment(
    isolated_butler: DiskButler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no equipment given, the site and the active rig are used."""
    from types import SimpleNamespace

    from astrometricslib.exceptions import ConfigurationError
    from wayfindinglib.data_access import equipment_catalog_reader

    planning = _planning(isolated_butler)
    catalog = SimpleNamespace(
        active_telescope=lambda: _configuration().telescope, active_camera=lambda: SimpleNamespace(id="c1")
    )
    monkeypatch.setattr(equipment_catalog_reader, "get_equipment_catalog", lambda config: catalog)

    session = planning.sessions.create("2026-08-10")

    assert (session.telescope_id, session.camera_id, session.site_profile_id) == ("t1", "c1", "default")
    empty = SimpleNamespace(active_telescope=lambda: None, active_camera=lambda: None)
    monkeypatch.setattr(equipment_catalog_reader, "get_equipment_catalog", lambda config: empty)
    with pytest.raises(ConfigurationError):
        # A night with no session needs equipment; 08-10 has one.
        planning.sessions.create("2026-08-11")


def test_remove_takes_entries_out_and_refuses_unknown_ones(isolated_butler: DiskButler) -> None:
    """Removal takes entries out; an entry no longer queued is not found."""
    planning = _planning(isolated_butler, _target("M 81"))
    package = _package(planning)
    filled = planning.sessions.add(_session(planning), [package, package])
    first, second = filled.queue

    remaining = planning.sessions.remove(filled, [first])

    assert [entry.id for entry in remaining.queue] == [second.id]
    with pytest.raises(NotFoundError):
        planning.sessions.remove(remaining, [first])


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
        sensor_noise=[SimpleNamespace(gain="100", offset="10", binning="1x1")],
    )


def test_each_plan_write_adds_one_to_the_revision(isolated_butler: DiskButler) -> None:
    """A write returns the stored session at the next revision."""
    planning = _planning(isolated_butler, _target("M 81"))
    session = _session(planning)
    assert session.revision == 0

    changed = planning.sessions.add(session, [_package(planning)])

    assert changed.revision == 1
    assert planning.sessions.get("2026-08-10").revision == 1


def test_a_stale_revision_is_refused_and_names_the_current_one(isolated_butler: DiskButler) -> None:
    """A second window that read revision 0 cannot overwrite revision 1."""
    planning = _planning(isolated_butler, _target("M 81"))
    session = _session(planning)
    package = _package(planning)
    planning.sessions.add(session, [package])

    with pytest.raises(PlanRevisionConflictError) as caught:
        planning.sessions.add(session, [package])

    assert caught.value.details["current_revision"] == 1
    assert len(planning.sessions.get("2026-08-10").queue) == 1


def test_the_database_write_refuses_a_revision_that_moved(isolated_butler: DiskButler) -> None:
    """A second write from the same revision stores nothing."""
    planning = _planning(isolated_butler, _target("M 81"))
    session = _session(planning)
    first = session.model_copy(update={"revision": 1, "status_detail": "first"})
    second = session.model_copy(update={"revision": 1, "status_detail": "second"})

    assert isolated_butler.put_session_if_revision(first, 0)
    assert not isolated_butler.put_session_if_revision(second, 0)
    assert planning.sessions.get("2026-08-10").status_detail == "first"
    assert not isolated_butler.put_session_if_revision(first.model_copy(update={"id": "nope"}), 0)


def test_heavy_lists_and_progress_come_only_when_asked_for(isolated_butler: DiskButler) -> None:
    """Stored samples stay out of a plain read and survive a write."""
    from wayfindinglib.models.session.observation_session import WeatherSample

    planning = _planning(isolated_butler, _target("M 81"))
    session = _session(planning)
    stored = isolated_butler.get("observation_session", {"session_id": session.id})
    stored.weather_samples = [WeatherSample(time=1.0)]
    isolated_butler.put(stored, "observation_session", {"session_id": session.id})

    plain = planning.sessions.get("2026-08-10")
    assert plain.weather_samples == []
    assert plain.progress is None
    assert "progress" not in plain.model_dump(by_alias=True)
    changed = planning.sessions.add(plain, [_package(planning)])
    full = planning.sessions.get("2026-08-10", include=["weather_samples", "progress"])
    assert len(full.weather_samples) == 1
    assert full.progress is not None
    assert full.progress.entries[0].entry_id == changed.queue[0].id
    with pytest.raises(InvalidArgumentError):
        planning.sessions.get("2026-08-10", include=["everything"])


def test_a_session_stored_before_the_night_plan_fields_still_loads(isolated_butler: DiskButler) -> None:
    """Old stored JSON lacks the checklist, framing and revision fields."""
    import json
    import sqlite3

    planning = _planning(isolated_butler, _target("M 81"))
    package = _package(planning)
    planning.sessions.add(_session(planning), [package])
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
                    for key in ("gain", "offset", "binning"):
                        request.pop(key, None)
                connection.execute(updates[table], (json.dumps(data), row_id))
        connection.commit()
    finally:
        connection.close()

    loaded = planning.sessions.get("2026-08-10")
    assert (loaded.checklist, loaded.framing, loaded.revision) == ([], None, 0)
    assert loaded.queue[0].exposure_requests[0].gain is None
    assert planning.sessions.add(loaded, [package]).revision == 1


def test_exact_duplicates_merge_and_conflicting_ones_are_refused(isolated_butler: DiskButler) -> None:
    """Identical requests add counts; a delay-only difference conflicts."""
    planning = _planning(isolated_butler, _target("M 81"))
    session = _session(planning)
    package = _package(planning)
    light = _rows(count=5)[0]

    changed = planning.sessions.add(
        session,
        [
            QueueRequest(
                package_id=package.id, exposure_requests=[light, light.model_copy(update={"count": 3})]
            )
        ],
    )
    assert [request.count for request in changed.queue[0].exposure_requests] == [8]
    with pytest.raises(InvalidArgumentError, match="delays"):
        planning.sessions.add(
            changed,
            [
                QueueRequest(
                    package_id=package.id,
                    exposure_requests=[light, light.model_copy(update={"delay_s": 2.0})],
                )
            ],
        )


def test_a_measured_camera_refuses_an_unmeasured_setting(
    isolated_butler: DiskButler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gain setting the profile never measured is refused."""
    planning = _planning(isolated_butler, _target("M 81"))
    monkeypatch.setattr(
        planning._context.astrometrics.calibration, "get_profile", lambda camera=None: _measured_profile()
    )
    session = _session(planning)
    package = _package(planning)
    good = _rows(gain=100, offset=10)
    bad = _rows(gain=250, offset=10)

    changed = planning.sessions.add(session, [QueueRequest(package_id=package.id, exposure_requests=good)])
    with pytest.raises(InvalidArgumentError, match="gain setting 100"):
        planning.sessions.add(changed, [QueueRequest(package_id=package.id, exposure_requests=bad)])
    assert len(planning.sessions.get("2026-08-10").queue) == 1


def test_an_unprofiled_camera_accepts_the_setting(isolated_butler: DiskButler) -> None:
    """Camera c1 has no profile, so any setting is accepted."""
    planning = _planning(isolated_butler, _target("M 81"))
    package = _package(planning)

    changed = planning.sessions.add(
        _session(planning), [QueueRequest(package_id=package.id, exposure_requests=_rows(gain=321))]
    )

    assert changed.queue[0].exposure_requests[0].gain == 321


def test_update_exposures_changes_a_pending_entry_only(isolated_butler: DiskButler) -> None:
    """New exposures replace a pending entry's; an unknown entry fails."""
    planning = _planning(isolated_butler, _target("M 81"))
    filled = planning.sessions.add(_session(planning), [_package(planning)])
    entry_id = filled.queue[0].id

    changed = planning.sessions.update(filled, exposures={entry_id: _rows(exposure_s=30.0, count=4)})

    assert changed.queue[0].exposure_requests[0].exposure_s == pytest.approx(30.0)
    with pytest.raises(NotFoundError):
        planning.sessions.update(changed, exposures={"missing": []})


def test_checklist_items_are_added_once_ticked_and_removed(isolated_butler: DiskButler) -> None:
    """A rule and setting are added once; ticks keep their history."""
    from datetime import UTC, datetime

    planning = _planning(isolated_butler, _target("M 81"))
    session = _session(planning)
    item = ChecklistItem(rule="refocus_when_temperature_drifts")

    first = planning.sessions.update(session, checklist=[item])
    again = planning.sessions.update(first, checklist=[*first.checklist, ChecklistItem(rule=item.rule)])
    assert again.revision == first.revision
    assert len(again.checklist) == 1

    def ticked(session: ObservationSession, hour: int, note: str) -> list[ChecklistItem]:
        """Give the session's checklist with its one item ticked.

        Parameters
        ----------
        session : `ObservationSession`
            The session as last read.
        hour : `int`
            The UTC hour of the tick.
        note : `str`
            The note to keep.

        Returns
        -------
        checklist : `list` [`ChecklistItem`]
            The new checklist.
        """
        moment = datetime(2026, 8, 11, hour, tzinfo=UTC)
        return [session.checklist[0].model_copy(update={"ticked_at": moment, "note": note})]

    once = planning.sessions.update(again, checklist=ticked(again, 3, "did it"))
    twice = planning.sessions.update(once, checklist=ticked(once, 4, "did it"))

    assert once.revision == again.revision + 1
    assert once.checklist[0].ticked_at is not None
    assert len(twice.checklist[0].tick_history) == 2
    assert twice.checklist[0].note == "did it"
    with pytest.raises(PlanRevisionConflictError):
        planning.sessions.update(once, checklist=[])
    assert planning.sessions.update(twice, checklist=[]).checklist == []


def test_framing_is_stored_and_the_sequence_summary_follows_ekos_order(isolated_butler: DiskButler) -> None:
    """Framing round trips; summary rows follow the Ekos Capture order."""
    planning = _planning(isolated_butler, _target("M 81"))
    target = planning._context.astrometrics.targets.get("M 81")
    package = planning.packages.create(
        target,
        _rows(count=12, gain=100, offset=10, binning=1),
        dither_config=DitherConfig(enabled=True, every_n_frames=4, pixels=5.0),
    )
    panels = planning.sky.get_panels(_point(150.0, 30.0), 1, 2, overlap_pct=10.0, rotation_deg=45.0)
    filled = planning.sessions.add(_session(planning), [package])
    changed = planning.sessions.update(
        filled, framing=Framing(center_ra_deg=150.0, center_dec_deg=30.0, rotation_deg=45.0, panels=panels)
    )

    stored = planning.sessions.get("2026-08-10")
    assert stored.framing.rotation_deg == pytest.approx(45.0)
    assert len(stored.framing.panels) == 2
    summary = SequenceSummary.from_session(changed)
    assert [field.name for field in summary.rows[0].fields] == [
        "filter",
        "exposure_s",
        "count",
        "binning",
        "gain",
        "offset",
    ]
    assert [field.value for field in summary.rows[0].fields][1:] == [60.0, 12, 1, 100, 10]
    assert summary.revision == stored.revision
    assert summary.dither_rows[0].size_pixels == pytest.approx(5.0)
    assert "Guide" in summary.dither_rows[0].size_ekos_screen
    assert "Capture" in summary.dither_rows[0].frequency_ekos_screen


@pytest.mark.parametrize(
    ("latitude", "longitude", "ra_deg", "dec_deg", "expected_rise"),
    [
        (45.76, -110.74, 101.3, -16.7, None),
        (45.76, -110.74, 37.9, 89.3, "Circumpolar"),
        (45.76, -110.74, 187.0, -75.0, "Never Rises"),
        (-33.9, 18.4, 101.3, -16.7, None),
    ],
)
def test_get_visibility_for_one_moment_matches_a_direct_altitude_calculation(
    isolated_butler: DiskButler,
    latitude: float,
    longitude: float,
    ra_deg: float,
    dec_deg: float,
    expected_rise: str | None,
) -> None:
    """Altitude and azimuth equal a direct astropy transform."""
    import astropy.units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord
    from astropy.time import Time

    planning = _planning(isolated_butler)
    engine = planning._context.sky_engine
    engine.latitude, engine.longitude = latitude, longitude
    engine.location = EarthLocation(
        lat=latitude * u.deg, lon=longitude * u.deg, height=engine.elevation * u.m
    )
    moment = "2026-11-07T23:02:09Z"
    entry = planning.sky.get_visibility([_point(ra_deg, dec_deg)], time=moment, include=["meridian"]).objects[
        0
    ]
    expected = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg).transform_to(
        AltAz(obstime=Time(moment), location=engine.location)
    )
    assert entry.altitude_deg == pytest.approx(expected.alt.deg, abs=1e-3)
    assert entry.azimuth_deg == pytest.approx(expected.az.deg, abs=1e-3)
    if expected_rise is not None:
        assert entry.rise_utc == expected_rise
        assert entry.set_utc == expected_rise
