"""Purpose: Unit tests for how StellarService answers the app's star requests.

Description: The rules for which stars are shown, how they are sorted, and
how the sky map is filled live in the libraries and are tested there
(`astrometricslib/api/test/test_star_query_display.py` and
`wayfindinglib/test/test_sky_sources.py`). These tests check what the
backend adds: the app's request arguments become the right library
arguments, the replies keep the keys the app reads, the slow whole-catalog
answers are cached until the catalog changes, and period searches are
limited to two at a time.
"""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from astrometricslib import StellarObject
from astrometricslib._internal.catalog import SpectralClassCount, TargetStarCount
from astrometricslib._internal.results import SpectroscopyResult
from astrometricslib.arguments import Page
from backend.services.data import stellar_service as stellar_service_module
from backend.services.data.stellar_service import DEEP_CATALOG_INSTALL_COMMAND, StellarService
from wayfindinglib._internal.planning import DeepCatalogStatus, SkySource

_ONE_CLASS = [SpectralClassCount(spectral_class="G", label="Sun-like", count=2)]
"""A non-empty class count, since an empty answer is never cached."""


@pytest.fixture(autouse=True)
def catalog_version(mocker: MockerFixture) -> list[int]:
    """Stand in for the library's star write counter.

    Returns
    -------
    version : `list` [`int`]
        A one-item list; change its item to make a write happen.
    """
    version = [1]
    mocker.patch.object(
        stellar_service_module,
        "get_library_data_version",
        side_effect=lambda *_args, **_kwargs: (("stars", version[0]),),
    )
    return version


def _service() -> StellarService:
    """Build a service over mock libraries.

    Returns
    -------
    service : `StellarService`
        A service whose ``astrometrics`` and ``wayfinder`` are mocks.
    """
    return StellarService(config=MagicMock(), astrometrics=MagicMock(), wayfinder=MagicMock())


def _page(*stars: StellarObject, total: int | None = None) -> Page[StellarObject]:
    """Wrap stars in a page as `StellarCatalog.query` returns them.

    Returns
    -------
    page : `Page` [`StellarObject`]
        The stars, with ``total`` matches in all (their count by default).
    """
    return Page[StellarObject](items=stars, total=len(stars) if total is None else total)


def _summary(star_id: str, **fields: object) -> dict[str, object]:
    """Give the summary row the app reads for a star with these fields.

    Returns
    -------
    row : `dict`
        The row `StellarService` builds.
    """
    return stellar_service_module._summary_row(StellarObject(id=star_id, **fields))


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({}, {"order_by": "id", "has_spectra": None, "has_photometry": None, "text": None, "target": None}),
        ({"filter_type": "With Spectra"}, {"order_by": "useful", "has_spectra": True}),
        ({"filter_type": "photometry"}, {"order_by": "useful", "has_photometry": True}),
        ({"search": "  vega "}, {"order_by": "useful", "text": "vega"}),
        ({"search": "   "}, {"order_by": "id", "text": None}),
    ],
)
def test_the_star_list_maps_the_app_arguments_to_the_library(arguments: dict, expected: dict) -> None:
    """Search and filter become library filters; rows keep the app keys."""
    service = _service()
    service.astrometrics.stars.query.return_value = _page(StellarObject(id="HD 1", name="One"))

    listed = service.get_displayable_stellar_object_summaries(**arguments)

    assert listed == [_summary("HD 1", name="One")]
    assert set(listed[0]) == {
        "id", "name", "ra", "dec", "targetIds", "hasSpectra", "hasPhotometry", "magnitude",
        "hasCatalogMagnitude", "spectralType",
    }  # fmt: skip
    call = service.astrometrics.stars.query.call_args.kwargs
    assert (call["limit"], call["offset"]) == (100, 0)
    for name, value in expected.items():
        assert call[name] == value


def test_a_target_scope_is_read_as_the_target_object() -> None:
    """The target id becomes a Target; an unknown one lists nothing."""
    service = _service()
    target = MagicMock()
    service.astrometrics.targets.get.return_value = target
    service.astrometrics.stars.query.return_value = _page()

    service.get_displayable_stellar_object_summaries(target_id="M 13")

    service.astrometrics.targets.get.assert_called_once_with("M 13")
    call = service.astrometrics.stars.query.call_args.kwargs
    assert (call["target"], call["order_by"]) == (target, "useful")

    service.astrometrics.targets.get.return_value = None
    assert service.get_displayable_stellar_object_summaries(target_id="Nowhere") == []
    assert service.count_displayable_stellar_objects(target_id="Nowhere") == 0


def test_the_star_list_pages_and_caps_a_limit_of_zero() -> None:
    """Offset passes through; a limit of zero asks for the largest page."""
    service = _service()
    service.astrometrics.stars.query.return_value = _page()

    service.get_displayable_stellar_object_summaries(limit=0, offset=200)

    call = service.astrometrics.stars.query.call_args.kwargs
    assert (call["limit"], call["offset"]) == (500, 200)


def test_the_count_is_the_library_total() -> None:
    """The count reads the total of a one-star page, not every row."""
    service = _service()
    service.astrometrics.stars.query.return_value = _page(StellarObject(id="HD 1"), total=42)
    service.astrometrics.targets.get.return_value = MagicMock()

    assert service.count_displayable_stellar_objects(target_id="M 13", filter_type="spectra") == 42
    call = service.astrometrics.stars.query.call_args.kwargs
    assert (call["limit"], call["has_spectra"]) == (1, True)


def test_target_availability_uses_the_app_keys() -> None:
    """Per-target counts reach the app under its keys, by target id."""
    service = _service()
    service.astrometrics.stars.count_by_target.return_value = Page[TargetStarCount].of([
        TargetStarCount(target_id="M 13", star_count=3, has_spectra=True, has_photometry=False)
    ])

    assert service.get_target_data_availability() == {
        "M 13": {"starCount": 3, "hasSpectra": True, "hasPhotometry": False}
    }
    service.astrometrics.stars.count_by_target.assert_called_once_with()


def test_the_class_star_colors_list_every_classed_star_and_our_own_type() -> None:
    """Every star in a class list or with a spectrum gets its facts."""
    service = _service()
    service.astrometrics.stars.count_by_class.return_value = Page[SpectralClassCount].of([
        SpectralClassCount(spectral_class="A", label="White", count=1),
        SpectralClassCount(spectral_class="C", label="Carbon", count=1),
    ])
    vega = StellarObject(
        id="Vega",
        spectral_type="A0V",
        b_minus_v=0.004,
        spectroscopy=SpectroscopyResult(
            wavelengths_angstrom=[5000.0], intensities=[1.0], self_determined_spectral_type="A1V"
        ),
    )
    carbon = StellarObject(id="Case 36", spectral_type="C5:,4", b_minus_v=2.0)
    service.astrometrics.stars.query.side_effect = [_page(vega), _page(carbon), _page(vega)]

    colors = service.get_class_star_colors()

    assert [(row["id"], row["ownSpectralType"], row["bMinusV"]) for row in colors] == [
        ("Vega", "A1V", 0.0),
        ("Case 36", "", None),
    ]
    assert colors[0]["spectralType"] == "A0V"
    calls = [call.kwargs for call in service.astrometrics.stars.query.call_args_list]
    assert calls == [{"spectral_class": "A"}, {"spectral_class": "C"}, {"has_spectra": True}]


def test_the_class_summary_and_a_class_listing_come_from_the_library() -> None:
    """Class counts and one class's stars come from the library."""
    service = _service()
    service.astrometrics.stars.count_by_class.return_value = Page[SpectralClassCount].of([
        SpectralClassCount(spectral_class="G", label="Yellow dwarfs", count=2)
    ])
    matched = StellarObject(
        id="HD 1",
        spectroscopy=SpectroscopyResult(
            wavelengths_angstrom=[5000.0], intensities=[1.0], self_determined_spectral_type_rms=0.02
        ),
    )
    service.astrometrics.stars.query.return_value = _page(matched, StellarObject(id="HD 2"))

    assert service.get_spectral_class_summary() == [
        {"spectralClass": "G", "label": "Yellow dwarfs", "count": 2}
    ]
    rows = service.get_stars_by_spectral_class("G2V")
    assert [(row["id"], row["selfDeterminedSpectralTypeRms"]) for row in rows] == [
        ("HD 1", 0.02),
        ("HD 2", None),
    ]
    assert service.astrometrics.stars.query.call_args.kwargs == {"spectral_class": "G2V", "order_by": "match"}


def test_whole_catalog_answers_are_cached_until_the_version_changes(catalog_version: list[int]) -> None:
    """A repeat is served from memory; a catalog write forces a new answer."""
    service = _service()
    service.astrometrics.stars.count_by_class.return_value = _ONE_CLASS

    service.get_spectral_class_summary()
    service.get_spectral_class_summary()
    assert service.astrometrics.stars.count_by_class.call_count == 1

    catalog_version[0] = 2
    service.get_spectral_class_summary()
    assert service.astrometrics.stars.count_by_class.call_count == 2


def test_an_unreadable_version_is_never_served_from_memory(mocker: MockerFixture) -> None:
    """With no readable write counter, every request asks the library."""
    mocker.patch.object(stellar_service_module, "get_library_data_version", return_value=None)
    service = _service()
    service.astrometrics.stars.count_by_class.return_value = _ONE_CLASS

    service.get_spectral_class_summary()
    service.get_spectral_class_summary()

    assert service.astrometrics.stars.count_by_class.call_count == 2


def test_warming_the_cache_answers_the_first_requests() -> None:
    """After warm-up, target and class counts cost no library call."""
    service = _service()
    service.astrometrics.stars.count_by_target.return_value = [
        TargetStarCount(target_id="M 13", star_count=3)
    ]
    service.astrometrics.stars.count_by_class.return_value = _ONE_CLASS

    service.warm_catalog_summary_cache()
    service.get_target_data_availability()
    service.get_spectral_class_summary()

    assert service.astrometrics.stars.count_by_target.call_count == 1
    assert service.astrometrics.stars.count_by_class.call_count == 1


def test_an_empty_answer_is_not_kept_or_announced() -> None:
    """An empty catalog at start-up must not hide data that arrives later."""
    service = _service()
    socket_manager = MagicMock()
    service.set_socket_manager(socket_manager)
    service.astrometrics.stars.count_by_target.return_value = []

    assert service.get_target_data_availability() == {}
    socket_manager.broadcast_ui_event_sync.assert_not_called()

    service.astrometrics.stars.count_by_target.return_value = [
        TargetStarCount(target_id="M 13", star_count=3)
    ]
    assert service.get_target_data_availability()["M 13"]["starCount"] == 3
    socket_manager.broadcast_ui_event_sync.assert_called_once()


def test_simultaneous_requests_for_one_answer_share_one_scan() -> None:
    """A request that arrives mid-scan waits instead of starting a second."""
    service = _service()
    scan_started = threading.Event()
    release_scan = threading.Event()

    def slow_count(**_: object) -> list[SpectralClassCount]:
        """Hold the scan open until the test lets it finish.

        Returns
        -------
        classes : `list` [`SpectralClassCount`]
            One class count.
        """
        scan_started.set()
        release_scan.wait(timeout=5)
        return _ONE_CLASS

    service.astrometrics.stars.count_by_class.side_effect = slow_count
    answers: list[object] = []
    first = threading.Thread(target=lambda: answers.append(service.get_spectral_class_summary()))
    second = threading.Thread(target=lambda: answers.append(service.get_spectral_class_summary()))

    first.start()
    assert scan_started.wait(timeout=5)
    second.start()
    release_scan.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert service.astrometrics.stars.count_by_class.call_count == 1
    assert len(answers) == 2


def test_cached_answers_expire_after_the_fallback_window(mocker: MockerFixture) -> None:
    """A write the version counter missed is caught by the time limit."""
    service = _service()
    service.astrometrics.stars.count_by_class.return_value = _ONE_CLASS
    fake_time = [1000.0]
    mocker.patch.object(stellar_service_module.time, "monotonic", side_effect=lambda: fake_time[0])

    service.get_spectral_class_summary()
    fake_time[0] += stellar_service_module._CATALOG_ANSWER_CACHE_FALLBACK_MAX_AGE_SECONDS + 1
    service.get_spectral_class_summary()

    assert service.astrometrics.stars.count_by_class.call_count == 2


def test_a_new_catalog_version_is_announced_once() -> None:
    """The first answer for a new version tells the app; cache hits do not."""
    service = _service()
    service.astrometrics.stars.count_by_class.return_value = _ONE_CLASS
    service.astrometrics.stars.count_by_target.return_value = [
        TargetStarCount(target_id="M 13", star_count=3)
    ]
    socket_manager = MagicMock()
    service.set_socket_manager(socket_manager)

    service.get_spectral_class_summary()
    service.get_target_data_availability()
    service.get_spectral_class_summary()

    socket_manager.broadcast_ui_event_sync.assert_called_once_with(
        "catalog:changed", {"dataset": "stellar_catalog"}
    )


def _sky_source(source_id: str, kind: str = "star") -> SkySource:
    """Build one sky source.

    Returns
    -------
    source : `SkySource`
        A source at RA 10, Dec 20.
    """
    return SkySource(id=source_id, ra=10.0, dec=20.0, name=source_id, common_name=source_id, type=kind)


def _found(sources: list[SkySource]) -> SimpleNamespace:
    """Wrap sources the way `query_sources` returns them.

    Returns
    -------
    result : `types.SimpleNamespace`
        A stand-in result with the ``sources`` the service reads.
    """
    return SimpleNamespace(sources=sources, stars_truncated=False, failed_catalogs=[])


def test_sky_map_sources_pass_the_limits_and_use_the_planetarium_keys() -> None:
    """The map's limits reach the library, and replies use camelCase keys."""
    service = _service()
    service.wayfinder.planning.query_sources.return_value = _found([_sky_source("Vega")])

    (source,) = service.get_sources(
        ra=10.0, dec=20.0, radius=2.0, limiting_magnitude=6.0, include_stars_without_catalog_magnitude=False
    )

    service.wayfinder.planning.query_sources.assert_called_once_with(
        10.0,
        20.0,
        2.0,
        source=["targets", "stars"],
        magnitude_max=6.0,
        include_stars_without_catalog_magnitude=False,
        limit=None,
    )
    assert source["id"] == "Vega"
    assert source["global"] is False
    assert "hasSpectra" in source and "has_spectra" not in source


def test_sky_map_sources_with_the_global_catalog_ask_for_online_objects() -> None:
    """include_catalog adds the online section."""
    service = _service()
    service.wayfinder.planning.query_sources.return_value = _found([])

    service.get_sources(ra=10.0, dec=20.0, radius=2.0, include_catalog=True)

    assert service.wayfinder.planning.query_sources.call_args.kwargs["source"] == [
        "targets",
        "stars",
        "simbad",
    ]


def test_planetarium_targets_are_every_library_target() -> None:
    """The targets layer asks for targets only, over the whole sky."""
    service = _service()
    service.wayfinder.planning.query_sources.return_value = _found([_sky_source("M 13", "target")])

    targets = service.get_planetarium_targets()

    service.wayfinder.planning.query_sources.assert_called_once_with(
        0.0, 0.0, 180.0, source=["targets"], limit=None
    )
    assert targets[0]["id"] == "M 13"
    assert targets[0]["type"] == "target"


def test_planetarium_targets_carry_the_catalog_size() -> None:
    """A target's catalog major axis reaches the map."""
    service = _service()
    galaxy = _sky_source("M 81", "target").model_copy(update={"major_axis_arcmin": 21.38})
    service.wayfinder.planning.query_sources.return_value = _found([galaxy, _sky_source("Vega", "target")])

    galaxy_row, star_row = service.get_planetarium_targets()

    assert galaxy_row["majorAxisArcmin"] == pytest.approx(21.38)
    assert star_row["majorAxisArcmin"] is None


def test_online_catalog_sources_pass_the_limiting_magnitude_to_the_drivers() -> None:
    """The map's limit travels to the drivers; replies name the driver."""
    service = _service()
    online = _sky_source("Gaia 1").model_copy(update={"is_global": True, "catalog_source": "deep_stars"})
    service.wayfinder.planning.query_sources.return_value = _found([online])

    answer = service.get_online_catalog_sources(
        ra=250.0, dec=36.0, radius=2.0, enabled_drivers=["deep_stars"], limiting_magnitude=14.0
    )
    (source,) = answer["sources"]
    assert answer["starsTruncated"] is False
    assert answer["failedCatalogs"] == []

    service.wayfinder.planning.query_sources.assert_called_once_with(
        ra_deg=250.0,
        dec_deg=36.0,
        radius_deg=2.0,
        source=["catalogs"],
        catalog_drivers=["deep_stars"],
        magnitude_max=14.0,
        limit=None,
    )
    assert source["catalogSource"] == "deep_stars"
    assert source["global"] is True


def test_online_catalog_sources_report_when_a_driver_cut_the_answer_short() -> None:
    """The map is told when the Gaia cap dropped stars."""
    service = _service()
    online = _sky_source("Gaia 1").model_copy(update={"is_global": True, "catalog_source": "deep_stars"})
    service.wayfinder.planning.query_sources.return_value = SimpleNamespace(
        sources=[online], stars_truncated=True, failed_catalogs=[]
    )

    answer = service.get_online_catalog_sources(
        ra=250.0, dec=36.0, radius=2.0, enabled_drivers=["deep_stars"], limiting_magnitude=14.0
    )

    assert answer["starsTruncated"] is True


def test_online_catalog_sources_name_the_drivers_that_failed() -> None:
    """The map is told which catalog could not answer."""
    service = _service()
    service.wayfinder.planning.query_sources.return_value = SimpleNamespace(
        sources=[], stars_truncated=False, failed_catalogs=["deep_stars"]
    )

    answer = service.get_online_catalog_sources(
        ra=250.0, dec=36.0, radius=2.0, enabled_drivers=["deep_stars"], limiting_magnitude=14.0
    )

    assert answer["sources"] == []
    assert answer["failedCatalogs"] == ["deep_stars"]


def test_deep_catalog_status_adds_the_install_command() -> None:
    """The status the UI sees names the command that downloads the catalog."""
    service = _service()
    service.wayfinder.planning.get_deep_catalog_status.return_value = DeepCatalogStatus(
        installed=False, complete=False, star_count=0, pixels_downloaded=0, size_megabytes=0.0
    )

    status = service.deep_catalog_status()

    assert status["installed"] is False
    assert status["installCommand"] == DEEP_CATALOG_INSTALL_COMMAND
    assert "build_deep_star_catalog" in status["installCommand"]


def test_star_records_serialize_their_data_flags_for_the_app() -> None:
    """The data flags of a star record reach the RPC reply."""
    from backend.services.rpc_protocol import serialize_rpc_result

    star = StellarObject(
        id="Vega",
        magnitude=0.03,
        spectroscopy=SpectroscopyResult(wavelengths_angstrom=[5000], intensities=[1.0]),
    )

    reply = serialize_rpc_result(star)

    assert reply["hasSpectra"] is True
    assert reply["hasPhotometry"] is False
    assert reply["hasCatalogMagnitude"] is True
    assert reply["plotData"] == {"wavelengths": [5000.0], "intensities": [1.0]}


def test_analyze_periodicity_records_the_search_on_the_star() -> None:
    """The RPC reads the star and records its search; unknown gives None."""
    service = _service()
    star = service.astrometrics.stars.get.return_value

    result = service.analyze_periodicity("Gaia DR3 1")

    service.astrometrics.stars.get.assert_called_once_with("Gaia DR3 1")
    service.astrometrics.stars.record_periodicity.assert_called_once_with(star)
    assert result is service.astrometrics.stars.record_periodicity.return_value

    service.astrometrics.stars.get.return_value = None
    assert service.analyze_periodicity("missing") is None


@pytest.mark.anyio
async def test_rpc_registry_routes_analyze_periodicity_to_the_stellar_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """astronomy:analyze_periodicity reaches the stellar service."""
    from backend.routers import rpc_router

    stellar_service = MagicMock()
    monkeypatch.setattr(rpc_router.container, "stellar_service", stellar_service, raising=False)

    registry = rpc_router.RPCHandlerRegistry()
    result = await registry.execute("astronomy:analyze_periodicity", {"star_id": "Gaia DR3 1"})

    stellar_service.analyze_periodicity.assert_called_once_with(star_id="Gaia DR3 1")
    assert result is stellar_service.analyze_periodicity.return_value


def test_period_searches_are_limited_to_two_at_a_time() -> None:
    """A third search waits until one of the first two finishes."""
    running = 0
    peak = 0
    lock = threading.Lock()
    release = threading.Event()

    def slow_search(_star: object) -> None:
        """Hold a search slot until released, recording the peak count."""
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        release.wait(timeout=2.0)
        with lock:
            running -= 1

    service = _service()
    service.astrometrics.stars.record_periodicity.side_effect = slow_search
    threads = [threading.Thread(target=service.analyze_periodicity, args=(f"star {i}",)) for i in range(5)]
    for thread in threads:
        thread.start()
    threading.Event().wait(0.3)
    assert peak == 2
    release.set()
    for thread in threads:
        thread.join(timeout=5.0)
    assert peak == 2
