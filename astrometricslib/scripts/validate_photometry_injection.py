r"""Measure which variability signals the candidate rule finds, on real data.

The photometry pipeline flags a star as a variable candidate when its score
is above 1 and its false discovery q-value is below 0.01 (see
`pipelines/photometry/processing/variability_indices.py`). On the real M 13
field the rule accepted 5 of the 37 stars the catalogs list as variable, and
nothing said whether the other 32 vary too little to see or whether the rule
is missing signals it should find. This script answers by injection: it adds
a signal of known depth to a star's real light curve and asks whether the rule
flags the star, across depths and star brightnesses.

For each realization the script:

1. reads every star of one target that has a light curve (read-only, from the
   catalog database);
2. leaves out the stars the rule already flags, and the stars the catalogs
   list as variable, as hosts for injection;
3. picks about 8 percent of the field as hosts, each with one signal (a
   sinusoid or a box transit) of one depth, multiplying the host's normalized
   flux, detrended flux, raw flux and their errors by the signal;
4. runs the rule on the whole field and records which injected stars were
   flagged and how many untouched stars were flagged.

Because the hosts' own noise is real, with its red noise and its flat-field
and centroid errors, the recovered fraction is a better measure than one made
on synthetic light curves with white noise.

    python -m astrometricslib.scripts.validate_photometry_injection \
        --target "M 13"

Limits. The injection is made on the light curves, so it measures the
candidate rule and the noise of the photometry. It does not test the
aperture photometry or the comparison-star normalization, which would need
signals added to the pixels. A signal is added to a normalized light curve
as it is, so the ensemble does not respond to it (a real variable among the
comparison stars would). Hosts are drawn from the field's unflagged stars,
which is a field with its own variables removed, and the injected stars
slightly change the field's noise model and thresholds, about as much as real
variables would. Depths are peak-to-peak magnitudes for the sinusoid and the
drop in magnitude for the transit.
"""

import argparse
import json
import math
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from astrometricslib import Astrometrics
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.photometry.processing.variability_indices import (
    FALSE_DISCOVERY_LEVEL,
    assess_variability,
)

DEPTHS_MAG = (0.005, 0.01, 0.02, 0.04, 0.08, 0.16)
SIGNAL_SHAPES = ("sinusoid", "transit")
BRIGHTNESS_BINS = 4

# The share of a field's stars that receive a signal in one realization. A
# bigger share changes the field's noise model by more than real variables
# would; a smaller one needs more realizations.
INJECTED_FRACTION = 0.08

# A sinusoid completes this many cycles over a light curve, and a transit
# lasts this fraction of its span, centred in it.
SINUSOID_CYCLES = 2.5
TRANSIT_DURATION_FRACTION = 0.2

DEFAULT_REALIZATIONS = 80
DEFAULT_MINIMUM_POINTS = 25

_QUERY = """
    SELECT data_json FROM stellar_objects
    WHERE target_id = ?
      AND json_extract(data_json, '$.photometry.falseDiscoveryQValue') IS NOT NULL
"""


@dataclass
class CellResult:
    """How many injected stars of one kind were flagged.

    Attributes
    ----------
    injected : `int`
        How many stars received this kind of signal.
    flagged : `int`
        How many of them the rule flagged.
    """

    injected: int = 0
    flagged: int = 0

    @property
    def completeness(self) -> float | None:
        """Give the share of injected stars that were flagged.

        Returns
        -------
        completeness : `float` or `None`
            Flagged over injected, or `None` when none were injected.
        """
        return self.flagged / self.injected if self.injected else None


@dataclass
class InjectionResult:
    """What a set of realizations found.

    Attributes
    ----------
    cells : `dict`
        `CellResult` for each ``(shape, depth, brightness_bin)``.
    untouched : `CellResult`
        How many stars that received no signal were flagged (``injected`` is
        the count of such stars over all realizations).
    brightness_edges : `list` [`float`]
        The instrumental-magnitude edges of the brightness bins, brightest
        first. ``brightness_edges[i]`` to ``brightness_edges[i + 1]`` is bin
        ``i``.
    hosts : `int`
        How many stars of the field could host a signal.
    field_size : `int`
        How many stars the field had.
    """

    cells: dict[tuple[str, float, int], CellResult] = field(default_factory=dict)
    untouched: CellResult = field(default_factory=CellResult)
    brightness_edges: list[float] = field(default_factory=list)
    hosts: int = 0
    field_size: int = 0


def signal_factor(shape: str, depth_mag: float, times_seconds: np.ndarray) -> np.ndarray:
    """Build the flux factor of a signal over a set of times.

    Parameters
    ----------
    shape : `str`
        ``"sinusoid"`` or ``"transit"``.
    depth_mag : `float`
        The peak-to-peak change in magnitudes for a sinusoid, and the drop in
        magnitudes during a transit.
    times_seconds : `numpy.ndarray`
        The times of the points, in seconds from any origin.

    Returns
    -------
    factor : `numpy.ndarray`
        The factor to multiply the fluxes by. 1 where there is no signal.

    Raises
    ------
    ValueError
        If the shape is not one of `SIGNAL_SHAPES`.
    """
    span = float(np.ptp(times_seconds))
    phase = (times_seconds - times_seconds.min()) / span if span > 0 else np.zeros_like(times_seconds)
    if shape == "sinusoid":
        magnitude_change = 0.5 * depth_mag * np.sin(2.0 * math.pi * SINUSOID_CYCLES * phase)
    elif shape == "transit":
        half = TRANSIT_DURATION_FRACTION / 2.0
        magnitude_change = np.where(np.abs(phase - 0.5) <= half, depth_mag, 0.0)
    else:
        raise ValueError(f"The signal shape must be one of {SIGNAL_SHAPES}, got {shape!r}.")
    return 10.0 ** (-0.4 * magnitude_change)


def inject_signal(star: StellarObject, shape: str, depth_mag: float) -> None:
    """Multiply a star's light curve by a signal, in place.

    The normalized flux, the detrended flux, the raw flux and their errors are
    all multiplied, so the star's relative errors stay as measured.

    Parameters
    ----------
    star : `StellarObject`
        The star. Its light curve must have one time for each value.
    shape : `str`
        ``"sinusoid"`` or ``"transit"``.
    depth_mag : `float`
        The depth of the signal in magnitudes.
    """
    photometry = star.photometry
    times = np.array([stamp.timestamp() for stamp in photometry.timestamps])
    factor = signal_factor(shape, depth_mag, times)
    for name in (
        "fluxes",
        "fluxes_normalized",
        "fluxes_detrended",
        "flux_errors",
        "fluxes_normalized_errors",
        "fluxes_detrended_errors",
    ):
        values = getattr(photometry, name)
        if values and len(values) == factor.size:
            setattr(photometry, name, (np.asarray(values, dtype=float) * factor).tolist())


def is_flagged(star: StellarObject) -> bool:
    """Say whether the candidate rule flagged a star in `assess_variability`.

    Parameters
    ----------
    star : `StellarObject`
        A star after `assess_variability` has written its numbers.

    Returns
    -------
    flagged : `bool`
        `True` when its score is above 1 and its q-value is below
        `FALSE_DISCOVERY_LEVEL`.
    """
    photometry = star.photometry
    return bool(
        photometry.variability_score is not None
        and photometry.variability_score > 1.0
        and photometry.false_discovery_q_value is not None
        and photometry.false_discovery_q_value < FALSE_DISCOVERY_LEVEL
    )


def run_injection(
    stars: Sequence[StellarObject],
    known_variable_ids: set[str],
    realizations: int,
    seed: int,
    minimum_points: int = DEFAULT_MINIMUM_POINTS,
) -> InjectionResult:
    """Inject signals into a field's real light curves; count recoveries.

    Parameters
    ----------
    stars : `Sequence` [`StellarObject`]
        The field's stars with light curves (not changed; each realization
        works on copies).
    known_variable_ids : `set` [`str`]
        Ids of stars the catalogs list as variable. They never host a signal.
    realizations : `int`
        How many random draws to make.
    seed : `int`
        Seed of the random generator, so a run can be repeated.
    minimum_points : `int`, optional
        A star needs at least this many points, all with a time, to host a
        signal.

    Returns
    -------
    result : `InjectionResult`
        The counts for each cell, and for the stars left untouched.
    """
    generator = np.random.default_rng(seed)
    baseline = [star.model_copy(deep=True) for star in stars]
    assess_variability(baseline)
    flagged_at_baseline = {star.id for star in baseline if is_flagged(star)}
    hosts = [
        index
        for index, star in enumerate(baseline)
        if star.id not in known_variable_ids
        and star.id not in flagged_at_baseline
        and star.photometry.instrumental_mag is not None
        and len(star.photometry.timestamps) >= minimum_points
        and len(star.photometry.timestamps) == len(star.photometry.fluxes_normalized)
    ]
    magnitudes = np.array([baseline[index].photometry.instrumental_mag for index in hosts])
    edges = [float(value) for value in np.quantile(magnitudes, np.linspace(0.0, 1.0, BRIGHTNESS_BINS + 1))]
    bin_of = {
        index: int(
            min(
                np.searchsorted(edges[1:-1], baseline[index].photometry.instrumental_mag), BRIGHTNESS_BINS - 1
            )
        )
        for index in hosts
    }
    result = InjectionResult(brightness_edges=edges, hosts=len(hosts), field_size=len(stars))
    per_realization = max(1, int(INJECTED_FRACTION * len(stars)))
    for _ in range(realizations):
        field_copy = [star.model_copy(deep=True) for star in stars]
        chosen = generator.choice(hosts, size=min(per_realization, len(hosts)), replace=False)
        injected: dict[int, tuple[str, float, int]] = {}
        for index in chosen:
            shape = SIGNAL_SHAPES[int(generator.integers(len(SIGNAL_SHAPES)))]
            depth = DEPTHS_MAG[int(generator.integers(len(DEPTHS_MAG)))]
            inject_signal(field_copy[int(index)], shape, depth)
            injected[int(index)] = (shape, depth, bin_of[int(index)])
        assess_variability(field_copy)
        for index, star in enumerate(field_copy):
            if index in injected:
                cell = result.cells.setdefault(injected[index], CellResult())
                cell.injected += 1
                cell.flagged += int(is_flagged(star))
            elif star.id not in known_variable_ids and star.id not in flagged_at_baseline:
                result.untouched.injected += 1
                result.untouched.flagged += int(is_flagged(star))
    return result


def wilson_interval(flagged: int, injected: int, z: float = 1.0) -> tuple[float, float]:
    """Give the Wilson score interval of a proportion.

    Parameters
    ----------
    flagged : `int`
        The number of successes.
    injected : `int`
        The number of trials. Above zero.
    z : `float`, optional
        How many standard deviations the interval spans (1 is about 68
        percent).

    Returns
    -------
    low, high : `float`
        The ends of the interval.
    """
    proportion = flagged / injected
    denominator = 1.0 + z**2 / injected
    centre = (proportion + z**2 / (2.0 * injected)) / denominator
    half = (
        z * math.sqrt(proportion * (1.0 - proportion) / injected + z**2 / (4.0 * injected**2)) / denominator
    )
    return max(0.0, centre - half), min(1.0, centre + half)


def format_report(result: InjectionResult, target: str) -> str:
    """Write the result as a table of completeness by depth and brightness.

    Parameters
    ----------
    result : `InjectionResult`
        What `run_injection` returned.
    target : `str`
        The target's name, for the heading.

    Returns
    -------
    text : `str`
        The report, one table for each signal shape.
    """
    lines = [
        f"{target}: {result.field_size} stars with light curves, {result.hosts} able to host a signal.",
        f"Rule: score above 1 and false discovery q-value below {FALSE_DISCOVERY_LEVEL:g}.",
    ]
    edges = result.brightness_edges
    for shape in SIGNAL_SHAPES:
        lines.append("")
        lines.append(f"{shape}: share of injected stars flagged (count), by depth and brightness")
        header = "depth (mag)  " + "  ".join(
            f"mag {edges[i]:5.1f}-{edges[i + 1]:5.1f}" for i in range(BRIGHTNESS_BINS)
        )
        lines.append(header)
        for depth in DEPTHS_MAG:
            cells = []
            for bin_index in range(BRIGHTNESS_BINS):
                cell = result.cells.get((shape, depth, bin_index))
                if cell is None or not cell.injected:
                    cells.append("        -     ")
                else:
                    cells.append(f"{cell.completeness:5.0%} ({cell.injected:3d}) ")
            lines.append(f"{depth:10.3f}   " + "  ".join(cells))
    if result.untouched.injected:
        low, high = wilson_interval(result.untouched.flagged, result.untouched.injected)
        lines.append("")
        share = result.untouched.flagged / result.untouched.injected
        lines.append(
            f"untouched stars flagged: {result.untouched.flagged} of {result.untouched.injected} "
            f"({share:.2%}; 68% interval {low:.2%} to {high:.2%})"
        )
    return "\n".join(lines)


def load_field(target: str, minimum_points: int) -> tuple[list[StellarObject], set[str]]:
    """Read a target's stars with light curves from the catalog database.

    Only stars that the latest photometry run recorded variability numbers
    for are used. The database is opened read-only.

    Parameters
    ----------
    target : `str`
        The target's id.
    minimum_points : `int`
        Stars with fewer normalized points are left out.

    Returns
    -------
    stars : `list` [`StellarObject`]
        The stars.
    known_variable_ids : `set` [`str`]
        The ids of stars the catalogs list as variable.
    """
    import os

    from astrometricslib.models.known_variability import KnownVariability

    database = os.path.join(str(Astrometrics().config.get_library_path()), "astrometrics.db")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    stars = []
    known = set()
    for (data_json,) in connection.execute(_QUERY, (target,)):
        star = StellarObject.model_validate(json.loads(data_json))
        if star.photometry is None or len(star.photometry.fluxes_normalized) < minimum_points:
            continue
        stars.append(star)
        if star.known_variability == KnownVariability.KNOWN_VARIABLE:
            known.add(star.id)
    return stars, known


def main(argv: Sequence[str] | None = None) -> int:
    """Run the injection on one target and print the report.

    Parameters
    ----------
    argv : `Sequence` [`str`], optional
        Command-line arguments. Defaults to ``sys.argv[1:]``.

    Returns
    -------
    status : `int`
        0 on success, 1 when the target has too few stars.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--target", required=True, help="The target's id, such as 'M 13'.")
    parser.add_argument("--realizations", type=int, default=DEFAULT_REALIZATIONS)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--minimum-points", type=int, default=DEFAULT_MINIMUM_POINTS)
    parser.add_argument("--json", help="Also write the counts to this file.")
    arguments = parser.parse_args(argv)

    stars, known = load_field(arguments.target, arguments.minimum_points)
    if len(stars) < 60:
        print(f"{arguments.target}: only {len(stars)} stars with usable light curves; nothing to measure.")
        return 1
    result = run_injection(stars, known, arguments.realizations, arguments.seed, arguments.minimum_points)
    print(format_report(result, arguments.target))
    if arguments.json:
        payload = {
            "target": arguments.target,
            "realizations": arguments.realizations,
            "seed": arguments.seed,
            "brightness_edges": result.brightness_edges,
            "cells": {
                f"{shape}|{depth}|{bin_index}": {"injected": cell.injected, "flagged": cell.flagged}
                for (shape, depth, bin_index), cell in sorted(result.cells.items())
            },
            "untouched": {"stars": result.untouched.injected, "flagged": result.untouched.flagged},
        }
        with open(arguments.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
