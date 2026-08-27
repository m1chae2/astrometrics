"""Tests for pre-filling the local Gaia cache from a target catalog.

Star identification fell back to ESA's Gaia TAP service whenever a field
had never been queried before, which inside a parallel batch meant six
worker processes querying at once; the 2026-08-24 run tripped the remote
circuit breaker and left Moon with "0 catalog-matched, 100
position-only". Seeding removes the network from the batch entirely.

Every test here is offline: the one function that would download is
replaced, so the suite never contacts ESA.
"""

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.tasks.stellar_tasks.astrometry_tasks import catalog_seeding
from astrometricslib.tasks.stellar_tasks.astrometry_tasks.catalog_seeding import (
    _angular_separation_degrees,
    _coordinate_from_header,
    derive_field_centers,
    seed_local_gaia_catalog,
)


class _Frame:
    """A frame record stand-in exposing only the `path` attribute used."""

    def __init__(self, path):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.path = path


class _Target:
    """A target stand-in exposing only `id` and `frames`."""

    def __init__(self, target_id, frames):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.id = target_id
        self.frames = frames


def _write_frame(path, **header_cards: object):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Write a tiny FITS frame carrying the given header cards.

    Returns
    -------
    path : `str`
        The path just written, as a string.
    """
    hdu = fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32))
    for keyword, value in header_cards.items():
        hdu.header[keyword] = value
    hdu.writeto(path, overwrite=True)
    return str(path)


def test_solved_wcs_is_preferred_over_mount_pointing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """CRVAL wins: it is where the camera looked, not where the mount aimed."""
    header = {"CRVAL1": 210.5, "CRVAL2": 54.3, "RA": 1.0, "DEC": 2.0}

    assert _coordinate_from_header(header) == (210.5, 54.3)


def test_decimal_pointing_is_used_when_the_frame_was_never_solved():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """An unsolved frame still contributes its mount pointing."""
    assert _coordinate_from_header({"RA": 85.4, "DEC": -1.85}) == (85.4, -1.85)


def test_sexagesimal_pointing_is_parsed_as_a_last_resort():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """OBJCTRA/OBJCTDEC are hourangle/degree strings, not decimals."""
    center = _coordinate_from_header({"OBJCTRA": "20 58 41.05", "OBJCTDEC": "44 34 36.48"})

    assert center is not None
    assert center[0] == pytest.approx(314.671, abs=1e-3)
    assert center[1] == pytest.approx(44.5768, abs=1e-3)


def test_header_without_any_pointing_yields_none():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A frame with no coordinates must not invent one."""
    assert _coordinate_from_header({"EXPTIME": 30.0}) is None


def test_separation_handles_right_ascension_wraparound():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """359.5 and 0.5 degrees are one degree apart, not 359."""
    assert _angular_separation_degrees(359.5, 0.0, 0.5, 0.0) == pytest.approx(1.0, abs=1e-6)


def test_separation_shrinks_near_the_pole():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Meridians converge, so equal RA offsets mean less sky near the pole.

    Polaris is in this library's own catalog, so a naive coordinate
    difference would badly overestimate separations for it.
    """
    at_equator = _angular_separation_degrees(0.0, 0.0, 10.0, 0.0)
    near_pole = _angular_separation_degrees(0.0, 89.0, 10.0, 89.0)

    assert near_pole < at_equator


def test_nearby_pointings_collapse_into_one_field(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Frames of one object drift by arcmin between sessions, not degrees."""
    frames = [
        _Frame(_write_frame(tmp_path / "a.fits", CRVAL1=210.0, CRVAL2=54.0)),
        _Frame(_write_frame(tmp_path / "b.fits", CRVAL1=210.02, CRVAL2=54.01)),
    ]

    field_centers = derive_field_centers([_Target("M 101", frames)])

    assert len(field_centers) == 1
    assert field_centers[0]["frames_examined"] == 2


def test_distinct_objects_stay_separate_fields(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Deduplication must not merge genuinely different pointings."""
    first = _Target("M 101", [_Frame(_write_frame(tmp_path / "a.fits", CRVAL1=210.0, CRVAL2=54.0))])
    second = _Target("M 42", [_Frame(_write_frame(tmp_path / "b.fits", CRVAL1=83.8, CRVAL2=-5.4))])

    assert len(derive_field_centers([first, second])) == 2


def test_two_targets_sharing_a_field_are_both_recorded(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """One download can cover several targets; the report must say so.

    Two catalog entries for the same patch of sky -- an object and a
    companion inside the same framing -- must cost one download, not
    two, and the field has to name both so the report explains itself.
    """
    first = _Target("NGC 4438", [_Frame(_write_frame(tmp_path / "a.fits", CRVAL1=186.96, CRVAL2=13.05))])
    second = _Target("NGC 4435", [_Frame(_write_frame(tmp_path / "b.fits", CRVAL1=186.92, CRVAL2=13.08))])

    field_centers = derive_field_centers([first, second])

    assert len(field_centers) == 1
    assert sorted(field_centers[0]["target_ids"]) == ["NGC 4435", "NGC 4438"]


def test_placeholder_origin_pointing_is_rejected(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """(0, 0) is an unset mount, not a real field in Cetus."""
    target = _Target("Sun", [_Frame(_write_frame(tmp_path / "a.fits", CRVAL1=0.0, CRVAL2=0.0))])

    assert derive_field_centers([target]) == []


def test_unreadable_frame_does_not_abort_the_sweep(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """One corrupt file must not cost the whole catalog its seeding."""
    broken = tmp_path / "broken.fits"
    broken.write_bytes(b"not a FITS file")
    good = _write_frame(tmp_path / "good.fits", CRVAL1=210.0, CRVAL2=54.0)
    target = _Target("M 101", [_Frame(str(broken)), _Frame(good)])

    assert len(derive_field_centers([target])) == 1


def test_frames_examined_per_target_is_capped(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Reading every frame of a large catalog costs time for nothing."""
    frames = [
        _Frame(_write_frame(tmp_path / f"f{index}.fits", CRVAL1=210.0, CRVAL2=54.0)) for index in range(6)
    ]

    field_centers = derive_field_centers([_Target("M 101", frames)], max_frames_per_target=2)

    assert field_centers[0]["frames_examined"] == 2


def test_seeding_visits_every_field_without_network(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The sweep downloads each distinct field exactly once."""
    first = _Target("M 101", [_Frame(_write_frame(tmp_path / "a.fits", CRVAL1=210.0, CRVAL2=54.0))])
    second = _Target("M 42", [_Frame(_write_frame(tmp_path / "b.fits", CRVAL1=83.8, CRVAL2=-5.4))])
    requested = []

    def _fake_seed(ra, dec, radius_deg=0.5, max_magnitude=18.0):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        requested.append((ra, dec))
        return 100

    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import StarIdentifier

    monkeypatch.setattr(StarIdentifier, "_seed_gaia_cache_for_field", staticmethod(_fake_seed))

    report = seed_local_gaia_catalog([first, second], request_delay_seconds=0)

    assert report["fields_total"] == 2
    assert report["fields_seeded"] == 2
    assert report["sources_cached"] == 200
    assert len(requested) == 2


def test_a_failing_field_does_not_stop_the_others(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """One unreachable field must not abandon the rest of the sweep."""
    first = _Target("M 101", [_Frame(_write_frame(tmp_path / "a.fits", CRVAL1=210.0, CRVAL2=54.0))])
    second = _Target("M 42", [_Frame(_write_frame(tmp_path / "b.fits", CRVAL1=83.8, CRVAL2=-5.4))])

    def _fake_seed(ra, dec, radius_deg=0.5, max_magnitude=18.0):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        if ra > 200:
            raise ConnectionError("TAP service unavailable")
        return 50

    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import StarIdentifier

    monkeypatch.setattr(StarIdentifier, "_seed_gaia_cache_for_field", staticmethod(_fake_seed))

    report = seed_local_gaia_catalog([first, second], request_delay_seconds=0, max_attempts=1)

    assert report["fields_seeded"] == 1
    assert report["fields_failed"] == 1
    assert report["sources_cached"] == 50


def test_a_transient_failure_is_retried(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A blip must not cost a field its catalog data."""
    target = _Target("M 101", [_Frame(_write_frame(tmp_path / "a.fits", CRVAL1=210.0, CRVAL2=54.0))])
    attempts = {"count": 0}

    def _fake_seed(ra, dec, radius_deg=0.5, max_magnitude=18.0):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise TimeoutError("cone search timed out")
        return 75

    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import StarIdentifier

    monkeypatch.setattr(StarIdentifier, "_seed_gaia_cache_for_field", staticmethod(_fake_seed))
    monkeypatch.setattr(catalog_seeding.time, "sleep", lambda _seconds: None)

    report = seed_local_gaia_catalog([target], request_delay_seconds=0)

    assert attempts["count"] == 2
    assert report["fields_seeded"] == 1
    assert report["sources_cached"] == 75
