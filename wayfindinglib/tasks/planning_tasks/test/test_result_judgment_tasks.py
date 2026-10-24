"""Purpose: Tests for `judge_star_result`, the trust check of one star.

Description: Checks that the task reads the star through the science library's
``stars`` catalog, refuses an unknown star or one that belongs to other
targets, returns the statements in priority order, and cites each per-star
result by star, run and statistic.
"""

from types import SimpleNamespace

import pytest

from astrometricslib import NotFoundError, PhotometryResult, SpectroscopyResult, StellarObject
from wayfindinglib.models.quality.statements import StatementKind
from wayfindinglib.tasks.planning_tasks.result_judgment_tasks import judge_star_result


def _library(*stars: StellarObject) -> SimpleNamespace:
    """Build a stand-in science library holding some stars.

    Returns
    -------
    library : `SimpleNamespace`
        Has a ``stars`` catalog with ``get``.
    """
    by_id = {star.id: star for star in stars}
    return SimpleNamespace(stars=SimpleNamespace(get=lambda object_id: by_id.get(object_id)))


def _poor_spectrum_star() -> StellarObject:
    """Build a star with a poorly matched spectrum and a period search.

    Returns
    -------
    star : `StellarObject`
        The star, recorded for target ``T``.
    """
    spectrum = SpectroscopyResult(
        wavelengths_angstrom=[4000.0, 5000.0],
        intensities=[1.0, 2.0],
        self_determined_spectral_type="K2V",
        self_determined_spectral_type_rms=0.4,
        self_determined_spectral_type_candidates=[
            {"spectral_type": "K2V", "rms": 0.4},
            {"spectral_type": "M0V", "rms": 0.6},
        ],
    )
    light_curve = PhotometryResult(
        periodogram={"best_period_days": 1.5, "verdict": "possible", "false_alarm_probability": 0.03},
    )
    return StellarObject(id="s1", target_ids=["T"], spectroscopy=spectrum, photometry=light_curve)


def test_statements_come_back_in_priority_order() -> None:
    """Warnings come before recommendations and missing-data notes."""
    statements = judge_star_result("T", "s1", astrometrics=_library(_poor_spectrum_star()))

    kinds = [statement.kind for statement in statements]
    assert kinds[0] == StatementKind.WARNING
    assert kinds == sorted(kinds, key=lambda kind: list(StatementKind).index(kind))
    assert all(
        statement.evidence or statement.kind == StatementKind.INSUFFICIENT_DATA for statement in statements
    )
    assert any(statement.rule == "period_use_result" for statement in statements)


def test_a_star_with_no_results_says_what_is_missing() -> None:
    """A star with no spectrum and no search gets one missing-data note."""
    star = StellarObject(id="s2", target_ids=["T"], spectroscopy=None, photometry=None)

    (statement,) = judge_star_result("T", "s2", astrometrics=_library(star))

    assert statement.kind == StatementKind.INSUFFICIENT_DATA
    assert "Missing" in statement.reason


def test_an_unknown_star_is_not_found() -> None:
    """A star that is not in the catalog raises `NotFoundError`."""
    with pytest.raises(NotFoundError):
        judge_star_result("T", "nope", astrometrics=_library())


def test_a_star_of_other_targets_is_not_found() -> None:
    """A star recorded only for other targets is refused for this one."""
    with pytest.raises(NotFoundError) as caught:
        judge_star_result("T", "s1", astrometrics=_library(StellarObject(id="s1", target_ids=["Other"])))

    assert caught.value.details["target_id"] == "T"


def test_a_star_with_no_recorded_targets_is_judged() -> None:
    """A star that lists no targets is not refused."""
    star = StellarObject(id="s3")

    (statement,) = judge_star_result("T", "s3", astrometrics=_library(star))

    assert statement.rule == "no_star_result"
