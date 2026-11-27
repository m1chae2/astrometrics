"""The Command Console's workspace: its preloaded names, help and completions.

The workspace is the set of names a console run can use. It holds the same
public objects a script or notebook would use (``astrometrics``,
``wayfinder``, ``np``, ``plt`` and ``fits``) plus the console's own helpers
(``help``, ``inspect_api``, ``selection``, ``show`` and ``doc``). This
module builds it, writes the help text, lists the variables for the
Workspace table and completes names. `ScriptingService` takes these methods
from `ConsoleWorkspaceMixin`.
"""

import code
import inspect
import logging
import rlcompleter
import sys
import textwrap
from typing import Any

from astrometricslib.exceptions import InvalidArgumentError
from backend.services.infrastructure.agent_code_policy import AGENT_VISIBLE_NAMES
from backend.services.infrastructure.app_selection_service import AppSelection
from backend.services.infrastructure.console_helpers import (
    ConsoleRuns,
    ConsoleSelection,
    describe_show_request,
    lookup_doc,
    make_show_request,
    show_argument_text,
)
from backend.services.infrastructure.scripting_figures import browser_pyplot

logger = logging.getLogger(__name__)

#: The objects the console preloads, with the line ``help`` prints for each.
#: They are the public entry points a library user has in a script or a
#: notebook. The app's internal services are deliberately not among them.
PRELOADED_OBJECTS = {
    "astrometrics": (
        "Astrometrics: observatories, targets (and targets.frames), stars, calibration, "
        "processing.<pipeline> (stacking, enhancement, astrometry, photometry, spectroscopy, asteroids), "
        "quality (records, frames, stacks, detections, blocks), visualization and jobs"
    ),
    "wayfinder": "Wayfinder: telescope control, planning, execution and review of past nights",
    "np": "NumPy",
    "plt": "Matplotlib pyplot (figures open in their own windows)",
    "fits": "astropy.io.fits, for reading FITS files",
    "selection": (
        "What is picked in Image Processing now (read-only): target (the library's Target, or None), "
        "path, kind (stack or frame) and star (the Astrometry overlay pick: name, library_id, ra_deg "
        "and dec_deg, and x_px and y_px, the star's column and row in the file at path, counted from 1)"
    ),
    "show": 'show(target="M 31", star=..., path=...) opens it in Image Processing or the Astronomy Manager',
    "doc": "doc(name) opens the name's entry in the Documentation tab; also typed as: doc name",
}

#: The script convention ``help`` prints, so a function written in the
#: console can move into the library almost unchanged.
SCRIPT_CONVENTION = (
    "Keep an analysis in a function with explicit inputs, such as def measure_night(target, night): ..., "
    "and use selection, show, doc and print only in the few lines that call it."
)

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

#: Names the Workspace table leaves out: the preloaded objects and the help
#: functions. The app shows the preloaded objects in its own pinned rows.
HIDDEN_WORKSPACE_NAMES = frozenset({*PRELOADED_OBJECTS, "help", "list_commands", "inspect_api"})

#: The longest docstring ``help(obj)`` prints, in characters.
HELP_TEXT_LIMIT = 6000


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


class SmartPrinter:
    """Callable/reprable wrapper that lazily renders help text.

    Deferring text generation to `__repr__`/`__call__` lets the
    interactive console print up-to-date help (e.g. typing
    ``help`` with no parentheses) without eagerly computing it.
    """

    def __init__(self, text_factory: Any) -> None:
        """Keep the function that writes the help text.

        Parameters
        ----------
        text_factory : `Any`
            A function with no arguments that returns the help text.
        """
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


class ConsoleWorkspaceMixin:
    """Build the console's workspace and describe what is in it.

    The class that uses it sets `container` (the app's service container,
    or `None`) and `_runs` (the runs going now) before it calls
    `_get_locals`, and keeps the console in `console` and its name
    completer in `completer`.
    """

    container: Any
    console: code.InteractiveConsole
    completer: rlcompleter.Completer
    _runs: ConsoleRuns

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

        help_obj = SmartPrinter(get_help_text)

        local_scope: dict[str, Any] = {
            "help": help_obj,
            "list_commands": help_obj,
            "inspect_api": inspect_api,
            "doc": self._doc,
            "show": self._show,
            "selection": ConsoleSelection(
                self._current_selection, find_target=self._find_target, find_offset=self._shown_offset
            ),
        }

        try:
            import numpy as np

            local_scope["np"] = np
        except ImportError:
            pass

        plt = browser_pyplot()
        if plt is not None:
            local_scope["plt"] = plt

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
            'Example: astrometrics.visualization.plot_image("/path/to/stack.fits", '
            'overlays=["detections", "stars"])'
        )
        lines.append("")
        lines.extend(textwrap.wrap(SCRIPT_CONVENTION, HELP_LINE_WIDTH))
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

    def _library(self) -> Any:
        """Return the console's ``astrometrics`` object, or `None`.

        Returns
        -------
        astrometrics : `Astrometrics` or `None`
            The library object of the app container, or `None` when the
            console has no container.
        """
        return getattr(self.container, "astrometrics", None) if self.container else None

    def _find_target(self, name: str) -> Any:
        """Look the picked target up in the library, for ``selection.target``.

        Parameters
        ----------
        name : `str`
            The target name Image Processing sent, such as ``"M 31"``.

        Returns
        -------
        target : `Target` or `None`
            The library's target, or `None` when it has none of that name.
        """
        astrometrics = self._library()
        return None if astrometrics is None else astrometrics.targets.get(name)

    def _shown_offset(self, path: str) -> tuple[float, float]:
        """Find where Image Processing's picture of a stack starts in it.

        Image Processing shows a stack's processed copy, which may be a crop
        of the stack. The stack's saved star names record the crop's corner.

        Parameters
        ----------
        path : `str`
            The stack.

        Returns
        -------
        offset_px : `tuple` [`float`, `float`]
            The crop's corner in pixels, or ``(0, 0)`` when the copy is the
            whole stack or nothing records it.
        """
        astrometrics = self._library()
        identification = (
            None if astrometrics is None else astrometrics.processing.astrometry.get_identification(path)
        )
        if identification is None:
            return (0.0, 0.0)
        return (float(identification.viewer_offset_x or 0), float(identification.viewer_offset_y or 0))

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
        astrometrics = self._library()
        if astrometrics is None:
            return None
        star = astrometrics.stars.get(text)
        if star is None:
            return None
        return star.id, star.name or star.id

    def _show(
        self,
        target: Any = None,
        *,
        star: Any = None,
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
        target : `str` or `Target`, optional
            The target, such as ``"M 31"`` or ``selection.target``.
        star : `str`, `StellarObject` or `ConsoleStar`, optional
            A star's name or library id, such as ``"Vega"``, or a star
            object such as ``selection.star``.
        path : `str`, optional
            A stack or frame to show in Image Processing. With no
            ``target``, the target picked in Image Processing is used.
        display : `str`, optional
            ``"Image Processing"`` or ``"Astronomy Manager"``.

        Notes
        -----
        `make_show_request` raises `TypeError` when no target, star or path
        is given (`show_argument_text` when one is not a name or a library
        object), and `ValueError` when the arguments do not fit together
        or the star is not in the library. The user reads it in the
        terminal like any other error in their code.
        """
        request = make_show_request(
            target=show_argument_text(target),
            star=show_argument_text(star),
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
            A name such as ``"get_rotation"``,
            ``"TargetFrames.get_rotation"`` or
            ``"astrometrics.targets.frames.delete"``, or the object itself.
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
