"""Purpose: Write a review table of every tuned parameter.

Description: The validation register
(``documentation/library_design/validation/``) lists tuned values in three
places: the setting bullets of each entry, the
design-estimate table at the end of each file, and a backlog of tuned values
that no entry covers yet (``parameter_inventory/tuning_not_registered.csv``).
This script reads all three and writes one CSV with a single row schema, one
Markdown page per register file, and a page that groups the automatic,
fixed-source settings by the equipment they depend on. For each row that has an
``<!-- impl: ... -->`` link it also finds the line of the constant in the code
and its live value. The output is for review and is not part of the published
documentation.

Usage::

    .venv/bin/python build/documentation/generate_parameter_table.py \
        --out <folder>
"""

from __future__ import annotations

import argparse
import ast
import csv
import importlib
import re
from collections import Counter
from pathlib import Path

DEFAULT_REPO = Path(__file__).resolve().parents[2]
REGISTER_FILES = {
    "stacking": "stacking.md",
    "astrometry": "astrometry.md",
    "photometry": "photometry.md",
    "spectroscopy": "spectroscopy.md",
    "asteroid_detection": "asteroid_detection.md",
    "wayfinding": "wayfinding.md",
}
BACKLOG_RELATIVE_PATH = Path("build/documentation/parameter_inventory/tuning_not_registered.csv")
COLUMNS = (
    "source",
    "register",
    "entry_id",
    "entry_title",
    "status",
    "setting",
    "relation",
    "role",
    "source_of_value",
    "equipment",
    "marks_are_draft",
    "code_location",
    "live_value",
    "impl_link",
    "impl_resolved",
)
EQUIPMENT_KINDS = ("mount", "telescope", "camera", "filter or grating")
CELL_LIMIT = 80

ENTRY_HEADING = re.compile(r"^### ([A-Z]{3}-\d{2,3})\s*$", re.MULTILINE)
FIELD_LINE = re.compile(r"^- \*\*([^*:]+):\*\*", re.MULTILINE)
RELATION_FORM = re.compile(r"Relation: (derived from|confirmed by|sanity check only|challenged by)\b")
NO_SETTING = re.compile(r"^(None|No tuned value)\b")
IMPL_COMMENT = re.compile(r"<!-- impl: ([^ ]+?) -->")
STATUS_LABEL = re.compile(r"^(Measured|Partly measured|Design estimate|Retired)\b")
ROLE_WORDS = r"(?:automatic \((?:sets data aside|gives a verdict|limits hardware|shapes results)\)|advisory)"
SOURCE_WORDS = (
    r"(?:computed each run|from configuration|from equipment settings|from tuning results|fixed in code)"
    r"(?: with a fallback in code)?"
)
EQUIPMENT_ITEM = r"(?:mount|telescope|camera|filter or grating)"
EQUIPMENT_WORDS = rf"(?:none|{EQUIPMENT_ITEM}(?:, {EQUIPMENT_ITEM})*)"
MARK_PATTERNS = {
    "role": re.compile(rf"\bRole: ({ROLE_WORDS})\."),
    "source_of_value": re.compile(rf"\bSource: ({SOURCE_WORDS})\."),
    "equipment": re.compile(rf"\bEquipment: ({EQUIPMENT_WORDS})\."),
}


def _entries(text: str) -> dict[str, str]:
    """Split a register file into its entries.

    Parameters
    ----------
    text : `str`
        The text of one register file.

    Returns
    -------
    entries : `dict` [`str`, `str`]
        Entry text keyed by entry ID, in file order. An entry runs from its
        heading to the next heading of level 3 or higher.
    """
    matches = list(ENTRY_HEADING.finditer(text))
    entries = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end]
        prose = re.sub(r"```.*?```", lambda block: " " * len(block.group(0)), body, flags=re.DOTALL)
        next_section = re.search(r"^#{1,3} ", prose, re.MULTILINE)
        entries[match.group(1)] = body[: next_section.start()] if next_section else body
    return entries


def _fields(body: str) -> dict[str, str]:
    """Split one entry into its fields.

    Parameters
    ----------
    body : `str`
        The text of one entry.

    Returns
    -------
    fields : `dict` [`str`, `str`]
        The text of each field keyed by its label, in file order.
    """
    matches = list(FIELD_LINE.finditer(body))
    fields = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        text = body[match.end() : end].strip("\n")
        fields[match.group(1)] = text if text.startswith("  ") else text.strip()
    return fields


def _entry_title(body: str) -> str:
    """Return the bold title line under an entry heading.

    Parameters
    ----------
    body : `str`
        The text of one entry, after its heading.

    Returns
    -------
    title : `str`
        The text of the first ``**bold**`` line, or an empty string.
    """
    match = re.search(r"^\*\*(.+?)\*\*\s*$", body, re.MULTILINE)
    return match.group(1) if match else ""


def _setting_name(text: str) -> str:
    """Pick the constant or config key a bullet or table cell names.

    Parameters
    ----------
    text : `str`
        A setting bullet or the setting cell of a design-estimate row.

    Returns
    -------
    name : `str`
        The first backtick span that looks like a constant or key (its last
        word), else the first span, else the text itself.
    """
    spans = re.findall(r"`([^`\n]+)`", text)
    for span in spans:
        word = span.split()[-1].rstrip(",.;:")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", word) and ("_" in word or word.isupper()):
            return word
    return spans[0] if spans else text


def parse_setting_bullet(line: str) -> dict[str, str]:
    """Read one setting bullet of an entry.

    Parameters
    ----------
    line : `str`
        A line of the "Settings it supports" field that starts with ``  - ``.

    Returns
    -------
    parsed : `dict` [`str`, `str`]
        Keys ``setting``, ``relation``, ``role``, ``source_of_value``,
        ``equipment`` and ``impl_link``. A mark that is absent is an empty
        string.
    """
    relation = RELATION_FORM.search(line)
    impl = IMPL_COMMENT.search(line)
    parsed = {
        "setting": _setting_name(line),
        "relation": relation.group(1) if relation else "",
        "impl_link": impl.group(1) if impl else "",
    }
    for key, pattern in MARK_PATTERNS.items():
        found = pattern.search(line)
        parsed[key] = found.group(1) if found else ""
    return parsed


def resolve_dotted(dotted: str) -> tuple[object | None, str]:
    """Import the longest module prefix of a dotted path, then walk attributes.

    Parameters
    ----------
    dotted : `str`
        A path such as ``astrometricslib.pipelines.shared.angles.name``.

    Returns
    -------
    target : `object` or `None`
        The named object, or `None` when it does not resolve.
    module_name : `str`
        The name of the module that was imported, empty when none was.
    """
    parts = dotted.split(".")
    for split in range(len(parts), 0, -1):
        module_name = ".".join(parts[:split])
        try:
            target: object = importlib.import_module(module_name)
        except ImportError:
            continue
        for attribute in parts[split:]:
            target = getattr(target, attribute, None)
            if target is None:
                return None, module_name
        return target, module_name
    return None, ""


def _find_line(tree: ast.AST, names: list[str]) -> int | None:
    """Find the line where a module-level or class-level name is defined.

    Parameters
    ----------
    tree : `ast.AST`
        The parsed module.
    names : `list` [`str`]
        The path below the module: a name, or a class name then a name.

    Returns
    -------
    line : `int` or `None`
        The 1-based line of the assignment, function or class, or `None`.
    """
    body = getattr(tree, "body", [])
    for position, name in enumerate(names):
        found = None
        for node in body:
            if isinstance(node, ast.Assign):
                if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                    found = node
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id == name:
                    found = node
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                if node.name == name:
                    found = node
            if found is not None:
                break
        if found is None:
            return None
        if position == len(names) - 1:
            return found.lineno
        body = getattr(found, "body", [])
    return None


def locate_impl(dotted: str, repo: Path) -> tuple[bool, str, str]:
    """Resolve an impl link to a code location and a live value.

    Parameters
    ----------
    dotted : `str`
        The dotted path of an ``<!-- impl: -->`` link.
    repo : `pathlib.Path`
        The repository root, used to shorten file paths.

    Returns
    -------
    resolved : `bool`
        Whether the path names an existing object.
    location : `str`
        ``path:line`` of the definition, empty when it cannot be found.
    value : `str`
        The live value as text when it is an int, float, tuple or string.
    """
    target, module_name = resolve_dotted(dotted)
    if target is None:
        return False, "", ""
    value = ""
    if isinstance(target, int | float | tuple | str):
        value = repr(target)
    location = ""
    module = importlib.import_module(module_name)
    source_file = getattr(module, "__file__", None)
    if source_file:
        path = Path(source_file)
        names = dotted[len(module_name) :].strip(".").split(".")
        names = [name for name in names if name]
        try:
            line = _find_line(ast.parse(path.read_text(encoding="utf-8")), names) if names else 1
        except OSError, SyntaxError:
            line = None
        try:
            shown = path.resolve().relative_to(repo.resolve())
        except ValueError:
            shown = path
        location = f"{shown}:{line}" if line else f"{shown}"
    return True, location, value


def _row(**fields: str) -> dict[str, str]:
    """Build a row with every column present.

    Parameters
    ----------
    **fields : `str`
        Values for any of `COLUMNS`.

    Returns
    -------
    row : `dict` [`str`, `str`]
        The row, with an empty string for each column not given.
    """
    row = dict.fromkeys(COLUMNS, "")
    row.update(fields)
    return row


def _with_location(row: dict[str, str], impl_link: str, repo: Path) -> None:
    """Fill the code location and live value of a row from its impl link.

    Parameters
    ----------
    row : `dict` [`str`, `str`]
        The row to update in place.
    impl_link : `str`
        The dotted path, or an empty string.
    repo : `pathlib.Path`
        The repository root.
    """
    row["impl_link"] = impl_link
    if not impl_link:
        return
    resolved, location, value = locate_impl(impl_link, repo)
    row["impl_resolved"] = "yes" if resolved else "no"
    row["code_location"] = location
    row["live_value"] = value


def entry_rows(register: str, text: str, repo: Path) -> list[dict[str, str]]:
    """List the setting rows of the entries of one register file.

    Parameters
    ----------
    register : `str`
        The register name, such as ``stacking``.
    text : `str`
        The text of the register file.
    repo : `pathlib.Path`
        The repository root.

    Returns
    -------
    rows : `list` [`dict` [`str`, `str`]]
        One row per setting bullet of every entry that is not retired.
    """
    rows = []
    for entry_id, body in _entries(text).items():
        fields = _fields(body)
        status_match = STATUS_LABEL.match(fields.get("Status", ""))
        status = status_match.group(1) if status_match else ""
        settings = fields.get("Settings it supports", "")
        if status == "Retired" or NO_SETTING.match(settings):
            continue
        for line in settings.splitlines():
            if not line.startswith("  - "):
                continue
            parsed = parse_setting_bullet(line)
            row = _row(
                source="register entry",
                register=register,
                entry_id=entry_id,
                entry_title=_entry_title(body),
                status=status,
                setting=parsed["setting"],
                relation=parsed["relation"],
                role=parsed["role"],
                source_of_value=parsed["source_of_value"],
                equipment=parsed["equipment"],
            )
            _with_location(row, parsed["impl_link"], repo)
            rows.append(row)
    return rows


def design_rows(register: str, text: str, repo: Path) -> list[dict[str, str]]:
    """List the rows of the design-estimate table of one register file.

    Parameters
    ----------
    register : `str`
        The register name.
    text : `str`
        The text of the register file.
    repo : `pathlib.Path`
        The repository root.

    Returns
    -------
    rows : `list` [`dict` [`str`, `str`]]
        One row per table row. The value as stated and where it lives go in
        the live-value column prefix and the setting text.
    """
    if "## Design estimates" not in text:
        return []
    section = text[text.index("## Design estimates") :]
    table = [line for line in section.splitlines() if line.startswith("|")]
    if len(table) < 3:
        return []
    columns = [cell.strip() for cell in table[0].strip("|").split("|")]
    rows = []
    for line in table[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        cells += [""] * (len(columns) - len(cells))
        named = dict(zip(columns, cells, strict=False))
        impl = IMPL_COMMENT.search(line)
        row = _row(
            source="design estimate",
            register=register,
            entry_id=named.get("ID", ""),
            entry_title=named.get("Setting", ""),
            status="Design estimate",
            setting=f"{named.get('Setting', '')} = {named.get('Value', '')} ({named.get('Where', '')})",
            role=named.get("Role", ""),
            source_of_value=named.get("Source", ""),
            equipment=named.get("Equipment", ""),
        )
        _with_location(row, impl.group(1) if impl else "", repo)
        rows.append(row)
    return rows


def _backlog_register(record: dict[str, str]) -> str:
    """Pick the register file a backlog line belongs with.

    Parameters
    ----------
    record : `dict` [`str`, `str`]
        One line of the backlog CSV.

    Returns
    -------
    register : `str`
        The register named by the related entry's prefix, else a guess from the
        file path, else ``unassigned``.
    """
    prefixes = {"STK": "stacking", "AST": "astrometry", "PHO": "photometry", "SPE": "spectroscopy"}
    prefixes.update({"MOV": "asteroid_detection", "WAY": "wayfinding"})
    related = record.get("related_entry", "")
    if related[:3] in prefixes:
        return prefixes[related[:3]]
    path = record.get("file", "")
    for register in ("stacking", "astrometry", "photometry", "spectroscopy", "asteroid_detection"):
        if f"pipelines/{register}/" in path:
            return register
    return "wayfinding" if path.startswith("wayfindinglib/") else "unassigned"


def backlog_rows(repo: Path) -> list[dict[str, str]] | None:
    """Read the backlog of tuned values that no entry covers yet.

    Parameters
    ----------
    repo : `pathlib.Path`
        The repository root.

    Returns
    -------
    rows : `list` [`dict` [`str`, `str`]] or `None`
        One row per backlog line, or `None` when the file does not exist.
    """
    path = repo / BACKLOG_RELATIVE_PATH
    if not path.is_file():
        return None
    rows = []
    with path.open(encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            file_name = record.get("file", "")
            row = _row(
                source="backlog",
                register=_backlog_register(record),
                entry_id=record.get("related_entry", ""),
                entry_title=record.get("note", ""),
                status="Not registered",
                setting=record.get("name", ""),
                role=re.sub(r"^automatic: (.*)$", r"automatic (\1)", record.get("draft_role", "")),
                source_of_value=record.get("draft_source", ""),
                equipment=record.get("draft_equipment", "").replace(";", ",").replace(",  ", ", "),
                marks_are_draft="yes",
                code_location=file_name,
            )
            rows.append(row)
    return rows


def _cell(value: str) -> str:
    """Trim a value for a Markdown table cell.

    Parameters
    ----------
    value : `str`
        The text.

    Returns
    -------
    cell : `str`
        The text with pipes escaped, newlines removed and length limited.
    """
    text = value.replace("|", "\\|").replace("\n", " ").strip()
    return text if len(text) <= CELL_LIMIT else text[: CELL_LIMIT - 3] + "..."


def _markdown_table(rows: list[dict[str, str]], columns: tuple[str, ...]) -> list[str]:
    """Format rows as a Markdown table.

    Parameters
    ----------
    rows : `list` [`dict` [`str`, `str`]]
        The rows.
    columns : `tuple` [`str`, ...]
        The columns to show, in order.

    Returns
    -------
    lines : `list` [`str`]
        Header, separator and one line per row.
    """
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(_cell(row[column]) for column in columns) + " |")
    return lines


def _page(title: str, intro: str, rows: list[dict[str, str]], columns: tuple[str, ...]) -> str:
    """Build one Markdown page.

    Parameters
    ----------
    title : `str`
        The page heading.
    intro : `str`
        What the rows are.
    rows : `list` [`dict` [`str`, `str`]]
        The rows.
    columns : `tuple` [`str`, ...]
        The columns to show.

    Returns
    -------
    text : `str`
        The page text.
    """
    header = (
        f"# {title}\n\n"
        "This page is generated by `build/documentation/generate_parameter_table.py`. "
        f"Do not edit it by hand. {intro}\n\n"
    )
    return header + "\n".join(_markdown_table(rows, columns)) + "\n"


def write_outputs(rows: list[dict[str, str]], out: Path) -> None:
    """Write the CSV and the Markdown pages.

    Parameters
    ----------
    rows : `list` [`dict` [`str`, `str`]]
        All rows.
    out : `pathlib.Path`
        The output folder, created when missing.
    """
    out.mkdir(parents=True, exist_ok=True)
    with (out / "parameter_table.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    page_columns = (
        "entry_id",
        "setting",
        "status",
        "relation",
        "role",
        "source_of_value",
        "equipment",
        "code_location",
        "live_value",
    )
    for register in (*REGISTER_FILES, "unassigned"):
        subset = [row for row in rows if row["register"] == register]
        text = _page(
            f"Parameter table: {register}",
            "Rows come from the entry settings and the design-estimate table. An empty role, "
            "source or equipment means the setting is not marked yet.",
            subset,
            page_columns,
        )
        (out / f"parameter_table_{register}.md").write_text(text, encoding="utf-8")
    sections = []
    equipment_columns = ("register", "entry_id", "setting", "status", "role", "equipment", "marks_are_draft")
    for kind in EQUIPMENT_KINDS:
        subset = [
            row
            for row in rows
            if row["role"].startswith("automatic")
            and row["source_of_value"].startswith(("fixed in code", "fixed", "fallback"))
            and kind in [item.strip() for item in row["equipment"].split(",")]
        ]
        sections.append(
            f"## {kind} ({len(subset)} rows)\n\n" + "\n".join(_markdown_table(subset, equipment_columns))
        )
    header = (
        "# Parameter table by equipment\n\n"
        "This page is generated by `build/documentation/generate_parameter_table.py`. "
        "Do not edit it by hand. It lists automatic settings with a fixed source, grouped by the "
        "equipment whose change would call for a new value. A row with `marks_are_draft` set to "
        "`yes` has marks that are drafts.\n\n"
    )
    (out / "parameter_table_by_equipment.md").write_text(
        header + "\n\n".join(sections) + "\n", encoding="utf-8"
    )


def build_rows(repo: Path) -> tuple[list[dict[str, str]], bool]:
    """Collect the rows from the register and the backlog.

    Parameters
    ----------
    repo : `pathlib.Path`
        The repository root.

    Returns
    -------
    rows : `list` [`dict` [`str`, `str`]]
        All rows.
    backlog_found : `bool`
        Whether the backlog file existed.
    """
    rows = []
    register_dir = repo / "documentation" / "library_design" / "validation"
    for register, file_name in REGISTER_FILES.items():
        text = (register_dir / file_name).read_text(encoding="utf-8")
        rows.extend(entry_rows(register, text, repo))
        rows.extend(design_rows(register, text, repo))
    backlog = backlog_rows(repo)
    if backlog is not None:
        rows.extend(backlog)
    return rows, backlog is not None


def summarize(rows: list[dict[str, str]]) -> list[str]:
    """Describe the rows in a few lines.

    Parameters
    ----------
    rows : `list` [`dict` [`str`, `str`]]
        All rows.

    Returns
    -------
    lines : `list` [`str`]
        Rows per source, marked and unmarked counts, and unresolved links.
    """
    per_source = Counter(row["source"] for row in rows)
    marked = sum(1 for row in rows if row["role"] and row["source_of_value"] and row["equipment"])
    unresolved = [row["impl_link"] for row in rows if row["impl_resolved"] == "no"]
    lines = [f"rows: {len(rows)}"]
    lines += [f"  {name}: {count}" for name, count in per_source.items()]
    lines.append(f"marked: {marked}, unmarked: {len(rows) - marked}")
    lines.append(f"impl links that did not resolve: {len(unresolved)}")
    lines += [f"  {link}" for link in unresolved[:10]]
    return lines


def main(argv: list[str] | None = None) -> int:
    """Run the generator.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; the process arguments when omitted.

    Returns
    -------
    status : `int`
        Zero on success.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path, help="Folder for the generated files.")
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO, help="Repository root.")
    args = parser.parse_args(argv)
    rows, backlog_found = build_rows(args.repo)
    if not backlog_found:
        print(f"note: {BACKLOG_RELATIVE_PATH} does not exist yet; the backlog source was skipped.")
    write_outputs(rows, args.out)
    print("\n".join(summarize(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
