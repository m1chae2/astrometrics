"""Purpose: Tests for the Gaia XP driver and its on-disk cache.

Description: The driver fetches Gaia DR3 XP spectra with astroquery and keeps
each answer in a compressed NumPy file. These tests replace astroquery's
download with a function that returns a canned reply or fails, and check that
the cache round-trips and records the download date, that a source with no XP
spectrum is remembered, that a failed download returns `None` without raising
and is logged once, and that the cache folder follows the same location rule
as the Gaia star catalog cache. No network is used.
"""

import logging
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import astroquery.gaia as gaia_module
import numpy as np
import pytest
from astropy.table import Table

from astrometricslib.drivers import gaia_xp_driver as driver_module
from astrometricslib.drivers.catalog_store import get_catalog_cache_path
from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.drivers.gaia_xp_driver import (
    AstroqueryGaiaXpDriver,
    _spectrum_from_files,
    gaia_xp_cache_path,
)
from astrometricslib.drivers.interfaces.gaia_xp_driver import GaiaXpDriver

SOURCE_ID = 3225773709225508736


def xp_reply(source_id: int = SOURCE_ID) -> dict[str, list[Table]]:
    """Build a reply shaped like ``Gaia.load_data`` gives for one source.

    Parameters
    ----------
    source_id : `int`, optional
        The source the file is named for.

    Returns
    -------
    files : `dict` [`str`, `list` [`astropy.table.Table`]]
        One file name mapped to a one-table list. The table holds 343 rows,
        336 to 1020 nm in steps of 2 nm, in the archive's units.
    """
    wavelength_nm = np.arange(336.0, 1021.0, 2.0)
    table = Table({
        "source_id": np.full(wavelength_nm.size, source_id),
        "wavelength": wavelength_nm,
        "flux": 1e-12 * np.exp(-(((wavelength_nm - 550.0) / 300.0) ** 2)),
        "flux_error": np.full(wavelength_nm.size, 1e-14),
    })
    return {f"XP_SAMPLED-Gaia DR3 {source_id}.csv": [table]}


@pytest.fixture
def archive(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Replace the Gaia archive client's download with a controllable one.

    Returns
    -------
    state : `types.SimpleNamespace`
        ``calls`` lists the source ids asked for. Set ``reply`` to the reply
        to give, or ``error`` to an exception to raise.
    """
    state = SimpleNamespace(calls=[], reply=xp_reply(), error=None)

    def load_data(*, ids: list[int], **_keywords: Any) -> dict[str, list[Table]]:
        """Stand in for ``Gaia.load_data``.

        Returns
        -------
        files : `dict`
            The configured reply. The configured error, when there is one,
            is raised instead.

        """
        state.calls.append(ids[0])
        if state.error is not None:
            raise state.error
        return state.reply

    monkeypatch.setattr(gaia_module.Gaia, "load_data", load_data, raising=False)
    return state


def test_the_driver_is_a_gaia_xp_driver(tmp_path: Path) -> None:
    """The concrete driver implements the interface."""
    assert isinstance(AstroqueryGaiaXpDriver(cache_directory=tmp_path), GaiaXpDriver)


def test_a_download_gives_angstroms_flux_and_error_in_order(tmp_path: Path, archive: SimpleNamespace) -> None:
    """The archive's nanometres become Angstroms and the arrays line up."""
    driver = AstroqueryGaiaXpDriver(cache_directory=tmp_path)

    wavelength, flux, flux_error = driver.sampled_spectrum(SOURCE_ID)

    assert archive.calls == [SOURCE_ID]
    assert wavelength.size == flux.size == flux_error.size == 343
    assert wavelength[0] == pytest.approx(3360.0)
    assert wavelength[-1] == pytest.approx(10200.0)
    assert np.all(np.diff(wavelength) == pytest.approx(20.0))
    assert flux.max() == pytest.approx(1e-12, rel=0.2)


def test_the_cache_round_trips_and_a_second_request_does_not_download(
    tmp_path: Path, archive: SimpleNamespace
) -> None:
    """A cached spectrum is read back identically, even by a new driver."""
    first = AstroqueryGaiaXpDriver(cache_directory=tmp_path).sampled_spectrum(SOURCE_ID)
    assert (tmp_path / f"gaia_dr3_{SOURCE_ID}.npz").exists()

    second = AstroqueryGaiaXpDriver(cache_directory=tmp_path).sampled_spectrum(SOURCE_ID)

    assert archive.calls == [SOURCE_ID]
    for downloaded, cached in zip(first, second, strict=True):
        assert np.array_equal(downloaded, cached)


def test_the_cache_records_the_download_date(tmp_path: Path, archive: SimpleNamespace) -> None:
    """The date of the download is stored with the spectrum."""
    driver = AstroqueryGaiaXpDriver(cache_directory=tmp_path)
    assert driver.download_date(SOURCE_ID) is None

    driver.sampled_spectrum(SOURCE_ID)

    date_text = driver.download_date(SOURCE_ID)
    assert date_text is not None
    assert len(date_text) == 10
    assert date_text[4] == "-"
    with np.load(tmp_path / f"gaia_dr3_{SOURCE_ID}.npz", allow_pickle=False) as stored:
        assert int(stored["source_id"]) == SOURCE_ID


def test_a_source_without_xp_gives_none_and_is_remembered(tmp_path: Path, archive: SimpleNamespace) -> None:
    """An empty reply is `None`, and the source is not asked about again."""
    archive.reply = {}
    driver = AstroqueryGaiaXpDriver(cache_directory=tmp_path)

    assert driver.sampled_spectrum(SOURCE_ID) is None
    assert driver.sampled_spectrum(SOURCE_ID) is None

    assert archive.calls == [SOURCE_ID]
    assert driver.download_date(SOURCE_ID) is not None


def test_a_failed_download_gives_none_is_not_cached_and_is_logged_once(
    tmp_path: Path, archive: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    """A network failure never raises, is logged once, and is not cached."""
    archive.error = OSError("network is down")
    driver = AstroqueryGaiaXpDriver(cache_directory=tmp_path)

    with caplog.at_level(logging.WARNING, logger=driver_module.logger.name):
        assert driver.sampled_spectrum(SOURCE_ID) is None
        assert driver.sampled_spectrum(SOURCE_ID + 1) is None

    warnings = [record for record in caplog.records if "Gaia XP download failed" in record.getMessage()]
    assert len(warnings) == 1
    assert not list(tmp_path.glob("*.npz"))


def test_after_a_failure_downloads_are_paused_until_the_retry_time(
    tmp_path: Path, archive: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run with the network down pays for one failure, not one per star."""
    archive.error = OSError("network is down")
    driver = AstroqueryGaiaXpDriver(cache_directory=tmp_path)
    driver.sampled_spectrum(SOURCE_ID)
    driver.sampled_spectrum(SOURCE_ID + 1)
    assert archive.calls == [SOURCE_ID]

    # Time passes, and the network comes back.
    monkeypatch.setattr(driver_module.time, "monotonic", lambda: 1e9)
    archive.error = None
    assert driver.sampled_spectrum(SOURCE_ID + 1) is not None
    assert archive.calls == [SOURCE_ID, SOURCE_ID + 1]


def test_a_reply_that_cannot_be_read_gives_none(tmp_path: Path, archive: SimpleNamespace) -> None:
    """A reply that is not a dictionary of tables is a failed download."""
    archive.reply = "not a reply"
    driver = AstroqueryGaiaXpDriver(cache_directory=tmp_path)

    assert driver.sampled_spectrum(SOURCE_ID) is None
    assert not list(tmp_path.glob("*.npz"))


def test_a_table_without_the_flux_columns_is_refused() -> None:
    """A table missing a column is an error the driver turns into `None`."""
    with pytest.raises(ValueError, match="lacks"):
        _spectrum_from_files({"file.csv": [Table({"wavelength": [1.0, 2.0]})]})
    with pytest.raises(ValueError, match="too few"):
        _spectrum_from_files({"file.csv": [Table({"wavelength": [1.0], "flux": [1.0], "flux_error": [1.0]})]})


def test_a_download_that_never_answers_is_abandoned(
    tmp_path: Path, archive: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stalled archive gives `None` after the timeout instead of hanging."""
    release = threading.Event()

    def stalled(**_keywords: Any) -> dict[str, list[Table]]:
        """Wait until the test lets go, as a stalled connection would.

        Returns
        -------
        files : `dict`
            An empty reply.
        """
        release.wait(5.0)
        return {}

    monkeypatch.setattr(gaia_module.Gaia, "load_data", stalled, raising=False)
    monkeypatch.setattr(driver_module, "DOWNLOAD_TIMEOUT_SECONDS", 0.2)
    driver = AstroqueryGaiaXpDriver(cache_directory=tmp_path)

    try:
        assert driver.sampled_spectrum(SOURCE_ID) is None
    finally:
        release.set()
    assert not list(tmp_path.glob("*.npz"))


def test_an_unreadable_cache_file_is_downloaded_again(tmp_path: Path, archive: SimpleNamespace) -> None:
    """A damaged cache file is ignored and replaced by a fresh download."""
    (tmp_path / f"gaia_dr3_{SOURCE_ID}.npz").write_bytes(b"not an npz file")

    spectrum = AstroqueryGaiaXpDriver(cache_directory=tmp_path).sampled_spectrum(SOURCE_ID)

    assert spectrum is not None
    assert archive.calls == [SOURCE_ID]


def test_the_cache_sits_in_the_catalogs_folder_next_to_the_gaia_star_cache(tmp_path: Path) -> None:
    """The XP cache follows the Gaia catalog cache's location rule."""
    config = SimpleNamespace(get_library_path=lambda: tmp_path)

    assert gaia_xp_cache_path(config) == tmp_path / "catalogs" / "gaia_xp"
    assert gaia_xp_cache_path(config).parent == get_catalog_cache_path(config).parent
    assert AstroqueryGaiaXpDriver(config).cache_directory == tmp_path / "catalogs" / "gaia_xp"


def test_the_driver_set_builds_the_built_in_driver_only_when_none_was_chosen(tmp_path: Path) -> None:
    """`Drivers` gives the chosen driver, else the built-in one."""
    chosen = AstroqueryGaiaXpDriver(cache_directory=tmp_path)

    assert Drivers(gaia_xp=chosen).gaia_xp_or_default() is chosen
    assert Drivers(gaia_xp=chosen).any_chosen
    assert isinstance(Drivers().gaia_xp_or_default(), AstroqueryGaiaXpDriver)
    assert not Drivers().any_chosen
