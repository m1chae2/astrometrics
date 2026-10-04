"""Purpose: Tests for the MCP tool inventory script.

Description: The script lists every tool the four MCP servers serve and drafts
a safety class for each. These tests check the name rules, the way earlier
human decisions are kept, the stale-permission check, and that the real
servers all give the script a tool list.
"""

import json
from pathlib import Path

import pytest

from backend.mcp import tool_inventory
from backend.mcp.tool_dispositions import (
    CATEGORIES,
    DECISIONS,
    DISPOSITIONS,
    PROPOSED_TOOLS,
    ProposedTool,
    categorize_tool,
    replaced_by_lookup,
)
from backend.mcp.tool_inventory import (
    TOOL_CLASSES,
    ToolRecord,
    apply_decisions,
    build_manifest,
    check_decisions,
    classify_tool,
    collect_inventory,
    collect_ui_server_tools,
    find_stale_permission_rules,
    main,
    merge_reviewed_entries,
    render_review_markdown,
)


@pytest.mark.parametrize(
    ("name", "expected_class"),
    [
        ("observatory_abort_motion", "safe-stop"),
        ("observatory_execute_safe_state", "safe-stop"),
        ("observatory_slew_to_target", "actuate"),
        ("observatory_capture_image", "actuate"),
        ("observatory_sync_coordinates", "actuate"),
        ("observatory_get_telescope_status", "observe"),
        ("observatory_analyze_guiding_session", "compute"),
        ("observatory_save_safety_rule_set", "change-data"),
        ("processing_run_stacking", "change-data"),
        ("star_find_by_position", "observe"),
        ("star_find_or_create_by_position", "change-data"),
        ("moving_object_detect_asteroids", "compute"),
        ("visualization_plot_photometry", "compute"),
        ("electron_run_python", "unrestricted"),
        ("backend_call_rpc", "unrestricted"),
        ("ui_run_tests", "develop"),
    ],
)
def test_classify_tool_uses_name_rules(name: str, expected_class: str) -> None:
    """Known tool names get the class the rules promise."""
    tool_class, _, _ = classify_tool(name, "Plain description.")
    assert tool_class == expected_class


def test_classify_tool_marks_unknown_names_unclassified() -> None:
    """A name no rule matches is flagged for a person to choose."""
    assert classify_tool("mystery_tool", "") == ("unclassified", "low", "No rule matched.")


def test_classify_tool_lowers_confidence_for_side_effect_words() -> None:
    """A read-only guess drops to low confidence on side-effect words."""
    _, quiet_confidence, _ = classify_tool("target_get_frame", "Return a frame path.")
    _, loud_confidence, reason = classify_tool("target_get_frame", "Return a path, then save it.")
    assert quiet_confidence == "high"
    assert loud_confidence == "low"
    assert "save" in reason


def test_classify_tool_does_not_flag_camera_words() -> None:
    """Camera words such as exposure do not count as a side effect."""
    _, confidence, _ = classify_tool("calibration_stats", "Count frames by exposure and filter.")
    assert confidence == "high"


def test_collect_ui_server_tools_reads_name_and_description(tmp_path: Path) -> None:
    """The TypeScript tool list is read from the source text."""
    source = tmp_path / "index.ts"
    source.write_text(
        'tools: [{ name: "ui_a", description: "Does A.", inputSchema: {} },\n'
        '{\n  name: "ui_b",\n  description: "Does B.",\n}]',
        encoding="utf-8",
    )
    records = collect_ui_server_tools(source)
    assert [(record.name, record.description) for record in records] == [
        ("ui_a", "Does A."),
        ("ui_b", "Does B."),
    ]


def test_merge_reviewed_entries_keeps_human_decisions() -> None:
    """A reviewed entry survives a new run; a gone tool is kept as stale."""
    fresh = ToolRecord("srv", "tool_one", "Fresh.", [], "observe", "high", "Guess.")
    inventory = {"srv": [fresh]}
    existing = {
        "tools": {
            "srv": {
                "tool_one": {
                    "tool_class": "actuate",
                    "confidence": "high",
                    "reason": "Person said so.",
                    "reviewed": True,
                },
                "tool_gone": {"tool_class": "observe", "reviewed": True, "description": "Old."},
                "tool_unreviewed": {"tool_class": "observe", "reviewed": False},
            }
        }
    }
    stale_entries = merge_reviewed_entries(inventory, existing)
    assert stale_entries == [("srv", "tool_gone")]
    assert fresh.tool_class == "actuate"
    assert fresh.reviewed is True
    names = {record.name: record for record in inventory["srv"]}
    assert names["tool_gone"].stale is True
    assert "tool_unreviewed" not in names


def test_find_stale_permission_rules_reports_only_missing_project_tools(tmp_path: Path) -> None:
    """Rules for tools that exist, or for other servers, are not reported."""
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({
            "permissions": {
                "ask": [
                    "mcp__srv__tool_one",
                    "mcp__srv__tool_gone",
                    "mcp__other__anything",
                    "Bash(rm *)",
                ]
            }
        }),
        encoding="utf-8",
    )
    inventory = {"srv": [ToolRecord("srv", "tool_one", "")]}
    stale_rules = find_stale_permission_rules(inventory, (settings, tmp_path / "missing.json"))
    assert stale_rules == [("settings.json", "ask", "mcp__srv__tool_gone")]


def test_render_review_markdown_marks_low_confidence_rows() -> None:
    """The report shows REVIEW for a low-confidence, unreviewed guess."""
    inventory = {"srv": [ToolRecord("srv", "mystery_tool", "Unknown.", category="stars")]}
    manifest = build_manifest(inventory)
    report = render_review_markdown(manifest, [("settings.json", "ask", "mcp__srv__old")], [])
    assert "REVIEW" in report
    assert "mcp__srv__old" in report


def test_main_writes_manifest_and_report(tmp_path: Path) -> None:
    """A run writes both files, and every tool has a known class."""
    assert main(["--output-dir", str(tmp_path)]) == 0
    manifest = json.loads((tmp_path / tool_inventory.MANIFEST_FILE_NAME).read_text(encoding="utf-8"))
    assert (tmp_path / tool_inventory.REVIEW_FILE_NAME).is_file()
    for entries in manifest["tools"].values():
        assert entries
        assert all(entry["tool_class"] in TOOL_CLASSES for entry in entries.values())


def test_collect_inventory_covers_all_four_servers() -> None:
    """Each of the four MCP servers contributes at least one tool."""
    inventory = collect_inventory()
    assert set(inventory) == {
        "astrometricslib-core",
        "wayfindinglib-core",
        "astrometrics-backend",
        "astrometrics-ui",
    }
    assert all(inventory.values())


@pytest.mark.parametrize(
    ("name", "tool_class", "expected_category"),
    [
        ("calibration_stats", "observe", "calibration"),
        ("target_get_calibration_frame_statistics", "observe", "calibration"),
        ("target_get", "observe", "targets"),
        ("processing_scan_target_directory", "change-data", "targets"),
        ("star_get_object", "observe", "stars"),
        ("processing_run_stacking", "change-data", "image-processing"),
        ("target_process_all_targets", "change-data", "image-processing"),
        ("planning_get_visibility", "observe", "planning-sessions"),
        ("execution_abort_session", "change-data", "planning-sessions"),
        ("observatory_slew_to_target", "actuate", "observatory-control"),
        ("observatory_download_remote_frames", "change-data", "observatory-sync"),
        ("observatory_get_telescope_status", "observe", "observatory-status"),
        ("observatory_save_safety_rule_set", "change-data", "observatory-config"),
        ("backend_call_rpc", "unrestricted", "app"),
        ("ui_run_tests", "develop", "developer"),
    ],
)
def test_categorize_tool_uses_name_and_class(name: str, tool_class: str, expected_category: str) -> None:
    """Names and classes place a tool in the category the plan expects."""
    assert categorize_tool(name, tool_class) == expected_category


def test_decisions_and_proposed_tools_agree_with_served_tools() -> None:
    """Every name in the decisions and proposed tools is served, once."""
    assert check_decisions(collect_inventory()) == []


def test_every_served_tool_gets_a_category_and_disposition() -> None:
    """No tool is left uncategorized, and each disposition is a known one."""
    for records in collect_inventory().values():
        for record in records:
            assert record.category in CATEGORIES, record.name
            assert record.disposition in DISPOSITIONS, record.name


def test_apply_decisions_merges_into_the_proposed_tool() -> None:
    """A replaced tool gets the new tool's category and merged status."""
    record = ToolRecord("srv", "star_get_object", "", tool_class="observe")
    apply_decisions(record, replaced_by_lookup())
    assert (record.category, record.disposition, record.merge_into) == ("stars", "merged", "star_query")


def test_apply_decisions_corrects_the_class_and_keeps_the_note() -> None:
    """A class correction replaces the drafted class and records why."""
    record = ToolRecord("srv", "star_analyze_periodicity", "", tool_class="compute")
    apply_decisions(record, replaced_by_lookup())
    assert record.tool_class == "change-data"
    assert record.disposition == "fix"
    assert "star catalog" in record.note


def test_config_writers_are_withheld() -> None:
    """The tools that change configuration are withheld from AI clients."""
    for name in ("observatory_set_active_camera", "observatory_save_safety_rule_set"):
        assert DECISIONS[name].disposition == "withhold"


def test_investigator_access_follows_class_and_rules() -> None:
    """Read-only tools are fully open; writers only with safe arguments."""
    by_name = {proposed.name: proposed for proposed in PROPOSED_TOOLS}
    assert by_name["star_query"].investigator_access() == "full"
    assert by_name["target_index_frames"].investigator_access() == "limited"
    assert by_name["app_controls"].investigator_access() == "limited"


def test_proposed_tool_without_replacements_is_allowed() -> None:
    """A new tool, such as the job history query, replaces nothing."""
    proposed = ProposedTool("jobs_query", "jobs-history", "x", (), (), "observe")
    assert proposed.replaces == ()
    assert proposed.investigator_access() == "full"


def test_every_hardware_control_tool_is_dropped() -> None:
    """Hardware control is out of scope, so no control tool is offered."""
    control_tools = [
        record
        for records in collect_inventory().values()
        for record in records
        if record.category == "observatory-control"
    ]
    assert len(control_tools) > 20
    assert {record.disposition for record in control_tools} == {"drop"}


def test_polar_alignment_assist_is_a_calculation_not_a_command() -> None:
    """The fit uses records passed in, so it is a monitoring calculation."""
    record = ToolRecord("srv", "observatory_run_polar_alignment_assist", "", tool_class="actuate")
    apply_decisions(record, replaced_by_lookup())
    assert (record.tool_class, record.category) == ("compute", "observatory-status")


def test_delegation_mode_switches_count_as_configuration() -> None:
    """The Safe Mode toggle writes saved state, so an AI may not change it."""
    for name in ("observatory_enter_controller_mode", "observatory_enter_monitoring_mode"):
        record = ToolRecord("srv", name, "", tool_class="actuate")
        apply_decisions(record, replaced_by_lookup())
        assert (record.category, record.disposition) == ("observatory-config", "withhold")


def test_tools_the_user_declined_are_dropped() -> None:
    """Tools the user declined stay dropped."""
    dropped = (
        "observatory_compute_focus_correction",
        "observatory_compute_guiding_correction",
        "observatory_compute_pointing_correction",
        "observatory_run_polar_alignment_assist",
        "diagnostics_flag_value_outliers",
        "observatory_drain_external_pulses",
        "observatory_refit_guiding_spectrum",
        "observatory_save_commissioning_run",
        "observatory_save_ekos_session_context",
        "observatory_save_guiding_run",
    )
    for name in dropped:
        assert DECISIONS[name].disposition == "drop", name


def test_writers_are_withheld_because_the_ai_is_read_only() -> None:
    """A writer with no read-only form is withheld, with the reason."""
    for name in ("star_create", "star_update", "target_create", "calibration_add"):
        record = ToolRecord("srv", name, "", tool_class="change-data")
        apply_decisions(record, replaced_by_lookup())
        assert record.disposition == "withhold", name
        assert "read-only" in record.note


def test_no_proposed_tool_offers_a_writer_to_the_investigator() -> None:
    """A writing proposed tool gives the investigator safe values or none."""
    for proposed in PROPOSED_TOOLS:
        if proposed.tool_class not in ("observe", "compute", "ingest"):
            assert proposed.investigator_access() in ("limited", "none"), proposed.name
        assert "operator" not in proposed.argument_rules, proposed.name


def test_log_ingest_tools_merge_into_the_log_sync_tool() -> None:
    """The three log ingest tools are replaced by the built log sync tool."""
    lookup = replaced_by_lookup()
    for name in (
        "observatory_fetch_and_ingest_new_guide_logs",
        "observatory_ingest_ekos_session_logs",
        "observatory_ingest_guiding_log_file",
    ):
        assert lookup[name].name == "observatory_sync_remote_logs"
    assert lookup["observatory_ingest_ekos_session_logs"].built is True


def test_the_frame_ingest_tool_is_built_and_replaces_the_download_tools() -> None:
    """The sync tool is built, and its predecessors are merged."""
    sync = next(proposed for proposed in PROPOSED_TOOLS if proposed.name == "observatory_sync_remote_frames")
    assert sync.built is True
    assert sync.tool_class == "ingest"
    assert sync.investigator_access() == "full"
    assert (
        replaced_by_lookup()["observatory_download_remote_targets"].name == "observatory_sync_remote_frames"
    )


def test_status_reads_merge_into_the_status_tools() -> None:
    """Night history, settings reads and device reads each have one home."""
    lookup = replaced_by_lookup()
    assert lookup["observatory_analyze_capture_session"].name == "observatory_night_history"
    assert lookup["observatory_get_pointing_model"].name == "observatory_night_history"
    assert lookup["observatory_get_observer_location"].name == "observatory_equipment_state"
    assert lookup["observatory_get_focuser_position"].name == "observatory_hardware_status"


def test_navigate_and_notify_are_allowed_but_pause_is_not() -> None:
    """The investigator may navigate and notify, but not pause jobs."""
    by_name = {proposed.name: proposed for proposed in PROPOSED_TOOLS}
    allowed = by_name["app_controls"].argument_rules["investigator"]["action"]["allowed"]
    assert set(allowed) == {"navigate", "notify"}
