"""Purpose: Keep the validation register complete and traceable.

Description: ``documentation/library_design/validation/`` holds one file per
pipeline. Each file lists the measurements behind the library's tuned values
as numbered entries (``STK-01``, ``PHO-03`` and so on) and ends with a table of
design estimates (``STK-D01`` and so on). The method documents cite an entry by
ID instead of repeating its numbers. These tests keep that chain unbroken:

- every entry has the fields a reviewer needs to repeat it on their own data,
  in the template order of ``validation/index.md``;
- every status, verdict and setting relation uses the allowed words;
- every row of a design-estimate table has a stable ID, and no setting is both
  supported by an entry and listed as a design estimate;
- every ``<!-- impl: -->`` link in the register names code that exists;
- every Python example in a procedure is valid Python;
- every script or evidence file an entry names exists;
- every link to an entry points at an ID that exists;
- every entry is cited by at least one method document;
- every data set ID an entry names is defined in ``validation/index.md``.
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
    "Acceptance criterion",
    "What to look at",
    "Our result",
    "Verdict",
    "Limits",
    "To tune for your setup",
    "Regenerate with",
    "Guarded by tests",
    "Raw record",
)
# The template order. "Runs" is optional: only a re-run entry has it.
FIELD_ORDER = (*REQUIRED_FIELDS[:9], "Runs", *REQUIRED_FIELDS[9:])
RETIRED_FIELDS = ("Claim", "Status")
ENTRY_HEADING = re.compile(r"^### ([A-Z]{3}-\d{2,3})\s*$", re.MULTILINE)
FIELD_LINE = re.compile(r"^- \*\*([^*:]+):\*\*", re.MULTILINE)
STATUS_FORM = re.compile(r"^(Measured|Partly measured|Design estimate|Retired)\. \S")
RETIRED_FORM = re.compile(r"^Retired\. Superseded by \[([A-Z]{3}-\d{2,3})\]\([^)]*\)\.")
VERDICT_FORM = re.compile(r"^(Pass|Fail|Inconclusive|Not judged)\b")
RELATION_FORM = re.compile(r"Relation: (derived from|confirmed by|sanity check only|challenged by)\b")
SUPPORTING_RELATIONS = ("derived from", "confirmed by")
NO_SETTING = re.compile(r"^(None|No tuned value)\b")
RUNS_HEADER = "| Date | Code version | Verdict | Record |"
DESIGN_HEADING = "## Design estimates"
DESIGN_ROW_ID = re.compile(r"^\| ([A-Z]{3}-D\d{2,3}) \|")
DATA_SET_ID = re.compile(r"\bDS-[A-Z0-9]+\b")
DATA_SET_ROW = re.compile(r"^\| (DS-[A-Z0-9]+) \|", re.MULTILINE)
IMPL_COMMENT = re.compile(r"<!-- impl: ([^ ]+?) -->")
PYTHON_BLOCK = re.compile(r"^[ \t]*```python\n(.*?)^[ \t]*```", re.DOTALL | re.MULTILINE)
REPO_PATH = re.compile(r"`((?:astrometricslib/scripts|documentation/evidence)/[\w./-]+?\.(?:py|json))`")
ENTRY_LINK = re.compile(r"validation/(\w+\.md)#([a-z]{3}-\d{2,3})\b")
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


def _fields(body: str) -> dict[str, str]:
    """Split one entry into its fields.

    Parameters
    ----------
    body : `str`
        The text of one entry, as returned by `_entries`.

    Returns
    -------
    fields : `dict` [`str`, `str`]
        The text of each field keyed by its label, in file order. A field runs
        from its ``- **Label:**`` line to the next such line.
    """
    matches = list(FIELD_LINE.finditer(body))
    fields = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        text = body[match.end() : end].strip("\n")
        fields[match.group(1)] = text if text.startswith("  ") else text.strip()
    return fields


def _is_retired(fields: dict[str, str]) -> bool:
    """Say whether an entry has been retired.

    Parameters
    ----------
    fields : `dict` [`str`, `str`]
        The fields of one entry, as returned by `_fields`.

    Returns
    -------
    retired : `bool`
        True when the status begins with "Retired".
    """
    return fields.get("Status", "").startswith("Retired")


def _setting_names(text: str) -> set[tuple[str, str]]:
    """Find the code constants and config keys that a piece of text names.

    Parameters
    ----------
    text : `str`
        A setting bullet, or the first cells of a design-estimate row.

    Returns
    -------
    names : `set` [`tuple` [`str`, `str`]]
        Pairs of a setting name and the file it is said to live in. The file is
        the empty string when the text names none ("in the same file").
    """
    files = re.findall(r"(\w+)\.py\b", text)
    names = set()
    for span in re.findall(r"`([^`\n]+)`", text):
        word = span.split()[-1].rstrip(",.;:")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", word) and ("_" in word or word.isupper()):
            names.update((word, name_file) for name_file in (files or [""]))
    return names


def _design_rows(path: Path) -> list[str]:
    """List the data rows of the design-estimate table of a register file.

    Parameters
    ----------
    path : `pathlib.Path`
        A register file.

    Returns
    -------
    rows : `list` [`str`]
        The table lines after the header and separator rows, in file order.
    """
    text = path.read_text(encoding="utf-8")
    section = text[text.index(DESIGN_HEADING) :]
    table = [line for line in section.splitlines() if line.startswith("|")]
    return table[2:]


@pytest.mark.parametrize("name", sorted(REGISTER_FILES))
def test_every_entry_has_the_fields_a_reviewer_needs(name: str) -> None:
    """Each entry has every field of the template."""
    incomplete = {}
    for entry_id, body in _entries(REGISTER_DIR / name).items():
        fields = _fields(body)
        needed = RETIRED_FIELDS if _is_retired(fields) else REQUIRED_FIELDS
        missing = [field for field in needed if field not in fields]
        if missing:
            incomplete[entry_id] = missing
    assert not incomplete, f"{name}: entries missing fields: {incomplete}"


@pytest.mark.parametrize("name", sorted(REGISTER_FILES))
def test_entry_fields_follow_the_template_order(name: str) -> None:
    """Fields use only the template labels, in the template order."""
    out_of_order = {}
    for entry_id, body in _entries(REGISTER_DIR / name).items():
        found = list(_fields(body))
        expected = [field for field in FIELD_ORDER if field in found]
        if found != expected:
            out_of_order[entry_id] = found
    assert not out_of_order, f"{name}: fields out of order or unknown: {out_of_order}"


@pytest.mark.parametrize("name", sorted(REGISTER_FILES))
def test_status_uses_an_allowed_label_and_gives_a_reason(name: str) -> None:
    """Each status reads ``<Label>. <qualifier>.``; retired ones link on."""
    all_ids = {entry_id for other in REGISTER_FILES for entry_id in _entries(REGISTER_DIR / other)}
    bad = {}
    for entry_id, body in _entries(REGISTER_DIR / name).items():
        status = _fields(body).get("Status", "")
        if not STATUS_FORM.match(status):
            bad[entry_id] = status[:60]
        elif status.startswith("Retired"):
            successor = RETIRED_FORM.match(status)
            if successor is None or successor.group(1) not in all_ids or successor.group(1) == entry_id:
                bad[entry_id] = status[:60]
    assert not bad, f"{name}: statuses that break the form: {bad}"


@pytest.mark.parametrize("name", sorted(REGISTER_FILES))
def test_verdict_uses_an_allowed_word(name: str) -> None:
    """Each verdict begins with Pass, Fail, Inconclusive or Not judged."""
    bad = {}
    for entry_id, body in _entries(REGISTER_DIR / name).items():
        fields = _fields(body)
        if _is_retired(fields):
            continue
        if not VERDICT_FORM.match(fields.get("Verdict", "")):
            bad[entry_id] = fields.get("Verdict", "")[:40]
    assert not bad, f"{name}: verdicts that break the form: {bad}"


@pytest.mark.parametrize("name", sorted(REGISTER_FILES))
def test_every_setting_states_its_relation_to_the_entry(name: str) -> None:
    """Each setting bullet states one of the four allowed relations."""
    bad = {}
    for entry_id, body in _entries(REGISTER_DIR / name).items():
        fields = _fields(body)
        if _is_retired(fields):
            continue
        settings = fields.get("Settings it supports", "")
        if NO_SETTING.match(settings):
            continue
        bullets = [line for line in settings.splitlines() if line.startswith("  - ")]
        missing = [line[:60] for line in bullets if not RELATION_FORM.search(line)]
        if not bullets or missing:
            bad[entry_id] = missing or ["no setting bullets"]
    assert not bad, f"{name}: settings without a relation: {bad}"


@pytest.mark.parametrize("name", sorted(REGISTER_FILES))
def test_runs_tables_are_well_formed(name: str) -> None:
    """A Runs table has the agreed columns and a valid verdict in every row."""
    bad = {}
    for entry_id, body in _entries(REGISTER_DIR / name).items():
        runs = _fields(body).get("Runs")
        if runs is None:
            continue
        rows = [line.strip() for line in runs.splitlines() if line.strip().startswith("|")]
        problems = []
        if len(rows) < 3 or rows[0] != RUNS_HEADER:
            problems.append("header")
        for row in rows[2:]:
            cells = [cell.strip() for cell in row.strip("|").split("|")]
            if len(cells) != 4 or not VERDICT_FORM.match(cells[2]):
                problems.append(row[:50])
        if problems:
            bad[entry_id] = problems
    assert not bad, f"{name}: bad Runs tables: {bad}"


@pytest.mark.parametrize("name", sorted(REGISTER_FILES))
def test_design_estimate_rows_have_stable_ids_and_a_risk_column(name: str) -> None:
    """Design-estimate rows have IDs such as ``STK-D01`` and a risk column."""
    text = (REGISTER_DIR / name).read_text(encoding="utf-8")
    header = next(line for line in text[text.index(DESIGN_HEADING) :].splitlines() if line.startswith("|"))
    assert "| ID |" in header and "Risk if wrong" in header, f"{name}: design table header is {header}"
    rows = _design_rows(REGISTER_DIR / name)
    assert rows, f"{name} has no design-estimate rows"
    ids = []
    for row in rows:
        match = DESIGN_ROW_ID.match(row)
        assert match, f"{name}: design row without an ID: {row[:60]}"
        ids.append(match.group(1))
    prefix = REGISTER_FILES[name] + "-D"
    assert all(row_id.startswith(prefix) for row_id in ids), f"{name}: wrong design row prefix"
    assert len(ids) == len(set(ids)), f"{name} repeats a design row ID"


@pytest.mark.parametrize("name", sorted(REGISTER_FILES))
def test_no_setting_is_both_supported_and_a_design_estimate(name: str) -> None:
    """A supported setting is not also listed as a design estimate."""
    design = set()
    for row in _design_rows(REGISTER_DIR / name):
        design |= _setting_names(" | ".join(row.strip("|").split("|")[1:4]))
    clashes = {}
    for entry_id, body in _entries(REGISTER_DIR / name).items():
        settings = _fields(body).get("Settings it supports", "")
        for line in settings.splitlines():
            relation = RELATION_FORM.search(line)
            if (
                not line.startswith("  - ")
                or relation is None
                or relation.group(1) not in SUPPORTING_RELATIONS
            ):
                continue
            for setting, setting_file in _setting_names(line):
                for other, other_file in design:
                    if setting == other and (
                        not setting_file or not other_file or setting_file == other_file
                    ):
                        clashes.setdefault(entry_id, set()).add(setting)
    assert not clashes, f"{name}: settings both supported and listed as design estimates: {clashes}"


def test_every_data_set_id_is_defined_once_in_the_index() -> None:
    """A ``DS-`` ID named in the register is defined in ``index.md``."""
    index_text = (REGISTER_DIR / "index.md").read_text(encoding="utf-8")
    defined = DATA_SET_ROW.findall(index_text)
    assert defined, "index.md defines no data sets"
    assert len(defined) == len(set(defined)), "index.md repeats a data set ID"
    used = set()
    for name in REGISTER_FILES:
        used.update(DATA_SET_ID.findall((REGISTER_DIR / name).read_text(encoding="utf-8")))
    undefined = sorted(used - set(defined))
    assert not undefined, f"Data set IDs the index does not define: {undefined}"


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
