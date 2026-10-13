"""Tests for the script that checks the variable-star cutoff against catalogs.

The checks need controls, or a finding of "no skill" could be a bug. These
tests build artificial fields where the answer is known: one in which known
variables really do vary more (the measure must see it), one in which nothing
differs (it must report chance), and one in which known variables are simply
brighter, and so quieter (it must not mistake that for a difference once
brightness is matched).
"""

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from astrometricslib.models.known_variability import KnownVariability
from astrometricslib.pipelines.photometry.post_processing.variability_skill import area_under_curve
from astrometricslib.scripts import measure_variability_cutoff as measure

KNOWN = KnownVariability.KNOWN_VARIABLE
UNLISTED = KnownVariability.NOT_LISTED


def make_field(
    generator: np.random.Generator,
    known_boost: float,
    brightness_noise: bool = False,
    known_brighter: bool = False,
    target: str = "T",
    unlisted_count: int = 600,
    known_count: int = 60,
) -> list[measure.LightCurveStar]:
    """Build one artificial target's stars.

    Parameters
    ----------
    generator : `numpy.random.Generator`
        The source of randomness.
    known_boost : `float`
        How much extra CV the known variables get from really varying.
    brightness_noise : `bool`, optional
        Make fainter stars noisier, as in real photometry.
    known_brighter : `bool`, optional
        Make the known variables brighter than the others.
    target : `str`, optional
        The target id.
    unlisted_count, known_count : `int`, optional
        How many stars of each kind.

    Returns
    -------
    stars : `list` [`measure.LightCurveStar`]
        The stars.
    """
    stars = []
    for status, count in ((UNLISTED, unlisted_count), (KNOWN, known_count)):
        for _ in range(count):
            flux = float(
                np.exp(generator.normal(8.0 + (1.0 if known_brighter and status is KNOWN else 0.0), 1.0))
            )
            noise = 0.02 + (0.4 / np.sqrt(flux / 100.0) if brightness_noise else 0.0)
            cv = abs(generator.normal(noise, noise * 0.25)) + (known_boost if status is KNOWN else 0.0)
            stars.append(measure.LightCurveStar(target, cv, 40, flux, status))
    return stars


def test_the_auc_is_one_for_perfect_separation_and_half_for_none() -> None:
    """Higher positives give 1, equal groups 0.5, lower positives 0."""
    assert area_under_curve([5, 6, 7], [1, 2, 3]) == pytest.approx(1.0)
    assert area_under_curve([1, 2, 3], [1, 2, 3]) == pytest.approx(0.5)
    assert area_under_curve([1, 2, 3], [5, 6, 7]) == pytest.approx(0.0)
    assert area_under_curve([], [1]) is None


def test_the_measure_sees_skill_when_known_variables_really_vary_more() -> None:
    """Positive control: a real excess is found, and the cutoff catches it."""
    stars = make_field(np.random.default_rng(1), known_boost=0.4)

    results, targets, count = measure.evaluate(stars, multipliers=(7.4,), minimum_stars=100)
    separation = measure.measure_separation(stars)

    assert (targets, count) == (1, 660)
    assert results[0].share(KNOWN) > 0.8
    assert results[0].share(UNLISTED) < 0.1
    assert separation.overall_auc > 0.9


def test_the_measure_reports_chance_when_nothing_differs() -> None:
    """Null control: no difference gives an AUC near one half."""
    stars = make_field(np.random.default_rng(2), known_boost=0.0)

    separation = measure.measure_separation(stars)

    assert separation.overall_auc == pytest.approx(0.5, abs=0.12)


def test_matching_brightness_removes_the_effect_of_known_variables_being_brighter() -> None:
    """Confound control: brighter, quieter known variables look quieter.

    Overall they come out below chance only because they are brighter. Compared
    with stars of similar brightness the difference disappears.
    """
    stars = make_field(
        np.random.default_rng(3),
        known_boost=0.0,
        brightness_noise=True,
        known_brighter=True,
        unlisted_count=3000,
        known_count=400,
    )

    separation = measure.measure_separation(stars)

    assert separation.overall_auc < 0.42
    assert separation.matched_auc == pytest.approx(0.5, abs=0.08)


def test_a_larger_multiplier_flags_fewer_stars() -> None:
    """Raising the multiplier can only flag fewer stars."""
    stars = make_field(np.random.default_rng(4), known_boost=0.2)

    results, _targets, _count = measure.evaluate(stars, multipliers=(3.0, 7.4, 16.0))

    shares = [result.share_of_all for result in results]
    assert shares == sorted(shares, reverse=True)


def test_small_targets_and_short_light_curves_are_left_out() -> None:
    """A small target, and stars with too few points, are dropped."""
    small = make_field(np.random.default_rng(5), 0.0, unlisted_count=40, known_count=5, target="Small")
    short = [measure.LightCurveStar("Short", 0.1, 3, 100.0, UNLISTED) for _ in range(500)]

    results, targets, count = measure.evaluate(small + short, multipliers=(7.4,))

    assert (targets, count) == (0, 0)
    assert results[0].share_of_all == pytest.approx(0.0)


def test_stars_are_read_from_the_database_with_their_catalog_status(tmp_path: Path) -> None:
    """A database row becomes a star with the status the catalogs give it."""
    database = tmp_path / "catalog.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE stellar_objects "
        "(id TEXT PRIMARY KEY, target_id TEXT, data_json TEXT, has_photometry INTEGER)"
    )

    def add(star_id: str, **fields: object) -> None:
        document = {
            "photometry": {"coefficientOfVariation": 0.1, "fluxesNormalized": [1.0] * 12, "meanFlux": 500.0}
        }
        document.update(fields)
        connection.execute(
            "INSERT INTO stellar_objects VALUES (?, 'T', ?, 1)", (star_id, json.dumps(document))
        )

    add("Algol", simbadObjectTypes="*|**|EB*|SB*|V*")
    add("Quiet", simbadObjectTypes="*|IR", vsxVariabilityType="NOT_IN_VSX")
    add("Unasked")
    connection.commit()

    stars = {star.status for star in measure.load_stars(connection)}
    connection.close()

    assert stars == {KNOWN, UNLISTED, KnownVariability.UNKNOWN}
