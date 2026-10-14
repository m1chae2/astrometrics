"""Measure how many good samples small-stack pixel rejection throws out.

Draws many pixels of pure Gaussian noise (no cosmic rays, no satellites) with
N frames each, applies the rejection limits to every pixel the way Siril's
Winsorized sigma clipping does (approximately), and counts how many samples
get rejected. All of those samples are good, so the count is the cost of the
limits. The script also adds one bright outlier of fixed size to each pixel
and counts how often the limits catch it, which is the benefit.

Three rules are compared: the plain Chauvenet limit on both sides, the
Chauvenet limit with a floor, and the floor with a looser low limit
(`rejection_bounds` in `utilities/rejection_thresholds.py`). Two ways of
estimating the spread are compared: the plain sample standard deviation, and
a Winsorized estimate (values beyond 1.5 sigma pulled in to that limit,
repeated until the estimate settles, then multiplied by 1.134). The Winsorized
estimate is a simplified model of Siril's, not a copy of its code.

Usage: ``python -m astrometricslib.scripts.rejection_small_n_check``
"""

import argparse

import numpy as np

from astrometricslib.utilities.rejection_thresholds import chauvenet_sigma, rejection_bounds

FRAME_COUNTS = (5, 8, 15, 40)
WINSORIZE_AT = 1.5
WINSORIZED_SCALE = 1.134
WINSORIZE_ITERATIONS = 50


def sample_estimate(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return each pixel's mean and sample standard deviation.

    Parameters
    ----------
    values : `numpy.ndarray`
        Array of shape (pixels, frames).

    Returns
    -------
    center, sigma : `numpy.ndarray`
        One mean and one standard deviation per pixel.
    """
    return values.mean(axis=1), values.std(axis=1, ddof=1)


def winsorized_estimate(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return each pixel's median and Winsorized standard deviation.

    Parameters
    ----------
    values : `numpy.ndarray`
        Array of shape (pixels, frames).

    Returns
    -------
    center, sigma : `numpy.ndarray`
        One median and one Winsorized standard deviation per pixel.
    """
    median = np.median(values, axis=1)
    sigma = values.std(axis=1, ddof=1)
    for _ in range(WINSORIZE_ITERATIONS):
        limit = (WINSORIZE_AT * sigma)[:, None]
        pulled_in = np.clip(values, median[:, None] - limit, median[:, None] + limit)
        sigma = WINSORIZED_SCALE * pulled_in.std(axis=1, ddof=1)
    return median, sigma


def rejected_mask(values: np.ndarray, estimate: str, low: float, high: float) -> np.ndarray:
    """Mark the samples that fall outside the limits.

    Parameters
    ----------
    values : `numpy.ndarray`
        Array of shape (pixels, frames).
    estimate : `str`
        ``"sample"`` or ``"winsorized"``.
    low, high : `float`
        Limits in standard deviations below and above the center.

    Returns
    -------
    mask : `numpy.ndarray`
        True where a sample is rejected, same shape as ``values``.
    """
    center, sigma = (sample_estimate if estimate == "sample" else winsorized_estimate)(values)
    deviation = (values - center[:, None]) / sigma[:, None]
    return (deviation < -low) | (deviation > high)


def measure(pixels: int, outlier_sigma: float, seed: int) -> list[dict[str, float | int | str]]:
    """Measure the rejected fraction and the outlier catch rate for each rule.

    Parameters
    ----------
    pixels : `int`
        Pixels drawn for each frame count.
    outlier_sigma : `float`
        Size of the bright outlier, in true standard deviations.
    seed : `int`
        Seed for the random number generator.

    Returns
    -------
    rows : `list` [`dict`]
        One row per estimator, frame count and rule.
    """
    rng = np.random.default_rng(seed)
    rows: list[dict[str, float | int | str]] = []
    for n_frames in FRAME_COUNTS:
        clean = rng.standard_normal((pixels, n_frames))
        with_outlier = clean.copy()
        with_outlier[:, 0] += outlier_sigma
        k = chauvenet_sigma(n_frames)
        bounds = rejection_bounds(n_frames, floor=2.5, low_extra=0.5)
        rules = {
            "chauvenet both sides": (k, k),
            "floor 2.5, symmetric": (max(k, 2.5), max(k, 2.5)),
            "floor 2.5, low +0.5": (bounds.low, bounds.high),
        }
        for estimate in ("sample", "winsorized"):
            for rule, (low, high) in rules.items():
                good = rejected_mask(clean, estimate, low, high)
                caught = rejected_mask(with_outlier, estimate, low, high)[:, 0]
                rows.append({
                    "estimate": estimate,
                    "n": n_frames,
                    "rule": rule,
                    "low": low,
                    "high": high,
                    "good_samples_rejected": float(good.mean()),
                    "pixels_losing_a_sample": float(good.any(axis=1).mean()),
                    "outlier_caught": float(caught.mean()),
                })
    return rows


def main() -> None:
    """Run the measurement and print one table per estimator."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pixels", type=int, default=100_000)
    parser.add_argument("--outlier-sigma", type=float, default=8.0)
    parser.add_argument("--seed", type=int, default=20261010)
    args = parser.parse_args()

    rows = measure(args.pixels, args.outlier_sigma, args.seed)
    for estimate in ("sample", "winsorized"):
        print(f"\nEstimate: {estimate}  ({args.pixels} pixels per N, outlier {args.outlier_sigma} sigma)")
        print(f"{'N':>3}  {'rule':<22} {'low':>6} {'high':>6}", end=" ")
        print(f"{'good rej %':>11} {'pix hit %':>10} {'caught %':>9}")
        for row in rows:
            if row["estimate"] != estimate:
                continue
            print(
                f"{row['n']:>3}  {row['rule']:<22} {row['low']:>6.3f} {row['high']:>6.3f} "
                f"{100 * row['good_samples_rejected']:>11.2f} {100 * row['pixels_losing_a_sample']:>10.2f} "
                f"{100 * row['outlier_caught']:>9.1f}"
            )


if __name__ == "__main__":
    main()
