"""Purpose: Enforce where each kind of content lives in the documentation.

Description: The library's documentation is split by purpose:

- ``Astrometrics_Library_Architecture.md`` explains the theory. It carries no
  implementation names (files, classes, functions, config keys) and no tuned
  values. Hidden ``<!-- impl: -->`` comments may name code, because they do
  not render; ``test_architecture_doc_claims.py`` checks them.
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

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_DIR = REPO_ROOT / "documentation" / "library_design"
ARCHITECTURE = DESIGN_DIR / "Astrometrics_Library_Architecture.md"

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
    # Angles on the sky.
    "0°",
    "30°",
    "90°",
    "360°",
    # Light-travel time across the Earth's orbit (barycentric correction).
    "8 minutes",
    # Worked illustration of a flat field.
    "80%",
    # Results of Chauvenet's criterion against a fixed 3-sigma cut (Eq. 2).
    "0.27%",
    "185 frames",
})
# Names of outside tools written in mixed case. Code names are not allowed.
ALLOWED_MIXED_CASE = frozenset({"SkyBoT", "StarNet"})

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
        removed. Line breaks are kept so line numbers still match.
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
        evidence), the pipeline review guides and the architecture paper.
    """
    readmes = [
        path for path in (REPO_ROOT / "astrometricslib").rglob("README.md") if "data" not in path.parts
    ]
    guides = sorted((DESIGN_DIR / "pipelines").glob("*.md"))
    return sorted(readmes) + guides + [ARCHITECTURE]


def test_architecture_names_no_code() -> None:
    """The architecture paper shows no file, class, function or config name."""
    text = _visible_text(ARCHITECTURE)
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
    assert not problems, (
        "The architecture paper explains the theory and names no code. Move these names to "
        f"Astrometrics_Library_Implementation.md (outside tools may be named in plain text): {problems}"
    )


def test_architecture_states_no_tuned_values() -> None:
    """The architecture paper describes each setting's idea, not its value."""
    text = _visible_text(ARCHITECTURE)
    problems = [
        f"line {_line_of(text, match.start())}: {match.group(0)!r}"
        for match in QUANTITY.finditer(text)
        if re.sub(r"\s", " ", match.group(0)) not in ALLOWED_QUANTITIES
    ]
    assert not problems, (
        "The architecture paper states no tuned values. Describe the idea instead "
        "(for example 'a fixed share of the session median') and put the value in "
        "Astrometrics_Library_Implementation.md. If a quantity is physics or a definition, "
        f"add it to ALLOWED_QUANTITIES with a comment: {problems}"
    )


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
