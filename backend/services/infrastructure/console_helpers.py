"""Helpers of the Command Console: stopping a run, the selection, show and doc.

The Command Console (`scripting_service`) runs code in one of the backend's
worker threads. This module holds the parts that are not about running the
code itself:

* `ConsoleRuns` keeps the runs going now. It stops one by raising
  `KeyboardInterrupt` in its thread (the same thing Ctrl+C does in a plain
  Python prompt) and collects the requests a run makes to the app.
* `ConsoleSelection` is the read-only ``selection`` object of the console.
  It reads what is picked in Image Processing each time it is used, and
  turns it into library terms: the target as a `Target`, and the star's
  position as 1-based pixels of the file shown (`console_star`).
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

from backend.services.infrastructure.app_selection_service import AppSelection, SelectedStar

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


@dataclass(frozen=True)
class ConsoleStar:
    """The star picked in Image Processing (``selection.star``).

    Attributes
    ----------
    id : `str` or `None`
        The star's id on the picture (its catalog key, or its name).
    name : `str` or `None`
        The star's name, such as ``"Vega"``.
    library_id : `str` or `None`
        The id of the star's record in the library, when it has one. Give
        it to ``astrometrics.stars.get`` for the `StellarObject`.
    ra_deg : `float` or `None`
        Right ascension (ICRS), in degrees. Usually `None` on a raw frame.
    dec_deg : `float` or `None`
        Declination (ICRS), in degrees. Usually `None` on a raw frame.
    x_px : `float` or `None`
        Column of the star's center in the file ``selection.path``, in
        pixels counted from 1 (the center of the first pixel is 1), as the
        library, FITS and DS9 count.
    y_px : `float` or `None`
        Row of the star's center, counted from 1 the same way.
    """

    id: str | None = None
    name: str | None = None
    library_id: str | None = None
    ra_deg: float | None = None
    dec_deg: float | None = None
    x_px: float | None = None
    y_px: float | None = None


def console_star(star: SelectedStar, offset_px: tuple[float, float] = (0.0, 0.0)) -> ConsoleStar:
    """Turn the star the app sent into the console's star, in file pixels.

    This is the one place where the app's star position becomes the
    position the console gives. The app sends the column and row of the
    star's center on the picture it shows, already counted from 1 (the
    Astrometry overlay counts that way). On a stack, that picture is the
    stack's processed copy, which may be a crop of the stack. Adding the
    crop's corner, ``offset_px``, gives the position in the stack file
    itself, so a script can pass ``selection.path`` and the position to a
    library call. On a raw frame the offset is zero.

    Parameters
    ----------
    star : `SelectedStar`
        The star as the app sent it.
    offset_px : `tuple` [`float`, `float`], optional
        Where the shown picture starts in the file, in pixels: ``(0, 0)``
        when it is the whole file.

    Returns
    -------
    star : `ConsoleStar`
        The star, with ``x_px`` and ``y_px`` in 1-based pixels of the file.
    """
    offset_x, offset_y = offset_px
    return ConsoleStar(
        id=star.id,
        name=star.name,
        library_id=star.library_id,
        ra_deg=star.ra,
        dec_deg=star.dec,
        x_px=None if star.x is None else star.x + offset_x,
        y_px=None if star.y is None else star.y + offset_y,
    )


class ConsoleSelection:
    """What is picked in Image Processing, read each time it is used.

    The console holds one of these as ``selection``. Each attribute reads the
    latest selection that Image Processing sent, so it is never out of date
    and works when Image Processing is in another window. It cannot be
    changed from the console: pick something in Image Processing instead.
    Every attribute is `None` when nothing is picked.

    The values are in the library's own terms, so a function that takes
    them works the same after it moves into the library: ``target`` is a
    `Target` and ``star`` gives 1-based pixels of the file ``path``.
    """

    __slots__ = ("_find_offset", "_find_target", "_read")

    def __init__(
        self,
        read: Callable[[], AppSelection],
        find_target: Callable[[str], Any] | None = None,
        find_offset: Callable[[str], tuple[float, float]] | None = None,
    ) -> None:
        """Make the selection object.

        Parameters
        ----------
        read : callable
            Returns the latest `AppSelection`.
        find_target : callable, optional
            Looks a target up by its name and returns the library's
            `Target`, or `None`. With none, ``target`` is always `None`.
        find_offset : callable, optional
            Given a stack's path, returns where the picture Image Processing
            shows for it starts in the stack, in pixels. With none, the
            offset is zero.
        """
        object.__setattr__(self, "_read", read)
        object.__setattr__(self, "_find_target", find_target)
        object.__setattr__(self, "_find_offset", find_offset)

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
    def target(self) -> Any:
        """The picked target as the library's `Target`, or `None`.

        It is `None` when nothing is picked or the library has no target of
        that name.
        """
        name = self._read().target
        if name is None or self._find_target is None:
            return None
        return self._find_target(name)

    @property
    def path(self) -> str | None:
        """The file shown: a stack or a raw frame."""
        return self._read().path

    @property
    def kind(self) -> str | None:
        """``"stack"`` or ``"frame"``: what kind of file is shown."""
        return self._read().kind

    @property
    def star(self) -> ConsoleStar | None:
        """The selected star, or `None`.

        It has ``id``, ``name``, ``library_id``, ``ra_deg`` and ``dec_deg``
        (usually `None` on a raw frame), and ``x_px`` and ``y_px``: the
        column and row of the star's center in the file ``path``, counted
        from 1 as the library, FITS, DS9 and the Star Profile panel count.
        Any may be `None`. Only a star picked with the Astrometry overlay
        counts.
        """
        selection = self._read()
        if selection.star is None:
            return None
        return console_star(selection.star, self._offset(selection))

    @property
    def set_at(self) -> Any:
        """When Image Processing sent this selection (UTC), or `None`."""
        return self._read().set_at

    def _offset(self, selection: AppSelection) -> tuple[float, float]:
        """Find where the shown picture starts in the shown file.

        Parameters
        ----------
        selection : `AppSelection`
            The selection the star belongs to.

        Returns
        -------
        offset_px : `tuple` [`float`, `float`]
            The crop corner of a stack's processed copy, or ``(0, 0)``.
        """
        if selection.kind != "stack" or not selection.path or self._find_offset is None:
            return (0.0, 0.0)
        return self._find_offset(selection.path)

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
        if selection.star is None:
            lines.append("  star:   -")
        else:
            star = console_star(selection.star, self._offset(selection))
            lines.append(
                f"  star:   {star.name or star.id or '-'}"
                f"  ra_deg {_format_number(star.ra_deg, 5)}  dec_deg {_format_number(star.dec_deg, 5)}"
                f"  x_px {_format_number(star.x_px, 1)}  y_px {_format_number(star.y_px, 1)}"
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


def show_argument_text(value: Any) -> str | None:
    """Turn a ``show`` argument into the text the app opens it by.

    ``show`` takes a name, or the object a script already holds: a `Target`
    (``selection.target``), a `StellarObject` or the selected star
    (``selection.star``).

    Parameters
    ----------
    value : `Any`
        A text, the selected star (found by ``library_id``, then
        ``name``), an object with ``id`` (a `Target` or a `StellarObject`),
        or `None`.

    Returns
    -------
    text : `str` or `None`
        The name or id to open, or `None` when there is none.

    Raises
    ------
    TypeError
        If the value is neither a text nor an object with a usable id.
    """
    if value is None or isinstance(value, str):
        return value
    # The selected star is found by its library record first, then its name.
    fields = ("library_id", "name", "id") if isinstance(value, ConsoleStar) else ("id",)
    for field_name in fields:
        text = getattr(value, field_name, None)
        if isinstance(text, str) and text:
            return text
    raise TypeError(f"show() takes a name or a library object, not {type(value).__name__}.")


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
        Such as ``["TargetFrames.get_rotation"]``, most specific first.
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
        Such as ``"astrometrics.targets.frames.delete"``.
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
