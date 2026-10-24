"""Purpose: The one list of every quality rule the library has.

Description: The library has three rule tables. `RESULT_QUALITY_RULES` holds
the rules about a target's captured data (`rules.py`). `STAR_RESULT_RULES`
holds the rules about whether one star's science result can be trusted
(`star_result_rules.py`). `SESSION_RULES` holds the rules of the per-night
analyses (`session_rules.py`). `ALL_RULES` joins them, so a check that must
hold for every rule (a register entry that exists, a confidence that matches
its status, a unique name, an action class that fits the statement kind) walks
one list and cannot miss a table.

A new rule table is added here, and only here.
"""

from wayfindinglib.analytics.result_quality.rules import RESULT_QUALITY_RULES
from wayfindinglib.analytics.result_quality.session_rules import SESSION_RULES
from wayfindinglib.analytics.result_quality.star_result_rules import STAR_RESULT_RULES
from wayfindinglib.models.quality.rule import QualityRule

__all__ = ["ALL_RULES"]

ALL_RULES: tuple[QualityRule, ...] = RESULT_QUALITY_RULES + STAR_RESULT_RULES + SESSION_RULES
"""Every rule of the three tables, the data-quality rules first."""
