"""Purpose: Tests for the judgment of per-star science results.

Description: Builds made-up star records with a spectrum or a period search
and checks the statements: a poor match, an ambiguous class, a good match, a
failed hold-out check, an unconfirmed result, too few cycles, a search that
found nothing, a search that cannot be tested, and a star with no result at
all. Every statement must cite the star result it used, and the copies of the
spectroscopy limits must equal the pipeline's.
"""

import importlib

import pytest

from astrometricslib import PhotometryResult, SpectroscopyResult
from wayfindinglib.analytics.result_quality import star_result_rules as sr
from wayfindinglib.models.quality.statements import ActionClass, StatementKind

STAR = "star-1"


def _spectrum(
    rms: float | None = 0.05, second: float = 0.20, next_class: float = 0.30, **extra: object
) -> SpectroscopyResult:
    """Build a spectrum result with a best match and two runners-up.

    Parameters
    ----------
    rms : `float` or `None`, optional
        The relative RMS of the best match. ``None`` for no type.
    second : `float`, optional
        The relative RMS of the second-best reference (the same class).
    next_class : `float`, optional
        The relative RMS of the best reference of another class.
    **extra
        Other fields of the result.

    Returns
    -------
    result : `SpectroscopyResult`
        The spectrum.
    """
    candidates = (
        []
        if rms is None
        else [
            {"spectral_type": "G2V", "rms": rms, "correlation": 0.99},
            {"spectral_type": "G5V", "rms": second, "correlation": 0.9},
            {"spectral_type": "K0V", "rms": next_class, "correlation": 0.8},
        ]
    )
    return SpectroscopyResult(
        wavelengths_angstrom=[4000.0, 5000.0],
        intensities=[1.0, 2.0],
        self_determined_spectral_type="" if rms is None else "G2V",
        self_determined_spectral_type_rms=rms,
        self_determined_spectral_type_candidates=candidates,
        generated_by_job_id="job-9",
        **extra,
    )


def _search(**fields: object) -> PhotometryResult:
    """Build a light curve with a period search.

    Parameters
    ----------
    **fields
        Fields of the period search.

    Returns
    -------
    result : `PhotometryResult`
        The light curve.
    """
    base = {
        "best_period_days": 0.37,
        "verdict": "detected",
        "false_alarm_probability": 0.001,
        "family_wise_false_alarm_probability": 0.004,
        "cycles_observed": 12.0,
        "unconfirmed": False,
        "verdict_checks": [],
    }
    base.update(fields)
    return PhotometryResult(periodogram=base, generated_by_job_id="job-3")


def _rules(statements: list) -> list[str]:
    """List the rule names of some statements.

    Returns
    -------
    names : `list` [`str`]
        The names, in order.
    """
    return [statement.rule for statement in statements]


def test_a_good_unambiguous_match_gives_the_number_behind_it() -> None:
    """A passing match is a recommendation with its RMS, not a bare yes."""
    statements = sr.judge_star_record(STAR, _spectrum(), None)

    assert _rules(statements) == [sr.SPECTRUM_USE_RESULT, sr.SPECTRAL_REACHABILITY]
    use = statements[0]
    assert use.kind == StatementKind.RECOMMENDATION
    assert "0.05" in use.message and "G2V" in use.message
    assert use.evidence[0].star_result is not None
    assert use.evidence[0].star_result.run_id == "job-9"
    assert use.evidence[0].star_result.star_id == STAR


def test_a_poor_match_is_a_warning_that_cites_the_limit() -> None:
    """A relative RMS above 0.15 warns and keeps the use-result note away."""
    statements = sr.judge_star_record(STAR, _spectrum(rms=0.4, second=0.5, next_class=0.6), None)

    assert sr.SPECTRAL_TYPE_POOR_MATCH in _rules(statements)
    assert sr.SPECTRUM_USE_RESULT not in _rules(statements)
    warning = statements[_rules(statements).index(sr.SPECTRAL_TYPE_POOR_MATCH)]
    assert warning.kind == StatementKind.WARNING
    assert warning.action_class == ActionClass.DATA_SELECTION
    assert warning.limits[0].register_entry == "SPE-16"
    assert "0.40" in warning.message


def test_an_ambiguous_class_warns_with_the_gap() -> None:
    """A next class within 0.02 makes the class letter uncertain."""
    statements = sr.judge_star_record(STAR, _spectrum(rms=0.05, second=0.06, next_class=0.06), None)

    assert sr.SPECTRAL_CLASS_AMBIGUOUS in _rules(statements)
    warning = statements[_rules(statements).index(sr.SPECTRAL_CLASS_AMBIGUOUS)]
    assert "0.010" in warning.message
    assert warning.limits[0].register_entry == "SPE-18"


def test_reachability_is_always_an_explicit_gap_in_knowledge() -> None:
    """'Not reachable at this resolution' is never guessed."""
    statements = sr.judge_star_record(STAR, _spectrum(), None)

    note = statements[_rules(statements).index(sr.SPECTRAL_REACHABILITY)]
    assert note.kind == StatementKind.INSUFFICIENT_DATA
    assert "signal-to-noise" in note.reason


def test_a_spectrum_with_no_type_says_why() -> None:
    """No type found is missing data with the classifier's note."""
    statements = sr.judge_star_record(
        STAR, _spectrum(rms=None, self_determined_spectral_type_note="the trail left the image"), None
    )

    assert statements[0].rule == sr.SPECTRAL_TYPE_NOT_FOUND
    assert "trail left the image" in statements[0].reason


def test_a_detected_period_gives_the_period_and_the_probability() -> None:
    """A passing period search states the period and the false-alarm chance."""
    statements = sr.judge_star_record(STAR, None, _search())

    use = next(s for s in statements if s.rule == sr.PERIOD_USE_RESULT)
    assert "0.37" in use.message and "0.004" in use.message
    assert use.evidence[0].star_result is not None
    assert use.evidence[0].star_result.run_id == "job-3"
    assert not [s for s in statements if s.kind == StatementKind.WARNING]


def test_a_failed_held_out_check_is_a_warning() -> None:
    """A failed check names itself and removes the use-it note."""
    checks = [{"name": "leave_one_night_out", "status": "failed", "detail": "period moved by 8%"}]
    statements = sr.judge_star_record(STAR, None, _search(verdict_checks=checks))

    assert sr.PERIOD_FAILED_CHECK in _rules(statements)
    assert sr.PERIOD_USE_RESULT not in _rules(statements)
    assert "leave_one_night_out" in statements[_rules(statements).index(sr.PERIOD_FAILED_CHECK)].message


def test_an_unconfirmed_result_and_few_cycles_are_advice() -> None:
    """Unchecked or short results warn at advice level and say what to do."""
    statements = sr.judge_star_record(STAR, None, _search(unconfirmed=True, cycles_observed=1.2))

    assert {sr.PERIOD_UNCONFIRMED, sr.PERIOD_FEW_CYCLES} <= set(_rules(statements))
    few = statements[_rules(statements).index(sr.PERIOD_FEW_CYCLES)]
    assert few.action_class == ActionClass.SESSION_PROCEDURE
    assert "1.2" in few.message


def test_a_search_that_found_nothing_does_not_claim_a_period() -> None:
    """Not detected is a result: do not claim a period."""
    statements = sr.judge_star_record(STAR, None, _search(verdict="not_detected"))

    assert _rules(statements) == [sr.PERIOD_NOT_DETECTED]
    assert statements[0].proposed_action is not None


def test_a_search_with_too_little_data_cannot_be_tested() -> None:
    """Insufficient data keeps the search's own note."""
    statements = sr.judge_star_record(
        STAR, None, _search(verdict="insufficient_data", note="only 3 measurements")
    )

    assert statements[0].kind == StatementKind.INSUFFICIENT_DATA
    assert "only 3 measurements" in statements[0].reason


def test_a_star_with_nothing_to_judge_names_what_is_missing() -> None:
    """No spectrum and no search gives one missing-data statement."""
    statements = sr.judge_star_record(STAR, SpectroscopyResult(), PhotometryResult())

    assert _rules(statements) == [sr.NO_STAR_RESULT]
    assert "spectrum" in statements[0].reason and "period search" in statements[0].reason


def test_every_rule_row_is_consistent_with_its_register_entry() -> None:
    """Rule rows have a register entry, and design-estimate rows say so."""
    names = [rule.name for rule in sr.STAR_RESULT_RULES]
    assert len(names) == len(set(names))
    for rule in sr.STAR_RESULT_RULES:
        assert rule.register_entry
        if rule.register_entry.startswith("WAY-D"):
            assert rule.confidence.value == "design_estimate"


@pytest.mark.parametrize(
    ("copy", "name"), [(sr.POOR_MATCH_RMS, "NO_GOOD_MATCH_RMS"), (sr.AMBIGUOUS_RMS_GAP, "AMBIGUOUS_RMS_GAP")]
)
def test_copied_limits_match_the_spectroscopy_pipeline(copy: float, name: str) -> None:
    """The quoted limits equal the ones the star record uses."""
    module = importlib.import_module("astrometricslib.models.stellar_source")
    assert getattr(module, name) == copy
