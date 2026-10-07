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
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from astrometricslib import StarQueryResult, StellarObject, TargetStarCount
from backend.services.data import stellar_service as stellar_service_module
from backend.services.data.stellar_service import DEEP_CATALOG_INSTALL_COMMAND, StellarService
from wayfindinglib import DeepCatalogStatus, SkySource


def _service() -> StellarService:
    """Build a service over mock libraries.

    Returns
    -------
    service : `StellarService`
        A service whose ``astrometrics`` and ``wayfinder`` are mocks.
    """
    return StellarService(config=MagicMock(), astrometrics=MagicMock(), wayfinder=MagicMock())


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({}, {"order": "id", "has_spectra": None, "has_photometry": None, "search": None}),
        ({"target_id": "M 13"}, {"order": "useful", "target_id": "M 13"}),
        ({"filter_type": "With Spectra"}, {"order": "useful", "has_spectra": True}),
        ({"filter_type": "photometry"}, {"order": "useful", "has_photometry": True}),
        ({"search": "  vega "}, {"order": "useful", "search": "vega"}),
        ({"search": "   "}, {"order": "id", "search": None}),
    ],
)
def test_the_star_list_maps_the_app_arguments_to_the_library(arguments: dict, expected: dict) -> None:
    """Scope, search and filter become library filters and a sort order."""
    service = _service()
    service.astrometrics.stars.query.return_value = StarQueryResult(detail="summary", stars=[{"id": "HD 1"}])

    listed = service.get_displayable_stellar_object_summaries(**arguments)

    assert listed == [{"id": "HD 1"}]
    call = service.astrometrics.stars.query.call_args.kwargs
    assert call["detail"] == "summary"
    assert (call["limit"], call["offset"]) == (100, 0)
    for name, value in expected.items():
        assert call[name] == value


def test_the_star_list_pages_and_lifts_the_limit_for_zero() -> None:
    """Offset passes through, and a limit of zero asks for every match."""
    service = _service()
    service.astrometrics.stars.query.return_value = StarQueryResult(detail="summary", stars=[])

    service.get_displayable_stellar_object_summaries(limit=0, offset=200)

    call = service.astrometrics.stars.query.call_args.kwargs
    assert (call["limit"], call["offset"]) == (None, 200)


def test_the_count_is_the_library_total() -> None:
    """The count reads the total of an ids query, not a page of rows."""
    service = _service()
    service.astrometrics.stars.query.return_value = StarQueryResult(
        detail="ids", ids=["HD 1"], total_matching=42
    )

    assert service.count_displayable_stellar_objects(target_id="M 13", filter_type="spectra") == 42
    call = service.astrometrics.stars.query.call_args.kwargs
    assert (call["detail"], call["limit"], call["has_spectra"]) == ("ids", 1, True)


def test_target_availability_uses_the_app_keys() -> None:
    """Per-target counts reach the app under its camelCase keys."""
    service = _service()
    service.astrometrics.stars.query.return_value = StarQueryResult(
        detail="target_counts",
        target_counts={"M 13": TargetStarCount(star_count=3, has_spectra=True, has_photometry=False)},
    )

    assert service.get_target_data_availability() == {
        "M 13": {"starCount": 3, "hasSpectra": True, "hasPhotometry": False}
    }
    service.astrometrics.stars.query.assert_called_once_with(detail="target_counts")


def test_the_class_summary_and_a_class_listing_come_from_the_library() -> None:
    """Class counts and one class's stars come from library queries."""
    service = _service()
    classes = [{"spectralClass": "G", "label": "Yellow dwarfs", "count": 2}]
    rows = [{"id": "HD 1", "selfDeterminedSpectralTypeRms": 0.02}]
    service.astrometrics.stars.query.side_effect = [
        StarQueryResult(detail="class_counts", classes=classes),
        StarQueryResult(detail="summary", stars=rows),
    ]

    assert service.get_spectral_class_summary() == classes
    assert service.get_stars_by_spectral_class("G2V") == rows
    assert service.astrometrics.stars.query.call_args.kwargs == {
        "spectral_class": "G2V",
        "order": "match",
        "limit": None,
    }


def test_whole_catalog_answers_are_cached_until_the_version_changes() -> None:
    """A repeat is served from memory; a catalog write forces a new answer."""
    service = _service()
    service.astrometrics.stars.query.return_value = StarQueryResult(detail="class_counts", classes=[])
    service.astrometrics.catalog_access.get_dataset_version.return_value = 1

    service.get_spectral_class_summary()
    service.get_spectral_class_summary()
    assert service.astrometrics.stars.query.call_count == 1

    service.astrometrics.catalog_access.get_dataset_version.return_value = 2
    service.get_spectral_class_summary()
    assert service.astrometrics.stars.query.call_count == 2


def test_warming_the_cache_answers_the_first_requests() -> None:
    """After warm-up, target and class counts cost no library call."""
    service = _service()
    service.astrometrics.stars.query.return_value = StarQueryResult(
        detail="target_counts", target_counts={}, classes=[]
    )

    service.warm_catalog_summary_cache()
    calls_after_warmup = service.astrometrics.stars.query.call_count
    service.get_target_data_availability()
    service.get_spectral_class_summary()

    assert calls_after_warmup == 2
    assert service.astrometrics.stars.query.call_count == 2


def test_cached_answers_expire_after_the_fallback_window(mocker: MockerFixture) -> None:
    """A write the version counter missed is caught by the time limit."""
    service = _service()
    service.astrometrics.stars.query.return_value = StarQueryResult(detail="class_counts", classes=[])
    fake_time = [1000.0]
    mocker.patch.object(stellar_service_module.time, "monotonic", side_effect=lambda: fake_time[0])

    service.get_spectral_class_summary()
    fake_time[0] += stellar_service_module._CATALOG_ANSWER_CACHE_FALLBACK_MAX_AGE_SECONDS + 1
    service.get_spectral_class_summary()

    assert service.astrometrics.stars.query.call_count == 2


def test_a_new_catalog_version_is_announced_once() -> None:
    """The first answer for a new version tells the app; cache hits do not."""
    service = _service()
    service.astrometrics.stars.query.return_value = StarQueryResult(
        detail="class_counts", classes=[], target_counts={}
    )
    socket_manager = MagicMock()
    service.set_socket_manager(socket_manager)
    service.astrometrics.catalog_access.get_dataset_version.return_value = 1

    service.get_spectral_class_summary()
    service.get_target_data_availability()
    service.get_spectral_class_summary()

    socket_manager.broadcast_ui_event_sync.assert_called_once_with(
        "catalog:changed", {"dataset": "stellar_catalog"}
    )


def test_full_star_records_include_single_frame_detections() -> None:
    """The rare caller that wants every record gets detections too."""
    service = _service()
    service.astrometrics.stars.query.return_value = StarQueryResult(
        detail="objects", objects=[StellarObject(id="HD 1")]
    )

    assert [star.id for star in service.get_stellar_objects("M 13")] == ["HD 1"]
    service.astrometrics.stars.query.assert_called_once_with(
        target_id="M 13", detail="objects", include_unresolved=True, limit=None
    )


def _sky_source(source_id: str, kind: str = "star") -> SkySource:
    """Build one sky source.

    Returns
    -------
    source : `SkySource`
        A source at RA 10, Dec 20.
    """
    return SkySource(id=source_id, ra=10.0, dec=20.0, name=source_id, common_name=source_id, type=kind)


def test_sky_map_sources_pass_the_limits_and_use_the_planetarium_keys() -> None:
    """The map's limits reach the library, and replies use camelCase keys."""
    service = _service()
    service.wayfinder.planning.get_sources.return_value = [_sky_source("Vega")]

    (source,) = service.get_sources(
        ra=10.0, dec=20.0, radius=2.0, limiting_magnitude=6.0, include_stars_without_catalog_magnitude=False
    )

    service.wayfinder.planning.get_sources.assert_called_once_with(
        10.0,
        20.0,
        2.0,
        include=["stars"],
        limiting_magnitude=6.0,
        include_stars_without_catalog_magnitude=False,
    )
    assert source["id"] == "Vega"
    assert source["global"] is False
    assert "hasSpectra" in source and "has_spectra" not in source


def test_sky_map_sources_with_the_global_catalog_ask_for_online_objects() -> None:
    """include_catalog adds the online section."""
    service = _service()
    service.wayfinder.planning.get_sources.return_value = []

    service.get_sources(ra=10.0, dec=20.0, radius=2.0, include_catalog=True)

    assert service.wayfinder.planning.get_sources.call_args.kwargs["include"] == ["stars", "online"]


def test_planetarium_targets_are_every_library_target() -> None:
    """The targets layer asks for targets only, over the whole sky."""
    service = _service()
    service.wayfinder.planning.get_sources.return_value = [_sky_source("M 13", "target")]

    targets = service.get_planetarium_targets()

    service.wayfinder.planning.get_sources.assert_called_once_with(0.0, 0.0, 180.0, include=[])
    assert targets[0]["id"] == "M 13"
    assert targets[0]["type"] == "target"


def test_online_catalog_sources_pass_the_limiting_magnitude_to_the_drivers() -> None:
    """The map's limit travels to the drivers; replies name the driver."""
    service = _service()
    online = _sky_source("Gaia 1").model_copy(update={"is_global": True, "catalog_source": "deep_stars"})
    service.wayfinder.planning.get_online_catalog_sources.return_value = [online]

    (source,) = service.get_online_catalog_sources(
        ra=250.0, dec=36.0, radius=2.0, enabled_drivers=["deep_stars"], limiting_magnitude=14.0
    )

    service.wayfinder.planning.get_online_catalog_sources.assert_called_once_with(
        ra_deg=250.0, dec_deg=36.0, radius_deg=2.0, enabled_driver_names=["deep_stars"], magnitude_limit=14.0
    )
    assert source["catalogSource"] == "deep_stars"
    assert source["global"] is True


def test_deep_catalog_status_adds_the_install_command() -> None:
    """The status the UI sees names the command that downloads the catalog."""
    service = _service()
    service.wayfinder.planning.deep_catalog_status.return_value = DeepCatalogStatus(
        installed=False, complete=False, star_count=0, pixels_downloaded=0, size_megabytes=0.0
    )

    status = service.deep_catalog_status()

    assert status["installed"] is False
    assert status["installCommand"] == DEEP_CATALOG_INSTALL_COMMAND
    assert "build_deep_star_catalog" in status["installCommand"]


def test_star_records_serialize_their_data_flags_for_the_app() -> None:
    """The data flags of a star record reach the RPC reply."""
    from astrometricslib import SpectroscopyResult
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


def test_analyze_periodicity_delegates_to_the_stars_api() -> None:
    """The RPC method hands the id to the library."""
    service = _service()

    result = service.analyze_periodicity("Gaia DR3 1")

    service.astrometrics.stars.analyze_periodicity.assert_called_once_with("Gaia DR3 1")
    assert result is service.astrometrics.stars.analyze_periodicity.return_value


@pytest.mark.anyio
async def test_rpc_registry_routes_analyze_periodicity_to_the_stellar_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """astronomy:analyze_periodicity reaches the stellar service."""
    from backend.routers import rpc_router

    stellar_service = MagicMock()
    monkeypatch.setattr(rpc_router.container, "stellar_service", stellar_service, raising=False)

    registry = rpc_router.RPCHandlerRegistry()
    result = await registry.execute("astronomy:analyze_periodicity", {"object_id": "Gaia DR3 1"})

    stellar_service.analyze_periodicity.assert_called_once_with(object_id="Gaia DR3 1")
    assert result is stellar_service.analyze_periodicity.return_value


def test_period_searches_are_limited_to_two_at_a_time() -> None:
    """A third search waits until one of the first two finishes."""
    running = 0
    peak = 0
    lock = threading.Lock()
    release = threading.Event()

    def slow_search(_object_id: str) -> None:
        """Hold a search slot until released, recording the peak count."""
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        release.wait(timeout=2.0)
        with lock:
            running -= 1

    service = _service()
    service.astrometrics.stars.analyze_periodicity.side_effect = slow_search
    threads = [threading.Thread(target=service.analyze_periodicity, args=(f"star {i}",)) for i in range(5)]
    for thread in threads:
        thread.start()
    threading.Event().wait(0.3)
    assert peak == 2
    release.set()
    for thread in threads:
        thread.join(timeout=5.0)
    assert peak == 2
