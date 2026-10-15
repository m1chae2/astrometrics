"""Purpose: Keep the Wayfinding library papers split into theory and code.

Description: ``Wayfinding_Library_Architecture.md`` explains the ideas behind
observation planning and observatory control. Like the Astrometrics library's
architecture paper, it names no code and states no tuned values; hidden
``<!-- impl: -->`` comments may name code because they do not render.
``Wayfinding_Library_Implementation.md`` carries the code names and settings.
The rules and their helpers are shared with
``astrometricslib/test/test_documentation_rules.py`` so both libraries follow
``documentation/Documentation_Conventions.md`` in the same way.
"""

import re
from pathlib import Path

import pytest

from astrometricslib.test.test_architecture_doc_claims import IMPL_COMMENT, _resolve
from astrometricslib.test.test_documentation_rules import (
    ARCHITECTURE_WORD_LIMIT,
    IMPLEMENTATION_WORD_LIMIT,
    assert_names_no_code,
    assert_states_no_tuned_values,
    assert_within_word_limit,
)

DESIGN_DIR = Path(__file__).resolve().parents[2] / "documentation" / "library_design"
ARCHITECTURE = DESIGN_DIR / "Wayfinding_Library_Architecture.md"
IMPLEMENTATION = DESIGN_DIR / "Wayfinding_Library_Implementation.md"
VERSION_LINE = re.compile(r"^\*Version [^*]*\*$", re.MULTILINE)


def test_architecture_names_no_code() -> None:
    """The architecture paper shows no file, class, function or config name."""
    assert_names_no_code(ARCHITECTURE)


def test_architecture_states_no_tuned_values() -> None:
    """The architecture paper describes each setting's idea, not its value."""
    assert_states_no_tuned_values(ARCHITECTURE)


@pytest.mark.parametrize("paper", [ARCHITECTURE, IMPLEMENTATION], ids=lambda path: path.stem)
def test_every_impl_link_names_code_that_exists(paper: Path) -> None:
    """Each hidden ``impl`` link in a Wayfinding paper imports and resolves."""
    names = IMPL_COMMENT.findall(paper.read_text(encoding="utf-8"))
    assert names, f"{paper.name} has no impl links"
    unresolved = sorted({name for name in names if _resolve(name) is None})
    assert not unresolved, (
        f"{len(unresolved)} impl link(s) in {paper.name} name code that does not exist: "
        f"{unresolved}. Update the document or the link when the code moves."
    )


@pytest.mark.parametrize("paper", [ARCHITECTURE, IMPLEMENTATION], ids=lambda path: path.stem)
def test_paper_has_the_paper_layout(paper: Path) -> None:
    """Each paper has a version line, an abstract and a references section."""
    text = paper.read_text(encoding="utf-8")
    assert VERSION_LINE.search(text), f"{paper.name} has no '*Version ... *' line under its title"
    assert re.search(r"^## Abstract$", text, re.MULTILINE), f"{paper.name} has no Abstract section"
    assert re.search(r"^## References$", text, re.MULTILINE), f"{paper.name} has no References section"


@pytest.mark.parametrize(
    ("paper", "limit"),
    [(ARCHITECTURE, ARCHITECTURE_WORD_LIMIT), (IMPLEMENTATION, IMPLEMENTATION_WORD_LIMIT)],
    ids=lambda value: value.stem if isinstance(value, Path) else str(value),
)
def test_papers_stay_within_their_length(paper: Path, limit: int) -> None:
    """Each Wayfinding paper is no longer than its limit."""
    assert_within_word_limit(paper, limit)
