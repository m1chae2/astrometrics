"""Tests that sessions are tied to the field before they are joined.

Purpose: each session divides a star's flux by its own comparison
ensemble. When two sessions share no comparison star, every star's
normalized level differs by one common factor (about 1.97 on M 57). These
tests build synthetic sessions with a known factor and check that the tie
finds it, that a constant star then joins without a step, that a star with a
real change between sessions keeps it, that a session with too few shared
stars is left untied with a reason and a warning, and that catalog variables
and flagged stars stay out of the factor.
"""

import logging
from datetime import date

import numpy as np
import pytest

from astrometricslib.models.quality.quality_summary import SessionTieRecord
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import Target
from astrometricslib.pipelines.photometry.batch import _match_and_merge_across_sessions
from astrometricslib.pipelines.photometry.processing.variability_analyzer import (
    identify_long_term_variable_candidates,
)
from astrometricslib.pipelines.photometry.session_tie import (
    MINIMUM_TIE_STARS,
    measure_session_tie,
    scale_session_stars,
)
from astrometricslib.pipelines.photometry.test.test_between_session_variability import (
    _analyzer_stub,
    _LinearWcs,
    _session_light_curve,
)
from astrometricslib.pipelines.shared.target_sessions import TargetSession

# The ensemble factor of the second session, the size seen on M 57.
_FIELD_FACTOR = 1.965
_STAR_COUNT = 40


def _make_session(index: int) -> TargetSession:
    """Build a session record for night `index`.

    Parameters
    ----------
    index : `int`
        Session number, used for the id and the night.

    Returns
    -------
    session : `TargetSession`
        The session record.
    """
    return TargetSession(
        id=f"T:night{index}",
        target_id="T",
        night_date=date(2026, 1, 1 + index),
        gain="800",
        offset="0",
        frame_paths=[f"night{index}_frame0.fits"],
    )


def _make_star(session: TargetSession, position: int, level: float, seed: int) -> StellarObject:
    """Build a star at a known pixel with a one-session light curve.

    Parameters
    ----------
    session : `TargetSession`
        The session the star was measured in.
    position : `int`
        Index of the star in the field; it sets the pixel, so the same index
        in two sessions is the same star.
    level : `float`
        The star's normalized level in this session.
    seed : `int`
        Noise seed.

    Returns
    -------
    star : `StellarObject`
        The star with its photometry.
    """
    star = StellarObject(id=f"{session.id}:s{position}")
    star.star_data = {"xcentroid": 30.0 * position + 10.0, "ycentroid": 100.0}
    day = 10 * int(session.id[-1])
    star.photometry = _session_light_curve(level, day, seed)
    return star


def _merge(
    levels_by_session: list[list[float]],
    session_flagged_positions: list[set[int]] | None = None,
    catalog_variable_positions: set[int] | None = None,
) -> tuple[list[StellarObject], list[SessionTieRecord]]:
    """Merge synthetic sessions through the real matching code.

    Parameters
    ----------
    levels_by_session : `list` [`list` [`float`]]
        For each session, the normalized level of the star at each position.
    session_flagged_positions : `list` [`set` [`int`]], optional
        For each session, the star positions its variability search flagged.
    catalog_variable_positions : `set` [`int`], optional
        Star positions a catalog lists as variable, in every session.

    Returns
    -------
    merged : `list` [`StellarObject`]
        The merged stars.
    records : `list` [`SessionTieRecord`]
        The tie record of each session.
    """
    sessions = [_make_session(index) for index in range(len(levels_by_session))]
    flagged_sets = session_flagged_positions or [set() for _ in sessions]
    catalog_variables = catalog_variable_positions or set()
    results = []
    for index, (session, levels) in enumerate(zip(sessions, levels_by_session, strict=True)):
        stars = [
            _make_star(session, position, level, seed=1000 * index + position)
            for position, level in enumerate(levels)
        ]
        for position in catalog_variables:
            if position < len(stars):
                stars[position].gaia_variable_flag = "VARIABLE"
        analyzer = _analyzer_stub(20, 5000.0 * (1 + index))
        analyzer.stellar_objects = stars
        results.append((analyzer, [stars[position] for position in sorted(flagged_sets[index])]))
    wcs = _LinearWcs(100.0, 20.0)
    records: list[SessionTieRecord] = []
    merged, _missing, _count = _match_and_merge_across_sessions(
        sessions,
        results,
        Target(id="T"),
        session_wcs_map={session.id: wcs for session in sessions},
        session_tie_records=records,
    )
    return merged, records


def _merged_by_position(merged: list[StellarObject], position: int) -> StellarObject:
    """Pick the merged star at a field position.

    Parameters
    ----------
    merged : `list` [`StellarObject`]
        The merged stars.
    position : `int`
        The star's index in the field.

    Returns
    -------
    star : `StellarObject`
        The star whose canonical id is the first session's id for `position`.
    """
    return next(star for star in merged if star.id == f"T:night0:s{position}")


def test_measure_session_tie_recovers_the_factor_and_clips_outliers() -> None:
    """The factor is the clipped median of the ratios, not their mean."""
    rng = np.random.default_rng(3)
    session_levels = rng.uniform(0.5, 2.0, 60)
    reference_levels = session_levels * 0.5 * (1 + rng.normal(0.0, 0.02, 60))
    # Three stars that really changed would drag a mean, not a clipped
    # median.
    reference_levels[:3] *= 3.0

    record = measure_session_tie("b", "a", reference_levels.tolist(), session_levels.tolist())

    assert record.status == "tied"
    assert record.factor == pytest.approx(0.5, rel=0.01)
    assert record.tie_star_count == 57
    assert record.spread == pytest.approx(0.02, abs=0.01)
    assert record.uncertainty == pytest.approx(1.2533 * record.spread / np.sqrt(57), rel=1e-6)


def test_measure_session_tie_without_enough_stars_is_untied_with_a_reason() -> None:
    """Fewer stars than the minimum leave the session untied and say why."""
    from astrometricslib.foundation.parameters import resolve

    minimum = int(resolve(MINIMUM_TIE_STARS).value)
    levels = [1.0 + 0.01 * index for index in range(minimum - 1)]

    record = measure_session_tie("b", "a", levels, [2.0 * level for level in levels])

    assert record.status == "untied"
    assert record.factor is None
    assert f"only {minimum - 1} tie star" in (record.reason or "")
    assert measure_session_tie("b", "a", [], []).status == "untied"


def test_scale_session_stars_scales_levels_and_errors_but_not_raw_flux() -> None:
    """The tie scales normalized and detrended values and errors, not raw."""
    session = _make_session(0)
    star = _make_star(session, 0, 1.0, seed=5)
    star.photometry.fluxes_normalized_errors = [0.01] * len(star.photometry.fluxes_normalized)
    star.photometry.fluxes_detrended_errors = [0.02] * len(star.photometry.fluxes_normalized)
    raw = list(star.photometry.fluxes)
    normalized = list(star.photometry.fluxes_normalized)

    scale_session_stars([star, StellarObject(id="no-light-curve")], 2.0)

    assert star.photometry.fluxes == raw
    assert star.photometry.fluxes_normalized == pytest.approx([2.0 * value for value in normalized])
    assert star.photometry.fluxes_normalized_errors[0] == pytest.approx(0.02)
    assert star.photometry.fluxes_detrended_errors[0] == pytest.approx(0.04)


def test_a_constant_star_joins_without_a_step_when_the_ensembles_differ() -> None:
    """A field factor of 1.965 is removed, so pooled scatter is small."""
    merged, records = _merge([[1.0] * _STAR_COUNT, [1.0 * _FIELD_FACTOR] * _STAR_COUNT])

    assert [record.status for record in records] == ["reference", "tied"]
    assert records[1].factor == pytest.approx(1 / _FIELD_FACTOR, rel=0.01)
    assert records[1].tie_star_count >= 30
    constant = _merged_by_position(merged, 5)
    # Untied, the 1.965 step alone would give a CV near 0.33.
    assert constant.photometry.coefficient_of_variation < 0.03
    levels = [summary.median_normalized_flux for summary in constant.photometry.session_summaries]
    assert levels[1] / levels[0] == pytest.approx(1.0, abs=0.02)
    assert [summary.tie_status for summary in constant.photometry.session_summaries] == ["reference", "tied"]
    assert identify_long_term_variable_candidates(merged) == []


def test_a_real_change_between_sessions_survives_the_tie() -> None:
    """A star 0.3 mag fainter in session B stays 0.759 times as bright."""
    levels_a = [1.0] * _STAR_COUNT
    levels_b = [_FIELD_FACTOR] * _STAR_COUNT
    levels_b[7] = _FIELD_FACTOR * 0.759
    merged, _records = _merge([levels_a, levels_b])

    flagged = identify_long_term_variable_candidates(merged)

    assert [star.id for star in flagged] == ["T:night0:s7"]
    summaries = flagged[0].photometry.session_summaries
    assert summaries[1].median_normalized_flux / summaries[0].median_normalized_flux == pytest.approx(
        0.759, abs=0.02
    )
    assert flagged[0].photometry.between_session_amplitude_mag == pytest.approx(0.30, abs=0.03)


def test_three_sessions_are_all_tied_to_the_reference_session() -> None:
    """Each later session gets its own factor onto the first session."""
    merged, records = _merge([
        [1.0] * _STAR_COUNT,
        [1.5] * _STAR_COUNT,
        [0.8] * _STAR_COUNT,
    ])

    assert [record.status for record in records] == ["reference", "tied", "tied"]
    assert records[1].factor == pytest.approx(1 / 1.5, rel=0.01)
    assert records[2].factor == pytest.approx(1 / 0.8, rel=0.01)
    assert _merged_by_position(merged, 3).photometry.coefficient_of_variation < 0.03


def test_the_session_with_the_most_measured_stars_is_the_reference() -> None:
    """The reference is the session with the most stars, not the earliest."""
    merged, records = _merge([[1.0] * 25, [2.0] * _STAR_COUNT])

    assert records[0].reference_session_id == "T:night1"
    assert [record.status for record in records] == ["tied", "reference"]
    assert records[0].factor == pytest.approx(2.0, rel=0.01)
    assert merged


def test_a_session_with_too_few_shared_stars_stays_untied_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Five shared stars are too few: untied, with a reason and a warning."""
    with caplog.at_level(logging.WARNING):
        merged, records = _merge([[1.0] * 5, [_FIELD_FACTOR] * 5])

    assert records[1].status == "untied"
    assert records[1].factor is None
    assert "tie star" in (records[1].reason or "")
    assert any("was not tied to the field" in message for message in caplog.messages)
    star = _merged_by_position(merged, 0)
    summaries = star.photometry.session_summaries
    assert summaries[1].tie_status == "untied"
    assert summaries[1].median_normalized_flux == pytest.approx(_FIELD_FACTOR, abs=0.05)
    # An untied session is not comparable: no between-session amplitude.
    identify_long_term_variable_candidates(merged)
    assert star.photometry.between_session_amplitude_mag is None


def test_catalog_variables_and_flagged_stars_are_left_out_of_the_factor() -> None:
    """Catalog variables and flagged stars do not move the factor.

    Twenty-five constant stars rise by a factor of 2 between the sessions.
    Fifteen catalog variables, ten flagged stars and fifteen more stars keep
    a ratio of 1. Counted, the 40 stars at a ratio of 1 would outnumber the 25
    and set the median to 1. With the 25 catalog and flagged stars left out,
    the 25 constant stars outnumber the 15 that remain, so the tie finds 0.5.
    """
    count = 65
    levels_a = [1.0] * count
    levels_b = [2.0] * 25 + [1.0] * 40
    catalog_variables = set(range(25, 40))
    flagged_positions = [set(range(40, 50)), set()]

    _merged, records = _merge(
        [levels_a, levels_b],
        session_flagged_positions=flagged_positions,
        catalog_variable_positions=catalog_variables,
    )

    assert records[1].status == "tied"
    assert records[1].factor == pytest.approx(0.5, rel=0.02)
    # Positions 50 to 64 keep a ratio of 1 and stay in with the first 25 stars.
    assert records[1].tie_star_count <= 40
