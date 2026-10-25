"""Purpose: Check every quality rule for plain wording and a produces value.

Description: The user interface shows only the plain message of a statement
(issue 96, decision D8). These checks walk `ALL_RULES`, the one list of every
rule, so a new rule cannot be added without plain wording:

* every rule has a plain template;
* the template uses no statistics term, has at most two sentences, and each
  sentence stays short (about 20 words; the limit here is 30, because a
  placeholder stands for a short phrase);
* a design-estimate rule that gives a finding or advice (not a note about
  missing data) uses a forecast or best-guess word;
* every rule declares what its proposed action becomes in the plan
  (``produces``): only ``more_frames`` makes a package row, every calibration
  action makes a checklist item, and everything else makes nothing;
* a star's classification and period results use the ``result`` kind.
"""

import re

import pytest

from wayfindinglib.analytics.result_quality.all_rules import ALL_RULES
from wayfindinglib.analytics.result_quality.star_result_rules import PERIOD_USE_RESULT, SPECTRUM_USE_RESULT
from wayfindinglib.models.quality.rule import QualityRule
from wayfindinglib.models.quality.statements import ActionClass, Confidence, StatementKind
from wayfindinglib.test.plain_wording_support import (
    FORECAST_WORDS,
    MAXIMUM_SENTENCES,
    banned_terms_in,
    sentences_of,
)

MAXIMUM_WORDS_PER_SENTENCE = 30
"""The most words a plain sentence may have (a placeholder counts as one)."""

MAXIMUM_WORDS_IN_ALL = 50
"""The most words a whole plain message may have."""


def _word_count(text: str) -> int:
    """Count the words of a template, a placeholder counting as one word.

    Returns
    -------
    count : `int`
        The number of whitespace-separated words.
    """
    return len(text.split())


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.name)
def test_every_rule_has_plain_wording(rule: QualityRule) -> None:
    """Every rule of every table has a plain template."""
    assert rule.plain_wording.strip(), rule.name


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.name)
def test_plain_wording_avoids_statistics_terms(rule: QualityRule) -> None:
    """The plain template uses none of the banned statistics terms."""
    assert banned_terms_in(rule.plain_wording) == [], rule.name


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.name)
def test_plain_wording_is_short(rule: QualityRule) -> None:
    """The plain template has at most two sentences of about 20 words."""
    sentences = sentences_of(rule.plain_wording)
    assert 1 <= len(sentences) <= MAXIMUM_SENTENCES, rule.name
    assert all(_word_count(sentence) <= MAXIMUM_WORDS_PER_SENTENCE for sentence in sentences), rule.name
    assert _word_count(rule.plain_wording) <= MAXIMUM_WORDS_IN_ALL, rule.name


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.name)
def test_design_estimates_use_forecast_words(rule: QualityRule) -> None:
    """A design-estimate finding hedges: it says it is a forecast."""
    if (
        rule.confidence != Confidence.DESIGN_ESTIMATE
        or rule.statement_kind == StatementKind.INSUFFICIENT_DATA
    ):
        pytest.skip("only design-estimate findings and advice need a forecast word")
    lowered = rule.plain_wording.lower()
    assert any(word in lowered for word in FORECAST_WORDS), rule.name


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.name)
def test_plain_wording_has_no_unbalanced_braces(rule: QualityRule) -> None:
    """Every placeholder is a closed ``{name}`` or ``{name:format}``."""
    for placeholder in re.findall(r"\{([^{}]*)\}", rule.plain_wording):
        assert re.fullmatch(r"[a-z_]+(:[.0-9a-z]+)?", placeholder), (rule.name, placeholder)
    assert rule.plain_wording.count("{") == rule.plain_wording.count("}"), rule.name


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.name)
def test_produces_follows_the_action(rule: QualityRule) -> None:
    """Only more_frames makes a package row; calibration makes checklist."""
    if rule.name == "more_frames":
        expected = "package_row"
    elif rule.action_class == ActionClass.CALIBRATION:
        expected = "checklist_item"
    else:
        expected = "none"
    assert rule.produces == expected, rule.name


def test_exactly_one_rule_makes_a_package_row() -> None:
    """The suggested package rows come from more_frames and nothing else."""
    assert [rule.name for rule in ALL_RULES if rule.produces == "package_row"] == ["more_frames"]


def test_a_rule_that_makes_nothing_still_says_so() -> None:
    """Exposure-only setting changes are declared as making nothing."""
    by_name = {rule.name: rule for rule in ALL_RULES}
    for name in (
        "guiding_limits_exposure",
        "underexposed_setting",
        "overexposed_setting",
        "seeing_limits_resolution",
    ):
        assert by_name[name].produces == "none", name


def test_star_results_use_the_result_kind() -> None:
    """A spectral classification and a period result are results."""
    by_name = {rule.name: rule for rule in ALL_RULES}
    for name in (SPECTRUM_USE_RESULT, PERIOD_USE_RESULT):
        assert by_name[name].statement_kind == StatementKind.RESULT, name
        assert by_name[name].action_class is None, name
