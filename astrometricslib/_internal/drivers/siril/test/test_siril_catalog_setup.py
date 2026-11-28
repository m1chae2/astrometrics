"""Tests for the setup of Siril's Gaia astrometry catalog.

A made-up catalog stands in for the 1.5 GB file, and the server is replaced
by an in-memory stream. Nothing is downloaded and Siril is not started.
"""

import bz2
import hashlib
import io
from pathlib import Path
from typing import Any

import pytest

from astrometricslib._internal.drivers import outside_services
from astrometricslib._internal.drivers.siril.core import catalog_setup
from astrometricslib._internal.drivers.siril.core.catalog_setup import (
    CatalogRelease,
    check_catalog,
    set_up_catalog,
)
from astrometricslib.foundation.errors import ExternalServiceError

CATALOG_BYTES = b"gaia stars " * 5000


def release_for(data: bytes) -> CatalogRelease:
    """Pin a made-up release to some bytes.

    Parameters
    ----------
    data : `bytes`
        The decompressed catalog.

    Returns
    -------
    release : `CatalogRelease`
        A release whose size and checksum match `data`.
    """
    return CatalogRelease(
        name="Test catalog",
        version="1",
        url="https://example.invalid/catalog.bz2",
        file_name="siril_cat_healpix8_astro.dat",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def serve(monkeypatch: pytest.MonkeyPatch, content: bytes) -> list[str]:
    """Make the download return some compressed bytes.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the download call.
    content : `bytes`
        What the server sends, already compressed.

    Returns
    -------
    requests : `list` [`str`]
        Filled with the address of each download.
    """
    requests: list[str] = []

    def open_download(url: str, *, timeout_seconds: float | None = None) -> Any:
        """Stand in for the download.

        Returns
        -------
        response : `io.BytesIO`
            The content, which works as a context manager.
        """
        requests.append(url)
        return io.BytesIO(content)

    monkeypatch.setattr(outside_services, "open_download", open_download)
    monkeypatch.setattr(catalog_setup, "require_online", lambda description: None)
    return requests


def test_a_matching_download_is_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the decompressed catalog is put in place and checks out."""
    release = release_for(CATALOG_BYTES)
    requests = serve(monkeypatch, bz2.compress(CATALOG_BYTES))

    path = set_up_catalog(release, folder=str(tmp_path))

    assert path == tmp_path / release.file_name
    assert path.read_bytes() == CATALOG_BYTES
    assert not list(tmp_path.glob("*.partial"))
    assert len(requests) == 1
    assert check_catalog(release, [str(tmp_path)]).ready


def test_an_installed_matching_catalog_is_not_downloaded_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a second setup downloads nothing."""
    release = release_for(CATALOG_BYTES)
    (tmp_path / release.file_name).write_bytes(CATALOG_BYTES)
    requests = serve(monkeypatch, b"")

    set_up_catalog(release, folder=str(tmp_path))

    assert requests == []


def test_a_download_with_the_wrong_checksum_is_refused_and_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a changed file is refused and leaves nothing behind."""
    release = release_for(CATALOG_BYTES)
    tampered = bytearray(CATALOG_BYTES)
    tampered[10] ^= 0xFF
    serve(monkeypatch, bz2.compress(bytes(tampered)))

    with pytest.raises(ExternalServiceError, match="does not match"):
        set_up_catalog(release, folder=str(tmp_path))

    assert list(tmp_path.iterdir()) == []


def test_a_broken_stream_is_refused_and_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a stream that is not bzip2 data leaves no file."""
    release = release_for(CATALOG_BYTES)
    serve(monkeypatch, b"not bzip2 data")

    with pytest.raises(ExternalServiceError):
        set_up_catalog(release, folder=str(tmp_path))

    assert list(tmp_path.iterdir()) == []


def test_check_reports_a_missing_a_short_and_a_changed_catalog(tmp_path: Path) -> None:
    """Verify each way the catalog can fail the pin is reported."""
    release = release_for(CATALOG_BYTES)
    folder = [str(tmp_path)]
    assert check_catalog(release, folder).path is None

    (tmp_path / release.file_name).write_bytes(CATALOG_BYTES[:-1])
    short = check_catalog(release, folder)
    assert (short.size_matches, short.ready) == (False, False)

    changed = bytes([CATALOG_BYTES[0] ^ 1]) + CATALOG_BYTES[1:]
    (tmp_path / release.file_name).write_bytes(changed)
    status = check_catalog(release, folder)
    assert (status.size_matches, status.checksum_matches, status.ready) == (True, False, False)
    assert (tmp_path / release.file_name).read_bytes() == changed


def test_an_address_that_is_not_https_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the setup refuses a plain http address before any download."""
    release = release_for(CATALOG_BYTES)
    release = CatalogRelease(**{**release.__dict__, "url": "http://example.invalid/catalog.bz2"})
    requests = serve(monkeypatch, b"")

    with pytest.raises(ExternalServiceError, match="https"):
        set_up_catalog(release, folder=str(tmp_path))

    assert requests == []
