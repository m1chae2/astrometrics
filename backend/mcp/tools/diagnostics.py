"""Purpose: MCP tools for system diagnostics and troubleshooting.

Uses the high-level interface, execute_rpc, and direct filesystem checks to
monitor state. REQ: AGENT-1.1
"""

import logging
import os

from backend.mcp.tool_registry import execute_rpc, get_astrometrics, registry

logger = logging.getLogger(__name__)


@registry.register(
    name="backend_inspect_service",
    description="Introspect backend services to view method signatures and docstrings.",
    input_schema={
        "type": "object",
        "properties": {"service_name": {"type": "string", "description": "Name of the service"}},
        "required": ["service_name"],
    },
)
async def inspect_service(service_name: str):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Introspect a backend service via the system:introspection RPC.

    Parameters
    ----------
    service_name : `str`
        Name or type of the service to look up.

    Returns
    -------
    result : `dict` or `str`
        The matching service's introspection data, or an error
        message string if the service was not found or the RPC
        failed.
    """
    res = await execute_rpc("system:introspection")
    if res.get("status") != "success":
        return f"Error: {res.get('message', 'Failed to retrieve introspection data')}"

    data = res.get("data", [])
    for svc in data:
        if svc.get("name") == service_name or svc.get("type") == service_name:
            return svc

    return f"Service '{service_name}' not found."


@registry.register(
    name="backend_read_logs",
    description=(
        "Read the tail of system logs. Can read 'backend' API logs, 'indi' logs, or a specific log file path."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "log_type": {
                "type": "string",
                "description": "Type of log to read: 'backend', 'indi', or 'custom'. Default 'backend'.",
            },
            "file_path": {
                "type": "string",
                "description": "Absolute path to log file if log_type is 'custom'.",
            },
            "lines": {"type": "integer", "description": "Number of lines to read (default 50)."},
        },
    },
)
async def read_logs(  # ruff: ignore[unused-async] -- awaited by ToolRegistry.execute  # ruff: ignore[missing-return-type-undocumented-public-function]
    log_type: str = "backend", file_path: str | None = None, lines: int = 50
):
    """Read system logs via direct file access.

    Reads the log file directly from disk rather than through the
    backend process, for reliable cross-process operation.

    Parameters
    ----------
    log_type : `str`, optional
        Type of log to read: ``"backend"``, ``"indi"``, or
        ``"custom"``. Default is ``"backend"``.
    file_path : `str`, optional
        Absolute path to the log file when `log_type` is
        ``"custom"``.
    lines : `int`, optional
        Number of trailing lines to read. Default is 50.

    Returns
    -------
    result : `str`
        The tail of the requested log file, or an error message.
    """
    target_file = None
    astrometrics = get_astrometrics()
    repo_root = str(astrometrics.config.get_project_root()) if astrometrics else os.getcwd()

    if log_type == "backend":
        logs_dir = (
            str(astrometrics.config.get_logs_path()) if astrometrics else os.path.join(repo_root, "logs")
        )
        if os.path.exists(logs_dir):
            # Find log files not containing "process_"
            files = [
                os.path.join(logs_dir, f)
                for f in os.listdir(logs_dir)
                if f.endswith(".log") and "process_" not in f
            ]
            if files:
                target_file = max(files, key=os.path.getmtime)
            else:
                # If no specific backend log, try to find any log file
                files = [os.path.join(logs_dir, f) for f in os.listdir(logs_dir) if f.endswith(".log")]
                if files:
                    target_file = max(files, key=os.path.getmtime)

    elif log_type == "indi":
        indi_log_dir = "/var/log/indi"
        if not os.path.exists(indi_log_dir):
            indi_log_dir = os.path.expanduser("~/.indi/logs")
            if not os.path.exists(indi_log_dir):
                return "Error: INDI log directory not found."
        files = [os.path.join(indi_log_dir, f) for f in os.listdir(indi_log_dir) if f.endswith(".log")]
        if not files:
            return f"No logs found in {indi_log_dir}"
        target_file = max(files, key=os.path.getmtime)
    elif log_type == "custom" and file_path:
        if ".." in file_path:
            return "Error: Path traversal not allowed."

        allowed_dirs = ["/var/log", os.path.expanduser("~/.indi"), repo_root]
        abs_path = os.path.abspath(file_path)
        if not any(abs_path.startswith(d) for d in allowed_dirs):
            return f"Error: Access denied to path: {file_path}"

        target_file = abs_path

    if target_file and os.path.exists(target_file):
        try:
            with open(target_file) as f:
                content = f.readlines()
                tail = content[-lines:]
                return "".join(tail)
        except Exception as e:
            return f"Error reading log: {e!s}"

    return f"Error: Could not locate log for {log_type}."


@registry.register(
    name="backend_diagnose_system",
    description="Perform a full suite of health checks on hardware, subsystems, and environment.",
)
async def diagnose_system():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Perform system-wide diagnostics via the system:health RPC.

    Returns
    -------
    result : `dict`
        On success, includes ``"status"`` set to ``"ok"`` and
        ``"result"`` with the health check data. On failure,
        includes ``"status"`` set to ``"error"`` and ``"message"``.
    """
    res = await execute_rpc("system:health")
    if res.get("status") == "success":
        return {"status": "ok", "result": res.get("data")}
    return {"status": "error", "message": res.get("message", "Failed to retrieve system health")}


@registry.register(
    name="backend_restart_backend", description="Safely restart the high-level interface backend service."
)
async def restart_backend():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Save system state and trigger the backend restart script.

    Returns
    -------
    result : `str`
        Confirmation message including the restart script's stdout
        and stderr, or an error message if the script could not be
        located or run.
    """
    import subprocess

    astrometrics = get_astrometrics()
    repo_root = str(astrometrics.config.get_project_root()) if astrometrics else os.getcwd()

    try:
        await execute_rpc("system:save")
    except Exception as exc:
        logger.debug("Best-effort system:save before shutdown failed: %s", exc)

    script_path = os.path.join(repo_root, "scripts", "linux", "run_backend.sh")
    if not os.path.exists(script_path):
        return f"Error: Restart script not found at {script_path}"

    try:
        res = subprocess.run([script_path, "restart"], cwd=repo_root, capture_output=True, text=True)
        return f"Backend restart signal sent.\n{res.stdout}\n{res.stderr}"
    except Exception as e:
        return f"Error: {e!s}"


@registry.register(
    name="backend_diagnose_plate_solver",
    description="Verify local plate-solver configuration and index file availability.",
)
async def diagnose_plate_solver():  # ruff: ignore[unused-async] -- awaited by ToolRegistry.execute  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify plate-solver configuration via the high-level interface.

    Returns
    -------
    result : `dict`
        Plate-solver diagnostic data from the high-level interface, or a
        dictionary with ``"status"`` and ``"message"`` if the
        astrometrics is unavailable.
    """
    astrometrics = get_astrometrics()
    if not astrometrics:
        return {"status": "error", "message": "Astrometrics high-level interface not available"}
    return astrometrics.diagnose_plate_solver()


@registry.register(
    name="backend_run_diagnostic_tests",
    description="Run the backend unit test suite (pytest).",
    input_schema={
        "type": "object",
        "properties": {
            "test_path": {
                "type": "string",
                "description": "Optional path to a specific test file or directory.",
            }
        },
    },
)
async def run_backend_tests(  # ruff: ignore[unused-async] -- awaited by ToolRegistry.execute  # ruff: ignore[missing-return-type-undocumented-public-function]
    test_path: str | None = None,
):
    """Run the backend pytest suite after validating the test path.

    Parameters
    ----------
    test_path : `str`, optional
        Path to a specific test file or directory, relative to the
        repository root. Rejected if it is absolute or contains
        ``".."``. If `None` (default), the full backend suite runs.

    Returns
    -------
    result : `str`
        Human-readable summary of the pytest run, or an error
        message if `test_path` is invalid.
    """
    from backend.mcp import mcp_diagnostics

    astrometrics = get_astrometrics()
    repo_root = str(astrometrics.config.get_project_root()) if astrometrics else os.getcwd()

    if test_path:
        if ".." in test_path or os.path.isabs(test_path):
            return "Error: Invalid test path."

        abs_path = os.path.join(repo_root, test_path)
        if not os.path.exists(abs_path):
            return f"Error: Path {test_path} does not exist."

    return mcp_diagnostics.run_backend_tests(repo_root, test_path)


@registry.register(
    name="backend_inspect_device_reservations",
    description=(
        "Query active lock files in the user data directory to inspect lock status, holding PID, "
        "and process details."
    ),
)
async def inspect_device_reservations():  # ruff: ignore[unused-async] -- awaited by ToolRegistry.execute  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Scan hardware advisory lock files in the library directory.

    Reports lock status, holding process ID (PID), and operational
    process details for the mount, camera, and focuser devices.


    Returns
    -------
    result : `dict`
        Mapping of device name to a dictionary with ``"status"``,
        ``"lock_file_exists"``, ``"pid"``, ``"process_name"``, and
        ``"command_line"``. On failure to reach the high-level
        interface, includes ``"status"`` and ``"message"`` instead.
    """
    astrometrics = get_astrometrics()
    if not astrometrics:
        return {"status": "error", "message": "Astrometrics high-level interface not available."}

    import fcntl

    library_path = astrometrics.config.get_library_path()
    lock_dir = os.path.join(str(library_path), "locks")

    devices = ["mount", "camera", "focuser"]
    results = {}

    for device in devices:
        lock_path = os.path.join(lock_dir, f"{device}.lock")
        if not os.path.exists(lock_path):
            results[device] = {
                "status": "UNLOCKED",
                "lock_file_exists": False,
                "pid": None,
                "process_name": None,
                "command_line": None,
            }
            continue

        is_locked = False
        try:
            fh = open(lock_path)
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fh, fcntl.LOCK_UN)
            except BlockingIOError:
                is_locked = True
            finally:
                fh.close()
        except Exception:
            is_locked = True

        if not is_locked:
            results[device] = {
                "status": "UNLOCKED",
                "lock_file_exists": True,
                "pid": None,
                "process_name": None,
                "command_line": None,
            }
            continue

        holding_pid = None
        try:
            target_inode = os.stat(lock_path).st_ino
            if os.path.exists("/proc/locks"):
                with open("/proc/locks") as f_locks:
                    for line in f_locks:
                        parts = line.strip().split()
                        if len(parts) >= 6:
                            lock_dev_ino = parts[5].split(":")
                            if len(lock_dev_ino) == 3:
                                try:
                                    ino = int(lock_dev_ino[2])
                                    if ino == target_inode:
                                        holding_pid = int(parts[4])
                                        break
                                except ValueError, IndexError:
                                    continue
        except Exception as exc:
            logger.debug("Failed to inspect /proc/locks for lock holder: %s", exc)

        process_name = None
        command_line = None

        if holding_pid:
            try:
                comm_path = f"/proc/{holding_pid}/comm"
                if os.path.exists(comm_path):
                    with open(comm_path) as f_comm:
                        process_name = f_comm.read().strip()
                cmd_path = f"/proc/{holding_pid}/cmdline"
                if os.path.exists(cmd_path):
                    with open(cmd_path) as f_cmd:
                        cmd_raw = f_cmd.read()
                        command_line = cmd_raw.replace("\x00", " ").strip()
            except Exception as exc:
                logger.debug("Failed to read holding process's command line: %s", exc)

        results[device] = {
            "status": "LOCKED",
            "lock_file_exists": True,
            "pid": holding_pid,
            "process_name": process_name,
            "command_line": command_line,
        }

    return results


@registry.register(
    name="backend_evaluate_spectroscopy_script",
    description=(
        "Run the test_analysis.py script in headless mode using standard CLI positional arguments and "
        "return stdout, runtime, and the generated PNG visual verification path."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "custom_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of absolute paths to custom FITS files to analyze.",
            },
            "vega_fits": {
                "type": "string",
                "description": "Optional absolute path to alternative Vega calibration stacked FITS.",
            },
        },
    },
)
async def evaluate_spectroscopy_script(  # ruff: ignore[unused-async] -- awaited by ToolRegistry.execute  # ruff: ignore[missing-return-type-undocumented-public-function]
    custom_files: list | None = None, vega_fits: str | None = None
):
    """Run test_analysis.py as a subprocess and report its outcome.

    Executes the script with the active Python virtualenv
    interpreter, monitoring runtime, exceptions, and the generated
    output PNG path.

    Parameters
    ----------
    custom_files : `list`, optional
        Absolute paths to custom FITS files to analyze.
    vega_fits : `str`, optional
        Absolute path to an alternative Vega calibration stacked
        FITS file.

    Returns
    -------
    result : `dict`
        On completion, includes ``"status"``, ``"returncode"``,
        ``"duration_seconds"``, ``"stdout"``, ``"stderr"``,
        ``"output_png_path"``, and ``"output_png_exists"``. On
        timeout or unexpected failure, includes ``"status"`` set to
        ``"error"`` and ``"message"``.
    """
    import subprocess
    import time

    astrometrics = get_astrometrics()
    repo_root = str(astrometrics.config.get_project_root()) if astrometrics else os.getcwd()

    python_bin = os.path.join(repo_root, ".venv", "bin", "python")
    script_path = os.path.join(repo_root, "backend", "test_analysis.py")

    if not os.path.exists(python_bin):
        python_bin = "python"  # Fallback to system python

    cmd = [python_bin, script_path, "--headless"]
    if vega_fits:
        cmd.extend(["--vega-fits", vega_fits])
    if custom_files:
        cmd.extend(custom_files)

    env = os.environ.copy()
    env["HEADLESS"] = "1"

    start_time = time.time()
    try:
        res = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, env=env, timeout=45)
        duration = time.time() - start_time

        output_png = os.path.join(repo_root, "scratch", "test_analysis_output.png")
        png_exists = os.path.exists(output_png)

        return {
            "status": "success" if res.returncode == 0 else "failed",
            "returncode": res.returncode,
            "duration_seconds": round(duration, 2),
            "stdout": res.stdout,
            "stderr": res.stderr,
            "output_png_path": output_png if png_exists else None,
            "output_png_exists": png_exists,
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Spectroscopy evaluation script timed out after 45 seconds."}
    except Exception as e:
        return {"status": "error", "message": f"Execution failed: {e!s}"}
