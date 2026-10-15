"""Purpose: Keep the documentation site's navigation simple and consistent.

Description: The Sphinx site under ``documentation/`` is organized into seven
sections (``documentation/Documentation_Conventions.md``, Section 1.1). The
root page lists only the section pages, and each section page lists its own
pages. These tests check, from the source files alone:

- the root toctree lists exactly the section pages;
- every page of the site is in exactly one toctree, so it has one place in the
  navigation, except pages marked ``:orphan:`` and generated API pages;
- no toctree entry carries its own label, so the navigation always shows each
  page's own title;
- each link on a section page uses the exact title of the page it links to,
  so a document has one name everywhere.
"""

import json
import re
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "documentation"
SECTION_PAGES = ("get_started", "tutorials", "how_to/index", "explanation", "reference", "evidence", "about")
# Folders and files Sphinx does not build into the website (conf.py
# exclude_patterns; papers.rst starts the PDF only), plus the API pages
# sphinx-automodapi generates and links itself.
SKIPPED = ("_build/", "plans/", "evidence/", "api/generated/", ".venv/", "papers.rst")
SUFFIXES = (".md", ".rst", ".ipynb")
RST_TOCTREE = re.compile(r"^\.\. toctree::\n((?:[ \t]+.*\n|\n)*)", re.MULTILINE)
MYST_TOCTREE = re.compile(r"^```\{toctree\}\n(.*?)^```", re.MULTILINE | re.DOTALL)
BULLET_LINK = re.compile(r"^- \[([^\]]+)\]\(([^)#]+)\)", re.MULTILINE)


def _docname(path: Path) -> str:
    """Return a source file's Sphinx document name.

    Parameters
    ----------
    path : `pathlib.Path`
        A source file under ``documentation/``.

    Returns
    -------
    name : `str`
        The path relative to the source folder, without its suffix.
    """
    return path.relative_to(SOURCE).with_suffix("").as_posix()


def _pages() -> list[Path]:
    """List the source files Sphinx builds into pages.

    Returns
    -------
    paths : `list` [`pathlib.Path`]
        Every Markdown, reStructuredText and notebook file, except skipped
        folders and READMEs.
    """
    pages = []
    for path in sorted(SOURCE.rglob("*")):
        relative = path.relative_to(SOURCE).as_posix()
        if path.suffix not in SUFFIXES or path.name == "README.md" or relative.startswith(SKIPPED):
            continue
        if ".ipynb_checkpoints" in path.parts:
            continue
        pages.append(path)
    return pages


def _source_of(docname: str) -> Path | None:
    """Find the source file of a document name.

    Parameters
    ----------
    docname : `str`
        A Sphinx document name, such as ``library_design/validation/index``.

    Returns
    -------
    path : `pathlib.Path` or `None`
        The source file, or `None` when none exists.
    """
    for suffix in SUFFIXES:
        candidate = SOURCE / f"{docname}{suffix}"
        if candidate.exists():
            return candidate
    return None


def _toctree_entries(path: Path) -> list[tuple[str, str]]:
    """Read the toctree entries of one page.

    Parameters
    ----------
    path : `pathlib.Path`
        A ``.md`` or ``.rst`` page.

    Returns
    -------
    entries : `list` [`tuple` [`str`, `str`]]
        Each entry as written, and the document name it points to.
    """
    text = path.read_text(encoding="utf-8")
    blocks = [match.group(1) for match in RST_TOCTREE.finditer(text)]
    blocks += [match.group(1) for match in MYST_TOCTREE.finditer(text)]
    entries = []
    for block in blocks:
        for raw in block.splitlines():
            entry = raw.strip()
            if not entry or entry.startswith(":"):
                continue
            target = re.sub(r"^.*<(.+)>$", r"\1", entry)
            if target.startswith("/"):
                docname = target.lstrip("/")
            else:
                docname = (path.parent / target).resolve().relative_to(SOURCE.resolve()).as_posix()
            entries.append((entry, re.sub(r"\.(md|rst|ipynb)$", "", docname)))
    return entries


def _title(path: Path) -> str:
    """Return the title a page shows in the navigation.

    Parameters
    ----------
    path : `pathlib.Path`
        A page.

    Returns
    -------
    title : `str`
        The first level-one heading.
    """
    if path.suffix == ".ipynb":
        cells = json.loads(path.read_text(encoding="utf-8"))["cells"]
        text = "\n".join("".join(cell["source"]) for cell in cells if cell["cell_type"] == "markdown")
    else:
        text = path.read_text(encoding="utf-8")
    if path.suffix == ".rst":
        match = re.search(r"^(\S.*)\n=+\s*$", text, re.MULTILINE)
    else:
        match = re.search(r"^# (.+)$", text, re.MULTILINE)
    assert match, f"{path.relative_to(SOURCE)} has no title"
    return match.group(1).strip()


def _is_orphan(path: Path) -> bool:
    """Say whether a page is marked as outside the navigation.

    Parameters
    ----------
    path : `pathlib.Path`
        A page.

    Returns
    -------
    orphan : `bool`
        True when the page starts with the ``:orphan:`` field.
    """
    if path.suffix == ".ipynb":
        return False
    return path.read_text(encoding="utf-8").lstrip().startswith((":orphan:", "---\norphan: true"))


def test_root_lists_exactly_the_section_pages() -> None:
    """The root toctree holds the seven section pages, in order."""
    entries = [docname for _, docname in _toctree_entries(SOURCE / "index.rst")]
    assert entries == list(SECTION_PAGES), (
        f"index.rst's toctree should list the section pages {SECTION_PAGES}, found {entries}. "
        "Put other pages in the toctree of their section page."
    )


def test_every_page_has_one_place_in_the_navigation() -> None:
    """Each page is in exactly one toctree, unless it is an orphan."""
    counts = Counter(
        docname for page in _pages() if page.suffix != ".ipynb" for _, docname in _toctree_entries(page)
    )
    problems = []
    for page in _pages():
        name = _docname(page)
        if name == "index" or _is_orphan(page):
            continue
        if counts[name] != 1:
            problems.append(f"{name}: in {counts[name]} toctrees")
    missing = sorted(name for name in counts if _source_of(name) is None)
    assert not problems and not missing, (
        "Each page belongs in exactly one section's toctree (mark a page ':orphan:' only when it is "
        f"reached by links alone). Problems: {problems}. Toctree entries with no page: {missing}"
    )


def test_toctree_entries_carry_no_labels() -> None:
    """The navigation shows each page's own title, never a second name."""
    labelled = [
        f"{_docname(page)}: {entry}"
        for page in _pages()
        if page.suffix != ".ipynb"
        for entry, _ in _toctree_entries(page)
        if "<" in entry
    ]
    assert not labelled, (
        "Write toctree entries as bare paths; change the page's own title instead of labeling "
        f"the entry: {labelled}"
    )


@pytest.mark.parametrize("section", SECTION_PAGES)
def test_section_links_use_page_titles(section: str) -> None:
    """Each link on a section page reads exactly as its target's title."""
    path = _source_of(section)
    assert path is not None, f"missing section page {section}"
    problems = []
    for text, target in BULLET_LINK.findall(path.read_text(encoding="utf-8")):
        destination = (path.parent / target).resolve()
        assert destination.exists(), f"{section}: link to missing {target}"
        title = _title(destination)
        if text != title:
            problems.append(f"'{text}' links to a page titled '{title}'")
    assert not problems, f"{section}: use each page's own title as the link text: {problems}"
