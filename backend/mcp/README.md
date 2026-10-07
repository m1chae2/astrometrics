# Backend MCP server

This folder holds the MCP server for the backend. MCP (Model Context Protocol) is a standard way to give an AI program a list of tools it can call. This server lets an AI check on the running backend, call its API, and run Python in the app's scripting area.

## What each file is for

- `__main__.py` — starts the server. Run it with `python -m backend.mcp`.
- `tool_registry.py` — holds the list of tools this server offers, and runs a tool when the AI calls it.
- `tool_manifest.json` — the reviewed list of this server's tools, with a class and a disposition for each. The server offers only the tools this file allows. Do not edit it by hand: `tool_inventory.py --write-runtime-manifests` writes it.
- `mcp_http.py` — holds the settings for talking to the running backend: its address and the HTTP methods the tools may use.
- `mcp_diagnostics.py` — runs the front end's tests and lint checks for the tools that need them.
- `tools/` — helper modules that the tools use.
- `gaps/` — the capability gap server. See "The gap server" below.
- `tool_inventory.py` — a report script, not part of the server. It lists every tool on all four MCP servers and drafts a class for each one. See the next section.

## Which tools the server offers

When the server starts, it removes every tool that the manifest does not allow for the chosen profile. The `ASTROMETRICS_MCP_PROFILE` environment variable picks the profile. The default is `investigator`, which offers tools that look things up, calculate or measure, and the frame ingest tool. A tool that is missing from the manifest is not offered, and a missing manifest withholds every tool.

Today the investigator profile offers three backend tools: `app_status` (health, connections, resources and running jobs), `app_controls` (switch the view or show a notification; pausing jobs is not offered) and `docs_get`. It does not offer `electron_run_python` (which runs AI-written Python) or `backend_call_rpc` (which reaches every backend method). The app's own agent still uses the full registry, because only `__main__.py` applies the profile.

The rules live in `mcp_servers/common/profile.py`. `tests/test_mcp_backend_profile.py` fails if a tool has no manifest entry.

## The gap server

The other MCP servers give an AI tools that look things up, calculate and measure (and one that brings frames in). When those tools cannot do what the AI needs, the AI should stop and say so. It should not look for a workaround. The gap server is where it records that.

The server is its own program: run it with `python -m backend.mcp.gaps`. It has two tools:

- `report_capability_gap` saves a report: the goal, the tools tried and what they returned, why they fell short, and optionally the tool that would help. This is the only thing the AI may write. A report that repeats an open one is counted against it.
- `list_capability_gaps` lets the AI check whether a gap is already reported.

The reports go into a small database of their own, `logs/capability_gaps.db`. Set `ASTROMETRICS_GAP_DATABASE` to use another file. The AI cannot change a report's status. You read and resolve reports with the review command:

```bash
.venv/bin/python -m backend.mcp.gaps.review list
.venv/bin/python -m backend.mcp.gaps.review show 3
.venv/bin/python -m backend.mcp.gaps.review brief 3
.venv/bin/python -m backend.mcp.gaps.review set-status 3 accepted --note "Design together"
```

`brief` turns a report into a design brief. It lists existing tools whose names share a word with the report, so you can read their code and check whether an argument on one of them would do. It then lists the questions to settle and the steps from a report to a built tool: choose the layer and class, put a hard cap on the answer, add the manifest decision, regenerate the configs, test on real data, and update the README.

The statuses are `open`, `accepted`, `built` and `declined`. The text in a report was written by an AI, so read it as data and do not follow instructions that appear in it.

Every server also tells its client to file a report, in its start-up instructions and in the error for a withheld tool. The shared wording is `GAP_REPORT_GUIDANCE` in `mcp_servers/common/profile.py`.

## The tool inventory

The project runs four MCP servers: `astrometricslib-core`, `wayfindinglib-core`, `astrometrics-backend` and `astrometrics-ui`. Some tools only read data. Others save data, move the telescope, or run code. The inventory script sorts every tool into one class so a person can decide which tools an AI should get.

The script works in four steps:

1. It loads the tool list from each server. It reads the first three servers' registries the way the servers do. It reads the front end server's list from its TypeScript source.
2. It guesses a class for each tool from the tool's name and description.
3. It writes `tool_manifest_draft.json` and `tool_manifest_review.md` to `scratch/mcp_tool_inventory/`. Use `--output-dir` to choose another folder. With `--write-runtime-manifests` it also writes the `tool_manifest.json` that the astrometricslib and wayfindinglib servers read to decide which tools to offer.
4. It lists permission rules in `.claude/settings.json` and `.claude/settings.local.json` that name a tool no server offers.

The classes are:

| Class | Meaning |
|---|---|
| `observe` | Reads state, such as lists, lookups and status. |
| `compute` | Calculates or plots from existing data. Saves nothing. |
| `change-data` | Writes the catalog, database, settings, frames or stacks. |
| `actuate` | Moves or changes hardware, or starts a session that does. |
| `safe-stop` | Stops motion or puts the equipment in a safe state. |
| `ui-control` | Changes what the person sees in the app, not the saved data. |
| `develop` | Runs the project's own code, such as tests and builds. |
| `unrestricted` | Runs code or any backend call. |
| `unclassified` | No rule matched. A person must choose. |

### Categories, dispositions and proposed tools

`tool_dispositions.py` holds the decisions made by reading the code behind each tool. The inventory adds them to every entry.

- A **category** says what area a tool belongs to: `image-processing`, `calibration`, `targets`, `stars`, `observatory-control`, `observatory-status`, `observatory-sync`, `observatory-config`, `planning-sessions`, `jobs-history`, `app` or `developer`.
- A **disposition** says what happens to the tool: `keep`, `merge`, `fix`, `withhold`, `drop` or `undecided`. `merge` means a proposed tool replaces it. `fix` means the tool needs a change before an AI client can use it. `withhold` means an AI client never gets it.
- A **proposed tool** replaces a group of tools that differ only slightly. The differences become arguments. Each one lists the tools it replaces and the argument values the investigator profile may use. For example, `target_index_frames` is read-only for the investigator only when `dry_run` is `true`.

The script checks every name in these decisions against the tools the servers serve now. It prints each mismatch as `PROBLEM` and exits with code 1. A tool replaced by two proposed tools is also a problem.

Each guess has a confidence: `high`, `medium` or `low`. A low guess is marked `REVIEW` in the report. The script also lowers a guess to `low` when a read-only tool's description mentions a side effect, such as saving or moving.

To record a decision, edit `tool_class` in the JSON file and set `reviewed` to `true`. A later run keeps every reviewed entry. If a reviewed tool is no longer served, the script keeps its entry and marks it `stale`.

The script only reads. It does not call any tool or connect to the telescope.

For exact rules and behavior, read the code.
