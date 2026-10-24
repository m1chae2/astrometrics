"""Tests for the class gap against signal-to-noise calibration.

The noise injection must hit the signal-to-noise it is asked for as the
project's own estimator reads it. The table logic is tested with a stand-in
classifier whose gap falls as the spectrum gets noisier, so the verdicts
(reachable, not reachable) and the field resampling can be checked exactly; one
test runs the real classifier on a bundled reference spectrum.
"""

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from astrometricslib.models.stellar_source import AMBIGUOUS_RMS_GAP
from astrometricslib.pipelines.spectroscopy.processing.class_gap_calibration import (
    CAMERA_NAME,
    CLASS_GAP_DEFINITION_VERSION,
    CLASS_GAP_SNR_ESTIMATOR,
    CONFIDENCE_LIMITED,
    ClassGapRow,
    SpectrumSample,
    class_gap_for_snr,
    class_gap_table,
    class_gap_table_path,
    classify_sample,
    degrade_to_snr,
    reachable_snr,
)
from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import blurred_reference_spectrum
from astrometricslib.pipelines.spectroscopy.processing.spectrum_signal import (
    estimate_spectrum_signal_to_noise,
)


def reference_sample(
    spectral_type: str = "G2V", field: str = "field-a", native_snr: float = 200.0
) -> SpectrumSample:
    """Build a sample from a bundled reference spectrum.

    Parameters
    ----------
    spectral_type : `str`, optional
        The reference to use.
    field : `str`, optional
        The field name to give it.
    native_snr : `float`, optional
        The signal-to-noise to record as its own.

    Returns
    -------
    sample : `SpectrumSample`
        A noise-free spectrum on a 5 A grid from 4200 to 8000 A.
    """
    wavelength, flux = blurred_reference_spectrum(spectral_type, 70.0)
    keep = (wavelength >= 4200.0) & (wavelength <= 8000.0)
    return SpectrumSample(field, spectral_type, wavelength[keep], flux[keep], 70.0, spectral_type, native_snr)


@pytest.mark.parametrize("target", [5.0, 12.0, 25.0])
def test_the_injected_noise_reads_the_signal_to_noise_asked_for(target: float) -> None:
    """The project's estimator reads the copy within 3 percent of target."""
    sample = reference_sample()
    noise = np.random.default_rng(1).standard_normal(sample.intensity.shape)
    degraded = degrade_to_snr(sample, target, noise)
    assert degraded is not None
    intensity, reading = degraded
    assert reading == pytest.approx(target, rel=0.031)
    assert estimate_spectrum_signal_to_noise(sample.wavelength_angstrom, intensity, 70.0) == pytest.approx(
        reading
    )


def test_a_spectrum_with_missing_samples_can_still_be_degraded() -> None:
    """NaN samples do not stop the noise scale being found."""
    sample = reference_sample()
    intensity = sample.intensity.copy()
    intensity[10:20] = np.nan
    holey = SpectrumSample(
        sample.field_id,
        sample.star_id,
        sample.wavelength_angstrom,
        intensity,
        sample.resolution_element_angstrom,
        sample.catalog_type,
        sample.native_snr,
    )
    noise = np.random.default_rng(5).standard_normal(intensity.shape)
    degraded = degrade_to_snr(holey, 12.0, noise)
    assert degraded is not None
    assert degraded[1] == pytest.approx(12.0, rel=0.031)


def test_a_spectrum_that_is_all_missing_gives_none() -> None:
    """With no finite sample there is no scale to set the noise by."""
    sample = reference_sample()
    empty = SpectrumSample(
        "f", "s", sample.wavelength_angstrom, np.full(sample.intensity.shape, np.nan), 70.0, "G2V", 100.0
    )
    assert degrade_to_snr(empty, 10.0, np.zeros(sample.intensity.shape)) is None


def test_a_spectrum_cannot_be_improved() -> None:
    """A target at or above the spectrum's own signal-to-noise gives none."""
    sample = reference_sample(native_snr=30.0)
    noise = np.random.default_rng(2).standard_normal(sample.intensity.shape)
    assert degrade_to_snr(sample, 30.0, noise) is None
    assert degrade_to_snr(sample, 50.0, noise) is None


def test_the_real_classifier_still_names_the_class_of_a_noisy_reference() -> None:
    """A G2V reference at a signal-to-noise of 20 is still class G."""
    sample = reference_sample()
    outcome_clean = classify_sample(sample, sample.intensity)
    assert outcome_clean.class_matches_catalog is True
    assert outcome_clean.gap_to_second_best is not None
    noise = np.random.default_rng(3).standard_normal(sample.intensity.shape)
    degraded = degrade_to_snr(sample, 20.0, noise)
    assert degraded is not None
    outcome = classify_sample(sample, degraded[0])
    assert outcome.class_matches_catalog is True
    assert outcome.steps_from_catalog is not None and outcome.steps_from_catalog < 20.0


def _fake_classifier(gap_at_infinite_snr: float) -> Callable[..., dict]:
    """Make a stand-in classifier whose gaps shrink with noise.

    Parameters
    ----------
    gap_at_infinite_snr : `float`
        The gap a noise-free spectrum gets.

    Returns
    -------
    classifier : `Callable`
        Reads the signal-to-noise of the given intensity and returns gaps
        ``gap * snr / (snr + 10)`` with the catalog's type.
    """

    def classifier(
        wavelength: np.ndarray, intensity: np.ndarray, resolution_element: float, **_: object
    ) -> dict:
        snr = estimate_spectrum_signal_to_noise(wavelength, intensity, resolution_element) or 0.0
        gap = gap_at_infinite_snr * min(snr, 1e6) / (min(snr, 1e6) + 10.0)
        return {"spectral_type": "G2V", "rms_gap_to_next_class": gap, "rms_gap_to_second_best": gap / 4.0}

    return classifier


def _samples() -> list[SpectrumSample]:
    """Build six samples in three fields.

    Returns
    -------
    samples : `list` [`SpectrumSample`]
        Two samples in each of three fields.
    """
    return [reference_sample(field=f"field-{index % 3}", native_snr=100.0) for index in range(6)]


def test_the_table_has_a_row_for_each_grid_value_and_falls_with_noise() -> None:
    """With gaps that fall with noise, medians rise with signal-to-noise."""
    table = class_gap_table(
        _samples(),
        snr_grid=(5.0, 10.0, 20.0),
        draws_per_spectrum=2,
        bootstrap_count=50,
        classifier=_fake_classifier(0.5),
    )
    assert [row.snr for row in table.rows] == [5.0, 10.0, 20.0]
    gaps = [row.median_gap_to_next_class for row in table.rows]
    assert gaps == sorted(gaps)
    assert table.native.snr is None
    assert all(row.field_count == 3 and row.spectrum_count == 12 for row in table.rows)
    assert (
        table.rows[0].gap_to_next_class_low
        <= table.rows[0].median_gap_to_next_class
        <= table.rows[0].gap_to_next_class_high
    )
    low, high = table.rows[0].leave_one_field_out_range
    assert low <= high


def test_the_class_gap_is_reached_where_it_first_exceeds_the_threshold_and_stays_there() -> None:
    """The gap 0.5 s/(s+10) passes 0.02 from s = 0.42, so all reach it."""
    table = class_gap_table(
        _samples(),
        snr_grid=(5.0, 10.0, 20.0),
        draws_per_spectrum=1,
        bootstrap_count=20,
        classifier=_fake_classifier(0.5),
    )
    assert table.class_reachable_snr == pytest.approx(5.0)
    # The subtype gap is a quarter of that: 0.125 s/(s+10), at least 0.02
    # from s = 1.9.
    assert table.subtype_reachable_snr == pytest.approx(5.0)


def test_a_level_that_never_separates_is_confidence_limited() -> None:
    """If the best spectra have gaps under the limit, the verdict says so."""
    table = class_gap_table(
        _samples(),
        snr_grid=(5.0, 20.0),
        draws_per_spectrum=1,
        bootstrap_count=20,
        classifier=_fake_classifier(0.01),
    )
    assert table.class_reachable_snr == CONFIDENCE_LIMITED
    assert table.subtype_reachable_snr == CONFIDENCE_LIMITED
    assert table.native.class_separated_share == pytest.approx(0.0)


def test_reachable_snr_needs_every_higher_value_to_reach_too() -> None:
    """A dip at a middle value moves the verdict up to the value above it."""

    def make(snr: float, share: float) -> ClassGapRow:
        return ClassGapRow(snr, 10, 3, 0.1, 0.1, 0.05, 0.15, share, share, 1.0, 1.0, (0.1, 0.1))

    rows = [make(3.0, 0.9), make(5.0, 0.3), make(8.0, 0.8), make(12.0, 0.9)]
    assert reachable_snr(rows, lambda row: row.class_separated_share) == pytest.approx(8.0)
    assert (
        reachable_snr([make(3.0, 0.9), make(5.0, 0.2)], lambda row: row.class_separated_share)
        == CONFIDENCE_LIMITED
    )


def test_the_table_is_reproducible_from_its_seed() -> None:
    """The same seed gives the same numbers."""
    arguments = {
        "snr_grid": (10.0,),
        "draws_per_spectrum": 2,
        "bootstrap_count": 30,
        "classifier": _fake_classifier(0.5),
    }
    first = class_gap_table(_samples(), seed=7, **arguments)
    second = class_gap_table(_samples(), seed=7, **arguments)
    assert first.rows[0].median_gap_to_next_class == second.rows[0].median_gap_to_next_class
    assert first.rows[0].gap_to_next_class_low == second.rows[0].gap_to_next_class_low


def _stored_table(path: Path, **overrides: object) -> None:
    """Write a small stored table.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write it.
    **overrides
        Fields to replace in the record, to make a table the lookup refuses.
    """
    rows = [{"snr": value, "median_gap_to_next_class": value / 100.0} for value in (3.0, 5.0, 12.0)]
    record = {
        "camera": CAMERA_NAME,
        "definition_version": CLASS_GAP_DEFINITION_VERSION,
        "ambiguous_rms_gap": AMBIGUOUS_RMS_GAP,
        "snr_estimator": CLASS_GAP_SNR_ESTIMATOR,
        "rows": rows,
        "class_reachable_snr": 5.0,
        "subtype_reachable_snr": CONFIDENCE_LIMITED,
    }
    path.write_text(json.dumps({**record, **overrides}), encoding="utf-8")


def test_the_lookup_gives_the_highest_grid_value_at_or_below_the_signal_to_noise(tmp_path: Path) -> None:
    """A spectrum at 8 gets the row for 5, never the better row for 12."""
    path = tmp_path / "table.json"
    _stored_table(path)
    entry, reason = class_gap_for_snr(8.0, table_path=path)
    assert entry is not None and reason == ""
    assert entry["snr"] == pytest.approx(5.0)
    assert entry["below_grid"] is False
    assert entry["subtype_reachable_snr"] == CONFIDENCE_LIMITED
    assert class_gap_for_snr(12.0, table_path=path)[0]["snr"] == pytest.approx(12.0)
    assert class_gap_for_snr(100.0, table_path=path)[0]["snr"] == pytest.approx(12.0)


def test_a_spectrum_below_the_grid_gets_the_lowest_row_and_a_flag(tmp_path: Path) -> None:
    """Below the lowest value nothing is known, and the entry says so."""
    path = tmp_path / "table.json"
    _stored_table(path)
    entry, _reason = class_gap_for_snr(1.0, table_path=path)
    assert entry is not None
    assert entry["snr"] == pytest.approx(3.0)
    assert entry["below_grid"] is True


def test_with_no_stored_table_the_lookup_gives_none_and_a_reason(tmp_path: Path) -> None:
    """A missing file is no answer, not a default."""
    entry, reason = class_gap_for_snr(10.0, table_path=tmp_path / "missing.json")
    assert entry is None
    assert "no class-gap table" in reason


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"camera": "Nikon D5300"}, "made for Nikon D5300"),
        ({"definition_version": CLASS_GAP_DEFINITION_VERSION + 1}, "definition version"),
        ({"ambiguous_rms_gap": AMBIGUOUS_RMS_GAP * 2}, "ambiguity threshold"),
        ({"snr_estimator": "something_else"}, "estimator"),
    ],
)
def test_the_lookup_refuses_a_table_made_another_way(
    tmp_path: Path, overrides: dict[str, object], expected: str
) -> None:
    """A table made for another camera or another way is refused."""
    path = tmp_path / "table.json"
    _stored_table(path, **overrides)
    entry, reason = class_gap_for_snr(10.0, table_path=path)
    assert entry is None
    assert expected in reason


def test_the_lookup_refuses_a_file_that_does_not_read(tmp_path: Path) -> None:
    """A broken file or one with missing fields gives None and a reason."""
    path = tmp_path / "table.json"
    path.write_text("{not json", encoding="utf-8")
    assert class_gap_for_snr(10.0, table_path=path)[0] is None
    path.write_text(json.dumps({"rows": []}), encoding="utf-8")
    entry, reason = class_gap_for_snr(10.0, table_path=path)
    assert entry is None
    assert "could not be read" in reason


def test_the_table_file_is_named_for_the_camera() -> None:
    """The shipped file carries the camera suffix."""
    assert class_gap_table_path("ZWO ASI 533MM Pro").name == "class_gap_against_snr_zwo_asi_533mm_pro.json"


def test_each_row_has_the_share_that_keeps_the_undegraded_class_with_intervals() -> None:
    """The stand-in always says G, so every copy keeps its undegraded class."""
    table = class_gap_table(
        _samples(),
        snr_grid=(5.0, 20.0),
        draws_per_spectrum=2,
        bootstrap_count=30,
        classifier=_fake_classifier(0.5),
    )
    for row in table.rows:
        assert row.undegraded_class_agreement_share == pytest.approx(1.0)
        low, high = row.share_intervals["class_separated_share"]
        assert low <= row.class_separated_share <= high
    assert table.native.undegraded_class_agreement_share is None
    assert set(table.native_agreement_by_field) == {"field-0", "field-1", "field-2"}
    assert table.native_agreement_by_field["field-0"]["count"] == pytest.approx(2.0)


def test_the_threshold_is_the_one_the_classifier_uses() -> None:
    """Separated shares use the project's own ambiguity limit."""
    table = class_gap_table(
        _samples()[:2],
        snr_grid=(20.0,),
        draws_per_spectrum=1,
        bootstrap_count=5,
        classifier=_fake_classifier(AMBIGUOUS_RMS_GAP * 0.5),
    )
    assert table.rows[0].class_separated_share == pytest.approx(0.0)
