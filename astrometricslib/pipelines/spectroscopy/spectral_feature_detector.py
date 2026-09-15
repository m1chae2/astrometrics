"""Detects named absorption features from a local dip in the spectrum.

A slitless grism at amateur resolution (R~100-300, roughly 2-5 nm per
resolution element) can't resolve individual metal lines the way a lab
spectrograph can, but it can resolve broad, well-separated features --
the hydrogen Balmer series, the Ca II H&K blend, the Mg b and Na D
blends -- as a measurable dip against the surrounding continuum. This
looks for exactly that: a local brightness dip at each feature's known
wavelength, scored against the noise in the surrounding continuum.

This is a heuristic detector, not a chemical-abundance measurement. It
reports which named features plausibly show up in the spectrum's shape,
and how confidently, not elemental abundances derived from first
principles.
"""

import numpy as np

from astrometricslib.pipelines.shared.quality.detection_confidence import significance_to_confidence

# Rest wavelengths of features broad or strong enough for a low-resolution
# slitless grism to plausibly resolve as a distinct dip. window_angstrom is
# the half-width of the feature's own core; the surrounding continuum is
# sampled just outside that core on each side.
NAMED_FEATURES: tuple[dict[str, object], ...] = (
    {"name": "Hydrogen Balmer series (H-alpha)", "wavelength_angstrom": 6563.0, "window_angstrom": 25.0},
    {"name": "Hydrogen Balmer series (H-beta)", "wavelength_angstrom": 4861.0, "window_angstrom": 25.0},
    {"name": "Hydrogen Balmer series (H-gamma)", "wavelength_angstrom": 4340.0, "window_angstrom": 20.0},
    {"name": "Hydrogen Balmer series (H-delta)", "wavelength_angstrom": 4102.0, "window_angstrom": 20.0},
    {"name": "Calcium II H & K", "wavelength_angstrom": 3950.0, "window_angstrom": 35.0},
    {"name": "Magnesium b triplet", "wavelength_angstrom": 5175.0, "window_angstrom": 20.0},
    {"name": "Sodium D doublet", "wavelength_angstrom": 5893.0, "window_angstrom": 15.0},
    {"name": "Iron/titanium blend (G band)", "wavelength_angstrom": 4300.0, "window_angstrom": 20.0},
)

# Below this many points in a continuum shoulder or a feature's core, the
# median/noise estimate is too noisy itself to trust.
_MINIMUM_WINDOW_POINTS = 3


def _local_continuum_and_noise(
    wavelength_angstrom: np.ndarray, intensity: np.ndarray, center: float, half_window: float
) -> tuple[float, float] | None:
    """Estimate the local continuum level and noise around one feature.

    Samples two "shoulder" bands flanking the feature's core -- close
    enough to reflect the true local continuum, far enough to avoid the
    feature itself.

    Returns
    -------
    continuum_and_noise : `tuple` [`float`, `float`] or `None`
        The shoulders' median (continuum level) and standard deviation
        (noise), or `None` if too few points fell in the shoulders.
    """
    left = (wavelength_angstrom >= center - 2 * half_window) & (wavelength_angstrom < center - half_window)
    right = (wavelength_angstrom > center + half_window) & (wavelength_angstrom <= center + 2 * half_window)
    shoulder = intensity[left | right]
    if shoulder.size < _MINIMUM_WINDOW_POINTS:
        return None
    return float(np.median(shoulder)), float(np.std(shoulder))


def detect_named_features(wavelength_angstrom: np.ndarray, intensity: np.ndarray) -> list[dict[str, object]]:
    """Look for named absorption features in an observed spectrum.

    For each feature in `NAMED_FEATURES`, compares the flux at its core
    against the local continuum estimated from its shoulders. A feature
    is reported only when the core is measurably dimmer than that
    continuum; the reported confidence saturates as the dip's depth grows
    relative to the local noise, but is a heuristic score, not a
    calibrated detection probability.

    Parameters
    ----------
    wavelength_angstrom : `np.ndarray`
        The observed spectrum's wavelength grid, in Angstroms.
    intensity : `np.ndarray`
        The observed spectrum's brightness at each wavelength.

    Returns
    -------
    detections : `list` [`dict`]
        One entry per detected feature, most confident first. Each has
        ``"feature"`` (name), ``"wavelength_angstrom"`` (its rest
        wavelength), ``"depth"`` (fractional dip below the local
        continuum), and ``"confidence"`` (0 to 1).
    """
    wavelength_angstrom = np.asarray(wavelength_angstrom, dtype=float)
    intensity = np.asarray(intensity, dtype=float)
    valid = np.isfinite(wavelength_angstrom) & np.isfinite(intensity)
    wavelength_angstrom = wavelength_angstrom[valid]
    intensity = intensity[valid]

    if wavelength_angstrom.size == 0:
        return []

    order = np.argsort(wavelength_angstrom)
    wavelength_angstrom = wavelength_angstrom[order]
    intensity = intensity[order]

    detections: list[dict[str, object]] = []
    for feature in NAMED_FEATURES:
        center = float(feature["wavelength_angstrom"])
        half_window = float(feature["window_angstrom"])

        covered = (
            center - 2 * half_window >= wavelength_angstrom[0]
            and center + 2 * half_window <= wavelength_angstrom[-1]
        )
        if not covered:
            continue

        continuum_and_noise = _local_continuum_and_noise(wavelength_angstrom, intensity, center, half_window)
        if continuum_and_noise is None:
            continue
        continuum, noise = continuum_and_noise
        if continuum <= 0:
            continue

        in_core = (wavelength_angstrom >= center - half_window) & (
            wavelength_angstrom <= center + half_window
        )
        core = intensity[in_core]
        if core.size < _MINIMUM_WINDOW_POINTS:
            continue

        depth = (continuum - float(np.median(core))) / continuum
        if depth <= 0:
            continue  # only reports absorption dips, not emission bumps

        relative_noise = noise / continuum
        significance = depth / relative_noise if relative_noise > 0 else float("inf")
        confidence = significance_to_confidence(significance)

        detections.append({
            "feature": feature["name"],
            "wavelength_angstrom": center,
            "depth": depth,
            "confidence": confidence,
        })

    detections.sort(key=lambda entry: entry["confidence"], reverse=True)
    return detections
