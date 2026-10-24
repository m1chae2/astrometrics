"""Purpose: Check the rule-support constants against the register.

Description: `rule_support.py` copies the status label of the register entries
behind the guiding, capture and sky analyses, and every design-estimate id it
or the analyses cite must be a row of the register's design-estimate table.
These tests fail when the register and the code drift apart.
"""

import re
from pathlib import Path

import pytest

from wayfindinglib.analytics.performance_envelope import REGISTER_ENTRY_BY_LIMIT
from wayfindinglib.models.quality.statements import Confidence
from wayfindinglib.session_analysis.rule_support import PIPELINE_CONFIDENCE, PIPELINE_REGISTER_ENTRIES

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTER_TEXT = (REPO_ROOT / "documentation" / "library_design" / "validation" / "wayfinding.md").read_text(
    encoding="utf-8"
)
STATUS_LABELS = {
    "Measured": Confidence.MEASURED,
    "Partly measured": Confidence.PARTLY_MEASURED,
    "Design estimate": Confidence.DESIGN_ESTIMATE,
}


def _entry_body(entry_id: str) -> str:
    """Return the text of one register entry, from its heading to the next.

    Parameters
    ----------
    entry_id : str
        The entry id, for example ``"WAY-45"``.

    Returns
    -------
    str
        The entry's text.
    """
    match = re.search(rf"^### {entry_id}\s*$(.*?)(?=^##)", REGISTER_TEXT, re.MULTILINE | re.DOTALL)
    assert match, f"{entry_id} has no entry in the register"
    return match.group(1)


@pytest.mark.parametrize("pipeline", sorted(PIPELINE_REGISTER_ENTRIES))
def test_pipeline_confidence_matches_the_status_label_of_its_entry(pipeline: str) -> None:
    """Each analysis confidence equals the status label of its entry."""
    body = _entry_body(PIPELINE_REGISTER_ENTRIES[pipeline])
    status_line = re.search(
        r"^- \*\*Status:\*\* (Measured|Partly measured|Design estimate)", body, re.MULTILINE
    )
    assert status_line, f"{pipeline}: the entry has no status label"
    assert STATUS_LABELS[status_line.group(1)] == PIPELINE_CONFIDENCE[pipeline]


def test_every_envelope_limit_names_an_existing_register_entry() -> None:
    """Every envelope limit points at an entry that exists."""
    for limit_name, entry_id in REGISTER_ENTRY_BY_LIMIT.items():
        assert re.search(rf"^### {entry_id}\s*$", REGISTER_TEXT, re.MULTILINE), f"{limit_name} -> {entry_id}"


def test_every_design_estimate_id_used_by_the_analyses_is_a_table_row() -> None:
    """Each ``WAY-D4x`` id used by the analyses is a table row."""
    rows = set(re.findall(r"^\| (WAY-D\d+) \|", REGISTER_TEXT, re.MULTILINE))
    used: set[str] = set()
    for path in (REPO_ROOT / "wayfindinglib" / "session_analysis").rglob("*.py"):
        used.update(re.findall(r"WAY-D4\d", path.read_text(encoding="utf-8")))
    assert used, "no design-estimate id found; the search is broken"
    assert used <= rows, f"not in the register: {sorted(used - rows)}"
