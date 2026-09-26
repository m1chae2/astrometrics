r"""Check that the spectral and period analyses tell signal from noise.

Each analysis (absorption-feature test, spectral-type match, period
search, dip search) always produces a "best" answer, so what matters is
how often it calls pure noise a finding, and how often it finds a real
signal. This script measures both, and never writes to the catalog.

1. Synthetic noise: many random spectra and light curves with no signal.
   The share called "detected" must stay near the cutoff each verdict
   promises (about 1%).
2. Injected signals: known absorption dips and known repeating patterns
   added to noise, to see how strong they must be to be found.
3. Real standards: Vega (A0V), Alcor and Alnath, whose spectra are
   extracted from their master stacks in memory and compared with what
   they should show. Spectra already stored for stars with catalog types
   are compared too.

    python -m astrometricslib.scripts.validate_spectral_and_period_analysis

The numbers it prints are the validation the thresholds in
`spectral_feature_detector`, `spectral_classifier` and
`periodicity_search` cite.
"""

import argparse
import sys

import numpy as np
from scipy.ndimage import gaussian_filter1d

from astrometricslib import Astrometrics
from astrometricslib.pipelines.photometry.periodicity_search import (
    VERDICT_DETECTED,
    box_search,
    lomb_scargle_search,
)
from astrometricslib.pipelines.spectroscopy.spectral_classifier import nearest_reference_type
from astrometricslib.pipelines.spectroscopy.spectral_feature_detector import detect_named_features

# The largest share of pure-noise trials allowed to be called "detected".
# The verdict cutoff is 1%; three times that leaves room for the finite
# number of trials.
_MAXIMUM_NOISE_DETECTION_RATE = 0.03


def _noise_spectrum(seed: int, wavelength: np.ndarray, noise_fraction: float) -> np.ndarray:
    """Build a spectrum with a curved continuum and correlated noise.

    Neighboring samples share noise (the spectrum is oversampled), which
    is what makes a plain per-sample noise estimate too trusting.

    Returns
    -------
    spectrum : `np.ndarray`
        A featureless spectrum with 1 as its typical level.
    """
    random_generator = np.random.default_rng(seed)
    noise = gaussian_filter1d(random_generator.normal(0.0, 1.0, wavelength.size), 1.2)
    noise = noise / noise.std() * noise_fraction
    continuum = 1.0 + 0.5 * np.sin((wavelength - 3800.0) / 4200.0 * np.pi * 0.9)
    return continuum * (1.0 + noise)


def validate_feature_detector(trials: int) -> bool:
    """Measure the feature test's false-positive rate and recovery of dips.

    Parameters
    ----------
    trials : `int`
        Number of random spectra per case.

    Returns
    -------
    passed : `bool`
        `True` when the noise detection rate is within its limit.
    """
    wavelength = np.arange(3800.0, 8000.0, 10.9)
    tested = detected = 0
    for seed in range(trials):
        for entry in detect_named_features(wavelength, _noise_spectrum(seed, wavelength, 0.02)):
            if entry["verdict"] != "not_covered":
                tested += 1
                detected += entry["verdict"] == "detected"
    rate = detected / max(tested, 1)
    print(f"Feature test, pure noise ({trials} spectra, {tested} feature tests): {rate:.2%} called detected")
    emission_detected = 0
    for seed in range(trials):
        for entry in detect_named_features(wavelength, _noise_spectrum(seed, wavelength, 0.02)):
            emission_detected += entry["verdict"] == "detected" and entry.get("kind") == "emission"
    print(
        f"  of which called emission (a bump, not a dip): {emission_detected / max(tested, 1):.2%}"
        " (both directions are tested and the p-value pays for both)"
    )
    print("  pure noise called inconclusive (a dip worth a second look), by noise level:")
    for noise_fraction in (0.02, 0.05, 0.10, 0.20):
        tested_at_level = inconclusive = 0
        for seed in range(trials):
            for entry in detect_named_features(wavelength, _noise_spectrum(seed, wavelength, noise_fraction)):
                if entry["verdict"] != "not_covered":
                    tested_at_level += 1
                    inconclusive += entry["verdict"] == "inconclusive"
        print(f"    {noise_fraction:.0%} noise: {inconclusive / max(tested_at_level, 1):.1%}")
    print("  injected H-alpha dip of a given depth (FWHM 50 A, 2% noise) -> share detected:")
    for depth in (0.05, 0.10, 0.20):
        hits = 0
        for seed in range(trials):
            spectrum = _noise_spectrum(1000 + seed, wavelength, 0.02)
            spectrum = spectrum - depth * np.exp(-0.5 * ((wavelength - 6563.0) / (50.0 / 2.355)) ** 2)
            entry = next(e for e in detect_named_features(wavelength, spectrum) if "H-alpha" in e["feature"])
            hits += entry["verdict"] == "detected"
        print(f"    {depth:.0%}: {hits / trials:.0%}")
    print("  injected H-alpha EMISSION bump (FWHM 50 A, 2% noise) -> share detected as emission:")
    for height in (0.05, 0.10, 0.20):
        hits = 0
        for seed in range(trials):
            spectrum = _noise_spectrum(2000 + seed, wavelength, 0.02)
            spectrum = spectrum + height * np.exp(-0.5 * ((wavelength - 6563.0) / (50.0 / 2.355)) ** 2)
            entry = next(e for e in detect_named_features(wavelength, spectrum) if "H-alpha" in e["feature"])
            hits += entry["verdict"] == "detected" and entry.get("kind") == "emission"
        print(f"    {height:.0%}: {hits / trials:.0%}")
    return rate <= _MAXIMUM_NOISE_DETECTION_RATE


def validate_period_searches(trials: int) -> bool:
    """Measure the period and dip searches' false-positive rates and recovery.

    Parameters
    ----------
    trials : `int`
        Number of random light curves per case.

    Returns
    -------
    passed : `bool`
        `True` when both searches' noise detection rates are within limit.
    """
    times = np.arange(200) * 0.0125 + np.random.default_rng(0).normal(0.0, 0.0125 * 0.05, 200)
    periodogram_false = box_false = 0
    for seed in range(trials):
        flux = 1.0 + np.random.default_rng(seed).normal(0.0, 0.01, times.size)
        periodogram_false += lomb_scargle_search(times, flux).verdict == VERDICT_DETECTED
        box_false += box_search(times, flux).verdict == VERDICT_DETECTED
    print(
        f"Cycle search, pure noise ({trials} light curves): {periodogram_false / trials:.2%} called detected"
    )
    print(f"Dip search, pure noise ({trials} light curves): {box_false / trials:.2%} called detected")
    print("  injected signals (1% noise, 200 points over 2.5 days) -> share detected:")
    for amplitude in (0.01, 0.02, 0.04):
        hits = 0
        for seed in range(trials):
            flux = 1.0 + 0.01 * np.random.default_rng(500 + seed).normal(size=times.size)
            flux += amplitude * np.sin(2 * np.pi * times / 0.4)
            hits += lomb_scargle_search(times, flux).verdict == VERDICT_DETECTED
        print(f"    0.4 day cycle, amplitude {amplitude:.0%}: {hits / trials:.0%}")
    for depth in (0.02, 0.04, 0.08):
        hits = 0
        for seed in range(trials):
            flux = 1.0 + 0.01 * np.random.default_rng(700 + seed).normal(size=times.size)
            flux[(times % 0.5) < 0.04] -= depth
            hits += box_search(times, flux).verdict == VERDICT_DETECTED
        print(f"    0.5 day dips, depth {depth:.0%}: {hits / trials:.0%}")
    return (
        periodogram_false / trials <= _MAXIMUM_NOISE_DETECTION_RATE
        and box_false / trials <= _MAXIMUM_NOISE_DETECTION_RATE
    )


def validate_standards(astrometrics: Astrometrics) -> None:
    """Compare real standard stars with what they should show.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        Provides the targets and catalog. Nothing is saved.
    """
    from astrometricslib.pipelines.astrometry.pipeline import AstrometryPipeline
    from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline

    camera_name = astrometrics.config.get_primary_camera_name()
    print(f"Standard stars from their master spectral stacks (camera {camera_name}):")
    for target_id, truth in (("Vega", "A0V"), ("Alcor", "A5V or A2V"), ("Alnath", "B7III")):
        target = astrometrics.targets.get(target_id)
        path = getattr(target, "stacked_spectral_target", None) if target else None
        if not path:
            print(f"  {target_id}: no master spectral stack")
            continue
        context = AstrometryPipeline().process(path, attempt_plate_solving=False)
        for star in SpectroscopyPipeline().process(context, limit=2):
            spectroscopy = star.spectroscopy
            balmer = [
                f"{str(f['feature']).split('(')[-1].strip(')')}:{f['verdict'][:3]}"
                for f in spectroscopy.probable_spectral_features
                if "Balmer" in f["feature"] and f["verdict"] != "not_covered"
            ]
            print(
                f"  {target_id} (expect {truth}): match {spectroscopy.self_determined_spectral_type}"
                f" rms {spectroscopy.self_determined_spectral_type_rms}; Balmer {', '.join(balmer)}"
            )

    print("Stored spectra of stars with a catalog type (match vs catalog):")
    summaries = [s for s in astrometrics.catalog_access.list_star_summaries() if s.has_spectra]
    good = agree = total = 0
    for star in astrometrics.catalog_access.get_by_ids("stellar_catalog", [s.id for s in summaries]):
        reference = nearest_reference_type(star.spectral_type)
        spectroscopy = star.spectroscopy
        if reference is None or spectroscopy.self_determined_spectral_type in ("", "Unknown"):
            continue
        total += 1
        if (spectroscopy.self_determined_spectral_type_rms or 1.0) <= 0.15:
            good += 1
            agree += reference[0] == spectroscopy.self_determined_spectral_type[0]
    print(
        f"  {total} stars with a catalog type and a match; {good} good matches; "
        f"letter agrees for {agree} of {good}"
    )


def main() -> int:
    """Run every check and report.

    Returns
    -------
    exit_code : `int`
        ``0`` when the noise checks pass, ``1`` otherwise.
    """
    parser = argparse.ArgumentParser(description="Validate the spectral and period analyses.")
    parser.add_argument("--trials", type=int, default=100, help="Random trials per synthetic check.")
    parser.add_argument("--skip-standards", action="store_true", help="Skip the real-star checks.")
    arguments = parser.parse_args()

    features_ok = validate_feature_detector(arguments.trials)
    periods_ok = validate_period_searches(max(20, arguments.trials // 4))
    if not arguments.skip_standards:
        validate_standards(Astrometrics())
    print("\nNoise checks:", "PASS" if features_ok and periods_ok else "FAIL")
    return 0 if features_ok and periods_ok else 1


if __name__ == "__main__":
    sys.exit(main())
