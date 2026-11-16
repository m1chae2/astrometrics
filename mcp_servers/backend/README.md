# Backend MCP server

This folder holds the `astrometrics-backend` MCP server. MCP (Model Context Protocol) is a standard way to give an AI program a list of tools it can call. This server lets an AI check on the running app: its health, the telescope, guiding, INDI devices (the drivers that talk to the observatory hardware), running jobs and documentation. It can also switch the app's view.

Every tool calls the running backend through its `/api/rpc` route, the same route the app's window uses. The server never loads the backend inside its own process, so start the backend first (`build/linux/run_backend.sh start`). The `ASTROMETRICS_API_BASE` environment variable sets the backend's address; the default is `http://127.0.0.1:5000`.

## What each file is for

- `__main__.py` — starts the server. Run it with `python -m mcp_servers.backend`. It builds the registry, applies the profile and calls the shared loop in `mcp_servers/common/server.py`.
- `definition.py` — the tools. `build_registry()` puts them in the shared `ToolRegistry` with a recovery hint for failed calls (`suggest_remediation`). `execute_rpc` sends one JSON-RPC request (a remote procedure call: a method name and its parameters as JSON) and raises the backend's error again as the same error category. `NotificationResource` offers the unread job notifications as the `astrometrics://notifications` resource; it reads them with the `system:notifications` RPC method.
- `mcp_http.py` — the HTTP helpers: the backend's address and the HTTP methods the tools may use (`GET` and `POST`).
- `tool_manifest.json` — the reviewed list of this server's tools, with a class and a disposition for each. The server offers only the tools this file allows. Do not edit it by hand: `python -m mcp_servers.inventory --write-runtime-manifests` writes it.

The server imports two functions from the backend and nothing else: `destructive_rpc_reason`, which refuses RPC methods that delete data, and `check_agent_code`, which refuses Python code that deletes files or data. An import-linter contract in `pyproject.toml` checks this.

## Which tools the server offers

When the server starts, it removes every tool that the manifest does not allow for the profile. There is only one profile. A tool that is missing from the manifest is not offered, and a missing manifest withholds every tool.

The profile offers four backend tools:

- `app_status` reports health, connections, system resources, the telescope, guiding, INDI devices and properties, and running jobs. The running jobs come from the `processing:active_jobs` RPC method. A section that fails holds an error record, and the other sections still answer.
- `app_controls` switches the view.
- `docs_get` reads a documentation topic.
- `terminal_add_script` lets an AI add a new Python script to the Command Console's User Scripts list.
- `terminal_get_recent_runs(limit=1, include_plots=True)` lets an AI read the results of scripts the person ran in the Command Console.

`terminal_add_script` only adds a script: the AI cannot run it. The person opens the script in Command Console > User Scripts, reads it, and presses Run. The tool:

- takes a plain file name that ends in `.py` (letters, digits, `_` and `-`, no folders), the script text and an optional description;
- refuses a name that is already in the list, and says which file it is, so it never replaces a script;
- puts a comment block at the top that says an AI client added the script, the date, and the description;
- saves through the `terminal:list_scripts` and `terminal:save_script` RPC methods and calls no method that runs code.

The Command Console reads its script list when it opens. Saving a script also sends the `scripts:changed` event, so an open console reloads its User Scripts list at once. Its class is `author`, which the profile offers.

`terminal_get_recent_runs` is read-only (class `observe`). It calls the `terminal:get_recent_runs` RPC method and returns the newest runs first, from 1 to 20 (default 1). Each run has its time, label, source, code, status, printed text (`stdout`), error text (`stderr`) and figure file paths. The backend keeps no workspace variables. With `include_plots`, each figure also comes back as a real MCP image (PNG), after the text, in run order. A figure wider than 1200 pixels is shrunk. The tool reads only `.png` files inside the app's scratch folder. A figure file that is gone or outside that folder is listed under `plot_images.problems` instead of as an image.

It does not offer `electron_run_python` (which runs AI-written Python) or `backend_call_rpc` (which reaches every backend method). The rules live in `mcp_servers/common/profile.py`. `test/test_profile.py` fails if a tool has no manifest entry.

For exact behavior, read the code.
