"""Purpose: List every MCP tool the project serves and draft a class for each.

Description: The project runs four MCP servers (astrometricslib-core,
wayfindinglib-core, astrometrics-backend and astrometrics-ui). An MCP
(Model Context Protocol) server gives an AI client a list of tools it may
call. This script loads each server's tool list, guesses a safety class for
every tool from its name and description, and writes two files for a person
to review:

* ``tool_manifest_draft.json``: one entry per tool. Edit ``tool_class`` and
  set ``reviewed`` to ``true`` when a decision is final.
* ``tool_manifest_review.md``: the same list as tables, with the guesses
  that need a closer look marked ``REVIEW``.

Running the script again keeps every entry marked ``reviewed``. The report
also lists permission rules in the Claude settings files that name a tool
no server serves.

Run it from the project root::

    .venv/bin/python -m backend.mcp.tool_inventory

The script only reads. It imports the tool registries the same way the MCP
servers do, so it does not connect to the telescope or call any tool.
"""

import argparse
import importlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.mcp.tool_dispositions import (
    CATEGORIES,
    DECISIONS,
    DISPOSITIONS,
    DROPPED_CATEGORIES,
    INTERIM_BLOCKS,
    NOT_OFFERED_CLASSES,
    PROPOSED_TOOLS,
    READ_ONLY_NOTE,
    ProposedTool,
    categorize_tool,
    find_problems,
    replaced_by_lookup,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "scratch" / "mcp_tool_inventory"
"""Where the draft files go unless ``--output-dir`` says otherwise."""

MANIFEST_FILE_NAME = "tool_manifest_draft.json"
REVIEW_FILE_NAME = "tool_manifest_review.md"

PYTHON_SERVER_MODULES = {
    "astrometricslib-core": "astrometricslib.mcp.tool_registry",
    "wayfindinglib-core": "wayfindinglib.mcp.tool_registry",
    "astrometrics-backend": "backend.mcp.tool_registry",
}
"""MCP server name -> module that builds its tool registry."""

UI_SERVER_NAME = "astrometrics-ui"
UI_SERVER_SOURCE = PROJECT_ROOT / "ui" / "mcp" / "src" / "index.ts"
"""The UI server is written in TypeScript, so its tool list is read as text."""

RUNTIME_MANIFEST_PATHS = {
    "astrometricslib-core": PROJECT_ROOT / "astrometricslib" / "mcp" / "tool_manifest.json",
    "wayfindinglib-core": PROJECT_ROOT / "wayfindinglib" / "mcp" / "tool_manifest.json",
    "astrometrics-backend": PROJECT_ROOT / "backend" / "mcp" / "tool_manifest.json",
    "astrometrics-ui": PROJECT_ROOT / "ui" / "mcp" / "tool_manifest.json",
}
"""Where each MCP server reads the manifest that limits its tools."""

SETTINGS_FILES = (
    PROJECT_ROOT / ".claude" / "settings.json",
    PROJECT_ROOT / ".claude" / "settings.local.json",
)
"""Claude settings files whose permission rules are checked for old names."""

TOOL_CLASSES = {
    "observe": "Reads state: lists, lookups, status, saved results.",
    "compute": "Calculates or plots from existing data. Writes nothing the app keeps.",
    "ingest": "Brings data from the telescope into the library. Adds files and records, never deletes.",
    "process": (
        "Runs the app's own processing stage on library frames, as its buttons do. Writes stacks and "
        "records, sets bad frames aside, never deletes."
    ),
    "change-data": "Writes the catalog, database, settings, frames or stacks.",
    "actuate": "Moves or changes hardware, or starts a session that does.",
    "safe-stop": "Stops motion or puts the equipment in a safe state. Always allowed.",
    "ui-control": "Changes what the person sees in the app, not the saved data.",
    "develop": "Runs the project's own code, such as tests and builds.",
    "unrestricted": "Runs code or any backend call. Reaches whatever the backend reaches.",
    "unclassified": "No rule matched. A person must choose a class.",
}
"""The proposed classes and what each one means."""

CONFIDENCE_LEVELS = ("high", "medium", "low")


@dataclass(frozen=True)
class ClassificationRule:
    """One name pattern and the class it suggests.

    Attributes
    ----------
    pattern : `re.Pattern` [`str`]
        Matched against the whole tool name.
    tool_class : `str`
        The class suggested when the pattern matches.
    confidence : `str`
        One of ``CONFIDENCE_LEVELS``. Low means a person should check.
    reason : `str`
        Plain-language reason shown in the review report.
    """

    pattern: re.Pattern[str]
    tool_class: str
    confidence: str
    reason: str


def _rule(pattern: str, tool_class: str, confidence: str, reason: str) -> ClassificationRule:
    """Build a rule from a pattern string.

    Parameters
    ----------
    pattern : `str`
        Regular expression matched against the whole tool name.
    tool_class : `str`
        Class suggested on a match.
    confidence : `str`
        ``"high"``, ``"medium"`` or ``"low"``.
    reason : `str`
        Why the pattern suggests this class.

    Returns
    -------
    rule : `ClassificationRule`
        The finished rule.
    """
    return ClassificationRule(re.compile(pattern), tool_class, confidence, reason)


CLASSIFICATION_RULES = (
    # Exact names first: these tools are known cases.
    _rule(
        r"^observatory_(mount_abort_motion|safety_execute_safe_state)$",
        "safe-stop",
        "high",
        "Stops motion or secures the equipment.",
    ),
    _rule(
        r"^(electron_run_python|backend_call_rpc)$",
        "unrestricted",
        "high",
        "Runs Python in the backend, or sends any RPC method.",
    ),
    _rule(
        r"^terminal_inspect_api$",
        "unrestricted",
        "medium",
        "Builds Python code from its argument and runs it in the backend.",
    ),
    _rule(r"^terminal_get_workspace$", "observe", "medium", "Lists variables held in the backend."),
    _rule(r"^backend_health_check$", "observe", "high", "Reports whether the backend answers."),
    _rule(r"^docs_get$", "observe", "medium", "Returns documentation text."),
    _rule(
        r"^ui_(pause|resume)_pipelines$",
        "ui-control",
        "low",
        "Freezes or thaws background jobs (Siril, plate solver). Affects running work.",
    ),
    _rule(
        r"^ui_(show_notification|navigate_mode|editor_sync)$",
        "ui-control",
        "medium",
        "Changes what the person sees in the app.",
    ),
    _rule(r"^ui_inspect_variable$", "observe", "medium", "Reads one variable from the app."),
    _rule(
        r"^ui_(run_tests|diagnose_code|audit_accessibility|build_check)$",
        "develop",
        "high",
        "Runs the front end's own tests, checks or build.",
    ),
    _rule(r"^typegen_contract_validator$", "develop", "high", "Developer check of model contracts."),
    # Writes to saved data.
    _rule(r"_save(_|$)", "change-data", "high", "Name says it saves."),
    _rule(
        r"^target_(add|create|reindex_frames)$",
        "change-data",
        "high",
        "Adds or changes targets in the catalog.",
    ),
    _rule(
        r"^star_(create|update|save_all|find_or_create_by_position)$",
        "change-data",
        "high",
        "Adds or changes stars in the catalog.",
    ),
    _rule(r"^calibration_add$", "change-data", "high", "Adds calibration data."),
    _rule(r"^calibration_refresh$", "change-data", "medium", "Probably rebuilds stored calibration."),
    _rule(
        r"^processing_(discard_previous_stack|restore_excluded_frames|swap_with_previous_stack|process_target)$",
        "change-data",
        "high",
        "Changes frames or stacks on disk or in the database.",
    ),
    _rule(
        r"^processing_run_spectroscopy_by_session$",
        "change-data",
        "high",
        "Pipeline run that writes results to the live catalog or stacks.",
    ),
    _rule(r"^processing_acquire_.+_slot$", "change-data", "low", "Takes a work slot (a lock). Check."),
    _rule(r"^star_tune_spectroscopy_calibration$", "change-data", "low", "May store a new calibration."),
    _rule(r"^planning_edit_queue$", "change-data", "high", "Changes the saved queue."),
    _rule(
        r"^planning_(build_deep_star_catalog|create_mosaic)$",
        "change-data",
        "high",
        "Builds or stores catalog or plan data.",
    ),
    _rule(
        r"^planning_create_plan$",
        "change-data",
        "high",
        "Records packages and sessions. Only kind=sequence writes nothing.",
    ),
    _rule(
        r"^observatory_(safety_apply_promotion_decision|equipment_set_active_.+)$",
        "change-data",
        "high",
        "Writes settings to local storage.",
    ),
    _rule(r"^execution_record_divergence$", "change-data", "medium", "Records a divergence in session data."),
    _rule(
        r"^observatory_guiding_(drain_external_pulses|refit_spectrum)$",
        "change-data",
        "low",
        "Clears or refits stored guiding state. Check.",
    ),
    _rule(
        r"^execution_(abort_session|reconcile_session|create_recorder)$",
        "change-data",
        "low",
        "Closes or records a session in the database. Stopping the mount is a different tool. Check.",
    ),
    # Hardware.
    _rule(
        r"^observatory_(mount_(slew|sync|park|unpark|set_tracking|manual_move|set_slew_rate|run_.+)"
        r"|imaging_(capture_image|set_filter|focus_move)|guiding_(pulse|expose|run_.+)"
        r"|safety_(open|close)_enclosure|equipment_(connect|disconnect|set_device_property))$",
        "actuate",
        "high",
        "Moves or commands equipment.",
    ),
    _rule(
        r"^observatory_safety_enter_(controller|monitoring)_mode$",
        "actuate",
        "low",
        "Switches who controls the hardware. Check.",
    ),
    _rule(
        r"^execution_(advance_session|execute_meridian_flip|recover_.+)$",
        "actuate",
        "high",
        "Runs or recovers an observing session.",
    ),
    # Reads.
    _rule(
        r"^observatory_remote_(list|check_connection)$",
        "observe",
        "medium",
        "Reads, but contacts the remote computer over the network.",
    ),
    _rule(
        r"^[a-z]+_(get|list|summarize|find|active|existing|is|scan|parse|stats)(_|$)",
        "observe",
        "high",
        "Name says it reads.",
    ),
    _rule(
        r"^observatory_(equipment_(cooling_ramp_rate|summarize_device)|[a-z]+_status)$",
        "observe",
        "high",
        "Returns a stored, derived or measured value.",
    ),
    _rule(
        r"^observatory_history_query$",
        "compute",
        "high",
        "Reads recorded nights and calculates the analyses.",
    ),
    _rule(
        r"^observatory_remote_sync_(frames|logs)$",
        "ingest",
        "high",
        "Brings new data from the telescope computer into the library. Never deletes.",
    ),
    _rule(
        r"^diagnostics_frame_quality$",
        "compute",
        "high",
        "Measures frames and returns statistics. Saves nothing.",
    ),
    _rule(
        r"^(observatory_history_frame_guiding|planning_get_visibility)$",
        "compute",
        "high",
        "Calculates from stored records and the sky. Saves nothing.",
    ),
    _rule(
        r"^planning_deep_catalog_status$",
        "compute",
        "high",
        "Reads the catalog file; the estimate queries the Gaia archive. Saves nothing.",
    ),
    _rule(
        r"^observatory_remote_frame_status$",
        "observe",
        "high",
        "Lists the frame files on the telescope computer, the drive and the library. Changes nothing.",
    ),
    _rule(
        r"^processing_(stack|remake_preview)$",
        "process",
        "high",
        "Stacks a target's chosen frames with the app's own stage, like the Stack button.",
    ),
    _rule(r"^app_status$", "observe", "high", "Reads the app's health, connections, resources and jobs."),
    _rule(
        r"^app_controls$",
        "ui-control",
        "high",
        "Switches the view or shows a notification. Pause and resume are not offered.",
    ),
    _rule(r"^star_query$", "observe", "high", "Reads library stars. Row counts are capped."),
    _rule(
        r"^(target_query|target_imaged_field_centers|planning_find_sources|planning_lookup_coordinates)$",
        "observe",
        "high",
        "Reads targets or stars in short, capped records.",
    ),
    _rule(
        r"^diagnostics_spectral_frame_check$",
        "compute",
        "high",
        "Measures raw spectrum frames and returns statistics. Saves nothing.",
    ),
    _rule(
        r"^processing_stack_summary$",
        "observe",
        "high",
        "Reads the summary saved with a target's stack. Measures and saves nothing.",
    ),
    _rule(
        r"^visualization_render_fits$",
        "observe",
        "high",
        "Draws a picture of a frame. Saves nothing.",
    ),
    _rule(
        r"^jobs_query$",
        "observe",
        "high",
        "Reads the job history and lineage. The logs database is opened read-only.",
    ),
    _rule(
        r"^calibration_query$",
        "observe",
        "high",
        "Reads calibration counts. refresh reloads the index into memory and writes nothing.",
    ),
    _rule(
        r"^diagnostics_stack_quality$",
        "compute",
        "high",
        "Measures a stack and compares it with another. Saves nothing.",
    ),
    _rule(
        r"^observatory_safety_assess$",
        "observe",
        "medium",
        "Reports an assessment. Check it changes nothing.",
    ),
    # Calculations.
    _rule(
        r"^(?:observatory_[a-z]+|[a-z]+)_(analyze|compute|calculate|estimate|plan|plot|convert"
        r"|detect|compare|measure|flag|resolve|assess)(_|$)",
        "compute",
        "high",
        "Name says it calculates or plots.",
    ),
)
"""Rules tried in order. The first match wins."""

WRITE_HINT_PATTERN = re.compile(
    r"\b(writes?|saves?|persists?|overwrit\w+|delet\w+|slews?|moves?|parks?|rsync|downloads?|uploads?"
    r"|queue[sd]?)\b",
    re.IGNORECASE,
)
"""Words in a description that suggest a side effect."""

HINT_CHECKED_CLASSES = frozenset({"observe", "compute", "ui-control"})
"""Classes whose descriptions are checked for side-effect words."""


@dataclass
class ToolRecord:
    """One tool and the class drafted for it.

    Attributes
    ----------
    server : `str`
        MCP server that serves the tool.
    name : `str`
        The tool's name.
    description : `str`
        The description the server gives clients.
    parameters : `list` [`str`]
        Names of the tool's arguments.
    tool_class : `str`
        Drafted class, one of ``TOOL_CLASSES``.
    confidence : `str`
        ``"high"``, ``"medium"`` or ``"low"``.
    reason : `str`
        Why the draft chose this class.
    category : `str`
        One of ``CATEGORIES`` in ``tool_dispositions``.
    disposition : `str`
        One of ``DISPOSITIONS`` in ``tool_dispositions``.
    merge_into : `str`
        The proposed tool that replaces this one, or an empty string.
    interim_block : `str`
        Why a read-only tool stays hidden for now, or an empty string.
    note : `str`
        Why the disposition or a corrected class was chosen.
    reviewed : `bool`
        `True` once a person has confirmed the entry.
    stale : `bool`
        `True` when a reviewed entry no longer matches any served tool.
    """

    server: str
    name: str
    description: str
    parameters: list[str] = field(default_factory=list)
    tool_class: str = "unclassified"
    confidence: str = "low"
    reason: str = "No rule matched."
    category: str = "uncategorized"
    disposition: str = "undecided"
    merge_into: str = ""
    interim_block: str = ""
    note: str = ""
    reviewed: bool = False
    stale: bool = False

    def to_manifest_entry(self) -> dict[str, Any]:
        """Return the entry as written to the manifest file.

        Returns
        -------
        entry : `dict` [`str`, `Any`]
            Everything except the server and name, which are the keys the
            entry is filed under.
        """
        entry = asdict(self)
        del entry["server"], entry["name"]
        return entry


def classify_tool(name: str, description: str) -> tuple[str, str, str]:
    """Guess a class for a tool from its name and description.

    Parameters
    ----------
    name : `str`
        The tool's name.
    description : `str`
        The description the server gives clients.

    Returns
    -------
    tool_class : `str`
        One of ``TOOL_CLASSES``.
    confidence : `str`
        ``"high"``, ``"medium"`` or ``"low"``. A description that mentions
        a side effect lowers confidence for read-only classes.
    reason : `str`
        Plain-language reason for the guess.
    """
    for rule in CLASSIFICATION_RULES:
        if rule.pattern.search(name):
            tool_class, confidence, reason = rule.tool_class, rule.confidence, rule.reason
            break
    else:
        return "unclassified", "low", "No rule matched."
    hint = WRITE_HINT_PATTERN.search(description)
    if hint and tool_class in HINT_CHECKED_CLASSES:
        confidence = "low"
        reason = f"{reason} Description mentions {hint.group(0)!r}."
    return tool_class, confidence, reason


def collect_python_server_tools(server: str, module_name: str) -> list[ToolRecord]:
    """List the tools a Python MCP server registers.

    Parameters
    ----------
    server : `str`
        MCP server name, used to label the records.
    module_name : `str`
        Module that builds the server's tool registry. Importing it
        registers the tools, as when the server starts.

    Returns
    -------
    records : `list` [`ToolRecord`]
        One record per tool, sorted by name, with a drafted class.
    """
    registry = importlib.import_module(module_name).registry
    records = []
    for name, entry in sorted(registry.tools.items()):
        tool_definition = entry["tool_def"]
        description = (tool_definition.description or "").strip()
        properties = (tool_definition.inputSchema or {}).get("properties", {})
        records.append(ToolRecord(server, name, description, sorted(properties)))
    return records


def collect_ui_server_tools(source_path: Path = UI_SERVER_SOURCE) -> list[ToolRecord]:
    """List the tools the TypeScript UI server declares.

    Parameters
    ----------
    source_path : `pathlib.Path`, optional
        The server's ``index.ts``. The tool list is read as text because
        the server is not Python.

    Returns
    -------
    records : `list` [`ToolRecord`]
        One record per ``name`` and ``description`` pair found.
    """
    source_text = source_path.read_text(encoding="utf-8")
    pairs = re.findall(r'name:\s*"([a-z_]+)",\s*description:\s*"([^"]*)"', source_text)
    return [ToolRecord(UI_SERVER_NAME, name, description.strip()) for name, description in sorted(pairs)]


def collect_inventory() -> dict[str, list[ToolRecord]]:
    """Collect and classify the tools of all four servers.

    Returns
    -------
    inventory : `dict` [`str`, `list` [`ToolRecord`]]
        Server name -> its tools, each with a drafted class.
    """
    inventory = {
        server: collect_python_server_tools(server, module_name)
        for server, module_name in PYTHON_SERVER_MODULES.items()
    }
    inventory[UI_SERVER_NAME] = collect_ui_server_tools()
    replaced_by = replaced_by_lookup()
    for records in inventory.values():
        for record in records:
            record.tool_class, record.confidence, record.reason = classify_tool(
                record.name, record.description
            )
            apply_decisions(record, replaced_by)
    return inventory


def apply_decisions(record: ToolRecord, replaced_by: dict[str, ProposedTool]) -> None:
    """Fill in a record's category, disposition and corrected class.

    Parameters
    ----------
    record : `ToolRecord`
        The record to update in place. It already has a drafted class.
    replaced_by : `dict` [`str`, `ProposedTool`]
        Tool name -> the proposed tool that replaces it.
    """
    record.interim_block = INTERIM_BLOCKS.get(record.name, "")
    decision = DECISIONS.get(record.name)
    if decision:
        record.note = decision.note
        if decision.tool_class:
            record.tool_class = decision.tool_class
            record.confidence = "medium"
            record.reason = f"Corrected after reading the code. {decision.note}"
    proposed = replaced_by.get(record.name)
    if proposed:
        record.category = proposed.category
        record.disposition = "merged" if proposed.built else "merge"
        record.merge_into = proposed.name
    else:
        record.category = categorize_tool(record.name, record.tool_class)
        record.disposition = (decision.disposition if decision else None) or "undecided"
        if record.disposition == "undecided" and record.category in DROPPED_CATEGORIES:
            record.disposition = "drop"
            record.note = DROPPED_CATEGORIES[record.category]
        elif record.disposition == "undecided" and record.tool_class in NOT_OFFERED_CLASSES:
            record.disposition = "withhold"
            record.note = record.note or READ_ONLY_NOTE


def merge_reviewed_entries(
    inventory: dict[str, list[ToolRecord]], existing_manifest: dict[str, Any]
) -> list[tuple[str, str]]:
    """Carry earlier human decisions over to a fresh inventory.

    A tool that the existing manifest marks ``reviewed`` keeps its class,
    confidence and reason. A reviewed tool that no server serves any more
    is added back with ``stale`` set, so the decision is not lost.

    Parameters
    ----------
    inventory : `dict` [`str`, `list` [`ToolRecord`]]
        The fresh inventory. Changed in place.
    existing_manifest : `dict` [`str`, `Any`]
        The manifest read from an earlier run, or an empty dict.

    Returns
    -------
    stale_entries : `list` [`tuple` [`str`, `str`]]
        ``(server, tool name)`` for each reviewed entry with no live tool.
    """
    stale_entries = []
    earlier_tools = existing_manifest.get("tools", {})
    for server, records in inventory.items():
        earlier_for_server = earlier_tools.get(server, {})
        live_names = {record.name for record in records}
        for record in records:
            earlier = earlier_for_server.get(record.name)
            if earlier and earlier.get("reviewed"):
                record.tool_class = earlier["tool_class"]
                record.confidence = earlier.get("confidence", record.confidence)
                record.reason = earlier.get("reason", record.reason)
                record.category = earlier.get("category", record.category)
                record.disposition = earlier.get("disposition", record.disposition)
                record.merge_into = earlier.get("merge_into", record.merge_into)
                record.note = earlier.get("note", record.note)
                record.reviewed = True
        for name, earlier in earlier_for_server.items():
            if name not in live_names and earlier.get("reviewed"):
                stale_entries.append((server, name))
                records.append(
                    ToolRecord(
                        server,
                        name,
                        earlier.get("description", ""),
                        earlier.get("parameters", []),
                        earlier["tool_class"],
                        earlier.get("confidence", "low"),
                        earlier.get("reason", ""),
                        reviewed=True,
                        stale=True,
                    )
                )
    return stale_entries


def build_manifest(inventory: dict[str, list[ToolRecord]]) -> dict[str, Any]:
    """Turn an inventory into the manifest written to JSON.

    Parameters
    ----------
    inventory : `dict` [`str`, `list` [`ToolRecord`]]
        Server name -> its tool records.

    Returns
    -------
    manifest : `dict` [`str`, `Any`]
        Timestamp, the class, category and disposition definitions, every
        tool entry, and the proposed replacement tools.
    """
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "classes": TOOL_CLASSES,
        "categories": CATEGORIES,
        "dispositions": DISPOSITIONS,
        "tools": {
            server: {record.name: record.to_manifest_entry() for record in records}
            for server, records in inventory.items()
        },
        "proposed_tools": [
            {**asdict(proposed), "investigator_access": proposed.investigator_access()}
            for proposed in PROPOSED_TOOLS
        ],
    }


def build_runtime_manifest(server: str, records: list[ToolRecord]) -> dict[str, Any]:
    """Build the small manifest a server reads when it starts.

    Parameters
    ----------
    server : `str`
        The MCP server name.
    records : `list` [`ToolRecord`]
        The server's tools. A stale record is left out.

    Returns
    -------
    manifest : `dict` [`str`, `Any`]
        The server name and, for each tool, its class, category,
        disposition and the tool that replaces it. The server uses it to
        decide which tools a profile may use.
    """
    return {
        "server": server,
        "generated_by": "backend/mcp/tool_inventory.py --write-runtime-manifests",
        "tools": {
            record.name: {
                "tool_class": record.tool_class,
                "category": record.category,
                "disposition": record.disposition,
                "merge_into": record.merge_into,
                "interim_block": record.interim_block,
            }
            for record in sorted(records, key=lambda record: record.name)
            if not record.stale
        },
    }


def write_runtime_manifests(inventory: dict[str, list[ToolRecord]]) -> list[Path]:
    """Write the runtime manifest of each MCP server.

    Parameters
    ----------
    inventory : `dict` [`str`, `list` [`ToolRecord`]]
        Server name -> its tool records.

    Returns
    -------
    written : `list` [`pathlib.Path`]
        The files written.
    """
    written = []
    for server, path in RUNTIME_MANIFEST_PATHS.items():
        manifest = build_runtime_manifest(server, inventory[server])
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written


def check_decisions(inventory: dict[str, list[ToolRecord]]) -> list[str]:
    """Check the proposed tools and decisions against the served tools.

    Parameters
    ----------
    inventory : `dict` [`str`, `list` [`ToolRecord`]]
        The tools that are served now.

    Returns
    -------
    problems : `list` [`str`]
        Unknown names, tools replaced twice, bad categories and similar.
        Empty when everything agrees. A tool left uncategorized is also
        reported.
    """
    live = [record for records in inventory.values() for record in records if not record.stale]
    problems = find_problems({record.name for record in live}, set(TOOL_CLASSES))
    problems += [f"{record.name} has no category" for record in live if record.category not in CATEGORIES]
    return problems


def find_stale_permission_rules(
    inventory: dict[str, list[ToolRecord]], settings_files: tuple[Path, ...] = SETTINGS_FILES
) -> list[tuple[str, str, str]]:
    """Find permission rules that name a tool no server serves.

    Parameters
    ----------
    inventory : `dict` [`str`, `list` [`ToolRecord`]]
        The tools that are served now.
    settings_files : `tuple` [`pathlib.Path`, ...], optional
        Claude settings files to scan. A missing file is skipped.

    Returns
    -------
    stale_rules : `list` [`tuple` [`str`, `str`, `str`]]
        ``(file name, rule list, rule)`` for each rule such as
        ``mcp__astrometricslib-core__target_delete`` whose server is one
        of ours but whose tool is not served.
    """
    served = {
        server: {record.name for record in records if not record.stale}
        for server, records in inventory.items()
    }
    stale_rules = []
    for settings_file in settings_files:
        if not settings_file.is_file():
            continue
        permissions = json.loads(settings_file.read_text(encoding="utf-8")).get("permissions", {})
        for rule_list, rules in permissions.items():
            if not isinstance(rules, list):
                continue
            for rule in rules:
                parts = rule.split("__", 2) if isinstance(rule, str) else []
                if (
                    len(parts) == 3
                    and parts[0] == "mcp"
                    and parts[1] in served
                    and parts[2] not in served[parts[1]]
                ):
                    stale_rules.append((settings_file.name, rule_list, rule))
    return stale_rules


def _table_cell(text: str, limit: int = 100) -> str:
    """Make text safe and short enough for one Markdown table cell.

    Parameters
    ----------
    text : `str`
        The text to clean.
    limit : `int`, optional
        Longest result in characters. Longer text is cut with an ellipsis.

    Returns
    -------
    cell : `str`
        The cleaned text.
    """
    cleaned = " ".join(text.replace("|", "/").split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def _render_proposed_tools(proposed_tools: list[dict[str, Any]]) -> list[str]:
    """Write the section that describes the proposed replacement tools.

    Parameters
    ----------
    proposed_tools : `list` [`dict`]
        The ``proposed_tools`` list from the manifest.

    Returns
    -------
    lines : `list` [`str`]
        Markdown lines: a summary table, then the arguments and notes.
    """
    lines = [
        "",
        "## Proposed tools",
        "",
        "Each tool replaces the listed tools. Argument rules show the values an investigator may use.",
        "",
        "| Tool | Category | Class | Replaces | Investigator access |",
        "|---|---|---|---|---|",
    ]
    for proposed in proposed_tools:
        replaced = len(proposed["replaces"]) or "new"
        lines.append(
            f"| `{proposed['name']}` | {proposed['category']} | {proposed['tool_class']} | {replaced} "
            f"| {proposed['investigator_access']} |"
        )
    for proposed in proposed_tools:
        lines += ["", f"### `{proposed['name']}`", "", proposed["summary"], ""]
        lines += [f"- Argument: {parameter}" for parameter in proposed["parameters"]]
        for profile, rules in proposed["argument_rules"].items():
            lines += [f"- Rule for {profile}: `{argument}` {rule}" for argument, rule in rules.items()]
        if proposed["replaces"]:
            lines.append("- Replaces: " + ", ".join(f"`{name}`" for name in proposed["replaces"]))
        if proposed["notes"]:
            lines.append(f"- Notes: {proposed['notes']}")
    return lines


def _render_undecided(all_entries: list[tuple[str, str, dict[str, Any]]]) -> list[str]:
    """Write the section that lists every undecided tool in one place.

    Parameters
    ----------
    all_entries : `list` [`tuple` [`str`, `str`, `dict`]]
        ``(server, tool name, manifest entry)`` for every tool.

    Returns
    -------
    lines : `list` [`str`]
        Markdown lines: the count, then one table per category with the
        tool's class and description, so a decision needs no scrolling.
    """
    undecided = [row for row in all_entries if row[2]["disposition"] == "undecided"]
    lines = ["", "## Undecided tools", "", f"**{len(undecided)} tools** have no decision yet."]
    if not undecided:
        return lines
    for category in CATEGORIES:
        rows = sorted((row for row in undecided if row[2]["category"] == category), key=lambda row: row[1])
        if not rows:
            continue
        lines += [
            "",
            f"### {category} ({len(rows)})",
            "",
            "| Tool | Server | Class | What it does | Note |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| `{name}` | {server} | {entry['tool_class']} | {_table_cell(entry['description'], 90)} "
            f"| {_table_cell(entry['note'], 80)} |"
            for server, name, entry in rows
        ]
    return lines


def render_review_markdown(
    manifest: dict[str, Any],
    stale_permission_rules: list[tuple[str, str, str]],
    stale_entries: list[tuple[str, str]],
) -> str:
    """Write the review report as Markdown.

    Parameters
    ----------
    manifest : `dict` [`str`, `Any`]
        The manifest from `build_manifest`.
    stale_permission_rules : `list` [`tuple` [`str`, `str`, `str`]]
        Rules from `find_stale_permission_rules`.
    stale_entries : `list` [`tuple` [`str`, `str`]]
        Reviewed manifest entries with no live tool.

    Returns
    -------
    report : `str`
        Summary counts, one table per server, and the lists of old names.
    """
    classes = list(TOOL_CLASSES)
    dispositions = list(DISPOSITIONS)
    all_entries = [
        (server, name, entry)
        for server, entries in manifest["tools"].items()
        for name, entry in entries.items()
    ]
    lines = [
        "# MCP tool inventory (draft)",
        "",
        f"Generated {manifest['generated_at']}. Edit `{MANIFEST_FILE_NAME}`, not this file.",
        "",
        "Read the sections in this order: the undecided tools, the proposed tools, then each category.",
        "In a category table, undecided tools come first and are marked UNDECIDED. A row marked REVIEW has a",
        "low-confidence class.",
        "",
        "Jump to: [Undecided tools](#undecided-tools), [Proposed tools](#proposed-tools)",
        "",
        "## Counts by category and disposition",
        "",
        "| Category | Total | " + " | ".join(dispositions) + " |",
        "|---|---|" + "---|" * len(dispositions),
    ]
    for category in CATEGORIES:
        in_category = [entry for _, _, entry in all_entries if entry["category"] == category]
        counts = [sum(1 for entry in in_category if entry["disposition"] == name) for name in dispositions]
        lines.append(f"| {category} | {len(in_category)} | " + " | ".join(map(str, counts)) + " |")
    lines += _render_undecided(all_entries)
    lines += ["", "## Counts by server and class", "", "| Server | Total | " + " | ".join(classes) + " |"]
    lines.append("|---|---|" + "---|" * len(classes))
    for server, entries in manifest["tools"].items():
        counts = [sum(1 for entry in entries.values() if entry["tool_class"] == name) for name in classes]
        lines.append(f"| {server} | {len(entries)} | " + " | ".join(map(str, counts)) + " |")
    lines += ["", "## Classes", ""]
    lines += [f"- `{name}`: {meaning}" for name, meaning in TOOL_CLASSES.items()]
    lines += ["", "## Categories", ""]
    lines += [f"- `{name}`: {meaning}" for name, meaning in CATEGORIES.items()]
    lines += ["", "## Dispositions", ""]
    lines += [f"- `{name}`: {meaning}" for name, meaning in DISPOSITIONS.items()]
    lines += _render_proposed_tools(manifest["proposed_tools"])
    for category in CATEGORIES:
        rows = [(server, name, entry) for server, name, entry in all_entries if entry["category"] == category]
        if not rows:
            continue
        lines += [
            "",
            f"## Category: {category}",
            "",
            "| Tool | Server | Class | Disposition | Merge into | Flag | Why or note |",
            "|---|---|---|---|---|---|---|",
        ]
        rows.sort(
            key=lambda row: (
                row[2]["disposition"] != "undecided",
                dispositions.index(row[2]["disposition"]),
                row[1],
            )
        )
        for server, name, entry in rows:
            flags = []
            if entry["disposition"] == "undecided":
                flags.append("**UNDECIDED**")
            if entry["confidence"] == "low" and not entry["reviewed"]:
                flags.append("REVIEW")
            flag = ", ".join(flags) or ("reviewed" if entry["reviewed"] else entry["confidence"])
            why = entry["note"] or entry["reason"]
            merge_target = f"`{entry['merge_into']}`" if entry["merge_into"] else ""
            lines.append(
                f"| `{name}` | {server} | {entry['tool_class']} | {entry['disposition']} "
                f"| {merge_target} | {flag} | {_table_cell(why, 110)} |"
            )
    lines += ["", "## Permission rules that name a tool no server serves", ""]
    lines += [
        f"- `{rule}` ({file_name}, `{rule_list}`)" for file_name, rule_list, rule in stale_permission_rules
    ]
    if not stale_permission_rules:
        lines.append("None.")
    if stale_entries:
        lines += ["", "## Reviewed manifest entries with no live tool", ""]
        lines += [f"- `{server}`: `{name}`" for server, name in stale_entries]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Build the inventory and write the draft manifest and review report.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments. Defaults to ``sys.argv[1:]``.

    Returns
    -------
    exit_code : `int`
        0 when the files were written and the proposed tools and decisions
        agree with the served tools. 1 when they disagree; the files are
        still written and each problem is printed.
    """
    parser = argparse.ArgumentParser(description="Draft a class for every MCP tool the project serves.")
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Folder for the output files."
    )
    parser.add_argument(
        "--write-runtime-manifests",
        action="store_true",
        help="Also write the tool_manifest.json that each MCP server reads.",
    )
    arguments = parser.parse_args(argv)

    manifest_path = arguments.output_dir / MANIFEST_FILE_NAME
    existing_manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    )

    inventory = collect_inventory()
    stale_entries = merge_reviewed_entries(inventory, existing_manifest)
    manifest = build_manifest(inventory)
    stale_permission_rules = find_stale_permission_rules(inventory)

    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    review_path = arguments.output_dir / REVIEW_FILE_NAME
    review_path.write_text(
        render_review_markdown(manifest, stale_permission_rules, stale_entries), encoding="utf-8"
    )

    for server, records in inventory.items():
        to_review = sum(1 for record in records if record.confidence == "low" and not record.reviewed)
        undecided = sum(1 for record in records if record.disposition == "undecided")
        print(f"{server}: {len(records)} tools, {to_review} low-confidence, {undecided} undecided")
    print(f"Stale permission rules: {len(stale_permission_rules)}")
    print(f"Wrote {manifest_path}\nWrote {review_path}")
    if arguments.write_runtime_manifests:
        for path in write_runtime_manifests(inventory):
            print(f"Wrote {path}")
    problems = check_decisions(inventory)
    for problem in problems:
        print(f"PROBLEM: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
