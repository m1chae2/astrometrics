"""Purpose: Keep the validation register complete and traceable.

Description: ``documentation/library_design/validation/`` holds one file per
pipeline. Each file lists the measurements behind the library's tuned values
as numbered entries (``STK-01``, ``PHO-03`` and so on). The method documents
cite an entry by ID instead of repeating its numbers. These tests keep that
chain unbroken:

- every entry has the fields a reviewer needs to repeat it on their own data;
- every ``<!-- impl: -->`` link in the register names code that exists;
- every Python example in a procedure is valid Python;
- every script or evidence file an entry names exists;
- every link to an entry points at an ID that exists;
- every entry is cited by at least one method document.
"""

import ast
import re
import textwrap
from pathlib import Path

import pytest

from astrometricslib.test.test_architecture_doc_claims import _resolve

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTER_DIR = REPO_ROOT / "documentation" / "library_design" / "validation"
REGISTER_FILES = {
    "stacking.md": "STK",
    "astrometry.md": "AST",
    "photometry.md": "PHO",
    "spectroscopy.md": "SPE",
    "asteroid_detection.md": "MOV",
}
REQUIRED_FIELDS = (
    "Claim",
    "Settings it supports",
    "Status",
    "Data you need",
    "Procedure",
    "What to look at",
    "Our result",
    "To tune for your setup",
)
ENTRY_HEADING = re.compile(r"^### ([A-Z]{3}-\d{2})\s*$", re.MULTILINE)
IMPL_COMMENT = re.compile(r"<!-- impl: ([^ ]+?) -->")
PYTHON_BLOCK = re.compile(r"^[ \t]*```python\n(.*?)^[ \t]*```", re.DOTALL | re.MULTILINE)
REPO_PATH = re.compile(r"`((?:astrometricslib/scripts|documentation/evidence)/[\w./-]+?\.(?:py|json))`")
ENTRY_LINK = re.compile(r"validation/(\w+\.md)#([a-z]{3}-\d{2})\b")
# Files that may cite register entries: every Markdown file in the library
# and its documentation, plus the generator that writes the review guides.
CITING_GLOBS = ("astrometricslib/**/*.md", "documentation/library_design/**/*.md")
CITING_FILES_EXTRA = ("build/documentation/generate_pipeline_review_docs.py",)


def _entries(path: Path) -> dict[str, str]:
    """Split one register file into its entries.

    Parameters
    ----------
    path : `pathlib.Path`
        A register file.

    Returns
    -------
    entries : `dict` [`str`, `str`]
        Entry text keyed by entry ID, in file order. An entry runs from its
        heading to the next heading of level 3 or higher.
    """
    text = path.read_text(encoding="utf-8")
    matches = list(ENTRY_HEADING.finditer(text))
    entries = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end]
        # Code blocks may hold Python comments that start with "# ", so they
        # are blanked out before looking for the next heading.
        prose = re.sub(r"```.*?```", lambda block: " " * len(block.group(0)), body, flags=re.DOTALL)
        next_section = re.search(r"^#{1,3} ", prose, re.MULTILINE)
        entries[match.group(1)] = body[: next_section.start()] if next_section else body
    return entries


def _citing_files() -> list[Path]:
    """List the files outside the register that may cite an entry.

    Returns
    -------
    files : `list` [`pathlib.Path`]
        Markdown files in the library and the design documents, and the
        review-guide generator, excluding the register itself.
    """
    files = {p for pattern in CITING_GLOBS for p in REPO_ROOT.glob(pattern)}
    files.update(REPO_ROOT / extra for extra in CITING_FILES_EXTRA)
    return sorted(p for p in files if REGISTER_DIR not in p.parents)


def test_every_register_file_exists() -> None:
    """Each pipeline has its register file."""
    missing = [name for name in REGISTER_FILES if not (REGISTER_DIR / name).is_file()]
    assert not missing, f"Missing register files: {missing}"


@pytest.mark.parametrize("name", sorted(REGISTER_FILES))
def test_entry_ids_are_unique_and_use_the_file_prefix(name: str) -> None:
    """IDs in one file are unique, numbered and use that pipeline's prefix."""
    text = (REGISTER_DIR / name).read_text(encoding="utf-8")
    ids = ENTRY_HEADING.findall(text)
    assert ids, f"{name} has no entries"
    assert len(ids) == len(set(ids)), f"{name} repeats an entry ID"
    wrong = [entry_id for entry_id in ids if not entry_id.startswith(REGISTER_FILES[name] + "-")]
    assert not wrong, f"{name} has IDs with the wrong prefix: {wrong}"


@pytest.mark.parametrize("name", sorted(REGISTER_FILES))
def test_every_entry_has_the_fields_a_reviewer_needs(name: str) -> None:
    """Each entry gives its claim, data, procedure, result and tuning."""
    incomplete = {}
    for entry_id, body in _entries(REGISTER_DIR / name).items():
        missing = [field for field in REQUIRED_FIELDS if f"**{field}:**" not in body]
        if missing:
            incomplete[entry_id] = missing
    assert not incomplete, f"{name}: entries missing fields: {incomplete}"


@pytest.mark.parametrize("name", sorted(REGISTER_FILES))
def test_register_impl_links_name_code_that_exists(name: str) -> None:
    """Each ``impl`` link in the register imports and resolves."""
    text = (REGISTER_DIR / name).read_text(encoding="utf-8")
    links = IMPL_COMMENT.findall(text)
    assert links, f"{name} has no impl links"
    unresolved = sorted({link for link in links if _resolve(link) is None})
    assert not unresolved, f"{name}: impl links to code that does not exist: {unresolved}"


@pytest.mark.parametrize("name", sorted(REGISTER_FILES))
def test_procedure_examples_are_valid_python(name: str) -> None:
    """Every ``python`` code block in the register parses."""
    text = (REGISTER_DIR / name).read_text(encoding="utf-8")
    broken = []
    for number, block in enumerate(PYTHON_BLOCK.findall(text), start=1):
        try:
            ast.parse(textwrap.dedent(block))
        except SyntaxError as error:
            broken.append(f"block {number}: {error}")
    assert not broken, f"{name}: invalid Python examples: {broken}"


@pytest.mark.parametrize("name", sorted(REGISTER_FILES))
def test_named_scripts_and_evidence_files_exist(name: str) -> None:
    """Every script or evidence path an entry names is in the repository."""
    text = (REGISTER_DIR / name).read_text(encoding="utf-8")
    missing = sorted({path for path in REPO_PATH.findall(text) if not (REPO_ROOT / path).is_file()})
    assert not missing, f"{name} names files that do not exist: {missing}"


def test_every_link_to_an_entry_points_at_an_existing_id() -> None:
    """A citation such as ``validation/stacking.md#stk-04`` resolves."""
    known = {
        (name, entry_id.lower()) for name in REGISTER_FILES for entry_id in _entries(REGISTER_DIR / name)
    }
    broken = []
    for path in _citing_files():
        for name, anchor in ENTRY_LINK.findall(path.read_text(encoding="utf-8")):
            if (name, anchor) not in known:
                broken.append(f"{path.relative_to(REPO_ROOT)}: {name}#{anchor}")
    assert not broken, f"Links to register entries that do not exist: {sorted(set(broken))}"


def test_every_entry_is_cited_by_a_method_document() -> None:
    """No entry is orphaned: some method document points a reader to it."""
    cited = set()
    for path in _citing_files():
        cited.update(ENTRY_LINK.findall(path.read_text(encoding="utf-8")))
    orphans = sorted(
        entry_id
        for name in REGISTER_FILES
        for entry_id in _entries(REGISTER_DIR / name)
        if (name, entry_id.lower()) not in cited
    )
    assert not orphans, f"Register entries no method document cites: {orphans}"
