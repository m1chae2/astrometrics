# Backend MCP server

This folder holds the `astrometrics-backend` MCP server. MCP (Model Context Protocol) is a standard way to give an AI program a list of tools it can call. This server lets an AI check on the running app: its health, the telescope, guiding, INDI devices (the drivers that talk to the observatory hardware), running jobs and documentation. It can also switch the app's view, and start the jobs the app's own buttons start (stacking, naming a stack's stars, making a preview again, and copying frames or logs from the telescope computer).

Every tool calls the running backend through its `/api/rpc` route, the same route the app's window uses. The server never loads the backend inside its own process, so start the backend first (`build/linux/run_backend.sh start`). The `ASTROMETRICS_API_BASE` environment variable sets the backend's address; the default is `http://127.0.0.1:5000`.

## What each file is for

- `__main__.py` — starts the server. Run it with `python -m mcp_servers.backend`. It builds the registry, applies the profile and calls the shared loop in `mcp_servers/common/server.py`.
- `definition.py` — the tools. `build_registry()` puts them in the shared `ToolRegistry` with a recovery hint for failed calls (`suggest_remediation`). `execute_rpc` sends one JSON-RPC request (a remote procedure call: a method name and its parameters as JSON) and raises the backend's error again as the same error category. `NotificationResource` offers the unread job notifications as the `astrometrics://notifications` resource; it reads them with the `system:notifications` RPC method.
- `app_jobs.py` — the tools that start a job in the app. Each calls the RPC method of the app's own button, named in `mcp_servers/common/app_job_tools.py`, so the job is the same job a click starts. See "Tools that start a job in the app" below.
- `mcp_http.py` — the HTTP helpers: the backend's address and the HTTP methods the tools may use (`GET` and `POST`).
- `tool_manifest.json` — the reviewed list of this server's tools, with a class and a disposition for each. The server offers only the tools this file allows. Do not edit it by hand: `python -m mcp_servers.inventory --write-runtime-manifests` writes it.

The server imports two functions from the backend and nothing else: `destructive_rpc_reason`, which refuses RPC methods that delete data, and `check_agent_code`, which refuses Python code that deletes files or data. An import-linter contract in `pyproject.toml` checks this.

## Which tools the server offers

When the server starts, it removes every tool that the manifest does not allow for the profile. There is only one profile. A tool that is missing from the manifest is not offered, and a missing manifest withholds every tool.

The profile offers these backend tools:

- `app_status` reports health, connections, system resources, the telescope, guiding, INDI devices and properties, and running jobs. The running jobs come from the `processing:active_jobs` RPC method. A section that fails holds an error record, and the other sections still answer.
- `app_controls` switches the view.
- `docs_get` reads a documentation topic.
- `terminal_add_script` lets an AI add a new Python script to the Command Console's User Scripts list.
- `terminal_get_recent_runs(limit=1, include_plots=True)` lets an AI read the results of scripts the person ran in the Command Console.

The profile also offers the five tools that start a job in the app (see the next section).

`terminal_add_script` only adds a script: the AI cannot run it. The person opens the script in Command Console > User Scripts, reads it, and presses Run. The tool:

- takes a plain file name that ends in `.py` (letters, digits, `_` and `-`, no folders), the script text and an optional description;
- refuses a name that is already in the list, and says which file it is, so it never replaces a script;
- puts a comment block at the top that says an AI client added the script, the date, and the description;
- saves through the `terminal:list_scripts` and `terminal:save_script` RPC methods and calls no method that runs code.

The Command Console reads its script list when it opens. Saving a script also sends the `scripts:changed` event, so an open console reloads its User Scripts list at once. Its class is `author`, which the profile offers.

`terminal_get_recent_runs` is read-only (class `observe`). It calls the `terminal:get_recent_runs` RPC method and returns the newest runs first, from 1 to 20 (default 1). Each run has its time, label, source, code, status, printed text (`stdout`), error text (`stderr`) and figure file paths. The backend keeps no workspace variables. With `include_plots`, each figure also comes back as a real MCP image (PNG), after the text, in run order. A figure wider than 1200 pixels is shrunk. The tool reads only `.png` files inside the app's scratch folder. A figure file that is gone or outside that folder is listed under `plot_images.problems` instead of as an image.

## Tools that start a job in the app

`processing_stack`, `processing_analyze`, `processing_identify_stack`, `processing_remake_preview`, `observatory_remote_sync_frames` and `observatory_remote_sync_logs` start slow work that writes to the library. `processing_cancel` (`processing:cancel` or `analysis:cancel`, after reading the active jobs with `jobs:query`) and `processing_enhance` (`processing:list_bright_objects` and `processing:set_bright_object`) change a job or a saved choice in the same way. Each one calls the RPC method the app's button calls: `processing:stack`, `analysis:analyze_image`, `analysis:identify_stack`, `processing:remake_preview`, `telescope:sync` and `telescope:sync_logs`. The backend therefore runs the work as its own job, in the same row of the job list, with the same progress, log and Cancel as a click. The library servers do not serve these tools. The table of tools, job types and Cancel buttons is in `mcp_servers/README.md`.

A started job's reply has a `follow` sentence that says how to follow it with `jobs_query`. A plan (`plan_only` or `dry_run`) starts no job. When the backend is not running, the call fails with "Could not connect to the backend", and nothing runs in the MCP process instead. `processing_analyze` reads the target's stacks with `target:get_files` and sends the same choice as the Analyze Target button (or the stack of each stage given). With `camera_id` or `focal_length_mm` it reads the target's stacks by camera and optic with `target:get`, sends the chosen one, and passes the camera and focal length to `analysis:analyze_image`. `processing_remake_preview` does the same with `camera_id` or `focal_length_mm`: it sends the chosen stack as `stack_path` to `processing:remake_preview`. `processing_identify_stack` reads the target's current imaging stack with `target:get` when no `stack_path` is given.

## What it does not offer

It does not offer `electron_run_python` (which runs AI-written Python) or `backend_call_rpc` (which reaches every backend method). The rules live in `mcp_servers/common/profile.py`. `test/test_profile.py` fails if a tool has no manifest entry.

For exact behavior, read the code.
