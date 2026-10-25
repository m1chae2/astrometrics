"""Purpose: Enforce where each kind of content lives in the documentation.

Description: The library's documentation is split by purpose:

- ``Astrometrics_Library_Architecture.md`` explains the theory. It carries no
  implementation names (files, classes, functions, config keys) and no tuned
  values. Hidden ``<!-- impl: -->`` comments may name code, because they do
  not render; ``test_architecture_doc_claims.py`` checks them.
- ``Wayfinding_Quality_Judgment.md`` explains the theory of how the Wayfinding
  library judges quality and recommends the next observation. It follows the
  same rules as the architecture paper.
- ``Astrometrics_Library_Implementation.md`` carries the code names and the
  tuned values.
- ``documentation/library_design/validation/`` (the validation register)
  carries the evidence: data sets, dates, results. The method documents cite
  an entry instead of repeating its numbers.

These tests catch content that drifts into the wrong document. Each failure
message says where the content belongs. ``test_validation_register.py``
checks the register itself.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_DIR = REPO_ROOT / "documentation" / "library_design"
ARCHITECTURE = DESIGN_DIR / "Astrometrics_Library_Architecture.md"
IMPLEMENTATION = DESIGN_DIR / "Astrometrics_Library_Implementation.md"
WAYFINDING_QUALITY_JUDGMENT = DESIGN_DIR / "Wayfinding_Quality_Judgment.md"
# Length limits for the papers (documentation/Documentation_Conventions.md,
# Section 3.4). A journal paper on scientific software runs 5,000 to 10,000
# words; the implementation paper is an index into the code, not a second
# paper. References and hidden <!-- impl: --> comments do not count.
ARCHITECTURE_WORD_LIMIT = 10_000
IMPLEMENTATION_WORD_LIMIT = 4_500
WAYFINDING_QUALITY_JUDGMENT_WORD_LIMIT = 5_500

# Numbers with units that are physics, definitions or worked illustrations, not
# tuned settings. Adding a value here asserts that it is not a tuned setting;
# a tuned value belongs in the implementation document instead.
ALLOWED_QUANTITIES = frozenset({
    # Magnitude scale: 5 magnitudes is a factor of 100; 0.01 mag is about 1%.
    "5 mag",
    "0.01 mag",
    "1%",
    # Photometric bands and the visible range.
    "440 nm",
    "550 nm",
    "4000 Å",
    "7000 Å",
    # Balmer lines of hydrogen.
    "4861 Å",
    "4340 Å",
    "4102 Å",
    "410.2 nm",
    # Unit definitions.
    "1 Å",
    "0.1 nm",
    "60 arcsec",
    "24 h",
    # Angles on the sky. A meridian flip turns the field half a turn (180°).
    "0°",
    "180°",
    "30°",
    "90°",
    "360°",
    # Sidereal rate: the sky turns 15° of hour angle per hour (physics).
    "15°",
    # Light-travel time across the Earth's orbit (barycentric correction).
    "8 minutes",
    # Worked illustration of a flat field.
    "80%",
    # Results of Chauvenet's criterion against a fixed 3-sigma cut (Eq. 2).
    "0.27%",
    "185 frames",
    # Quantities that are part of the title of a cited paper.
    "1 minute",
    "25000 Å",
})
# Outside tools and authors written in mixed case. Code names are not allowed.
ALLOWED_MIXED_CASE = frozenset({
    "AstroImageJ",
    "NumPy",
    "PixInsight",
    "SciPy",
    "SkyBoT",
    "StarNet",
    "VanderPlas",
    "VizieR",
})

UNIT = (
    r"(?:px|pixels?|arcsec|arcseconds?|arcminutes?|%|percent|σ|sigma|frames?|stars?|mag|"
    r"magnitudes?|seconds?|minutes?|hours?|nm|Å|degrees?|°C|°|s\b|h\b)"
)
QUANTITY = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?\s?" + UNIT)
# An ISO date marks a measurement record; the version line under each title is
# the one place a method document may carry one.
ISO_DATE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
VERSION_LINE = re.compile(r"^\*Version [^*]*\*$")


def _visible_text(path: Path) -> str:
    """Return the text a reader sees, without math, comments or link targets.

    Parameters
    ----------
    path : `pathlib.Path`
        A Markdown file.

    Returns
    -------
    text : `str`
        The file with HTML comments, LaTeX math, link targets and HTML anchors
        removed. For the architecture paper, fenced diagrams are removed too.
        Line breaks are kept so line numbers still match.
    """

    def blank(match: re.Match[str]) -> str:
        """Replace a match with the same number of newlines it held.

        Parameters
        ----------
        match : `re.Match`
            The text to blank out.

        Returns
        -------
        blanked : `str`
            One newline for each newline in the match.
        """
        return "\n" * match.group(0).count("\n")

    text = path.read_text(encoding="utf-8")
    if path.name.endswith("_Library_Architecture.md"):
        # A figure in a paper is a fenced Mermaid diagram, not prose.
        text = re.sub(r"^```.*?^```[ \t]*$", blank, text, flags=re.DOTALL | re.MULTILINE)
    text = re.sub(r"<!--.*?-->", blank, text, flags=re.DOTALL)
    text = re.sub(r"\$\$.*?\$\$", blank, text, flags=re.DOTALL)
    text = re.sub(r"\$[^$\n]+\$", "", text)
    text = re.sub(r"\]\([^)]*\)", "]", text)
    return re.sub(r"<a id=[^>]*></a>", "", text)


def _line_of(text: str, index: int) -> int:
    """Find the line a character sits on.

    Parameters
    ----------
    text : `str`
        The whole document.
    index : `int`
        A character offset into ``text``.

    Returns
    -------
    line : `int`
        The 1-based line number.
    """
    return text.count("\n", 0, index) + 1


def _method_documents() -> list[Path]:
    """List the documents that describe methods and must not hold evidence.

    Returns
    -------
    paths : `list` [`pathlib.Path`]
        Every README under ``astrometricslib/`` except the ones that describe
        shipped data files (their download dates are provenance, not
        evidence), the pipeline review guides, the architecture paper and the
        quality measurements paper.
    """
    readmes = [
        path for path in (REPO_ROOT / "astrometricslib").rglob("README.md") if "data" not in path.parts
    ]
    guides = sorted((DESIGN_DIR / "pipelines").glob("*.md"))
    return sorted(readmes) + guides + [ARCHITECTURE, WAYFINDING_QUALITY_JUDGMENT]


def _implementation_paper_name(paper: Path) -> str:
    """Name the implementation paper that holds a theory paper's code names.

    Parameters
    ----------
    paper : `pathlib.Path`
        A theory paper: an architecture paper, or the quality measurements
        paper of the Astrometrics library.

    Returns
    -------
    name : `str`
        The file name of the matching implementation paper.
    """
    if paper.name == WAYFINDING_QUALITY_JUDGMENT.name:
        return "Wayfinding_Library_Implementation.md"
    return paper.name.replace("Architecture", "Implementation")


def assert_names_no_code(paper: Path) -> None:
    """Check that an architecture paper shows no code names.

    Parameters
    ----------
    paper : `pathlib.Path`
        An architecture paper. The calling test fails when its visible text
        names a file, class, function or config key.
    """
    text = _visible_text(paper)
    problems = []
    for match in re.finditer(r"`([^`]+)`", text):
        span = match.group(1)
        # A companion document named by file, or a Sphinx cross-reference role.
        if re.fullmatch(r"[A-Za-z_]+\.md", span) or "</" in span:
            continue
        problems.append(f"line {_line_of(text, match.start())}: `{span}`")
    for match in re.finditer(r"\b[a-z][a-z0-9]*_[a-z0-9_]+\b", text):
        if not text[match.end() : match.end() + 3] == ".md":
            problems.append(f"line {_line_of(text, match.start())}: {match.group(0)}")
    for match in re.finditer(r"\b[A-Za-z]*[a-z][A-Z][A-Za-z]*\b", text):
        if match.group(0) not in ALLOWED_MIXED_CASE:
            problems.append(f"line {_line_of(text, match.start())}: {match.group(0)}")
    for match in re.finditer(r"\b[\w/]+\.(?:py|json|toml|fits)\b|\[[A-Z][a-z]+\.[A-Z]\w*\]", text):
        problems.append(f"line {_line_of(text, match.start())}: {match.group(0)}")
    implementation = _implementation_paper_name(paper)
    assert not problems, (
        f"{paper.name} explains the theory and names no code. Move these names to "
        f"{implementation} (outside tools may be named in plain text): {problems}"
    )


def assert_states_no_tuned_values(paper: Path) -> None:
    """Check that an architecture paper states no tuned values.

    Parameters
    ----------
    paper : `pathlib.Path`
        An architecture paper. The calling test fails when its visible text
        holds a quantity that is not in `ALLOWED_QUANTITIES`.
    """
    text = _visible_text(paper)
    problems = [
        f"line {_line_of(text, match.start())}: {match.group(0)!r}"
        for match in QUANTITY.finditer(text)
        if re.sub(r"\s", " ", match.group(0)) not in ALLOWED_QUANTITIES
    ]
    implementation = _implementation_paper_name(paper)
    assert not problems, (
        f"{paper.name} states no tuned values. Describe the idea instead "
        "(for example 'a fixed share of the session median') and put the value in "
        f"{implementation}. If a quantity is physics or a definition, "
        f"add it to ALLOWED_QUANTITIES with a comment: {problems}"
    )


def paper_word_count(paper: Path) -> int:
    """Count the words a reader sees in a paper, without its references.

    Parameters
    ----------
    paper : `pathlib.Path`
        A paper.

    Returns
    -------
    words : `int`
        The number of words outside HTML comments and outside the
        ``## References`` section.
    """
    text = re.sub(r"<!--.*?-->", "", paper.read_text(encoding="utf-8"), flags=re.DOTALL)
    text = re.sub(r"^## References\n.*?(?=^## |\Z)", "", text, flags=re.MULTILINE | re.DOTALL)
    return len(text.split())


def assert_within_word_limit(paper: Path, limit: int) -> None:
    """Check that a paper stays within its length limit.

    Parameters
    ----------
    paper : `pathlib.Path`
        A paper. The calling test fails when it has more than ``limit``
        words.
    limit : `int`
        The most words the paper may have.
    """
    words = paper_word_count(paper)
    assert words <= limit, (
        f"{paper.name} has {words} words; the limit is {limit}. Tighten the prose, cut repetition, "
        "or move detail to the review guides, the READMEs or the glossary instead of raising the limit."
    )


def test_architecture_names_no_code() -> None:
    """The architecture paper shows no file, class, function or config name."""
    assert_names_no_code(ARCHITECTURE)


def test_architecture_states_no_tuned_values() -> None:
    """The architecture paper describes each setting's idea, not its value."""
    assert_states_no_tuned_values(ARCHITECTURE)


def test_wayfinding_quality_judgment_names_no_code() -> None:
    """The judgment paper shows no code name."""
    assert_names_no_code(WAYFINDING_QUALITY_JUDGMENT)


def test_wayfinding_quality_judgment_states_no_tuned_values() -> None:
    """The judgment paper states no tuned value."""
    assert_states_no_tuned_values(WAYFINDING_QUALITY_JUDGMENT)


def test_validation_evidence_lives_in_the_register() -> None:
    """Method documents carry no measurement records; the register does."""
    problems = []
    for path in _method_documents():
        text = _visible_text(path)
        for number, line in enumerate(text.splitlines(), start=1):
            if VERSION_LINE.match(line.strip()):
                continue
            for match in ISO_DATE.finditer(line):
                problems.append(f"{path.relative_to(REPO_ROOT)}:{number}: {match.group(0)}")
    assert not problems, (
        "A dated result is validation evidence. Move it to its entry in "
        "documentation/library_design/validation/ and cite the entry from here "
        f"(for example '(partly measured; see [STK-08](...#stk-08))'): {problems}"
    )


@pytest.mark.parametrize(
    ("paper", "limit"),
    [
        (ARCHITECTURE, ARCHITECTURE_WORD_LIMIT),
        (IMPLEMENTATION, IMPLEMENTATION_WORD_LIMIT),
        (WAYFINDING_QUALITY_JUDGMENT, WAYFINDING_QUALITY_JUDGMENT_WORD_LIMIT),
    ],
    ids=lambda value: value.stem if isinstance(value, Path) else str(value),
)
def test_papers_stay_within_their_length(paper: Path, limit: int) -> None:
    """Each Astrometrics paper is no longer than its limit."""
    assert_within_word_limit(paper, limit)
