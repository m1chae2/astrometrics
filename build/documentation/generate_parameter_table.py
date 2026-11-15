"""Purpose: Write a review table of every tuned parameter.

Description: Every tuned value in ``astrometricslib/`` and ``wayfindinglib/``
is a ``TunedParameter`` declaration, which holds its role, source, equipment,
kind, pending status and the ID of the validation register entry that holds
its evidence. This script reads both parameter registries
(``astrometricslib.parameters.collect_parameters``), finds each declaration
in the code with ``ast``, reads the status and title of its register entry
from ``documentation/library_design/validation/``, and adds the backlog of
tuning values that are not declared yet
(``parameter_inventory/tuning_not_registered.csv``). It writes one CSV with a
single row schema, one Markdown page per register file, and a page that lists
the automatic parameters that are hardcoded or pending conversion, grouped by
the equipment (or the site) they depend on. The output is for review and is
not part of the published documentation.

Usage::

    .venv/bin/python build/documentation/generate_parameter_table.py \
        --out <folder>
"""

from __future__ import annotations

import argparse
import ast
import csv
import re
from collections import Counter
from pathlib import Path

from astrometricslib.parameters import EquipmentKind, ParameterRegistry, collect_parameters

DEFAULT_REPO = Path(__file__).resolve().parents[2]
LIBRARIES = ("astrometricslib", "wayfindinglib")
REGISTER_FILES = {
    "stacking": "stacking.md",
    "astrometry": "astrometry.md",
    "photometry": "photometry.md",
    "spectroscopy": "spectroscopy.md",
    "asteroid_detection": "asteroid_detection.md",
    "wayfinding": "wayfinding.md",
}
REGISTER_PREFIXES = {
    "STK": "stacking",
    "AST": "astrometry",
    "PHO": "photometry",
    "SPE": "spectroscopy",
    "MOV": "asteroid_detection",
    "WAY": "wayfinding",
}
BACKLOG_RELATIVE_PATH = Path("build/documentation/parameter_inventory/tuning_not_registered.csv")
COLUMNS = (
    "row_type",
    "parameter",
    "module",
    "constant",
    "location",
    "default",
    "unit",
    "role",
    "source",
    "pending_conversion",
    "equipment",
    "kind",
    "register",
    "register_entry",
    "status",
    "entry_title",
    "description",
)
EQUIPMENT_KINDS = tuple(kind.value for kind in EquipmentKind)
CELL_LIMIT = 80

ENTRY_HEADING = re.compile(r"^### ([A-Z]{3}-\d{2,3})\s*$", re.MULTILINE)
DESIGN_ROW_ID = re.compile(r"^\| ([A-Z]{3}-D\d{2,3}) \|", re.MULTILINE)
FIELD_LINE = re.compile(r"^- \*\*([^*:]+):\*\*", re.MULTILINE)
STATUS_LABEL = re.compile(r"^(Measured|Partly measured|Design estimate|Retired)\b")
MEASURED = "Measured"
DESIGN_ESTIMATE = "Design estimate"


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


def _status_of(body: str) -> str:
    """Read the status label of one entry.

    Parameters
    ----------
    body : `str`
        The text of one entry, after its heading.

    Returns
    -------
    status : `str`
        The label on the ``**Status:**`` line, or an empty string.
    """
    for match in FIELD_LINE.finditer(body):
        if match.group(1) == "Status":
            label = STATUS_LABEL.match(body[match.end() :].strip())
            return label.group(1) if label else ""
    return ""


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


def _design_rows(text: str) -> dict[str, str]:
    """Read the design-estimate table of one register file.

    Parameters
    ----------
    text : `str`
        The text of one register file.

    Returns
    -------
    titles : `dict` [`str`, `str`]
        The setting text of each design-estimate row, keyed by row ID.
    """
    if "## Design estimates" not in text:
        return {}
    section = text[text.index("## Design estimates") :]
    table = [line for line in section.splitlines() if line.startswith("|")]
    if len(table) < 3:
        return {}
    columns = [cell.strip() for cell in table[0].strip("|").split("|")]
    titles = {}
    for line in table[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        named = dict(zip(columns, cells, strict=False))
        if DESIGN_ROW_ID.match(line):
            titles[named["ID"]] = named.get("Setting", "")
    return titles


def read_register_index(repo: Path) -> dict[str, dict[str, str]]:
    """Read every entry and design-estimate row of the validation register.

    Parameters
    ----------
    repo : `pathlib.Path`
        The repository root.

    Returns
    -------
    index : `dict` [`str`, `dict` [`str`, `str`]]
        Keyed by entry ID (``STK-05``) or design-estimate row ID
        (``STK-D07``). Each value has ``register``, ``status`` and ``title``.
        A design-estimate row has the status ``Design estimate``, because it
        is never measured.
    """
    index: dict[str, dict[str, str]] = {}
    folder = repo / "documentation" / "library_design" / "validation"
    for register, file_name in REGISTER_FILES.items():
        text = (folder / file_name).read_text(encoding="utf-8")
        for entry_id, body in _entries(text).items():
            index[entry_id] = {"register": register, "status": _status_of(body), "title": _entry_title(body)}
        for row_id, title in _design_rows(text).items():
            index[row_id] = {"register": register, "status": DESIGN_ESTIMATE, "title": title}
    return index


def find_declarations(repo: Path) -> dict[str, tuple[str, int, str]]:
    """Find where each ``TunedParameter`` is declared in the code.

    Parameters
    ----------
    repo : `pathlib.Path`
        The repository root.

    Returns
    -------
    found : `dict` [`str`, `tuple` [`str`, `int`, `str`]]
        For each parameter name: the repository-relative file, the line of
        the assignment and the constant name it is assigned to. Test, script
        and cache folders are skipped.
    """
    found: dict[str, tuple[str, int, str]] = {}
    for library in LIBRARIES:
        for path in sorted((repo / library).rglob("*.py")):
            relative = path.relative_to(repo).as_posix()
            if "/test" in relative or "/scripts/" in relative or "__pycache__" in relative:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in tree.body:
                if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
                    continue
                call = node.value
                if not (isinstance(call.func, ast.Name) and call.func.id == "TunedParameter"):
                    continue
                target = node.targets[0]
                for keyword in call.keywords:
                    if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                        name = str(keyword.value.value)
                        if isinstance(target, ast.Name):
                            found.setdefault(name, (relative, node.lineno, target.id))
    return found


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


def _register_of(entry_id: str) -> str:
    """Name the register file an entry ID belongs to.

    Parameters
    ----------
    entry_id : `str`
        An entry or design-estimate ID such as ``STK-05``.

    Returns
    -------
    register : `str`
        The register name, or ``unassigned`` for an empty or unknown ID.
    """
    return REGISTER_PREFIXES.get(entry_id[:3], "unassigned")


def declared_rows(repo: Path, registries: list[ParameterRegistry]) -> list[dict[str, str]]:
    """List one row for each declared parameter.

    Parameters
    ----------
    repo : `pathlib.Path`
        The repository root.
    registries : `list` [`ParameterRegistry`]
        The registries of the two libraries.

    Returns
    -------
    rows : `list` [`dict` [`str`, `str`]]
        One row per parameter, in registry order.
    """
    index = read_register_index(repo)
    declarations = find_declarations(repo)
    rows = []
    for registry in registries:
        for parameter in registry.all():
            file, line, constant = declarations.get(parameter.name, ("", 0, ""))
            entry = index.get(parameter.register_entry or "", {})
            rows.append(
                _row(
                    row_type="declared",
                    parameter=parameter.name,
                    module=file.removesuffix(".py").replace("/", "."),
                    constant=constant,
                    location=f"{file}:{line}" if file else "",
                    default=repr(parameter.default),
                    unit=parameter.unit,
                    role=parameter.role.value,
                    source=parameter.source.value,
                    pending_conversion="yes" if parameter.pending_conversion else "",
                    equipment=", ".join(sorted(kind.value for kind in parameter.equipment)),
                    kind=parameter.kind.value if parameter.kind else "",
                    register=entry.get("register", _register_of(parameter.register_entry or "")),
                    register_entry=parameter.register_entry or "",
                    status=entry.get("status", ""),
                    entry_title=entry.get("title", ""),
                    description=parameter.description,
                )
            )
    return rows


def backlog_rows(repo: Path) -> list[dict[str, str]]:
    """Read the backlog of tuning values that are not declared yet.

    Parameters
    ----------
    repo : `pathlib.Path`
        The repository root.

    Returns
    -------
    rows : `list` [`dict` [`str`, `str`]]
        One row per backlog line; empty when the file does not exist. Its
        role, source and equipment are the draft marks of the backlog.
    """
    path = repo / BACKLOG_RELATIVE_PATH
    if not path.is_file():
        return []
    rows = []
    with path.open(encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            file_name = record.get("file", "")
            related = record.get("related_entry", "")
            register = _register_of(related)
            if register == "unassigned":
                for name in REGISTER_FILES:
                    if f"pipelines/{name}/" in file_name:
                        register = name
                if file_name.startswith("wayfindinglib/"):
                    register = "wayfinding"
            rows.append(
                _row(
                    row_type="backlog",
                    parameter=record.get("name", ""),
                    module=file_name.removesuffix(".py").replace("/", "."),
                    constant=record.get("name", ""),
                    location=file_name,
                    role=re.sub(r"^automatic: (.*)$", r"automatic (\1)", record.get("draft_role", "")),
                    source=record.get("draft_source", ""),
                    equipment=", ".join(
                        part.strip() for part in record.get("draft_equipment", "").split(";") if part.strip()
                    ),
                    register=register,
                    register_entry=related,
                    description=record.get("note", ""),
                )
            )
    return rows


def build_rows(repo: Path) -> list[dict[str, str]]:
    """Collect the rows of both registries and the backlog.

    Parameters
    ----------
    repo : `pathlib.Path`
        The repository root.

    Returns
    -------
    rows : `list` [`dict` [`str`, `str`]]
        Declared rows, then backlog rows.
    """
    registries = [collect_parameters(library) for library in LIBRARIES]
    return declared_rows(repo, registries) + backlog_rows(repo)


def is_automatic(row: dict[str, str]) -> bool:
    """Say whether the software acts on a row's value without asking.

    Parameters
    ----------
    row : `dict` [`str`, `str`]
        A table row.

    Returns
    -------
    automatic : `bool`
        True for every role except ``recommendation``; an empty role is not
        automatic.
    """
    return bool(row["role"]) and not row["role"].startswith("recommendation")


def automatic_not_measured(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """List the declared automatic parameters whose entry is not Measured.

    Parameters
    ----------
    rows : `list` [`dict` [`str`, `str`]]
        All table rows.

    Returns
    -------
    unmeasured : `list` [`dict` [`str`, `str`]]
        One row per parameter name with ``parameter``, ``register_entry``,
        ``role`` and ``status``, sorted by name. A parameter with no entry has
        an empty entry and status.
    """
    unmeasured = {
        row["parameter"]: {key: row[key] for key in ("parameter", "register_entry", "role", "status")}
        for row in rows
        if row["row_type"] == "declared" and is_automatic(row) and row["status"] != MEASURED
    }
    return [unmeasured[name] for name in sorted(unmeasured)]


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
        "parameter",
        "row_type",
        "default",
        "unit",
        "role",
        "source",
        "pending_conversion",
        "equipment",
        "kind",
        "register_entry",
        "status",
        "location",
    )
    for register in (*REGISTER_FILES, "unassigned"):
        subset = [row for row in rows if row["register"] == register]
        text = _page(
            f"Parameter table: {register}",
            "Rows are the declared parameters whose register entry is in this file, then the "
            "backlog rows (`backlog`), whose role, source and equipment are drafts.",
            subset,
            page_columns,
        )
        (out / f"parameter_table_{register}.md").write_text(text, encoding="utf-8")
    sections = []
    equipment_columns = (
        "parameter",
        "row_type",
        "register_entry",
        "status",
        "role",
        "source",
        "kind",
        "pending_conversion",
    )
    for kind in EQUIPMENT_KINDS:
        subset = [
            row
            for row in rows
            if is_automatic(row)
            and (row["source"] in {"hardcoded", "fixed"} or row["pending_conversion"])
            and kind in [item.strip() for item in row["equipment"].split(",")]
        ]
        sections.append(
            f"## {kind} ({len(subset)} rows)\n\n" + "\n".join(_markdown_table(subset, equipment_columns))
        )
    header = (
        "# Parameter table by equipment\n\n"
        "This page is generated by `build/documentation/generate_parameter_table.py`. "
        "Do not edit it by hand. It lists the automatic parameters that are hardcoded, fixed in "
        "code (backlog) or pending conversion, grouped by the equipment, or the observing site, "
        "whose change would call for a new value.\n\n"
    )
    (out / "parameter_table_by_equipment.md").write_text(
        header + "\n\n".join(sections) + "\n", encoding="utf-8"
    )


def summarize(rows: list[dict[str, str]]) -> list[str]:
    """Describe the rows in a few lines.

    Parameters
    ----------
    rows : `list` [`dict` [`str`, `str`]]
        All rows.

    Returns
    -------
    lines : `list` [`str`]
        Rows per type, and declared rows with no register entry found.
    """
    per_type = Counter(row["row_type"] for row in rows)
    no_entry = sum(1 for row in rows if row["row_type"] == "declared" and not row["status"])
    lines = [f"rows: {len(rows)}"]
    lines += [f"  {name}: {count}" for name, count in per_type.items()]
    lines.append(f"declared rows with no register entry found: {no_entry}")
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
    rows = build_rows(args.repo)
    write_outputs(rows, args.out)
    print("\n".join(summarize(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
