"""Tests the one catalog rule: stored first, live second, stale on outage.

Every test points the cache folder at its own temporary folder, so no test
reads or writes the real library's stored answers.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from astropy.table import Table

from astrometricslib._internal.drivers.cached_simbad_driver import CachedSimbadDriver
from astrometricslib._internal.drivers.catalog_answer_store import write_stored_answer
from astrometricslib._internal.drivers.catalog_cache import (
    CatalogUnavailableError,
    answer_through_cache,
    catalog_use_scope,
)
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.photometry.post_processing.known_variability_labels import (
    is_listed_as_variable,
)
from astrometricslib.pipelines.shared import variable_star_catalogs as variable_catalogs
from astrometricslib.pipelines.shared.stored_variability import fill_known_variability
from astrometricslib.test.synthetic.fake_simbad import FakeSimbad


def counting_simbad() -> FakeSimbad:
    """Build a SIMBAD stand-in with fixed answers that counts live calls.

    Set its ``failure`` to take it down; its ``call_count`` counts the
    questions that reached it.

    Returns
    -------
    simbad : `FakeSimbad`
        One fixed star for a region, one fixed row for any name, one row for
        a table question.
    """
    return FakeSimbad(
        region=lambda coordinates, radius: Table({
            "main_id": ["* bet Per"],
            "ra": [51.08],
            "dec": [49.86],
            "otypes": ["EB*|V*"],
        }),
        objects=lambda name: Table({"main_id": [name], "ra": [10.0], "dec": [20.0]}),
        tap=lambda query: Table({"oid": [1]}),
    )


def test_fresh_answer_is_used_without_a_live_call() -> None:
    """A stored answer inside its lifetime is read and nothing is asked."""
    live = []
    write_stored_answer("SIMBAD", "q", Table({"a": [1]}), "SIMBAD")
    with catalog_use_scope() as log:
        table = answer_through_cache("SIMBAD", "q", 10, lambda: live.append(1))
    assert table["a"][0] == 1
    assert not live
    assert log.live_calls == 0


def test_stale_answer_is_refreshed() -> None:
    """An answer older than its lifetime is asked again and replaced."""
    old = datetime.now(UTC) - timedelta(days=30)
    write_stored_answer("SIMBAD", "q", Table({"a": [1]}), "SIMBAD", fetched_at=old)
    with catalog_use_scope() as log:
        table = answer_through_cache("SIMBAD", "q", 10, lambda: Table({"a": [2]}))
    assert table["a"][0] == 2
    assert log.live_calls == 1
    with catalog_use_scope() as second:
        assert answer_through_cache("SIMBAD", "q", 10, lambda: None)["a"][0] == 2
    assert second.live_calls == 0


def test_outage_falls_back_to_stale_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    """A failed live question uses the old answer and names its date."""
    old = datetime.now(UTC) - timedelta(days=30)
    write_stored_answer("SIMBAD", "q", Table({"a": [1]}), "SIMBAD", fetched_at=old)

    def down() -> None:
        """Fail like an unreachable service.

        Raises
        ------
        ExternalServiceError
            Always.
        """
        raise ExternalServiceError("down")

    with catalog_use_scope() as log, caplog.at_level("WARNING"):
        table = answer_through_cache("SIMBAD", "q", 10, down)
    assert table["a"][0] == 1
    assert f"used stored results from {old:%Y-%m-%d}" in caplog.text
    assert all(record.levelname == "WARNING" for record in caplog.records)
    assert log.warnings() == [f"SIMBAD could not be reached; used stored results from {old:%Y-%m-%d}"]


def test_outage_with_nothing_stored_is_unavailable() -> None:
    """No stored answer and no service raises the plain status error."""

    def down() -> None:
        """Fail like an unreachable service.

        Raises
        ------
        OSError
            Always.
        """
        raise OSError("no network")

    with catalog_use_scope() as log, pytest.raises(CatalogUnavailableError, match="no stored results"):
        answer_through_cache("SIMBAD", "q", 10, down)
    assert log.count("unavailable") == 1


def test_cached_simbad_region_second_call_uses_no_network() -> None:
    """A region is asked once per tile; asking again reads the store."""
    inner = counting_simbad()
    driver = CachedSimbadDriver(inner)
    from astropy.coordinates import SkyCoord

    center = SkyCoord(51.08, 49.86, unit="deg")
    first = driver.query_region(center, "0.05d", votable_fields=("otypes",))
    assert driver.query_object("Algol")["ra"][0] == pytest.approx(10.0)
    calls = inner.call_count
    assert calls >= 1
    inner.failure = ExternalServiceError("down")
    second = driver.query_region(center, "0.05d", votable_fields=("otypes",))
    assert inner.call_count == calls
    assert first is not None and second is not None
    assert driver.query_object("algol")["ra"][0] == pytest.approx(10.0)
    assert inner.call_count == calls


def test_cached_simbad_region_outage_without_store_raises() -> None:
    """A field never stored and SIMBAD down raises the plain status error."""
    inner = counting_simbad()
    inner.failure = ExternalServiceError("down")
    from astropy.coordinates import SkyCoord

    with pytest.raises(CatalogUnavailableError):
        CachedSimbadDriver(inner).query_region(SkyCoord(1, 2, unit="deg"), "0.1d")


def test_comparison_stars_listed_by_vsx_are_excluded_when_simbad_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A VSX-listed star is marked variable from stored tiles, SIMBAD down."""
    vsx_row = Table({
        "Name": ["V1"],
        "RAJ2000": [30.0],
        "DEJ2000": [40.0],
        "Type": ["EA"],
        "max": [9.0],
        "min": [10.0],
        "f_min": [""],
        "n_max": ["V"],
        "n_min": ["V"],
        "Period": [2.0],
    })
    asked = []

    def ask(
        catalog: str, columns: list[str], ra: float, dec: float, radius: float, filters: Any = None
    ) -> Table:
        """Answer VSX with one variable, and the Gaia table with none.

        Returns
        -------
        table : `astropy.table.Table`
            The rows.
        """
        asked.append(catalog)
        if catalog == variable_catalogs.VSX_CATALOG:
            return vsx_row
        return Table(names=columns, dtype=[object] * len(columns))

    monkeypatch.setattr(variable_catalogs, "_ask_vizier_circle", ask)
    simbad = counting_simbad()
    simbad.failure = ExternalServiceError("down")
    stars = [StellarObject(id="V1", ra=30.0, declination=40.0)]
    fill_known_variability(stars, CachedSimbadDriver(simbad))
    assert is_listed_as_variable(stars[0])
    live_before = len(asked)
    again = [StellarObject(id="V1", ra=30.0, declination=40.0)]
    fill_known_variability(again, CachedSimbadDriver(simbad))
    assert is_listed_as_variable(again[0])
    assert len(asked) == live_before


def test_star_identifier_simbad_failure_logs_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    """The SIMBAD region failure in the star identifier is not an ERROR."""
    from types import SimpleNamespace

    from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier

    class Down:
        """A SIMBAD driver that cannot be reached."""

        def query_region(self, *args: Any, **kwargs: Any) -> None:
            """Fail like an outage.

            Raises
            ------
            CatalogUnavailableError
                Always.
            """
            raise CatalogUnavailableError("SIMBAD could not be reached; no stored results for this field")

    stand_in = SimpleNamespace(simbad=Down())
    with caplog.at_level("WARNING"):
        result = StarIdentifier._query_simbad_region(stand_in, 10.0, 20.0, None, 0, 0)  # type: ignore[arg-type]
    assert result == (None, None)
    assert caplog.records
    assert all(record.levelname == "WARNING" for record in caplog.records)
