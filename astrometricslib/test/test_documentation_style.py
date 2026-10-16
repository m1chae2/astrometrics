r"""Purpose: Enforce the layout and linking rules of the library documentation.

Description: ``documentation/Documentation_Conventions.md`` sets how the
library's documents are laid out and linked. These tests check the rules a
script can check exactly:

- every README under ``astrometricslib/`` carries the glossary line and ends
  with the pointer to the code;
- pipeline and stage READMEs have their required sections, in order;
- a status label sits next to every link into the validation register, and
  only the three defined labels are used;
- a value the register gives for a code constant equals the constant's
  current value, so a changed threshold cannot leave stale evidence behind;
- every "§n" reference in the implementation document names a section of
  the architecture paper;
- every relative link and anchor in the documentation resolves;
- every numbered display equation has the one shape both the docs site and
  GitHub can draw: a single top-level ``\tag{n}`` and no blank line, with
  each paper's numbers running 1, 2, 3 and so on.

Whether MathJax can draw every equation at all is checked on the built site
by ``build/documentation/math_check/check_math.mjs``.

Prose style (spelling, terms, units, sentence length) is checked by Vale and
Markdown mechanics by markdownlint; see ``build/documentation/lint_docs.sh``.
"""

import re
import unicodedata
from pathlib import Path

import pytest

from astrometricslib.test.test_architecture_doc_claims import _resolve

REPO_ROOT = Path(__file__).resolve().parents[2]
LIBRARY = REPO_ROOT / "astrometricslib"
DESIGN_DIR = REPO_ROOT / "documentation" / "library_design"
ARCHITECTURE = DESIGN_DIR / "Astrometrics_Library_Architecture.md"
IMPLEMENTATION = DESIGN_DIR / "Astrometrics_Library_Implementation.md"
REGISTER_DIR = DESIGN_DIR / "validation"

GLOSSARY_SENTENCE = "This document also defines each term the first time it appears."
CODE_POINTER = "For exact behavior, read the code."
PIPELINES = ("stacking", "astrometry", "photometry", "spectroscopy", "asteroid_detection")
STAGES = ("pre_processing", "processing", "post_processing")
PIPELINE_SECTIONS = (
    "Terms used here",
    "What the pipeline does, in order",
    "Settings",
    "Quality outputs",
    "Where each piece lives",
    "Limits",
    "Scope",
)
STAGE_SECTIONS = ("What this stage does, in order", "Settings", "Quality metrics", "Files")
STATUS_BEFORE_LINK = re.compile(
    r"\((?:measured|partly measured|design estimate); see \[[^\]]+\]\([^)]+\)", re.IGNORECASE
)
REGISTER_LINK = re.compile(r"\[[^\]]+\]\(([^)]*validation/\w+\.md#[\w-]+)\)")
NON_STANDARD_LABELS = re.compile(
    r"\b(?:designed value|design value|designed limit|designed, not measured)\b",
    re.IGNORECASE,
)
# A register line that states a code constant's value, e.g.
# "`CLOUD_STAR_FRACTION` = 0.5 in ... <!-- impl: a.b.CLOUD_STAR_FRACTION -->".
CONSTANT_VALUE = re.compile(r"`([A-Z][A-Z0-9_]+)` = (-?\d+(?:\.\d+)?(?:/\d+)?)[^\n]*?<!-- impl: ([\w.]+) -->")
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")


def _readmes() -> list[Path]:
    """List every README under the library package.

    Returns
    -------
    paths : `list` [`pathlib.Path`]
        The README files, sorted.
    """
    return sorted(LIBRARY.rglob("README.md"))


def _headings(text: str, level: int) -> list[str]:
    """Return the headings of one level, in document order.

    Parameters
    ----------
    text : `str`
        A Markdown document.
    level : `int`
        The number of ``#`` characters (2 for ``##``).

    Returns
    -------
    headings : `list` [`str`]
        The heading texts, stripped.
    """
    outside_code = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    return [
        match.group(1).strip() for match in re.finditer(rf"^{'#' * level} (.+)$", outside_code, re.MULTILINE)
    ]


def _slug(heading: str) -> str:
    """Turn a heading into its anchor, as GitHub and MyST do.

    Parameters
    ----------
    heading : `str`
        Heading text.

    Returns
    -------
    anchor : `str`
        Lowercase, with punctuation removed and spaces turned into hyphens.
    """
    text = re.sub(r"`([^`]*)`", r"\1", heading).strip().lower()
    kept = []
    for char in text:
        category = unicodedata.category(char)
        if char in " -_" or category[0] in "LN":
            kept.append(char)
    return "".join(kept).replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    """Collect the anchors a Markdown file offers.

    Parameters
    ----------
    path : `pathlib.Path`
        A Markdown file.

    Returns
    -------
    anchors : `set` [`str`]
        Heading slugs and explicit ``<a id="...">`` anchors.
    """
    text = path.read_text(encoding="utf-8")
    outside_code = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    anchors = {_slug(match.group(1)) for match in re.finditer(r"^#{1,6} (.+)$", outside_code, re.MULTILINE)}
    anchors.update(re.findall(r'<a id="([^"]+)"', text))
    return anchors


def _documentation_files() -> list[Path]:
    """List the hand-written and generated Markdown documents to check.

    Returns
    -------
    paths : `list` [`pathlib.Path`]
        The library READMEs, the top-level documentation pages, the how-to
        guides, the design papers, the review guides and the register.
    """
    docs = REPO_ROOT / "documentation"
    files = set(_readmes())
    files.update(docs.glob("*.md"))
    files.update((docs / "how_to").glob("*.md"))
    files.update(DESIGN_DIR.glob("Astrometrics_Library_*.md"))
    files.update((DESIGN_DIR / "pipelines").glob("*.md"))
    files.update(REGISTER_DIR.glob("*.md"))
    return sorted(files)


@pytest.mark.parametrize("path", _readmes(), ids=lambda path: str(path.relative_to(LIBRARY)))
def test_readme_has_glossary_line_and_code_pointer(path: Path) -> None:
    """Every library README links the glossary and points back to the code."""
    text = path.read_text(encoding="utf-8")
    assert "Glossary.md" in text and GLOSSARY_SENTENCE in text, (
        f"{path.relative_to(REPO_ROOT)} lacks the glossary line (Documentation_Conventions.md §6.5)"
    )
    last_line = text.rstrip().splitlines()[-1].strip()
    assert last_line == CODE_POINTER, (
        f"{path.relative_to(REPO_ROOT)} must end with {CODE_POINTER!r}, not {last_line!r}"
    )


@pytest.mark.parametrize("pipeline", PIPELINES)
def test_pipeline_readme_has_required_sections_in_order(pipeline: str) -> None:
    """A pipeline README follows the layout in the conventions, §3.1."""
    path = LIBRARY / "pipelines" / pipeline / "README.md"
    headings = _headings(path.read_text(encoding="utf-8"), 2)
    positions = [headings.index(section) if section in headings else -1 for section in PIPELINE_SECTIONS]
    missing = [
        section for section, position in zip(PIPELINE_SECTIONS, positions, strict=True) if position < 0
    ]
    assert not missing, f"{path.relative_to(REPO_ROOT)} lacks the sections {missing}"
    assert positions == sorted(positions), f"{path.relative_to(REPO_ROOT)} has its sections out of order"
    text = path.read_text(encoding="utf-8")
    assert f"library_design/pipelines/{pipeline}.md" in text, (
        f"{path.relative_to(REPO_ROOT)} does not link its review guide"
    )


@pytest.mark.parametrize(
    "path",
    [
        LIBRARY / "pipelines" / p / s / "README.md"
        for p in PIPELINES
        for s in STAGES
        if (LIBRARY / "pipelines" / p / s).is_dir()
    ],
    ids=lambda path: str(path.relative_to(LIBRARY / "pipelines").parent),
)
def test_stage_readme_has_required_sections_in_order(path: Path) -> None:
    """A stage README follows the layout in the conventions, §3.2."""
    headings = _headings(path.read_text(encoding="utf-8"), 2)
    positions = [headings.index(section) if section in headings else -1 for section in STAGE_SECTIONS]
    missing = [section for section, position in zip(STAGE_SECTIONS, positions, strict=True) if position < 0]
    assert not missing, f"{path.relative_to(REPO_ROOT)} lacks the sections {missing}"
    assert positions == sorted(positions), f"{path.relative_to(REPO_ROOT)} has its sections out of order"


def _enclosing_parenthesis(text: str, index: int) -> str:
    """Return the text from the innermost unclosed "(" before a position.

    Parameters
    ----------
    text : `str`
        A document.
    index : `int`
        The position of a link.

    Returns
    -------
    segment : `str`
        The text between the opening parenthesis and the position, or an
        empty string when the position is not inside parentheses.
    """
    depth = 0
    for position in range(index - 1, max(-1, index - 400), -1):
        char = text[position]
        if char == ")":
            depth += 1
        elif char == "(":
            if depth == 0:
                return text[position + 1 : index]
            depth -= 1
    return ""


def test_readme_register_links_carry_a_status_label() -> None:
    """A README link into the register comes with its status label."""
    label = re.compile(r"\b(?:measured|partly measured|design estimates?)\b[^()]*?\bsee\b", re.IGNORECASE)
    in_sentence = re.compile(
        r"\b(?:measured|partly measured|design estimates?)\b.*?\b(?:see|links? to)\b", re.IGNORECASE
    )
    problems = []
    for path in _readmes():
        # The scripts catalog points each script at the entries it produces;
        # those links name evidence, not a tuned value.
        if path == LIBRARY / "scripts" / "README.md":
            continue
        text = re.sub(r"`[^`\n]*`", lambda code: "x" * len(code.group(0)), path.read_text(encoding="utf-8"))
        for match in REGISTER_LINK.finditer(text):
            sentence_start = max(text.rfind(". ", 0, match.start()), text.rfind("\n", 0, match.start()))
            sentence = text[sentence_start + 1 : match.start()]
            if not label.search(_enclosing_parenthesis(text, match.start())) and not in_sentence.search(
                sentence
            ):
                line = text.count("\n", 0, match.start()) + 1
                problems.append(f"{path.relative_to(REPO_ROOT)}:{line}: {match.group(0)[:70]}")
    assert not problems, (
        "Each link into the validation register sits in a parenthesis with its status label, as in "
        "'(partly measured; see [STK-08](...))' (Documentation_Conventions.md §2): " + "; ".join(problems)
    )


def test_only_the_three_status_labels_are_used() -> None:
    """No document uses a status word other than the three defined labels."""
    problems = []
    for path in _documentation_files():
        text = re.sub(r"`[^`]*`", "", path.read_text(encoding="utf-8"))
        for match in NON_STANDARD_LABELS.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            problems.append(f"{path.relative_to(REPO_ROOT)}:{line}: {match.group(0)!r}")
    assert not problems, f"Use measured, partly measured or design estimate instead: {problems}"


def test_register_values_match_the_code() -> None:
    """A register value for a code constant equals the live value."""
    mismatches = []
    checked = 0
    for path in sorted(REGISTER_DIR.glob("*.md")):
        for match in CONSTANT_VALUE.finditer(path.read_text(encoding="utf-8")):
            name, stated, dotted = match.groups()
            if not dotted.endswith("." + name):
                continue
            live = _resolve(dotted)
            if not isinstance(live, (int, float)) or isinstance(live, bool):
                continue
            checked += 1
            numerator, _, denominator = stated.partition("/")
            value = float(numerator) / float(denominator or 1)
            if abs(float(live) - value) > 1e-9 * max(1.0, abs(float(live))):
                mismatches.append(f"{path.name}: {name} is {live} in the code but {stated} in the register")
    assert checked >= 20, f"Only {checked} register values were checked; the pattern may be broken"
    assert not mismatches, (
        "A tuned value changed without its register entry. Rerun the entry's procedure or mark the "
        f"result as made with an earlier version, then update the value: {mismatches}"
    )


def test_implementation_section_references_exist_in_the_architecture_paper() -> None:
    """Each "§n" in the implementation document names a real section."""
    architecture = ARCHITECTURE.read_text(encoding="utf-8")
    sections = set(re.findall(r"^#{2,4} (\d+(?:\.\d+)*)\.? ", architecture, re.MULTILINE))
    text = IMPLEMENTATION.read_text(encoding="utf-8")
    references = set(re.findall(r"§(\d+(?:\.\d+)*)", text))
    unknown = sorted(reference for reference in references if reference not in sections)
    assert references, "The implementation document cites no architecture sections"
    assert not unknown, f"Architecture sections cited by the implementation document do not exist: {unknown}"


def test_relative_links_and_anchors_resolve() -> None:
    """Every relative documentation link resolves, anchor included."""
    problems = []
    anchor_cache: dict[Path, set[str]] = {}
    for path in _documentation_files():
        text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
        # Examples written as inline code are not links.
        text = re.sub(r"`[^`\n]*`", lambda code: " " * len(code.group(0)), text)
        for match in MARKDOWN_LINK.finditer(text):
            target = match.group(1)
            if re.match(r"[a-z][a-z0-9+.-]*:", target) or target.startswith("<"):
                continue
            file_part, _, anchor = target.partition("#")
            destination = (path.parent / file_part).resolve() if file_part else path
            line = text.count("\n", 0, match.start()) + 1
            where = f"{path.relative_to(REPO_ROOT)}:{line}"
            if not destination.exists():
                problems.append(f"{where}: missing file {file_part}")
                continue
            if anchor and destination.suffix == ".md":
                if destination not in anchor_cache:
                    anchor_cache[destination] = _anchors(destination)
                if anchor not in anchor_cache[destination]:
                    problems.append(f"{where}: no anchor #{anchor} in {destination.name}")
    assert not problems, f"Broken documentation links: {problems}"


@pytest.mark.parametrize("skill", ["documentation-style", "code-documentation-style", "answer-from-docs"])
def test_agent_skill_copies_match(skill: str) -> None:
    """The ``.agent`` copy of each shared skill equals the ``.claude`` copy."""
    original = REPO_ROOT / ".claude" / "skills" / skill / "SKILL.md"
    copy = REPO_ROOT / ".agent" / "skills" / skill / "SKILL.md"
    assert copy.read_bytes() == original.read_bytes(), (
        f"{copy.relative_to(REPO_ROOT)} differs from {original.relative_to(REPO_ROOT)}; copy it over"
    )


DISPLAY_MATH = re.compile(r"^[ \t]*\$\$\n(.*?)^[ \t]*\$\$", re.MULTILINE | re.DOTALL)
EQUATION_TAG = re.compile(r"\\tag\{([^}]*)\}")
ENVIRONMENT = re.compile(r"\\(begin|end)\{[^}]+\}")


def _equation_files() -> list[Path]:
    """List the Markdown files whose display equations are checked.

    Returns
    -------
    paths : `list` [`pathlib.Path`]
        Every Markdown file under ``documentation/`` except build output, and
        every README in the two libraries.
    """
    files = {
        path
        for path in (REPO_ROOT / "documentation").rglob("*.md")
        if "_build" not in path.parts and "plans" not in path.parts
    }
    for library in ("astrometricslib", "wayfindinglib"):
        files.update((REPO_ROOT / library).rglob("README.md"))
    return sorted(files)


def _tag_depth(equation: str, position: int) -> int:
    r"""Count the environments still open at one place in an equation.

    Parameters
    ----------
    equation : `str`
        The TeX source of one display equation.
    position : `int`
        A character offset into ``equation``.

    Returns
    -------
    depth : `int`
        The number of ``\begin{...}`` blocks not yet closed at ``position``.
    """
    depth = 0
    for match in ENVIRONMENT.finditer(equation[:position]):
        depth += 1 if match.group(1) == "begin" else -1
    return depth


@pytest.mark.parametrize("path", _equation_files(), ids=lambda path: str(path.relative_to(REPO_ROOT)))
def test_numbered_equations_have_a_drawable_shape(path: Path) -> None:
    r"""Each tagged equation has one top-level tag and no blank line.

    MathJax and GitHub refuse a ``\tag`` inside an environment such as
    ``aligned`` or ``cases``, refuse two tags in one equation, and treat a
    blank line as the end of the equation. The docs build passes the rest to
    MathJax unchanged (``_numbered_equations_unwrapped`` in
    ``documentation/conf.py``).
    """
    problems = []
    text = path.read_text(encoding="utf-8")
    for block in DISPLAY_MATH.finditer(text):
        equation = block.group(1)
        tags = list(EQUATION_TAG.finditer(equation))
        if not tags:
            continue
        line = text.count("\n", 0, block.start()) + 1
        if len(tags) > 1:
            problems.append(f"line {line}: {len(tags)} tags in one equation")
        if any(_tag_depth(equation, tag.start()) for tag in tags):
            problems.append(
                f"line {line}: \\tag sits inside an environment; put it after the last \\end{{...}}"
            )
        if re.search(r"\n[ \t]*\n", equation.strip()):
            problems.append(f"line {line}: blank line inside a numbered equation")
    assert not problems, f"{path.relative_to(REPO_ROOT)}: {problems}"


@pytest.mark.parametrize("path", sorted(DESIGN_DIR.glob("*_Library_*.md")), ids=lambda path: path.name)
def test_paper_equation_numbers_run_in_order(path: Path) -> None:
    """A paper numbers its equations 1, 2, 3 and so on, with no gaps."""
    numbers = EQUATION_TAG.findall(path.read_text(encoding="utf-8"))
    expected = [str(number) for number in range(1, len(numbers) + 1)]
    assert numbers == expected, f"{path.name} numbers its equations {numbers}; expected {expected}"
