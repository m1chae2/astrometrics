"""Tests for measuring two stacks and comparing them.

The synthetic stacks have a known sky level, a known amount of pixel noise and
a known vignette, so each measurement can be checked against what was put in.
"""

from pathlib import Path

import numpy as np
import pytest

from astrometricslib.foundation.errors import ProcessingError
from astrometricslib.pipelines.stacking.post_processing import stack_comparison as sc

SIZE = 512
SKY = 1000.0


def make_stack(noise: float = 0.01, vignette: float = 0.0, seed: int = 0) -> np.ndarray:
    """Build a sky with noise and a radial vignette.

    Parameters
    ----------
    noise : `float`
        Pixel noise as a fraction of the sky.
    vignette : `float`
        Fractional dimming at the corners.
    seed : `int`
        Seed of the noise.

    Returns
    -------
    stack : `numpy.ndarray`
        A square image with the given sky level.
    """
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    radius = np.hypot(y - SIZE / 2, x - SIZE / 2) / (SIZE / 2 * np.sqrt(2))
    sky = SKY * (1.0 - vignette * radius**2)
    return sky * (1.0 + noise * np.random.default_rng(seed).standard_normal((SIZE, SIZE)))


def test_the_sky_level_and_noise_are_measured() -> None:
    """The sky is the median; the fine-grain noise uses neighbours."""
    measured = sc.measure_stack_array(make_stack(noise=0.02))

    assert measured.sky_level == pytest.approx(SKY, rel=0.01)
    assert measured.fine_grain_noise_fraction == pytest.approx(0.02, rel=0.1)


def test_a_vignette_raises_the_flatness_numbers() -> None:
    """A stack with a 5% vignette is less flat than one without."""
    flat = sc.measure_stack_array(make_stack(vignette=0.0))
    vignetted = sc.measure_stack_array(make_stack(vignette=0.05))

    assert vignetted.flatness_rms > 5 * flat.flatness_rms
    # The darkest block sits a little inside the corner, so just under 5%.
    assert 0.03 < vignetted.flatness_peak_to_peak < 0.06


def test_a_bright_star_does_not_change_the_flatness() -> None:
    """Blocks holding a star are dropped, so the sky is what is measured."""
    plain = make_stack()
    starred = plain.copy()
    starred[200:204, 300:304] += 60000.0

    assert sc.measure_stack_array(starred).flatness_rms == pytest.approx(
        sc.measure_stack_array(plain).flatness_rms, rel=0.2
    )


def test_a_blank_stack_cannot_be_measured() -> None:
    """An all-zero stack has no sky, so measuring it raises."""
    with pytest.raises(ProcessingError, match="no measurable sky"):
        sc.measure_stack_array(np.zeros((SIZE, SIZE)))


def test_the_comparison_reports_the_change_of_each_measurement() -> None:
    """New flats show up as a large drop in flatness and none in noise."""
    before = sc.measure_stack_array(make_stack(vignette=0.05, seed=1), "before.fits")
    after = sc.measure_stack_array(make_stack(vignette=0.01, seed=2), "after.fits")

    comparison = sc.compare_measurements(before, after)

    assert comparison.before.path == "before.fits"
    assert comparison.changes["flatness_rms"] < -0.5
    assert abs(comparison.changes["fine_grain_noise_fraction"]) < 0.05
    assert any("flatness (rms)" in line and "down" in line for line in comparison.summary)
    assert any("fine-grain noise" in line and "unchanged" in line for line in comparison.summary)


def test_compare_stacks_reads_the_files(tmp_path: Path) -> None:
    """The file form measures both stacks from disk."""
    from astropy.io import fits

    fits.writeto(tmp_path / "a.fits", make_stack(seed=1).astype(np.float32))
    fits.writeto(tmp_path / "b.fits", make_stack(seed=2).astype(np.float32))

    comparison = sc.compare_stacks(str(tmp_path / "a.fits"), str(tmp_path / "b.fits"))

    assert comparison.before.sky_level == pytest.approx(SKY, rel=0.01)
    assert len(comparison.summary) == 6


def with_empty_edge(image: np.ndarray, width: int = 64) -> np.ndarray:
    """Zero the left edge of an image, as a registered stack has.

    Returns
    -------
    edged : `numpy.ndarray`
        A copy with `width` columns set to exactly zero.
    """
    edged = image.copy()
    edged[:, :width] = 0.0
    return edged


def test_an_empty_edge_does_not_change_the_sky_noise_or_flatness() -> None:
    """Zero pixels are left out of the sky, the noise and the blocks."""
    plain = sc.measure_stack_array(make_stack(noise=0.02))
    edged = sc.measure_stack_array(with_empty_edge(make_stack(noise=0.02)))

    assert edged.sky_level == pytest.approx(plain.sky_level, rel=0.01)
    assert edged.fine_grain_noise_fraction == pytest.approx(plain.fine_grain_noise_fraction, rel=0.1)
    assert edged.flatness_rms == pytest.approx(plain.flatness_rms, abs=0.002)
    assert edged.zero_fraction == pytest.approx(64 / SIZE)


def test_a_stack_too_small_for_flatness_says_why() -> None:
    """The flatness is None with a reason, not zero."""
    small = make_stack()[:32, :32]

    measured = sc.measure_stack_array(small)

    assert measured.flatness_rms is None
    assert "smaller than one flatness block" in measured.not_measured["flatness_rms"]


def test_the_noise_sentence_says_not_measured_with_the_reason() -> None:
    """With no apertures the noise is not measured, and the reason shows."""
    before = sc.measure_stack_array(make_stack(seed=1))
    after = sc.measure_stack_array(make_stack(seed=2))

    comparison = sc.compare_measurements(before, after)

    line = next(line for line in comparison.summary if line.startswith("noise in empty sky"))
    assert "not measured" in line
    assert "measured on a stack file" in line
    assert comparison.changes["normalized_noise"] is None


def stack_with_noise(value: float, uncertainty: float) -> sc.StackMeasurements:
    """Make measurements that carry an empty-aperture noise.

    Returns
    -------
    measurements : `StackMeasurements`
        With the given noise and uncertainty.
    """
    measured = sc.measure_stack_array(make_stack())
    measured.normalized_noise = value
    measured.normalized_noise_uncertainty = uncertainty
    return measured


def test_a_noise_change_inside_the_error_is_called_unchanged() -> None:
    """A change smaller than the combined error is not reported as a change."""
    comparison = sc.compare_measurements(stack_with_noise(0.0100, 0.0005), stack_with_noise(0.0103, 0.0005))

    line = next(line for line in comparison.summary if line.startswith("noise in empty sky"))
    assert "unchanged within the measurement error" in line
    assert "% of the reference stars' brightness" in line


def test_a_noise_change_outside_the_error_is_reported() -> None:
    """A large drop is reported with its size."""
    comparison = sc.compare_measurements(stack_with_noise(0.0100, 0.0002), stack_with_noise(0.0070, 0.0002))

    line = next(line for line in comparison.summary if line.startswith("noise in empty sky"))
    assert "down 30%" in line


def test_the_stack_noise_uses_the_stored_definition_with_the_registration_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reference frame and trimmed edges come from the record."""
    from astrometricslib.pipelines.shared.registration_record import (
        RegistrationRecord,
        write_registration_record,
    )
    from astrometricslib.pipelines.stacking.post_processing import record_quality
    from astrometricslib.pipelines.stacking.post_processing.stack_noise import StackNoiseOutcome

    stack = tmp_path / "S_Stacked.fits"
    stack.write_bytes(b"")
    reference = tmp_path / "ref.fits"
    reference.write_bytes(b"")
    write_registration_record(
        str(stack),
        RegistrationRecord(
            light_paths=(),
            homographies=(),
            included=(),
            crop_offset=(3, 5),
            reference_frame=str(reference),
            is_spectral=False,
        ),
    )
    seen = {}

    def fake(*args: object, **kwargs: object) -> StackNoiseOutcome:
        """Record what the measurement was asked, and report no result.

        Returns
        -------
        outcome : `StackNoiseOutcome`
            An outcome with no result.
        """
        seen["args"], seen["kwargs"] = args, kwargs
        return StackNoiseOutcome(None, "no apertures")

    monkeypatch.setattr(record_quality, "measure_noise_safely", fake)

    outcome, notes = sc.measure_empty_aperture_noise(str(stack), "apertures.json")

    assert outcome.reason == "no apertures"
    assert notes == []
    assert seen["args"][:4] == (str(stack), str(reference), False, (3, 5))
    assert seen["kwargs"]["may_choose_apertures"] is False


def test_a_stack_with_no_record_says_the_reference_is_not_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With neither record the note says so, and the measurement still runs."""
    from astrometricslib.pipelines.stacking.post_processing import record_quality
    from astrometricslib.pipelines.stacking.post_processing.stack_noise import StackNoiseOutcome

    stack = tmp_path / "S_Stacked.fits"
    stack.write_bytes(b"")
    monkeypatch.setattr(record_quality, "measure_noise_safely", lambda *a, **k: StackNoiseOutcome(None, "x"))

    _, notes = sc.measure_empty_aperture_noise(str(stack), "apertures.json")

    assert "not recoverable" in notes[0]
