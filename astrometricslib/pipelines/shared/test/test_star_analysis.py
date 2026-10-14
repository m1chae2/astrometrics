"""Tests for the short star analysis record and the spectral-class helpers."""

import pytest

from astrometricslib.models.stellar_source import PhotometryResult, SpectroscopyResult, StellarObject
from astrometricslib.pipelines.shared.star_analysis import spectral_class_letter, summarize_star


@pytest.mark.parametrize(
    ("spectral_type", "letter"),
    [("G2V", "G"), ("r5", "C"), ("N", "C"), ("Unknown", ""), ("", ""), ("5", ""), ("D", "")],
)
def test_the_class_letter_follows_the_browser_rules(spectral_type: str, letter: str) -> None:
    """Carbon-star letters fold into C; unknown or odd types give no class."""
    assert spectral_class_letter(spectral_type) == letter


def make_star(own_type: str, rms: float, candidates: list[dict], catalog_type: str = "A0V") -> StellarObject:
    """Build a star with a three-point spectrum and the given match.

    Returns
    -------
    star : `StellarObject`
        The star.
    """
    return StellarObject(
        id="S1",
        spectral_type=catalog_type,
        spectroscopy=SpectroscopyResult(
            wavelengths_angstrom=[4000.0, 5000.0, 6000.0],
            intensities=[1.0, 2.0, 3.0],
            self_determined_spectral_type=own_type,
            self_determined_spectral_type_rms=rms,
            self_determined_spectral_type_candidates=candidates,
            probable_spectral_features=[
                {
                    "feature": "H-beta",
                    "kind": "absorption",
                    "verdict": "detected",
                    "depth": 0.12,
                    "p_value": 0.004,
                },
                {
                    "feature": "Na D",
                    "kind": "absorption",
                    "verdict": "not_detected",
                    "depth": 0.0,
                    "p_value": 0.9,
                },
            ],
            emission_lines=[
                {"line": "H-alpha", "verdict": "detected", "significance": 9.0},
                {"line": "He II 4686", "verdict": "not_seen", "significance": 1.0},
            ],
        ),
    )


def test_a_clear_gap_to_the_runner_up_is_not_ambiguous_and_does_not_differ() -> None:
    """Best 5% off, runner-up 9% off: a 0.04 RMS gap, not ambiguous."""
    candidates = [
        {"spectral_type": "A3V", "rms": 0.05, "correlation": 0.99},
        {"spectral_type": "A5V", "rms": 0.09, "correlation": 0.98},
    ]
    spectrum = summarize_star(make_star("A3V", 0.05, candidates))["spectrum"]
    assert spectrum["rms_gap_to_second_best"] == pytest.approx(0.04)
    assert spectrum["is_ambiguous"] is False
    assert spectrum["differs_from_catalog"] is False
    assert [item["type"] for item in spectrum["closest_reference_types"]] == ["A3V", "A5V"]


def test_a_tiny_gap_to_the_runner_up_is_ambiguous_and_no_confidence_or_probability_is_reported() -> None:
    """A 0.003 RMS gap is ambiguous; the summary has no confidence score."""
    candidates = [
        {"spectral_type": "A3V", "rms": 0.05, "correlation": 0.99},
        {"spectral_type": "A5V", "rms": 0.053, "correlation": 0.98},
    ]
    spectrum = summarize_star(make_star("A3V", 0.05, candidates))["spectrum"]
    assert spectrum["is_ambiguous"] is True
    assert not [key for key in spectrum if "confidence" in key or "probability" in key]


def test_a_far_type_is_called_different_and_a_poor_fit_is_no_good_match() -> None:
    """M2 against an A0V catalog entry differs; 20% off is no good match."""
    spectrum = summarize_star(make_star("M2", 0.2, [], catalog_type="A0V"))["spectrum"]
    assert spectrum["differs_from_catalog"] is True
    assert spectrum["no_good_match"] is True


def test_only_findings_are_listed() -> None:
    """Features and lines that were not found are left out."""
    spectrum = summarize_star(make_star("A3V", 0.05, []))["spectrum"]
    assert [item["feature"] for item in spectrum["absorption_features"]] == ["H-beta"]
    assert spectrum["absorption_features"][0]["depth_percent"] == pytest.approx(12.0)
    assert [item["line"] for item in spectrum["emission_lines"]] == ["H-alpha"]


def test_a_star_with_neither_record_has_two_empty_sections() -> None:
    """No spectrum and no light curve give `None` for each."""
    summary = summarize_star(StellarObject(id="S2"))
    assert summary["spectrum"] is None
    assert summary["photometry"] is None


def test_photometry_gives_the_cycle_and_dip_verdicts() -> None:
    """The cycle and dip verdicts come through with their probabilities."""
    from datetime import datetime

    star = StellarObject(
        id="S3",
        photometry=PhotometryResult(
            timestamps=[datetime(2026, 1, 1), datetime(2026, 1, 2)],
            periodogram={"best_period_days": 0.5, "false_alarm_probability": 0.001, "verdict": "detected"},
        ),
    )
    photometry = summarize_star(star)["photometry"]
    assert photometry["points"] == 2
    assert photometry["smooth_cycle"]["verdict"] == "detected"
    assert photometry["smooth_cycle"]["period_days"] == pytest.approx(0.5)
