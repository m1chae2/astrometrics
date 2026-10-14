# Backend MCP server

This folder holds the `astrometrics-backend` MCP server. MCP (Model Context Protocol) is a standard way to give an AI program a list of tools it can call. This server lets an AI check on the running app: its health, the telescope, guiding, INDI devices (the drivers that talk to the observatory hardware), running jobs and documentation. It can also switch the app's view or show a notification.

Every tool calls the running backend through its `/api/rpc` route, the same route the app's window uses. The server never loads the backend inside its own process, so start the backend first (`build/linux/run_backend.sh start`). The `ASTROMETRICS_API_BASE` environment variable sets the backend's address; the default is `http://127.0.0.1:5000`.

## What each file is for

- `__main__.py` — starts the server. Run it with `python -m mcp_servers.backend`. It builds the registry, applies the profile and calls the shared loop in `mcp_servers/common/server.py`.
- `definition.py` — the tools. `build_registry()` puts them in the shared `ToolRegistry` with a recovery hint for failed calls (`suggest_remediation`). `execute_rpc` sends one JSON-RPC request (a remote procedure call: a method name and its parameters as JSON) and raises the backend's error again as the same error category. `NotificationResource` offers the unread job notifications as the `astrometrics://notifications` resource; it reads them with the `system:notifications` RPC method.
- `mcp_http.py` — the HTTP helpers: the backend's address and the HTTP methods the tools may use (`GET` and `POST`).
- `tool_manifest.json` — the reviewed list of this server's tools, with a class and a disposition for each. The server offers only the tools this file allows. Do not edit it by hand: `python -m mcp_servers.inventory --write-runtime-manifests` writes it.

The server imports two functions from the backend and nothing else: `destructive_rpc_reason`, which refuses RPC methods that delete data, and `check_agent_code`, which refuses Python code that deletes files or data. An import-linter contract in `pyproject.toml` checks this.

## Which tools the server offers

When the server starts, it removes every tool that the manifest does not allow for the chosen profile. The `ASTROMETRICS_MCP_PROFILE` environment variable picks the profile. A tool that is missing from the manifest is not offered, and a missing manifest withholds every tool.

The investigator profile offers three backend tools:

- `app_status` reports health, connections, system resources, the telescope, guiding, INDI devices and properties, and running jobs. The running jobs come from the `processing:active_jobs` RPC method. A section that fails holds an error record, and the other sections still answer.
- `app_controls` switches the view or shows a notification.
- `docs_get` reads a documentation topic.

It does not offer `electron_run_python` (which runs AI-written Python) or `backend_call_rpc` (which reaches every backend method). The rules live in `mcp_servers/common/profile.py`. `test/test_profile.py` fails if a tool has no manifest entry.

For exact behavior, read the code.
