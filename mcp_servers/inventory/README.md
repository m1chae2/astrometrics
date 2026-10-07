# MCP tool inventory

The project runs four MCP servers: `astrometricslib-core`, `wayfindinglib-core`, `astrometrics-backend` and `astrometrics-ui`. Some tools only read data. Others save data, move the telescope, or run code. The inventory script sorts every tool into one class so a person can decide which tools an AI should get.

Run it from the project root with `.venv/bin/python -m mcp_servers.inventory`. The script works in four steps:

1. It loads the tool list from each server. It reads the first three servers' registries the way the servers do. It reads the front end server's list from its TypeScript source.
2. It guesses a class for each tool from the tool's name and description.
3. It writes `tool_manifest_draft.json` and `tool_manifest_review.md` to `scratch/mcp_tool_inventory/`. Use `--output-dir` to choose another folder. With `--write-runtime-manifests` it also writes the `tool_manifest.json` that each server reads to decide which tools to offer: `mcp_servers/astrometrics_core/`, `mcp_servers/wayfinding_core/`, `mcp_servers/backend/` and `ui/mcp/`.
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

## What each file is for

- `tool_inventory.py` — the script: it collects, classifies and writes.
- `tool_dispositions.py` — the decisions a person made about each tool.
- `__main__.py` — runs the script.

## Categories, dispositions and proposed tools

`tool_dispositions.py` holds the decisions made by reading the code behind each tool. The inventory adds them to every entry.

- A **category** says what area a tool belongs to: `image-processing`, `calibration`, `targets`, `stars`, `observatory-control`, `observatory-status`, `observatory-sync`, `observatory-config`, `planning-sessions`, `jobs-history`, `app` or `developer`.
- A **disposition** says what happens to the tool: `keep`, `merge`, `fix`, `withhold`, `drop` or `undecided`. `merge` means a proposed tool replaces it. `fix` means the tool needs a change before an AI client can use it. `withhold` means an AI client never gets it.
- A **proposed tool** replaces a group of tools that differ only slightly. The differences become arguments. Each one lists the tools it replaces and the argument values the investigator profile may use. For example, `target_index_frames` is read-only for the investigator only when `dry_run` is `true`.

The script checks every name in these decisions against the tools the servers serve now. It prints each mismatch as `PROBLEM` and exits with code 1. A tool replaced by two proposed tools is also a problem.

Each guess has a confidence: `high`, `medium` or `low`. A low guess is marked `REVIEW` in the report. The script also lowers a guess to `low` when a read-only tool's description mentions a side effect, such as saving or moving.

To record a decision, edit `tool_class` in the JSON file and set `reviewed` to `true`. A later run keeps every reviewed entry. If a reviewed tool is no longer served, the script keeps its entry and marks it `stale`.

The script only reads. It does not call any tool or connect to the telescope.

For exact rules and behavior, read the code.
