"""The Command Console's runs: running code, stopping it and its history.

`ConsoleRunMixin` runs the user's or an agent's code in the console's
workspace, returns what it printed, its result and its figures, stops a run
on Ctrl+C, and keeps the last `RECENT_RUN_LIMIT` finished runs for
`terminal:get_recent_runs`. `ScriptingService` takes these methods from it.
"""

import code
import contextlib
import io
import json
import logging
import threading
import time
import traceback
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from astrometricslib.exceptions import InvalidArgumentError
from backend.services.infrastructure.agent_code_policy import check_agent_code
from backend.services.infrastructure.console_helpers import ConsoleRuns

logger = logging.getLogger(__name__)

#: How many finished console runs the service remembers. A run that falls
#: off the end loses its saved figure files too.
RECENT_RUN_LIMIT = 20

#: The most characters of printed text (stdout or stderr) kept for one run.
#: A longer text is cut and a note says so.
RECENT_RUN_TEXT_LIMIT = 20_000

#: Run sources that start in a Command Console window. That window opens
#: the run's figures from the reply, so the app does not broadcast them.
CONSOLE_RUN_SOURCES = frozenset({"repl", "editor"})

#: The longest label kept for a run, in characters.
RECENT_RUN_LABEL_LIMIT = 80

#: The message a stopped run ends with.
STOPPED_MESSAGE = "Stopped."


def _cut_text(text: str) -> str:
    """Cut printed text to `RECENT_RUN_TEXT_LIMIT` characters.

    Parameters
    ----------
    text : `str`
        The printed text.

    Returns
    -------
    kept : `str`
        The text itself, or its first characters followed by a note that
        says how many characters were cut.
    """
    if len(text) <= RECENT_RUN_TEXT_LIMIT:
        return text
    cut_count = len(text) - RECENT_RUN_TEXT_LIMIT
    return f"{text[:RECENT_RUN_TEXT_LIMIT]}\n... [{cut_count} more characters were cut]"


def _without_interrupt_traceback(text: str) -> str:
    """Drop the ``KeyboardInterrupt`` traceback a stopped run printed last.

    Parameters
    ----------
    text : `str`
        The run's error output.

    Returns
    -------
    text : `str`
        The output without that last traceback; other output is kept.
    """
    if not text.rstrip().endswith("KeyboardInterrupt"):
        return text
    start = text.rfind("Traceback (most recent call last):")
    return text[:start] if start != -1 else text


class ConsoleRunMixin:
    """Run code in the console and remember the finished runs.

    The class that uses it sets `container`, `console`, `_runs`,
    `_recent_runs` and `_recent_runs_lock`, and gives `_agent_scope`,
    `get_workspace_manifest`, `_capture_changed_figures` and
    `_broadcast_figures`.
    """

    container: Any
    console: code.InteractiveConsole
    _runs: ConsoleRuns
    _recent_runs: deque[dict[str, Any]]
    _recent_runs_lock: threading.Lock

    def stop_runs(self) -> dict[str, Any]:
        """Stop the console code that is running now (Ctrl+C in the terminal).

        The code stops at its next Python step with `KeyboardInterrupt`; a
        long call inside a C library (one long ``time.sleep``, a large NumPy
        sum) ends first. Threads the code started itself are not stopped.
        The workspace keeps every variable set before the stop.

        Returns
        -------
        result : `dict`
            ``stopped``: the labels of the runs that were stopped (empty when
            nothing was running).
        """
        return self._runs.stop()

    def execute(self, code_str: str) -> str:
        """Execute code in the interactive console.

        Parameters
        ----------
        code_str : `str`
            Python code to run.

        Returns
        -------
        output : `str`
            The captured stdout/stderr produced by `code_str`.
        """
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            try:
                if "\n" in code_str.strip():
                    exec(code_str, self.console.locals)  # ruff: ignore[exec-builtin]
                else:
                    self.console.push(code_str)
            except Exception:
                # The error is in the user's own code. Its traceback goes to
                # the console output, where the user reads it; the server log
                # keeps a copy at debug level.
                traceback.print_exc()
                logger.debug("Console code raised an error.", exc_info=True)
        return buffer.getvalue()

    def execute_structured(
        self, code_str: str, source: str = "repl", label: str | None = None
    ) -> dict[str, Any]:
        """Execute code and return a structured result envelope.

        Parameters
        ----------
        code_str : `str`
            The Python code snippet to execute.
        source : `str`, optional
            The originator of the execution ('repl', 'editor', 'agent').
            Defaults to 'repl'.
        label : `str`, optional
            A short name for the run, such as the script's file name. When
            missing, the first line of the code is used.

        Returns
        -------
        envelope : `dict[str, Any]`
            Contains status (``"success"``, ``"error"``, or ``"stopped"``
            when Ctrl+C stopped it), stdout, stderr, result, plots, the
            updated workspace manifest, and ``app_requests``: what the code
            asked the app to show with ``show`` and ``doc``, for the window
            that ran it. A stopped run's output ends with "Stopped.". Only
            figures that are new in this run, or got new parts in it, are
            returned. Runs that do not
            come from a console (see `CONSOLE_RUN_SOURCES`) also broadcast
            ``terminal:plot_generated`` to the open windows. Agent code
            that breaks the rules in `agent_code_policy` is not run and
            comes back as an error.

        Raises
        ------
        PermissionError
            Raised inside the run when agent code breaks the policy; it is
            caught and reported in the envelope, not passed to the caller.
        """
        start_time = time.time()
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        plots: list[str] = []
        interactive_plots: list[dict[str, Any]] = []
        result: Any = None
        status = "success"

        # Broadcast event to UI if execution is triggered by an agent
        # or external source
        if source == "agent" and self.container and hasattr(self.container, "socket_manager"):
            self.container.socket_manager.broadcast_ui_event_sync(
                "terminal:agent_exec",
                {"code": code_str, "timestamp": time.time()},
            )

        # Code from an agent may only use the public astrometrics libraries
        # (see agent_code_policy) and runs in a reduced scope, so the app's
        # internal services are not reachable by name.
        refusal = check_agent_code(code_str) if source == "agent" else None
        scope = self._agent_scope() if source == "agent" else self.console.locals

        stripped_code = code_str.strip()
        run_label = label or (stripped_code.splitlines()[0][:RECENT_RUN_LABEL_LIMIT] if stripped_code else "")
        run = self._runs.begin(run_label, source)
        try:
            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                try:
                    plt = self.console.locals.get("plt")

                    if refusal:
                        raise PermissionError(refusal)
                    if source == "agent":
                        exec(code_str, scope)  # ruff: ignore[exec-builtin]
                    elif "\n" in code_str.strip():
                        exec(code_str, scope)  # ruff: ignore[exec-builtin]
                    else:
                        self.console.push(code_str)

                    if "result" in scope:
                        raw_res = scope["result"]
                        try:
                            json.dumps(raw_res)
                            result = raw_res
                        except TypeError, OverflowError:
                            result = str(raw_res)

                    if plt:
                        self._capture_changed_figures(plt, plots, interactive_plots)

                except KeyboardInterrupt:
                    # Ctrl+C in the terminal stopped the run (see
                    # `stop_runs`). It is not an error in the code.
                    status = "stopped"
                except Exception:
                    # The error is in the caller's own code. It is recorded
                    # as the run's status; its traceback is returned as
                    # stderr.
                    status = "error"
                    traceback.print_exc(file=stderr_buf)
                    logger.debug("Console code raised an error.", exc_info=True)

        except KeyboardInterrupt:
            # The stop arrived just after the code ended.
            status = "stopped"
        finally:
            # Cancels a stop that is still waiting, so it cannot hit the
            # thread's next task.
            self._runs.finish(run)

        stdout_text = stdout_buf.getvalue()
        stderr_text = stderr_buf.getvalue()
        if status == "success" and run.stop_asked and stderr_text.rstrip().endswith("KeyboardInterrupt"):
            # A single line runs through the interactive console, which
            # catches the stop itself and prints its traceback.
            status = "stopped"
        if status == "stopped":
            stderr_text = _without_interrupt_traceback(stderr_text)
            if stdout_text and not stdout_text.endswith("\n"):
                stdout_text += "\n"
            stdout_text += STOPPED_MESSAGE + "\n"

        execution_time_ms = int((time.time() - start_time) * 1000)

        self._remember_run(
            code_str=code_str,
            source=source,
            label=label,
            status=status,
            stdout=stdout_text,
            stderr=stderr_text,
            plot_paths=plots,
            execution_time_ms=execution_time_ms,
        )

        # The console that started a run opens the figures from the run's
        # reply, so it is not told again. Other windows are told about runs
        # that did not come from a console.
        if source not in CONSOLE_RUN_SOURCES:
            self._broadcast_figures(plots, interactive_plots)

        return {
            "status": status,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "result": result,
            "plots": plots,
            "interactive_plots": interactive_plots,
            "execution_time_ms": execution_time_ms,
            "workspace": self.get_workspace_manifest(),
            "app_requests": run.app_requests,
        }

    def _remember_run(
        self,
        *,
        code_str: str,
        source: str,
        label: str | None,
        status: str,
        stdout: str,
        stderr: str,
        plot_paths: list[str],
        execution_time_ms: int,
    ) -> None:
        """Add one finished run to the history and drop the oldest extra runs.

        Printed text is cut to `RECENT_RUN_TEXT_LIMIT` characters, with a
        note. A run that leaves the history has its figure files deleted.
        Workspace variables are not kept.

        Parameters
        ----------
        code_str : `str`
            The code that ran.
        source : `str`
            Who ran it ('repl', 'editor' or 'agent').
        label : `str` or `None`
            A short name, or `None` to use the first line of the code.
        status : `str`
            'success', 'error' or 'stopped'.
        stdout, stderr : `str`
            The printed text and the error text.
        plot_paths : `list` [`str`]
            The PNG file of every figure the run made.
        execution_time_ms : `int`
            How long the run took.
        """
        first_line = next((line.strip() for line in code_str.splitlines() if line.strip()), "")
        short_label = (label or first_line or "(empty)").strip()
        if len(short_label) > RECENT_RUN_LABEL_LIMIT:
            short_label = short_label[: RECENT_RUN_LABEL_LIMIT - 3] + "..."
        entry = {
            "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "label": short_label,
            "source": source,
            "source_code": code_str,
            "status": status,
            "stdout": _cut_text(stdout),
            "stderr": _cut_text(stderr),
            "plot_paths": list(dict.fromkeys(plot_paths)),
            "execution_time_ms": execution_time_ms,
        }
        dropped: list[dict[str, Any]] = []
        with self._recent_runs_lock:
            self._recent_runs.appendleft(entry)
            while len(self._recent_runs) > RECENT_RUN_LIMIT:
                dropped.append(self._recent_runs.pop())
        for old_entry in dropped:
            for plot_path in old_entry["plot_paths"]:
                try:
                    Path(plot_path).unlink(missing_ok=True)
                except OSError:
                    logger.debug("Could not delete the old figure file %s.", plot_path, exc_info=True)

    def get_recent_runs(self, limit: int = 1) -> list[dict[str, Any]]:
        """Return the most recent console runs, newest first.

        Parameters
        ----------
        limit : `int`, optional
            How many runs to return, from 1 to `RECENT_RUN_LIMIT`.
            Defaults to 1.

        Returns
        -------
        runs : `list` [`dict` [`str`, `Any`]]
            For each run: ``ran_at`` (ISO time), ``label``, ``source``,
            ``source_code``, ``status``, ``stdout``, ``stderr``,
            ``plot_paths`` (PNG files, one per figure) and
            ``execution_time_ms``.

        Raises
        ------
        InvalidArgumentError
            If ``limit`` is not a whole number from 1 to `RECENT_RUN_LIMIT`.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= RECENT_RUN_LIMIT:
            raise InvalidArgumentError(
                f"limit must be a whole number from 1 to {RECENT_RUN_LIMIT}. Got {limit!r}."
            )
        with self._recent_runs_lock:
            return [dict(entry) for entry in list(self._recent_runs)[:limit]]

    def run_repl_command(self, command: str) -> str:
        """Run `command` through the interactive console and return output.

        Parameters
        ----------
        command : `str`
            The command string to evaluate.

        Returns
        -------
        output : `str`
            The captured stdout/stderr produced by `command`.
        """
        return self.execute(command)
