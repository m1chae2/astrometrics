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
| `ingest` | Brings data from the telescope into the library. Adds files and records, never deletes. |
| `process` | Runs the app's own processing stage on library frames, as its buttons do. |
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

## Categories and dispositions

`tool_dispositions.py` holds a table, `DECISIONS`, with one row for every tool the servers serve. A person writes each row after reading the code behind the tool. The inventory adds the row to the tool's entry.

- A **category** says what area a tool belongs to: `image-processing`, `calibration`, `targets`, `stars`, `observatory-control`, `observatory-status`, `observatory-sync`, `observatory-config`, `planning-sessions`, `jobs-history`, `app` or `developer`. The inventory picks it from the tool's name and class.
- A **disposition** says what happens to the tool: `keep`, `fix`, `withhold`, `drop` or `undecided`. Only `keep` is offered. `fix` means the tool needs a change before an AI client can use it. `withhold` means an AI client never gets it, because it writes, runs code or reaches every backend method. `drop` means it is out of scope or a broader tool replaced it. A tool with no row is `undecided` and is not offered.
- A row can also correct the drafted class, and carries a short note that says why. A server quotes the note when it refuses the tool.
- `INTERIM_BLOCKS` hides a few `keep` tools until a known problem is fixed.

The script checks the table against the tools the servers serve now. It prints each mismatch as `PROBLEM` and exits with code 1. A served tool with no row, a row for a tool no server serves, and a withheld tool with no note are all problems. `test/test_served_tools.py` checks that the tools each library server offers are its reflected public methods minus the rows that say not to offer them, so a new library method fails a test until it has a row.

Each guess has a confidence: `high`, `medium` or `low`. A low guess is marked `REVIEW` in the report. The script also lowers a guess to `low` when a read-only tool's description mentions a side effect, such as saving or moving.

To record a decision, edit `tool_class` in the JSON file and set `reviewed` to `true`. A later run keeps every reviewed entry. If a reviewed tool is no longer served, the script keeps its entry and marks it `stale`.

The script never calls a tool or connects to the telescope.

For exact rules and behavior, read the code.
