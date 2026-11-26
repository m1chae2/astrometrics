"""Helpers of the Command Console: stopping a run, the selection, show and doc.

The Command Console (`scripting_service`) runs code in one of the backend's
worker threads. This module holds the parts that are not about running the
code itself:

* `ConsoleRuns` keeps the runs going now. It stops one by raising
  `KeyboardInterrupt` in its thread (the same thing Ctrl+C does in a plain
  Python prompt) and collects the requests a run makes to the app.
* `ConsoleSelection` is the read-only ``selection`` object of the console.
  It reads what is picked in Image Processing each time it is used.
* `make_show_request` checks the arguments of ``show(...)`` and turns them
  into a request that the app carries out with its own navigation.
* `lookup_doc` finds the API reference entry that documents a name or an
  object, for ``doc(...)``.

None of this is in the libraries: it is about the app's screens.
"""

import ctypes
import inspect
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from backend.services.infrastructure.app_selection_service import AppSelection

#: The displays ``show(...)`` can open, by the words a script may use.
SHOW_DISPLAYS = {
    "image processing": "Image Processing",
    "image": "Image Processing",
    "astronomy manager": "Astronomy Manager",
    "astronomy": "Astronomy Manager",
}

#: How many close names ``doc(...)`` suggests when nothing matches.
DOC_SUGGESTION_COUNT = 5


def _raise_in_thread(thread_id: int, exception: type[BaseException] | None) -> bool:
    """Raise an exception in another thread, or cancel one not raised yet.

    The exception is raised the next time that thread runs a Python step. A
    thread waiting inside a long C call (a single long ``time.sleep``, a
    large NumPy operation) sees it only when that call returns.

    Parameters
    ----------
    thread_id : `int`
        The thread's ident, from `threading.get_ident`.
    exception : `type` or `None`
        The exception class to raise, or `None` to cancel one that is
        waiting to be raised.

    Returns
    -------
    found : `bool`
        `True` when the thread exists.
    """
    target = ctypes.py_object(exception) if exception is not None else None
    changed = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id), target)
    if changed > 1:
        # More than one thread state was changed, which should never happen:
        # undo it so no other thread is interrupted.
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id), None)
        return False
    return changed == 1


@dataclass
class ConsoleRun:
    """One run of console code that is going now.

    Attributes
    ----------
    thread_id : `int`
        The worker thread that runs the code.
    label : `str`
        What runs: a script's name, or the line typed.
    source : `str`
        Where the run came from (``"editor"``, ``"repl"`` or ``"agent"``).
    stop_asked : `bool`
        `True` once a stop was sent to the run.
    app_requests : `list` of `dict`
        What the run asked the app to show (``show`` and ``doc``).
    """

    thread_id: int
    label: str
    source: str
    stop_asked: bool = False
    app_requests: list[dict[str, Any]] = field(default_factory=list)


class ConsoleRuns:
    """Keep the console runs going now, stop them, and collect requests."""

    def __init__(self) -> None:
        """Start with no runs."""
        self._lock = threading.Lock()
        self._runs: dict[int, ConsoleRun] = {}

    def begin(self, label: str, source: str) -> ConsoleRun:
        """Record that the calling thread starts running console code.

        Parameters
        ----------
        label : `str`
            What runs.
        source : `str`
            Where the run came from.

        Returns
        -------
        run : `ConsoleRun`
            The run; give it to `finish` when the code ends.
        """
        run = ConsoleRun(thread_id=threading.get_ident(), label=label, source=source)
        with self._lock:
            self._runs[run.thread_id] = run
        return run

    def finish(self, run: ConsoleRun) -> bool:
        """Record that a run ended, and cancel a stop that came too late.

        A stop sent just as the code ended may still be waiting to be raised.
        It is cancelled here, so it cannot hit the next task of the thread.
        If it is raised during this call, the call starts again.

        Parameters
        ----------
        run : `ConsoleRun`
            The run `begin` gave.

        Returns
        -------
        stopped : `bool`
            `True` when a stop was sent to the run.
        """
        while True:
            try:
                with self._lock:
                    if self._runs.get(run.thread_id) is run:
                        del self._runs[run.thread_id]
                    if run.stop_asked:
                        _raise_in_thread(run.thread_id, None)
                return run.stop_asked
            except KeyboardInterrupt:
                continue

    def stop(self) -> dict[str, Any]:
        """Stop every console run going now.

        Returns
        -------
        result : `dict`
            ``stopped``: the labels of the runs a stop was sent to (empty
            when nothing was running).
        """
        stopped: list[str] = []
        with self._lock:
            for run in self._runs.values():
                if _raise_in_thread(run.thread_id, KeyboardInterrupt):
                    run.stop_asked = True
                    stopped.append(run.label)
        return {"stopped": stopped}

    def running(self) -> list[dict[str, str]]:
        """List the runs going now.

        Returns
        -------
        runs : `list` of `dict`
            ``label`` and ``source`` of each run.
        """
        with self._lock:
            return [{"label": run.label, "source": run.source} for run in self._runs.values()]

    def add_app_request(self, request: dict[str, Any]) -> bool:
        """Add a request to the app to the run of the calling thread.

        Code that started its own thread is matched to the newest run.

        Parameters
        ----------
        request : `dict`
            The request, such as one from `make_show_request`.

        Returns
        -------
        added : `bool`
            `False` when no run is going, so the request has nowhere to go.
        """
        with self._lock:
            run = self._runs.get(threading.get_ident())
            if run is None and self._runs:
                run = list(self._runs.values())[-1]
            if run is None:
                return False
            run.app_requests.append(request)
            return True


def _format_number(value: float | None, digits: int) -> str:
    """Format a number for the selection summary.

    Parameters
    ----------
    value : `float` or `None`
        The number.
    digits : `int`
        Digits after the decimal point.

    Returns
    -------
    text : `str`
        The number, or ``"-"`` when there is none.
    """
    return "-" if value is None else f"{value:.{digits}f}"


class ConsoleSelection:
    """What is picked in Image Processing, read each time it is used.

    The console holds one of these as ``selection``. Each attribute reads the
    latest selection that Image Processing sent, so it is never out of date
    and works when Image Processing is in another window. It cannot be
    changed from the console: pick something in Image Processing instead.
    Every attribute is `None` when nothing is picked.
    """

    __slots__ = ("_read",)

    def __init__(self, read: Callable[[], AppSelection]) -> None:
        """Make the selection object.

        Parameters
        ----------
        read : callable
            Returns the latest `AppSelection`.
        """
        object.__setattr__(self, "_read", read)

    def __setattr__(self, name: str, value: Any) -> None:
        """Refuse every change, because the selection follows the app.

        Parameters
        ----------
        name : `str`
            The attribute name.
        value : `Any`
            The value that was given.

        Raises
        ------
        AttributeError
            Always.
        """
        raise AttributeError(
            "selection is read-only. Pick a target, file or star in Image Processing instead."
        )

    @property
    def target(self) -> str | None:
        """The target's name, such as ``"M 31"``."""
        return self._read().target

    @property
    def path(self) -> str | None:
        """The file shown: a stack or a raw frame."""
        return self._read().path

    @property
    def kind(self) -> str | None:
        """``"stack"`` or ``"frame"``: what kind of file is shown."""
        return self._read().kind

    @property
    def star(self) -> Any:
        """The selected star, or `None`.

        It has ``id``, ``name``, ``library_id``, ``ra`` and ``dec``
        (degrees; usually `None` on a raw frame) and ``x`` and ``y`` (the
        1-based column and row of the star's center on the shown picture,
        as FITS, DS9 and the Star Profile panel count). Any may be `None`.
        Only a star picked with the Astrometry overlay counts.
        """
        return self._read().star

    @property
    def set_at(self) -> Any:
        """When Image Processing sent this selection (UTC), or `None`."""
        return self._read().set_at

    def __repr__(self) -> str:
        """Give a short readable summary of the selection.

        Returns
        -------
        summary : `str`
            A few lines: the target, the file and the star.
        """
        selection = self._read()
        if selection.target is None and selection.path is None and selection.star is None:
            return (
                "Nothing is selected in Image Processing (selection.target, .path, .kind and .star are None)."
            )
        when = selection.set_at.strftime("%Y-%m-%d %H:%M:%S UTC") if selection.set_at else "unknown time"
        lines = [f"Image Processing selection (set {when})", f"  target: {selection.target or '-'}"]
        lines.append(f"  file:   {selection.kind or '-'}  {selection.path or '-'}")
        star = selection.star
        if star is None:
            lines.append("  star:   -")
        else:
            lines.append(
                f"  star:   {star.name or star.id or '-'}"
                f"  ra {_format_number(star.ra, 5)}  dec {_format_number(star.dec, 5)}"
                f"  x {_format_number(star.x, 1)}  y {_format_number(star.y, 1)}"
            )
        return "\n".join(lines)

    __str__ = __repr__


def _display_name(display: str) -> str:
    """Match the display a script names to one ``show`` can open.

    Parameters
    ----------
    display : `str`
        Such as ``"Image Processing"`` or ``"astronomy"``.

    Returns
    -------
    display : `str`
        The display's own name.

    Raises
    ------
    ValueError
        If ``show`` cannot open that display.
    """
    name = SHOW_DISPLAYS.get(display.strip().lower())
    if name is None:
        raise ValueError(f"show() opens 'Image Processing' or 'Astronomy Manager', not {display!r}.")
    return name


def make_show_request(
    *,
    target: str | None,
    star: str | None,
    path: str | None,
    display: str | None,
    selection: AppSelection,
    find_star: Callable[[str], tuple[str, str] | None],
) -> dict[str, Any]:
    """Check the arguments of ``show(...)`` and build the app request.

    Without ``display``: a file or a target opens in Image Processing (a
    star given with a target is selected on the target's stack), and a star
    alone opens in the Astronomy Manager.

    Parameters
    ----------
    target : `str` or `None`
        The target to open.
    star : `str` or `None`
        A star's name or library id.
    path : `str` or `None`
        A stack or frame to show in Image Processing.
    display : `str` or `None`
        ``"Image Processing"`` or ``"Astronomy Manager"``.
    selection : `AppSelection`
        The current selection. A ``path`` with no ``target`` uses its target.
    find_star : callable
        Looks a star up; returns its library id and name, or `None`.

    Returns
    -------
    request : `dict`
        ``kind`` (``"show"``), ``display``, ``target``, ``star_id``,
        ``star_name`` and ``path``.

    Raises
    ------
    TypeError
        If no target, star or path is given.
    ValueError
        If the arguments do not fit together, or the star is not in the
        library.
    """
    if not (target or star or path):
        raise TypeError('show() needs target=, star= or path=, for example show(target="M 31").')
    if path and not target:
        target = selection.target
        if not target:
            raise ValueError('show(path=...) needs target= too, for example show(target="M 31", path=...).')
    star_id = star_name = None
    if star:
        found = find_star(star)
        if found is None:
            raise ValueError(f"No star named {star!r} is in the library.")
        star_id, star_name = found
    if display:
        chosen = _display_name(display)
    elif star and not target:
        chosen = "Astronomy Manager"
    else:
        chosen = "Image Processing"
    if chosen == "Astronomy Manager":
        if not star_id:
            raise ValueError(
                'The Astronomy Manager shows a star: give star=..., for example show(star="Vega").'
            )
        if path:
            raise ValueError(
                "The Astronomy Manager does not show files: leave out path=, or use Image Processing."
            )
    if chosen == "Image Processing" and not target:
        raise ValueError("Image Processing shows a target: give target=... too.")
    return {
        "kind": "show",
        "display": chosen,
        "target": target,
        "star_id": star_id,
        "star_name": star_name,
        "path": path,
    }


def describe_show_request(request: dict[str, Any]) -> str:
    """Write the line ``show`` prints about what it opens.

    Parameters
    ----------
    request : `dict`
        A request from `make_show_request`.

    Returns
    -------
    line : `str`
        Such as ``"Opening M 31 (star Vega) in Image Processing."``
    """
    parts = [request["target"] or request["star_name"] or ""]
    if request["star_name"] and request["target"]:
        parts.append(f"(star {request['star_name']})")
    if request["path"]:
        parts.append(f"({request['path']})")
    return f"Opening {' '.join(part for part in parts if part)} in {request['display']}."


def _object_entry_names(obj: Any) -> list[str]:
    """List the reference entry names that could document an object.

    Parameters
    ----------
    obj : `Any`
        A class, a function, a method, or an instance of a public class.

    Returns
    -------
    names : `list` of `str`
        Such as ``["TargetCatalog.delete_images"]``, most specific first.
    """
    if inspect.ismodule(obj):
        return []
    if inspect.isclass(obj):
        return [klass.__name__ for klass in obj.__mro__ if klass is not object]
    owner = getattr(obj, "__self__", None)
    name = getattr(obj, "__name__", None)
    if callable(obj) and name:
        if owner is not None and not inspect.ismodule(owner):
            owner_class = owner if inspect.isclass(owner) else type(owner)
            return [f"{klass.__name__}.{name}" for klass in owner_class.__mro__ if klass is not object]
        qualified = getattr(obj, "__qualname__", name)
        return [qualified, name] if qualified != name else [name]
    return [klass.__name__ for klass in type(obj).__mro__ if klass is not object]


def resolve_console_name(name: str, scope: dict[str, Any]) -> Any:
    """Follow a dotted name through the console's variables.

    Parameters
    ----------
    name : `str`
        Such as ``"astrometrics.targets.delete_images"``.
    scope : `dict`
        The console's variables.

    Returns
    -------
    obj : `Any`
        The object the name reaches, or `None` when its first part is not a
        console variable or a later part is missing.
    """
    first, *rest = name.split(".")
    if first not in scope:
        return None
    obj = scope[first]
    for part in rest:
        try:
            obj = getattr(obj, part)
        except AttributeError:
            return None
    return obj


@dataclass(frozen=True)
class DocLookup:
    """What ``doc(...)`` found for a name.

    Attributes
    ----------
    matches : `list` of `dict`
        The reference entries that fit (``topic_id``, ``anchor_id`` and
        ``qualified_name``).
    suggestions : `list` of `str`
        Close entry names, when nothing fits.
    """

    matches: list[dict[str, str]]
    suggestions: list[str]


def lookup_doc(
    query: Any,
    scope: dict[str, Any],
    find_entries: Callable[[str], list[dict[str, Any]]],
) -> DocLookup:
    """Find the reference entries that document a name or an object.

    A text query is first followed through the console's variables, so
    ``"catalog.delete"`` (or the object ``catalog.delete`` itself) finds the
    method of the class that object belongs to. Otherwise the text is
    looked up by name in the reference (``find_entries``), which may give
    several entries for a bare method name, or close names when none fits.

    Parameters
    ----------
    query : `Any`
        A name, or the object itself.
    scope : `dict`
        The console's variables.
    find_entries : callable
        Looks a name up in the API reference. Returns entries with
        ``topic_id``, ``anchor_id``, ``qualified_name`` and ``suggestion``
        (`True` for a close name rather than a match).

    Returns
    -------
    lookup : `DocLookup`
        The entries that fit, or close names when none does.
    """
    obj = resolve_console_name(query, scope) if isinstance(query, str) else query
    if obj is not None:
        for candidate in _object_entry_names(obj):
            exact = [
                entry
                for entry in find_entries(candidate)
                if not entry.get("suggestion") and entry.get("qualified_name") == candidate
            ]
            if exact:
                return DocLookup(exact[:1], [])
    text = query if isinstance(query, str) else getattr(query, "__name__", type(query).__name__)
    found = find_entries(text)
    matches = [entry for entry in found if not entry.get("suggestion")]
    if matches:
        return DocLookup(matches, [])
    return DocLookup([], [entry["qualified_name"] for entry in found if entry.get("suggestion")])
