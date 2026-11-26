"""Interactive Python scripting environment for the application.

The Command Console runs the user's Python here. Its workspace holds the
same public objects a script or notebook would use: ``astrometrics`` (the
`astrometricslib.Astrometrics` object), ``wayfinder`` (the
`wayfindinglib.Wayfinder` object), ``np``, ``plt`` and ``fits``. The app's
internal services are not in it. The module also gives workspace lists, code
completions, figure exports, script recipes (from the tutorial notebooks and
scripts in ``documentation/notebooks/``), documentation and run history.
"""

import code
import contextlib
import inspect
import io
import json
import logging
import os
import rlcompleter
import sys
import tempfile
import textwrap
import threading
import time
import traceback
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from astrometricslib.drivers import scratch_directory
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from backend.services.infrastructure.agent_code_policy import AGENT_VISIBLE_NAMES, check_agent_code
from backend.services.infrastructure.app_selection_service import AppSelection
from backend.services.infrastructure.console_helpers import (
    ConsoleRuns,
    ConsoleSelection,
    describe_show_request,
    lookup_doc,
    make_show_request,
)

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

#: The objects the console preloads, with the line ``help`` prints for each.
#: They are the public entry points a library user has in a script or a
#: notebook. The app's internal services are deliberately not among them.
PRELOADED_OBJECTS = {
    "astrometrics": "Astrometrics: targets, stars, processing, quality and visualization",
    "wayfinder": "Wayfinder: telescope control, planning, execution and review",
    "np": "NumPy",
    "plt": "Matplotlib pyplot (figures open in their own windows)",
    "fits": "astropy.io.fits, for reading FITS files",
    "selection": (
        "What is picked in Image Processing now (read-only): target, path, kind (stack or frame) "
        "and star (the Astrometry overlay pick: name, ra and dec in degrees, x and y as the 0-based "
        "pixel column and row, as NumPy counts)"
    ),
    "show": 'show(target="M 31", star=..., path=...) opens it in Image Processing or the Astronomy Manager',
    "doc": "doc(name) opens the name's entry in the Documentation tab; also typed as: doc name",
}

#: The commands the terminal itself handles. They are typed alone on a line
#: and never reach Python, so a variable with the same name still works.
TERMINAL_COMMANDS = {
    "clear": "clears the terminal screen",
    "clear('workspace')": "clears your variables (the preloaded names stay)",
}

#: The width ``help`` wraps its two-column list to, in characters.
HELP_LINE_WIDTH = 78

#: The width of the name column of ``help``'s list, in characters.
HELP_NAME_WIDTH = 20

#: The message a stopped run ends with.
STOPPED_MESSAGE = "Stopped."

#: Names the Workspace table leaves out: the preloaded objects and the help
#: functions. The app shows the preloaded objects in its own pinned rows.
HIDDEN_WORKSPACE_NAMES = frozenset({*PRELOADED_OBJECTS, "help", "list_commands", "inspect_api"})

#: The longest docstring ``help(obj)`` prints, in characters.
HELP_TEXT_LIMIT = 6000

#: The folder that holds the tutorial notebooks and the recipe scripts.
NOTEBOOKS_ROOT = Path(__file__).resolve().parents[3] / "documentation" / "notebooks"


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


def inspect_api(obj_or_path: Any) -> dict[str, Any]:
    """Return API introspection details for an object or dotted path.

    Parameters
    ----------
    obj_or_path : `Any`
        An object, function, class, or module to inspect.

    Returns
    -------
    info : `dict[str, Any]`
        Structured dictionary containing name, docstring, type, and
        callable method signatures.

    Raises
    ------
    InvalidArgumentError
        If ``obj_or_path`` is ``None``.
    """
    if obj_or_path is None:
        raise InvalidArgumentError("There is no object to inspect. Pass an object, not None.")

    obj = obj_or_path
    obj_name = getattr(obj, "__name__", str(obj))
    doc = inspect.getdoc(obj) or ""
    summary = doc.split("\n\n")[0] if "\n\n" in doc else doc

    methods: list[dict[str, Any]] = []
    if not callable(obj):
        for attr_name in sorted(dir(obj)):
            if attr_name.startswith("_"):
                continue
            try:
                attr = getattr(obj, attr_name)
            except Exception:
                # A property can run any code, so any error is possible.
                # Listing the API must go on, so the error is only logged.
                logger.debug("Attribute '%s' inaccessible during inspect_api.", attr_name, exc_info=True)
                continue
            if callable(attr):
                method_doc = inspect.getdoc(attr) or ""
                method_summary = method_doc.split("\n\n")[0] if "\n\n" in method_doc else method_doc
                sig_str = "(*args, **kwargs)"
                try:
                    sig_str = str(inspect.signature(attr))
                except (ValueError, TypeError) as exc:
                    logger.debug("Signature inaccessible for '%s': %s", attr_name, exc)
                methods.append({
                    "name": attr_name,
                    "signature": sig_str,
                    "summary": method_summary[:120],
                })

    sig = ""
    if callable(obj):
        try:
            sig = str(inspect.signature(obj))
        except ValueError, TypeError:
            sig = "(*args, **kwargs)"

    return {
        "name": obj_name,
        "type": type(obj).__name__,
        "signature": sig,
        "summary": summary[:200],
        "methods": methods,
    }


def public_help_text(obj: Any) -> str:
    """Write the help that ``help(obj)`` prints in the console.

    It gives the object's signature and docstring, then each public method
    or attribute with the first line of its docstring. Names that start
    with ``_`` are private and are left out, so the text shows only the
    public API.

    Parameters
    ----------
    obj : `Any`
        The object, class, function or module to describe.

    Returns
    -------
    text : `str`
        The help text.
    """
    info = inspect_api(obj)
    doc = inspect.getdoc(obj) or "(no description)"
    if len(doc) > HELP_TEXT_LIMIT:
        doc = doc[:HELP_TEXT_LIMIT] + "\n... (cut; see the Documentation tab for the rest)"
    lines = [f"{info['name']}{info['signature']}  [{info['type']}]", "", doc]
    if info["methods"]:
        lines += ["", "Public methods:"]
        for method in info["methods"]:
            first_line = method["summary"].splitlines()[0] if method["summary"] else ""
            lines.append(f"  {method['name']}{method['signature']}")
            if first_line:
                lines.append(f"      {first_line}")
    if not callable(obj) and not inspect.ismodule(obj):
        attributes = [
            name
            for name in sorted(dir(obj))
            if not name.startswith("_") and name not in {m["name"] for m in info["methods"]}
        ]
        if attributes:
            lines += ["", "Public attributes: " + ", ".join(attributes)]
    return "\n".join(lines)


def _notebook_recipe_code(notebook_path: Path) -> tuple[str, str, str]:
    """Turn a tutorial notebook into a console script.

    The code cells become the script, in order. Each text cell becomes a
    block of ``#`` comments above the code it explains. A line that makes a
    new ``Astrometrics()`` or ``Wayfinder()`` object is turned into a
    comment, because the console already holds the app's own objects under
    those names; a second copy would open the library and the observatory
    again.

    Parameters
    ----------
    notebook_path : `Path`
        The ``.ipynb`` file.

    Returns
    -------
    title : `str`
        The notebook's first heading, or its file name.
    description : `str`
        The first paragraph after that heading.
    code : `str`
        The script.

    Raises
    ------
    ValueError
        If the file is not a notebook.
    """
    notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise ValueError(f"{notebook_path.name} has no cells.")
    title = notebook_path.stem.replace("_", " ")
    description = ""
    found_heading = False
    script_lines = [
        f'"""Recipe made from the tutorial notebook {notebook_path.relative_to(NOTEBOOKS_ROOT).as_posix()}.',
        "",
        "The console already holds `astrometrics` and `wayfinder`, so the",
        "notebook's lines that make new ones are comments here.",
        '"""',
        "",
    ]
    for cell in cells:
        source = cell.get("source", "")
        text = "".join(source) if isinstance(source, list) else str(source)
        if cell.get("cell_type") == "markdown":
            paragraphs = [part.strip() for part in text.strip().split("\n\n") if part.strip()]
            if paragraphs and paragraphs[0].startswith("#") and not found_heading:
                found_heading = True
                title = paragraphs[0].splitlines()[0].lstrip("#").strip()
                if len(paragraphs) > 1:
                    description = " ".join(paragraphs[1].split())
            script_lines += [f"# {line}".rstrip() for line in text.strip().splitlines()]
            script_lines.append("")
        elif cell.get("cell_type") == "code":
            for line in text.splitlines():
                stripped = line.strip()
                if stripped in ("astrometrics = Astrometrics()", "wayfinder = Wayfinder()"):
                    indent = line[: len(line) - len(line.lstrip())]
                    line = f"{indent}# {stripped}  (the console already has this object)"
                script_lines.append(line)
            script_lines.append("")
    return title, description, "\n".join(script_lines).rstrip() + "\n"


def _two_column_lines(name: str, description: str) -> list[str]:
    """Write one row of ``help``'s list, wrapped under its description.

    Parameters
    ----------
    name : `str`
        The left column, such as ``"astrometrics"``.
    description : `str`
        The right column.

    Returns
    -------
    lines : `list` of `str`
        The row; lines after the first start under the description.
    """
    indent = " " * (HELP_NAME_WIDTH + 2)
    first_indent = f"  {name:<{HELP_NAME_WIDTH}}"
    if len(first_indent) > len(indent):
        # A long name gets its own line; the description starts under it.
        return [
            first_indent.rstrip(),
            *textwrap.wrap(description, HELP_LINE_WIDTH, initial_indent=indent, subsequent_indent=indent),
        ]
    return textwrap.wrap(description, HELP_LINE_WIDTH, initial_indent=first_indent, subsequent_indent=indent)


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


class ScriptingService:
    """Manage an interactive Python console over the public libraries."""

    class SmartPrinter:
        """Callable/reprable wrapper that lazily renders help text.

        Deferring text generation to `__repr__`/`__call__` lets the
        interactive console print up-to-date help (e.g. typing
        ``help`` with no parentheses) without eagerly computing it.
        """

        def __init__(self, text_factory: Any) -> None:
            self.text_factory = text_factory

        def __repr__(self) -> str:
            """Return the rendered text so bare-name repl echo works.

            Returns
            -------
            text : `str`
                The freshly generated help text.
            """
            return self.text_factory()

        def __call__(self, obj: Any = None) -> None:
            """Print the help text, or the public help of one object.

            Parameters
            ----------
            obj : `Any`, optional
                The object to describe. With none, the list of preloaded
                objects is printed.
            """
            if obj is None:
                print(self.text_factory())
            else:
                print(public_help_text(obj))

    def __init__(self, container: Any = None) -> None:
        """Initialize the interactive scripting service.

        Parameters
        ----------
        container : `Any`, optional
            Application service container exposing domain and
            infrastructure services.
        """
        self.container = container
        # The runs going now: Ctrl+C in the terminal stops them, and show()
        # and doc() add their requests to the app to them.
        self._runs = ConsoleRuns()
        self.console = code.InteractiveConsole(locals=self._get_locals())
        self.completer = rlcompleter.Completer(self.console.locals)
        self.active_editor_code = ""
        self.active_figure_managers: dict[int, Any] = {}
        # What each figure looked like when a run last showed it.
        self._figure_signatures: dict[int, tuple[Any, ...]] = {}
        self._recent_runs: deque[dict[str, Any]] = deque()
        self._recent_runs_lock = threading.Lock()

    def reset_workspace(self) -> list[dict[str, Any]]:
        """Discard every console-local variable and start a fresh console.

        Rebuilds `console` and `completer` from scratch, so any
        variables the user defined or objects loaded into scope
        (via `execute`) are dropped. The preloaded objects
        (`astrometrics`, `wayfinder`, `np`, `plt`, `fits`) are restored.

        Returns
        -------
        workspace : `list` of `dict`
            The workspace manifest for the freshly reset console,
            same shape as `get_workspace_manifest`.
        """
        self.console = code.InteractiveConsole(locals=self._get_locals())
        self.completer = rlcompleter.Completer(self.console.locals)
        return self.get_workspace_manifest()

    def get_figure_manager(self, fignum: int) -> Any:
        """Return the WebAgg figure manager for the given figure number.

        Parameters
        ----------
        fignum : `int`
            Figure number.

        Returns
        -------
        manager : `Any` or `None`
            The active FigureManagerWebAgg instance if present.
        """
        if fignum in self.active_figure_managers:
            return self.active_figure_managers[fignum]

        try:
            from matplotlib._pylab_helpers import Gcf

            mgr = Gcf.get_fig_manager(fignum)
            if mgr:
                if mgr.toolbar is None:
                    import matplotlib.backends.backend_webagg_core as webagg_core

                    mgr.toolbar = webagg_core.NavigationToolbar2WebAgg(mgr.canvas)
                    mgr.canvas.toolbar = mgr.toolbar
                self.active_figure_managers[fignum] = mgr
            return mgr
        except ImportError, ValueError, TypeError, RuntimeError:
            return None

    def get_completions(self, text: str) -> list[str]:
        """Return a list of possible completions for the given text.

        Parameters
        ----------
        text : `str`
            The input prefix to complete.

        Returns
        -------
        results : `list` of `str`
            The candidate completions for `text`.
        """
        results = []
        state = 0
        while True:
            match = self.completer.complete(text, state)
            if match is None:
                break
            results.append(match)
            state += 1
        return results

    def _get_locals(self) -> dict[str, Any]:
        """Return the dictionary of objects available in the script scope.

        The console offers what a script or notebook that uses the libraries
        would have: the two public library objects (``astrometrics`` and
        ``wayfinder``), NumPy (``np``), Matplotlib (``plt``) and Astropy's
        FITS module (``fits``), plus ``help``, ``inspect_api`` and ``doc``.
        The app's own services are not in the scope (see
        `PRELOADED_OBJECTS`).

        Returns
        -------
        local_scope : `dict[str, Any]`
            The names and objects to expose in the console.
        """

        def get_help_text() -> str:
            return self._generate_help_text()

        help_obj = self.SmartPrinter(get_help_text)

        local_scope: dict[str, Any] = {
            "help": help_obj,
            "list_commands": help_obj,
            "inspect_api": inspect_api,
            "doc": self._doc,
            "show": self._show,
            "selection": ConsoleSelection(self._current_selection),
        }

        try:
            import numpy as np

            local_scope["np"] = np
        except ImportError:
            pass

        try:
            import matplotlib

            # Configure the interactive WebAggCore backend. Unlike GUI backends
            # (Tk/Qt) which crash multi-threaded servers, WebAgg renders to
            # an HTML5 canvas and streams interactive events (pan/zoom/coords)
            # over WebSockets to the client.
            matplotlib.use("module://matplotlib.backends.backend_webagg_core")
            import matplotlib.backends.backend_webagg_core as webagg_core

            webagg_core.FigureManagerWebAgg._toolbar2_class = webagg_core.NavigationToolbar2WebAgg

            import matplotlib.pyplot as plt

            local_scope["plt"] = plt
        except ImportError:
            pass

        try:
            from astropy.io import fits

            local_scope["fits"] = fits
        except ImportError:
            pass

        if not self.container:
            return local_scope

        # Only the libraries' public objects. The app's services (targets,
        # telescope, jobs, ...) are left out: a script written here must also
        # run in a notebook, where only the public libraries exist.
        for name in ("astrometrics", "wayfinder"):
            value = getattr(self.container, name, None)
            if value is not None:
                local_scope[name] = value
        return local_scope

    def _generate_help_text(self) -> str:
        """Generate the help text that ``help`` prints with no argument.

        Returns
        -------
        help_text : `str`
            What each preloaded object is, then the names the user made.
        """
        current_locals = self.console.locals if hasattr(self, "console") else dict.fromkeys(PRELOADED_OBJECTS)
        lines = ["Preloaded objects:"]
        for name, description in PRELOADED_OBJECTS.items():
            if name in current_locals:
                lines.extend(_two_column_lines(name, description))
        lines.append("\nTerminal commands (typed alone on a line):")
        for name, description in TERMINAL_COMMANDS.items():
            lines.extend(_two_column_lines(name, description))
        own_names = sorted(
            name for name in current_locals if not name.startswith("_") and name not in HIDDEN_WORKSPACE_NAMES
        )
        if own_names:
            lines.append("\nYour variables:")
            lines.extend(f"  {name}" for name in own_names)
        lines.append("")
        lines.extend(
            textwrap.wrap(
                "help(obj) prints an object's description and public methods. Tab completes names. "
                "inspect_api(obj) gives the same as data. Ctrl+C in the terminal stops a run.",
                HELP_LINE_WIDTH,
            )
        )
        lines.append(
            'Example: astrometrics.visualization.render_fits("/path/to/stack.fits", kind="figure", '
            'overlays=["detections", "stars"])'
        )
        return "\n".join(lines)

    def _current_selection(self) -> AppSelection:
        """Return what is picked in Image Processing now.

        Returns
        -------
        selection : `AppSelection`
            The latest selection, or an empty one when the console has no
            app container.
        """
        service = getattr(self.container, "app_selection_service", None) if self.container else None
        return service.current() if service is not None else AppSelection()

    def _find_star(self, text: str) -> tuple[str, str] | None:
        """Look a star up in the library by its name or id.

        Parameters
        ----------
        text : `str`
            Such as ``"Vega"``.

        Returns
        -------
        star : `tuple` of `str` or `None`
            The star's library id and name, or `None` when it is not there.
        """
        astrometrics = getattr(self.container, "astrometrics", None) if self.container else None
        if astrometrics is None:
            return None
        star = astrometrics.stars.get(text)
        if star is None:
            return None
        return star.id, star.name or star.id

    def _show(
        self,
        target: str | None = None,
        *,
        star: str | None = None,
        path: str | None = None,
        display: str | None = None,
    ) -> None:
        """Open a target, star or file in the app (the console's ``show``).

        With no ``display``, a target or a file opens in Image Processing (a
        star given with a target is selected on the target's stack) and a
        star alone opens in the Astronomy Manager. When another window
        already shows that display, that window shows it; otherwise this
        window switches to it. The app does it when the run ends.

        Parameters
        ----------
        target : `str`, optional
            The target, such as ``"M 31"``.
        star : `str`, optional
            A star's name or library id, such as ``"Vega"``.
        path : `str`, optional
            A stack or frame to show in Image Processing. With no
            ``target``, the target picked in Image Processing is used.
        display : `str`, optional
            ``"Image Processing"`` or ``"Astronomy Manager"``.

        Notes
        -----
        `make_show_request` raises `TypeError` when no target, star or path
        is given, and `ValueError` when the arguments do not fit together
        or the star is not in the library. The user reads it in the
        terminal like any other error in their code.
        """
        request = make_show_request(
            target=target,
            star=star,
            path=path,
            display=display,
            selection=self._current_selection(),
            find_star=self._find_star,
        )
        if self._runs.add_app_request(request):
            print(describe_show_request(request))
        else:
            print("show() opens things only from a Command Console run.")

    def _doc(self, name: Any) -> None:
        """Open a name's reference entry in the Documentation tab (``doc``).

        Parameters
        ----------
        name : `Any`
            A name such as ``"delete_images"``,
            ``"TargetCatalog.delete_images"`` or
            ``"astrometrics.targets.delete_images"``, or the object itself.
            When a bare method name fits several classes, the full names
            are listed and nothing opens.
        """
        from backend.services.infrastructure import api_doc_generator

        find_entries = getattr(api_doc_generator, "find_doc_entries", None)
        if find_entries is None:
            print("The documentation index is not available.")
            return
        lookup = lookup_doc(name, self.console.locals, find_entries)
        shown_name = name if isinstance(name, str) else getattr(name, "__name__", repr(name))
        if not lookup.matches:
            line = f"No documentation found for {shown_name}."
            if lookup.suggestions:
                line += " Close names: " + ", ".join(lookup.suggestions) + "."
            print(line)
            return
        if len(lookup.matches) > 1:
            print(f"{shown_name} is documented in {len(lookup.matches)} places. Use the full name:")
            for number, entry in enumerate(lookup.matches, start=1):
                print(f"  {number}. doc {entry['qualified_name']}")
            return
        entry = lookup.matches[0]
        request = {
            "kind": "doc",
            "topic_id": entry["topic_id"],
            "anchor_id": entry["anchor_id"],
            "qualified_name": entry["qualified_name"],
        }
        if self._runs.add_app_request(request):
            print(f"Opening {entry['qualified_name']} in the Documentation tab.")
        else:
            print(f"{entry['qualified_name']} is in the documentation topic {entry['topic_id']}.")

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

    def _default_locals_keys(self) -> list[str]:
        """Return the names the console preloads.

        Returns
        -------
        keys : `list` of `str`
            The preloaded names, helpers included.
        """
        return sorted(HIDDEN_WORKSPACE_NAMES)

    def help(self) -> None:
        """Print available commands and objects."""
        print(self._generate_help_text())

    def list_commands(self) -> None:
        """List available objects in the scripting environment."""
        print(self._generate_help_text())

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

    def get_introspection_tree(self) -> list[dict[str, Any]]:
        """Return a tree of available objects and their methods.

        Returns
        -------
        objects : `list` of `dict`
            One entry per console-local object, describing its type,
            docstring, and callable methods.
        """
        objects = []
        local_scope = self.console.locals

        for name, obj in local_scope.items():
            if name.startswith("_"):
                continue

            obj_info = {
                "name": name,
                "type": type(obj).__name__,
                "doc": inspect.getdoc(obj) or "",
                "methods": [],
            }

            try:
                for attr_name in dir(obj):
                    if attr_name.startswith("_"):
                        continue
                    try:
                        attr = getattr(obj, attr_name)
                    except Exception:
                        # A property runs code, so it can raise anything.
                        logger.debug("Skipping unreadable attribute '%s'.", attr_name, exc_info=True)
                        continue
                    if callable(attr):
                        method_info = {"name": attr_name, "doc": inspect.getdoc(attr) or "", "args": []}
                        try:
                            sig = inspect.signature(attr)
                            method_info["args"] = [str(param) for param in sig.parameters.values()]
                        except ValueError, TypeError:
                            method_info["args"] = ["*args", "**kwargs"]
                        obj_info["methods"].append(method_info)
            except Exception:
                # Any object can sit in the console, and its dir() can raise
                # anything. One bad object must not hide all the others.
                logger.debug("Failed to introspect methods for one console object.", exc_info=True)
            objects.append(obj_info)
        return objects

    def get_workspace_manifest(self) -> list[dict[str, Any]]:
        """Return a structured manifest of active objects in console scope.

        Returns
        -------
        variables : `list` of `dict`
            List of variable summaries including name, type, shape,
            dtype, size in bytes, and human-friendly string preview. The
            preloaded objects are left out; the app lists them itself.
        """
        manifest = []
        ignored_names = HIDDEN_WORKSPACE_NAMES

        local_scope = self.console.locals
        for name, val in local_scope.items():
            if name.startswith("_") or name in ignored_names:
                continue

            type_name = type(val).__name__
            shape = getattr(val, "shape", None)
            dtype = getattr(val, "dtype", None)
            size_bytes = getattr(val, "nbytes", sys.getsizeof(val))

            summary = str(val)
            if len(summary) > 80:
                summary = summary[:77] + "..."

            manifest.append({
                "name": name,
                "type": type_name,
                "shape": str(shape) if shape is not None else None,
                "dtype": str(dtype) if dtype is not None else None,
                "size_bytes": int(size_bytes) if isinstance(size_bytes, (int, float)) else 0,
                "summary": summary,
            })
        return manifest

    def get_preloaded_objects(self) -> list[dict[str, Any]]:
        """List the public objects the console preloads, for the pinned rows.

        Returns
        -------
        objects : `list` of `dict`
            One entry per object in `PRELOADED_OBJECTS` order: ``name``,
            ``type`` (its class or module name, or `None` when it is not in
            the console), ``description`` and ``available`` (whether the
            console holds it now; ``astrometrics`` and ``wayfinder`` are
            missing when the console has no app container).
        """
        objects = []
        for name, description in PRELOADED_OBJECTS.items():
            value = self.console.locals.get(name)
            if value is None:
                type_name = None
            elif inspect.ismodule(value):
                type_name = value.__name__
            elif inspect.ismethod(value) or inspect.isfunction(value):
                type_name = "function"
            else:
                type_name = type(value).__name__
            objects.append({
                "name": name,
                "type": type_name,
                "description": description,
                "available": value is not None,
            })
        return objects

    def _agent_scope(self) -> dict[str, Any]:
        """Return the reduced variable scope that agent code runs in.

        The scope holds only the names in `AGENT_VISIBLE_NAMES` (refreshed
        from the console each time) plus whatever variables the agent
        created itself on earlier calls.

        Returns
        -------
        scope : `dict[str, Any]`
            The variables agent code can see.
        """
        if not hasattr(self, "_agent_variables"):
            self._agent_variables: dict[str, Any] = {}
        for name in AGENT_VISIBLE_NAMES:
            if name in self.console.locals:
                self._agent_variables[name] = self.console.locals[name]
        return self._agent_variables

    @staticmethod
    def _figure_signature(fig: Any) -> tuple[Any, ...]:
        """Describe a figure's make-up, to tell whether a run changed it.

        The signature holds the figure object's identity and how many parts
        each axes holds, so a new figure, or a figure with plots, labels or
        images added, differs from its earlier signature. Changing the data
        of a part that is already there is not noticed.

        Parameters
        ----------
        fig : `matplotlib.figure.Figure`
            The figure to describe.

        Returns
        -------
        signature : `tuple`
            A value that compares equal when the figure has not changed.
        """
        return (id(fig), tuple(len(axes.get_children()) for axes in fig.axes))

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

                    # Capture active figures for both interactive WebAgg
                    # display and fallback PNGs
                    if plt:
                        # Forget the figures that were closed.
                        open_numbers = set(plt.get_fignums())
                        for closed in set(self._figure_signatures) - open_numbers:
                            del self._figure_signatures[closed]
                    if plt and plt.get_fignums():
                        from matplotlib._pylab_helpers import Gcf

                        for fignum in plt.get_fignums():
                            fig = plt.figure(fignum)
                            # A figure left open by an earlier run, and not
                            # changed since, is not shown again.
                            signature = self._figure_signature(fig)
                            if self._figure_signatures.get(fignum) == signature:
                                continue
                            self._figure_signatures[fignum] = signature
                            mgr = Gcf.get_fig_manager(fignum)
                            if mgr:
                                self.active_figure_managers[fignum] = mgr
                            with tempfile.NamedTemporaryFile(
                                suffix=".png", delete=False, dir=scratch_directory()
                            ) as tmp_file:
                                fig.savefig(tmp_file.name, bbox_inches="tight", dpi=100)
                                plots.append(tmp_file.name)
                                # The interactive page draws the figure at this
                                # size, so the app opens its window to fit it.
                                width, height = fig.canvas.get_width_height()
                                interactive_plots.append({
                                    "figure_id": fignum,
                                    "plot_path": tmp_file.name,
                                    "url": f"/figure/{fignum}",
                                    "width": int(width),
                                    "height": int(height),
                                })

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
        broadcast_plots = source not in CONSOLE_RUN_SOURCES
        if not broadcast_plots:
            pass
        elif interactive_plots and self.container and hasattr(self.container, "socket_manager"):
            for item in interactive_plots:
                self.container.socket_manager.broadcast_ui_event_sync(
                    "terminal:plot_generated",
                    {
                        "plot_path": item["plot_path"],
                        "figure_id": item["figure_id"],
                        "interactive_url": item["url"],
                        "width": item["width"],
                        "height": item["height"],
                    },
                )
        elif plots and self.container and hasattr(self.container, "socket_manager"):
            for plot_path in plots:
                self.container.socket_manager.broadcast_ui_event_sync(
                    "terminal:plot_generated",
                    {"plot_path": plot_path},
                )

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

    def _get_recipe_dirs(self) -> list[Path]:
        """Return the folders that hold the recipe scripts.

        Returns
        -------
        dirs : `list` of `Path`
            The ``scripts`` folders of the tutorial notebooks.
        """
        return [
            NOTEBOOKS_ROOT / "astrometrics" / "scripts",
            NOTEBOOKS_ROOT / "wayfinding" / "control" / "scripts",
            NOTEBOOKS_ROOT / "wayfinding" / "execution" / "scripts",
        ]

    def _recipe_files(self) -> list[Path]:
        """List every recipe file: the tutorial notebooks, then the scripts.

        The notebooks are the ones a library user reads in
        ``documentation/notebooks/``, so the console's recipes and the
        notebooks show the same examples.

        Returns
        -------
        files : `list` of `Path`
            The ``.ipynb`` notebooks in sorted order, then the ``.py``
            recipe scripts.
        """
        notebooks = (
            sorted(path for path in NOTEBOOKS_ROOT.rglob("*.ipynb") if ".ipynb_checkpoints" not in path.parts)
            if NOTEBOOKS_ROOT.exists()
            else []
        )
        scripts = [
            path
            for recipe_dir in self._get_recipe_dirs()
            if recipe_dir.exists()
            for path in sorted(recipe_dir.glob("*.py"))
            if path.name != "__init__.py"
        ]
        return [*notebooks, *scripts]

    @staticmethod
    def _recipe_category(path: Path) -> str:
        """Name the library a recipe belongs to.

        Parameters
        ----------
        path : `Path`
            The recipe file.

        Returns
        -------
        category : `str`
            ``astrometrics`` or ``wayfinding``: the first folder under
            ``documentation/notebooks/``.
        """
        try:
            return path.relative_to(NOTEBOOKS_ROOT).parts[0]
        except ValueError, IndexError:
            return path.parent.name

    def list_recipes(self) -> list[dict[str, Any]]:
        """List the built-in recipes: the tutorial notebooks and scripts.

        Returns
        -------
        recipes : `list` of `dict`
            One entry per recipe with ``id``, ``name``, ``filename``,
            ``category`` (the library), ``kind`` (``notebook`` or
            ``script``), ``description`` and ``size_bytes``.
        """
        recipes = []
        for recipe_file in self._recipe_files():
            description = ""
            name = recipe_file.stem.replace("_", " ").title()
            kind = "notebook" if recipe_file.suffix == ".ipynb" else "script"
            try:
                if kind == "notebook":
                    name, description, _code = _notebook_recipe_code(recipe_file)
                else:
                    content = recipe_file.read_text(encoding="utf-8")
                    if content.startswith('"""') or content.startswith("'''"):
                        delim = '"""' if content.startswith('"""') else "'''"
                        parts = content.split(delim)
                        if len(parts) >= 2:
                            description = parts[1].strip().split("\n\n")[0]
            except (OSError, ValueError) as exc:
                logger.debug("Failed to read recipe %s: %s", recipe_file, exc)
                continue
            recipes.append({
                "id": recipe_file.stem,
                "name": name,
                "filename": recipe_file.name,
                "category": self._recipe_category(recipe_file),
                "kind": kind,
                "description": " ".join(description.split())[:160],
                "size_bytes": recipe_file.stat().st_size,
            })
        return recipes

    def get_recipe(self, recipe_id: str) -> dict[str, Any]:
        """Retrieve the code of a recipe, as a script the console can run.

        A notebook comes back as one script (see `_notebook_recipe_code`).

        Parameters
        ----------
        recipe_id : `str`
            Recipe identifier or file name.

        Returns
        -------
        data : `dict[str, Any]`
            Recipe identifier, file name (``.py`` for a notebook too) and
            code text.

        Raises
        ------
        NotFoundError
            If the requested recipe cannot be found.
        """
        for recipe_file in self._recipe_files():
            if recipe_file.stem != recipe_id and recipe_file.name != recipe_id:
                continue
            if recipe_file.suffix == ".ipynb":
                _title, _description, code_text = _notebook_recipe_code(recipe_file)
                return {"id": recipe_file.stem, "filename": f"{recipe_file.stem}.py", "code": code_text}
            return {
                "id": recipe_file.stem,
                "filename": recipe_file.name,
                "code": recipe_file.read_text(encoding="utf-8"),
            }
        raise NotFoundError(f"Recipe {recipe_id!r} not found.")

    def _get_user_scripts_dir(self) -> Path:
        """Return the folder for the user's saved scripts.

        It is ``scripts`` inside the user's data folder,
        ``~/.local/share/astrometrics/scripts`` (or under ``$XDG_DATA_HOME``
        when that is set).

        Returns
        -------
        dir_path : `Path`
            Path object pointing to user scripts directory.
        """
        data_root = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        user_dir = Path(data_root) / "astrometrics" / "scripts"
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir

    def list_user_scripts(self) -> list[dict[str, Any]]:
        """List scripts saved by the user in their scripts folder.

        Returns
        -------
        scripts : `list` of `dict`
            List of user script summaries.
        """
        user_dir = self._get_user_scripts_dir()
        scripts = []
        for py_file in sorted(user_dir.glob("*.py")):
            scripts.append({
                "id": py_file.stem,
                "name": py_file.stem.replace("_", " ").title(),
                "filename": py_file.name,
                "size_bytes": py_file.stat().st_size,
                "modified_at": py_file.stat().st_mtime,
            })
        return scripts

    def read_user_script(self, filename: str) -> dict[str, Any]:
        """Read a user script from ~/.local/share/astrometrics/scripts.

        Parameters
        ----------
        filename : `str`
            The script filename (e.g. 'my_analysis.py').

        Returns
        -------
        data : `dict[str, Any]`
            Filename and code content.

        Raises
        ------
        NotFoundError
            If the requested script does not exist.
        """
        safe_name = Path(filename).name
        if not safe_name.endswith(".py"):
            safe_name += ".py"
        script_path = self._get_user_scripts_dir() / safe_name
        if not script_path.exists():
            raise NotFoundError(f"User script {safe_name!r} not found.")

        return {
            "filename": safe_name,
            "code": script_path.read_text(encoding="utf-8"),
        }

    def save_user_script(self, filename: str, content: str) -> dict[str, Any]:
        """Save user script to ~/.local/share/astrometrics/scripts.

        Parameters
        ----------
        filename : `str`
            The script filename.
        content : `str`
            Python code to save.

        Returns
        -------
        data : `dict[str, Any]`
            Filename and save status.

        Notes
        -----
        After the file is written, the service broadcasts a ``scripts:changed``
        event with the file name, so open Command Consoles reload their list.
        """
        safe_name = Path(filename).name
        if not safe_name.endswith(".py"):
            safe_name += ".py"
        script_path = self._get_user_scripts_dir() / safe_name
        script_path.write_text(content, encoding="utf-8")
        # Tell every open window, so the User Scripts list shows the new file.
        if self.container and hasattr(self.container, "socket_manager"):
            self.container.socket_manager.broadcast_ui_event_sync(
                "scripts:changed",
                {"filename": safe_name},
            )
        return {
            "filename": safe_name,
            "size_bytes": script_path.stat().st_size,
            "status": "saved",
        }

    def _get_doc_root(self) -> Path:
        """Return root documentation path.

        Returns
        -------
        doc_dir : `Path`
            Path pointing to the repository's documentation directory.
        """
        return Path(__file__).resolve().parent.parent.parent.parent / "documentation"

    def list_doc_topics(self) -> list[dict[str, Any]]:
        """List markdown documentation guides available for in-console reading.

        Returns
        -------
        topics : `list` of `dict`
            List of documentation topics with ID, title, and relative path.
        """
        doc_root = self._get_doc_root()
        from .api_doc_generator import get_api_topics

        topics: list[dict[str, Any]] = [dict(t) for t in get_api_topics()]
        for doc_file in sorted(doc_root.rglob("*.md")):
            # Ignore build artifacts
            rel_parts = doc_file.relative_to(doc_root).parts
            if any(part.startswith(("_", ".")) for part in rel_parts):
                continue

            title = doc_file.stem.replace("_", " ").replace("-", " ").title()
            try:
                for line in doc_file.read_text(encoding="utf-8").splitlines():
                    if line.startswith("# "):
                        title = line[2:].strip()
                        break
            except (OSError, ValueError) as exc:
                logger.debug("Failed reading title for doc topic %s: %s", doc_file, exc)

            topic_id = str(doc_file.relative_to(doc_root).as_posix())
            if any(t["id"] == topic_id for t in topics):
                continue

            topics.append({
                "id": topic_id,
                "title": title,
                "path": topic_id,
                "category": rel_parts[0] if len(rel_parts) > 1 else "General",
            })
        return topics

    def search_doc_topics(self, query: str) -> list[dict[str, Any]]:
        """Find doc topics whose title, path, category, or body match `query`.

        Parameters
        ----------
        query : `str`
            Case-insensitive search text. An empty or blank query
            returns every topic, unfiltered.

        Returns
        -------
        matches : `list` of `dict`
            Topics from `list_doc_topics`, in the same order, each with
            an added `snippet` key (text surrounding the match) when
            the match was found in the body text rather than the
            title, path, or category.
        """
        topics = self.list_doc_topics()
        trimmed = query.strip().lower()
        if not trimmed:
            return topics

        matches: list[dict[str, Any]] = []
        for topic in topics:
            if (
                trimmed in topic["title"].lower()
                or trimmed in topic["path"].lower()
                or trimmed in (topic.get("category") or "").lower()
            ):
                matches.append(topic)
                continue

            try:
                content = self.get_doc_topic(topic["id"])["content"]
            except NotFoundError:
                continue

            match_pos = content.lower().find(trimmed)
            if match_pos == -1:
                continue

            snippet_start = max(0, match_pos - 40)
            snippet_end = min(len(content), match_pos + len(trimmed) + 40)
            snippet = content[snippet_start:snippet_end].replace("\n", " ").strip()
            matches.append({**topic, "snippet": snippet})

        return matches

    def get_doc_topic(self, topic_id: str) -> dict[str, Any]:
        """Retrieve markdown content for a documentation topic.

        Parameters
        ----------
        topic_id : `str`
            Relative path or topic ID.

        Returns
        -------
        data : `dict[str, Any]`
            Topic ID, title, and markdown content.

        Raises
        ------
        NotFoundError
            If the requested documentation file does not exist.
        """
        from .api_doc_generator import get_api_topic_content

        # 1. Check virtual / generated API documentation
        api_topic = get_api_topic_content(topic_id)
        if api_topic is not None:
            return api_topic

        # 2. Check filesystem documentation
        doc_root = self._get_doc_root()
        safe_path = (doc_root / topic_id).resolve()
        if not safe_path.exists() or not safe_path.is_file():
            fallback_md = (doc_root / f"{topic_id}.md").resolve()
            if fallback_md.exists() and fallback_md.is_file():
                safe_path = fallback_md
            else:
                raise NotFoundError(f"Documentation topic {topic_id!r} not found.")

        if not str(safe_path).startswith(str(doc_root)):
            raise NotFoundError(f"Documentation topic {topic_id!r} not found.")

        content = safe_path.read_text(encoding="utf-8")
        title = safe_path.stem.replace("_", " ").replace("-", " ").title()
        for line in content.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break

        return {
            "id": topic_id,
            "title": title,
            "content": content,
        }

    def get_editor_buffer(self) -> dict[str, Any]:
        """Retrieve active code in the Command Console editor.

        Returns
        -------
        data : `dict[str, Any]`
            The current editor text buffer.
        """
        return {"code": self.active_editor_code}

    def set_editor_buffer(self, code_content: str) -> dict[str, Any]:
        """Update the active code buffer in the Command Console editor.

        Parameters
        ----------
        code_content : `str`
            Code to set in the editor.

        Returns
        -------
        status : `dict[str, Any]`
            Confirmation and length of set code.
        """
        self.active_editor_code = code_content
        if self.container and hasattr(self.container, "socket_manager"):
            self.container.socket_manager.broadcast_ui_event_sync(
                "editor:sync",
                {"code": code_content},
            )
        return {"status": "success", "length": len(code_content)}
