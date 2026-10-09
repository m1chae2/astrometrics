r"""Measure how the variable-star cutoff behaves against what the catalogs say.

The photometry pipeline flags a star as a variable candidate when its
coefficient of variation (CV: scatter divided by mean brightness) is above
``max(0.02, median + k * MAD)`` of the field's stars, with ``k`` = 7.4. That
multiplier was chosen so that "roughly the top 3%" are flagged, and nothing
checked it. This script checks it against the catalogs.

For each target it takes the stored CV of every star with a light curve, builds
the cutoff for each multiplier, and counts how many stars of each catalog
status land above it:

- known variables (SIMBAD, Gaia DR3 or VSX lists them): the share flagged is
  the recall of the cutoff on stars that really vary;
- stars the catalogs do not list: the share flagged is an upper bound on the
  false-alarm rate, because some of those flags are real variables nobody has
  catalogued (the point of the search);
- stars not looked up: reported for completeness.

It reads the catalog database and writes nothing.

    python -m astrometricslib.scripts.measure_variability_cutoff

Limits: the stored CV of a star is its latest (a long-term CV after sessions
are merged), while the pipeline builds a cutoff per session, so the cutoff
here is built per target; targets with fewer than ``--minimum-stars`` stars
are left out because a median and MAD of a handful of stars mean little; and a
star is counted under the target it is saved against.
"""

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.stats import rankdata

from astrometricslib import Astrometrics
from astrometricslib.models.known_variability import KnownVariability, combine_known_variability
from astrometricslib.pipelines.photometry.processing.variability_analyzer import (
    DEFAULT_VARIABILITY_SIGMA_THRESHOLD,
    adaptive_cv_cutoff,
)

# The multipliers to try. 7.4 is the pipeline's own.
MULTIPLIERS = (3.0, 4.0, 5.0, 6.0, DEFAULT_VARIABILITY_SIGMA_THRESHOLD, 9.0, 12.0, 16.0)

DEFAULT_MINIMUM_STARS = 100

# Light curves with fewer points than this are left out: with so few, a CV
# says little about variability.
DEFAULT_MINIMUM_POINTS = 8

_QUERY = """
    SELECT id, target_id,
           json_extract(data_json, '$.photometry.coefficientOfVariation'),
           json_array_length(data_json, '$.photometry.fluxesNormalized'),
           json_extract(data_json, '$.photometry.meanFlux'),
           json_extract(data_json, '$.simbadObjectTypes'),
           json_extract(data_json, '$.gaiaVariableFlag'),
           json_extract(data_json, '$.vsxVariabilityType')
    FROM stellar_objects
    WHERE has_photometry = 1
      AND json_extract(data_json, '$.photometry.coefficientOfVariation') IS NOT NULL
"""


@dataclass(frozen=True)
class LightCurveStar:
    """What the check needs to know about one star.

    Attributes
    ----------
    target_id : `str`
        The target the star is saved against.
    coefficient_of_variation : `float`
        The star's stored CV.
    points : `int`
        The number of points in its light curve.
    mean_flux : `float` or `None`
        Its mean brightness in detector counts, used to compare stars of
        similar brightness (fainter stars are noisier).
    status : `KnownVariability`
        What the catalogs say.
    """

    target_id: str
    coefficient_of_variation: float
    points: int
    mean_flux: float | None
    status: KnownVariability


@dataclass
class MultiplierResult:
    """How one multiplier did across all the targets used.

    Attributes
    ----------
    multiplier : `float`
        The ``k`` in ``median + k * MAD``.
    flagged : `collections.Counter`
        Stars above the cutoff, by catalog status.
    total : `collections.Counter`
        All stars used, by catalog status.
    """

    multiplier: float
    flagged: Counter = field(default_factory=Counter)
    total: Counter = field(default_factory=Counter)

    def share(self, status: KnownVariability) -> float | None:
        """Give the share of a status that was flagged.

        Parameters
        ----------
        status : `KnownVariability`
            The catalog status.

        Returns
        -------
        share : `float` or `None`
            Flagged stars divided by all stars of that status, or `None` if
            there are none.
        """
        count = self.total[status]
        return self.flagged[status] / count if count else None

    @property
    def share_of_all(self) -> float:
        """Give the share of all stars used that was flagged.

        Returns
        -------
        share : `float`
            Flagged divided by total, over every status.
        """
        total = sum(self.total.values())
        return sum(self.flagged.values()) / total if total else 0.0

    @property
    def known_share_of_labelled_flags(self) -> float | None:
        """Give the share of catalog-checked flags that are known variables.

        Returns
        -------
        share : `float` or `None`
            Known variables among the flagged stars the catalogs have an
            answer for (known or not listed), or `None` if none are flagged.
        """
        known = self.flagged[KnownVariability.KNOWN_VARIABLE]
        listed_not = self.flagged[KnownVariability.NOT_LISTED]
        return known / (known + listed_not) if known + listed_not else None


def classify_row(row: Sequence) -> LightCurveStar:
    """Turn a database row into a `LightCurveStar`.

    Parameters
    ----------
    row : `Sequence`
        ``id``, ``target_id``, CV, point count, mean flux, SIMBAD types, Gaia
        flag, VSX type, as selected by the query.

    Returns
    -------
    star : `LightCurveStar`
        The star with its catalog status.
    """
    _star_id, target_id, cv, points, mean_flux, simbad_types, gaia_flag, vsx_type = row
    return LightCurveStar(
        target_id=str(target_id),
        coefficient_of_variation=float(cv),
        points=int(points or 0),
        mean_flux=float(mean_flux) if mean_flux else None,
        status=combine_known_variability(simbad_types or "", gaia_flag or "", vsx_type or ""),
    )


def evaluate(
    stars: Iterable[LightCurveStar],
    multipliers: Sequence[float] = MULTIPLIERS,
    minimum_stars: int = DEFAULT_MINIMUM_STARS,
    minimum_points: int = DEFAULT_MINIMUM_POINTS,
) -> tuple[list[MultiplierResult], int, int]:
    """Count how many stars of each status each multiplier flags.

    Parameters
    ----------
    stars : `Iterable` [`LightCurveStar`]
        Every star with a light curve.
    multipliers : `Sequence` [`float`], optional
        The ``k`` values to try.
    minimum_stars : `int`, optional
        Targets with fewer stars (after the point filter) are left out.
    minimum_points : `int`, optional
        Stars with shorter light curves are left out.

    Returns
    -------
    results, targets_used, stars_used : `tuple`
        One `MultiplierResult` per multiplier, and how many targets and stars
        were used.
    """
    by_target: dict[str, list[LightCurveStar]] = defaultdict(list)
    for star in stars:
        if star.points >= minimum_points:
            by_target[star.target_id].append(star)
    usable = {target: members for target, members in by_target.items() if len(members) >= minimum_stars}
    results = [MultiplierResult(multiplier) for multiplier in multipliers]
    for members in usable.values():
        cvs = [member.coefficient_of_variation for member in members]
        for result in results:
            cutoff = adaptive_cv_cutoff(cvs, result.multiplier).cutoff
            for member in members:
                result.total[member.status] += 1
                if member.coefficient_of_variation > cutoff:
                    result.flagged[member.status] += 1
    return results, len(usable), sum(len(members) for members in usable.values())


# How many brightness bins each target is split into for the comparison.
BRIGHTNESS_BINS = 5

# A brightness bin needs at least this many known variables and not-listed
# stars to give an AUC that means anything.
MINIMUM_PER_GROUP_IN_BIN = 10


@dataclass(frozen=True)
class SeparationResult:
    """How well CV separates known variables from unlisted stars.

    Attributes
    ----------
    overall_auc : `float` or `None`
        The probability that a random known variable has a higher CV than a
        random unlisted star, over all stars of all targets together. 0.5
        means no separation. Brightness is not controlled.
    matched_auc : `float` or `None`
        The same, but compared only between stars of similar brightness in
        the same target, averaged over those groups weighted by the number of
        known variables. This is the fair comparison.
    groups_used : `int`
        How many target-and-brightness groups had enough stars.
    """

    overall_auc: float | None
    matched_auc: float | None
    groups_used: int


def area_under_curve(positives: Sequence[float], negatives: Sequence[float]) -> float | None:
    """Work out the chance a positive has a higher value than a negative.

    Parameters
    ----------
    positives : `Sequence` [`float`]
        Values for the stars that really vary.
    negatives : `Sequence` [`float`]
        Values for the stars the catalogs do not list.

    Returns
    -------
    auc : `float` or `None`
        The Mann-Whitney probability (ties count half), or `None` if either
        group is empty.
    """
    if not len(positives) or not len(negatives):
        return None
    combined = np.concatenate([positives, negatives])
    ranks = rankdata(combined)
    positive_rank_sum = float(np.sum(ranks[: len(positives)]))
    count = len(positives)
    return (positive_rank_sum - count * (count + 1) / 2.0) / (count * len(negatives))


def measure_separation(
    stars: Iterable[LightCurveStar],
    minimum_stars: int = DEFAULT_MINIMUM_STARS,
    minimum_points: int = DEFAULT_MINIMUM_POINTS,
) -> SeparationResult:
    """Measure how well CV separates known variables from unlisted stars.

    Parameters
    ----------
    stars : `Iterable` [`LightCurveStar`]
        Every star with a light curve.
    minimum_stars : `int`, optional
        Targets with fewer usable stars are left out.
    minimum_points : `int`, optional
        Stars with shorter light curves are left out.

    Returns
    -------
    result : `SeparationResult`
        The overall and brightness-matched AUC.
    """
    by_target: dict[str, list[LightCurveStar]] = defaultdict(list)
    for star in stars:
        if star.points >= minimum_points and star.mean_flux:
            by_target[star.target_id].append(star)
    all_known: list[float] = []
    all_unlisted: list[float] = []
    weighted_sum = 0.0
    weight = 0
    groups = 0
    for members in by_target.values():
        if len(members) < minimum_stars:
            continue
        for member in members:
            if member.status is KnownVariability.KNOWN_VARIABLE:
                all_known.append(member.coefficient_of_variation)
            elif member.status is KnownVariability.NOT_LISTED:
                all_unlisted.append(member.coefficient_of_variation)
        fluxes = np.array([member.mean_flux for member in members])
        edges = np.quantile(fluxes, np.linspace(0.0, 1.0, BRIGHTNESS_BINS + 1)[1:-1])
        bins = np.searchsorted(edges, fluxes)
        for bin_index in range(BRIGHTNESS_BINS):
            in_bin = [member for member, label in zip(members, bins, strict=True) if label == bin_index]
            known = [
                m.coefficient_of_variation for m in in_bin if m.status is KnownVariability.KNOWN_VARIABLE
            ]
            unlisted = [m.coefficient_of_variation for m in in_bin if m.status is KnownVariability.NOT_LISTED]
            if len(known) < MINIMUM_PER_GROUP_IN_BIN or len(unlisted) < MINIMUM_PER_GROUP_IN_BIN:
                continue
            auc = area_under_curve(known, unlisted)
            if auc is not None:
                weighted_sum += auc * len(known)
                weight += len(known)
                groups += 1
    return SeparationResult(
        overall_auc=area_under_curve(all_known, all_unlisted),
        matched_auc=weighted_sum / weight if weight else None,
        groups_used=groups,
    )


def _percent(value: float | None) -> str:
    """Write a share as a percentage.

    Returns
    -------
    text : `str`
        ``"--"`` for `None`.
    """
    return "--" if value is None else f"{value:6.1%}"


def _auc_text(value: float | None) -> str:
    """Write an AUC.

    Returns
    -------
    text : `str`
        Three decimals, or ``--`` for `None`.
    """
    return "--" if value is None else f"{value:.3f}"


def format_report(
    results: Sequence[MultiplierResult],
    targets_used: int,
    stars_used: int,
    separation: SeparationResult | None = None,
) -> str:
    """Write the comparison as a table.

    Parameters
    ----------
    results : `Sequence` [`MultiplierResult`]
        From `evaluate`.
    targets_used : `int`
        How many targets were used.
    stars_used : `int`
        How many stars were used.
    separation : `SeparationResult`, optional
        How well CV separates known variables from unlisted stars.

    Returns
    -------
    text : `str`
        The report.
    """
    totals = results[0].total if results else Counter()
    lines = [
        f"{stars_used} stars with light curves in {targets_used} targets.",
        "Catalog status of those stars: "
        + ", ".join(f"{status.value} {totals[status]}" for status in KnownVariability),
        "",
        "  k      all flagged   known variables   not listed   not looked up   known share of flagged",
        "                       (recall)          (flagged)    (flagged)       (known vs. not listed)",
    ]
    for result in results:
        marker = "  <- pipeline" if result.multiplier == DEFAULT_VARIABILITY_SIGMA_THRESHOLD else ""
        lines.append(
            f"{result.multiplier:5.1f}  {_percent(result.share_of_all):>11}   "
            f"{_percent(result.share(KnownVariability.KNOWN_VARIABLE)):>15}   "
            f"{_percent(result.share(KnownVariability.NOT_LISTED)):>10}   "
            f"{_percent(result.share(KnownVariability.UNKNOWN)):>13}   "
            f"{_percent(result.known_share_of_labelled_flags):>22}{marker}"
        )
    lines += [
        "",
        "Known variables flagged is the cutoff's recall on stars that really vary.",
        "Not listed flagged is an upper bound on the false-alarm rate: some of those",
        "are variables nobody has catalogued yet.",
    ]
    if separation is not None:
        lines += [
            "",
            "How well CV tells a known variable from an unlisted star (AUC; 0.5 is no better than chance):",
            f"  all stars together:                 {_auc_text(separation.overall_auc)}",
            f"  stars of similar brightness only:   {_auc_text(separation.matched_auc)}"
            f"   ({separation.groups_used} target and brightness groups)",
        ]
    return "\n".join(lines)


def load_stars(connection: sqlite3.Connection) -> list[LightCurveStar]:
    """Read every star with a stored CV from the catalog database.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection to the catalog database.

    Returns
    -------
    stars : `list` [`LightCurveStar`]
        The stars, with their catalog status.
    """
    return [classify_row(row) for row in connection.execute(_QUERY)]


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser covering the filters and an optional JSON output.
    """
    parser = argparse.ArgumentParser(
        prog="measure_variability_cutoff",
        description="Measure the variable-star cutoff against what the catalogs say.",
    )
    parser.add_argument("--minimum-stars", type=int, default=DEFAULT_MINIMUM_STARS)
    parser.add_argument("--minimum-points", type=int, default=DEFAULT_MINIMUM_POINTS)
    parser.add_argument("--output", default=None, help="Also write the numbers to this JSON file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the measurement from the command line.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        ``0`` on success, ``1`` if the catalog database cannot be found.
    """
    import os

    arguments = _build_argument_parser().parse_args(argv)
    database = os.path.join(str(Astrometrics().config.get_library_path()), "astrometrics.db")
    if not os.path.exists(database):
        print(f"No catalog database at {database}.")
        return 1
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        stars = load_stars(connection)
    finally:
        connection.close()
    results, targets_used, stars_used = evaluate(
        stars, minimum_stars=arguments.minimum_stars, minimum_points=arguments.minimum_points
    )
    separation = measure_separation(stars, arguments.minimum_stars, arguments.minimum_points)
    print(format_report(results, targets_used, stars_used, separation))
    if arguments.output:
        payload = {
            "targets_used": targets_used,
            "stars_used": stars_used,
            "overall_auc": separation.overall_auc,
            "matched_auc": separation.matched_auc,
            "multipliers": [
                {
                    "multiplier": result.multiplier,
                    "flagged": {status.value: result.flagged[status] for status in KnownVariability},
                    "total": {status.value: result.total[status] for status in KnownVariability},
                }
                for result in results
            ],
        }
        with open(arguments.output, "w", encoding="utf-8") as output_file:
            json.dump(payload, output_file, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
