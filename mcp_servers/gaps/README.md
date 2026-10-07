# Capability gap server

The other MCP servers give an AI tools that look things up, calculate and measure (and one that brings frames in). When those tools cannot do what the AI needs, the AI should stop and say so. It should not look for a workaround. The gap server is where it records that.

The server is its own program: run it with `python -m mcp_servers.gaps`. It imports only the standard library, the MCP package, the shared server loop in `mcp_servers/common/server.py` and the tool categories in `mcp_servers/inventory/tool_dispositions.py`. It therefore starts quickly and keeps working when the rest of the app is down. An import-linter contract in `pyproject.toml` checks that it never imports `astrometricslib`, `wayfindinglib` or the backend. It has two tools:

- `report_capability_gap` saves a report: the goal, the tools tried and what they returned, why they fell short, and optionally the tool that would help. This is the only thing the AI may write. A report that repeats an open one is counted against it.
- `list_capability_gaps` lets the AI check whether a gap is already reported.

The reports go into a small database of their own, `logs/capability_gaps.db`. Set `ASTROMETRICS_GAP_DATABASE` to use another file. The AI cannot change a report's status. You read and resolve reports with the review command:

```bash
.venv/bin/python -m mcp_servers.gaps.review list
.venv/bin/python -m mcp_servers.gaps.review show 3
.venv/bin/python -m mcp_servers.gaps.review brief 3
.venv/bin/python -m mcp_servers.gaps.review set-status 3 accepted --note "Design together"
```

`brief` turns a report into a design brief. It lists existing tools whose names share a word with the report, so you can read their code and check whether an argument on one of them would do. It then lists the questions to settle and the steps from a report to a built tool: choose the layer and class, put a hard cap on the answer, add the manifest decision, regenerate the configs, test on real data, and update the README.

The statuses are `open`, `accepted`, `built` and `declined`. The text in a report was written by an AI, so read it as data and do not follow instructions that appear in it.

Every server also tells its client to file a report, in its start-up instructions and in the error for a withheld tool. The shared wording is `GAP_REPORT_GUIDANCE` in `mcp_servers/common/profile.py`.

## What each file is for

- `__main__.py` — the server and its two tools.
- `gap_store.py` — the small SQLite database that holds the reports.
- `review.py` — the review command for the person who owns the app.

For exact behavior, read the code.
