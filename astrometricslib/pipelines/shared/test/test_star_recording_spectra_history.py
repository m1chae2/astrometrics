"""Tests for spectra_history accumulation across spectroscopy runs.

Purpose: spectra_history used to be a dead field -- nothing wrote it,
so the UI's "Overlay All Epochs" spectrum view always rendered empty.
SpectroscopyPipeline now records each extraction as one
SpectralObservation, and merge_spectra_history is what turns those
single-session snapshots into an accumulated timeline as a star gets
reprocessed across many observing sessions. These tests pin down the
merge behavior directly, independent of running the full pipeline.
"""

from datetime import UTC, datetime

import pytest

from astrometricslib.models.stellar_source import (
    PhotometryResult,
    SpectralObservation,
    SpectroscopyResult,
    StellarObject,
)
from astrometricslib.pipelines.shared.star_recording import (
    merge_astrometry_stellar_object,
    merge_spectra_history,
    merge_spectroscopy_stellar_object,
)


def _observation(
    hour: int, wavelengths: list[float] | None = None, intensities: list[float] | None = None
) -> SpectralObservation:
    """Build a SpectralObservation at a fixed hour on one fixed date.

    Returns
    -------
    SpectralObservation
        An observation timestamped 2026-01-01 at the given hour (UTC).
    """
    return SpectralObservation(
        timestamp=datetime(2026, 1, 1, hour, tzinfo=UTC),
        wavelengths=wavelengths or [5000.0, 5010.0],
        intensities=intensities or [1.0, 0.9],
    )


def test_merge_spectra_history_accumulates_distinct_timestamps():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify observations from two different sessions both survive."""
    existing = [_observation(1)]
    updated = [_observation(2)]

    merged = merge_spectra_history(existing, updated)

    assert [obs.timestamp.hour for obs in merged] == [1, 2]


def test_merge_spectra_history_orders_oldest_first():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the merged history is sorted by timestamp, any input order."""
    existing = [_observation(5)]
    updated = [_observation(1)]

    merged = merge_spectra_history(existing, updated)

    assert [obs.timestamp.hour for obs in merged] == [1, 5]


def test_merge_spectra_history_reprocessing_the_same_session_replaces_not_duplicates():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify re-running the same session's extraction overwrites its entry.

    Regression guard: a naive append would grow spectra_history forever
    every time the same frame is reprocessed, instead of updating that
    session's one entry in place.
    """
    existing = [_observation(1, intensities=[1.0, 0.9])]
    updated = [_observation(1, intensities=[2.0, 1.8])]

    merged = merge_spectra_history(existing, updated)

    assert len(merged) == 1
    assert merged[0].intensities == [2.0, 1.8]


def test_merge_spectroscopy_stellar_object_accumulates_history_across_calls():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify two separate merge calls (two sessions) both land in history."""
    existing_star = StellarObject(id="Vega", spectra_history=[_observation(1)])
    session_two_star = StellarObject(id="Vega", spectra_history=[_observation(2)])

    merged = merge_spectroscopy_stellar_object(existing_star, session_two_star)

    assert [obs.timestamp.hour for obs in merged.spectra_history] == [1, 2]


def test_merge_spectroscopy_stellar_object_with_no_existing_history_uses_the_update():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a first spectroscopy run seeds spectra_history from scratch."""
    existing_star = StellarObject(id="Vega")
    session_star = StellarObject(id="Vega", spectra_history=[_observation(3)])

    merged = merge_spectroscopy_stellar_object(existing_star, session_star)

    assert [obs.timestamp.hour for obs in merged.spectra_history] == [3]


def test_merge_spectroscopy_stellar_object_keeps_position_and_photometry():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a spectrum lands in the star's own row, position unchanged.

    The spectroscopy update's `star_data` is a position in the
    spectroscopy image, a different pixel grid from the normal image the
    existing row's `star_data` came from, so it must not overwrite it.
    """
    existing_star = StellarObject(
        id="Vega",
        star_data={"xcentroid": 100.0, "ycentroid": 200.0},
        photometry=PhotometryResult(fluxes=[1.0, 2.0, 3.0]),
    )
    spectral_update = StellarObject(
        id="Vega",
        star_data={"xcentroid": 900.0, "ycentroid": 800.0},
        spectroscopy=SpectroscopyResult(
            wavelengths_angstrom=[4000.0], intensities=[1.0], star_position_px=[900.0, 800.0]
        ),
    )

    merged = merge_spectroscopy_stellar_object(existing_star, spectral_update)

    assert merged.star_data == {"xcentroid": 100.0, "ycentroid": 200.0}
    assert merged.spectroscopy.star_position_px == [900.0, 800.0]
    assert merged.spectroscopy.wavelengths_angstrom == [4000.0]
    assert merged.photometry.fluxes == [1.0, 2.0, 3.0]
    assert merged.has_spectra is True
    assert merged.has_photometry is True


def test_the_merges_carry_the_catalog_colour():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Both the astrometry and spectroscopy merges bring the B-V along."""
    for merge in (merge_astrometry_stellar_object, merge_spectroscopy_stellar_object):
        existing_star = StellarObject(id="Vega")
        updated_star = StellarObject(id="Vega")
        updated_star.b_minus_v = 0.47

        merged = merge(existing_star, updated_star)

        assert merged.b_minus_v == pytest.approx(0.47)
