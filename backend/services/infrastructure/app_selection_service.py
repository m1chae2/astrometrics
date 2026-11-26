"""Keep what is picked in Image Processing, for the Command Console.

Image Processing sends its selection here (``ui:set_selection``) each time
the target, the shown file or the selected star changes. The Command
Console's ``selection`` object reads the latest one when a script asks for
it, so a console in another window sees the same pick. Nothing is saved to
disk: the selection starts empty each time the backend starts.
"""

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

#: The kinds of file Image Processing shows.
ShownFileKind = Literal["stack", "frame"]


@dataclass(frozen=True)
class SelectedStar:
    """The star picked on the shown picture.

    Attributes
    ----------
    id : `str` or `None`
        The star's id on the picture (its catalog key, or its name).
    name : `str` or `None`
        The star's name, such as ``"Vega"``.
    library_id : `str` or `None`
        The id of the star's record in the library, when it has one.
    ra : `float` or `None`
        Right ascension, in degrees. Usually `None` on a raw frame.
    dec : `float` or `None`
        Declination, in degrees. Usually `None` on a raw frame.
    x : `float` or `None`
        Column of the star's center on the shown picture, in pixels,
        counted from 0 as NumPy and Astropy count (the Star Profile panel
        counts from 1).
    y : `float` or `None`
        Row of the star's center on the shown picture, in pixels, counted
        from 0 the same way.

    Notes
    -----
    Only a star picked with the Astrometry overlay is sent, not the Star
    Profile pin.
    """

    id: str | None = None
    name: str | None = None
    library_id: str | None = None
    ra: float | None = None
    dec: float | None = None
    x: float | None = None
    y: float | None = None


@dataclass(frozen=True)
class AppSelection:
    """What is picked in Image Processing at one moment.

    Attributes
    ----------
    target : `str` or `None`
        The target's name, such as ``"M 31"``.
    path : `str` or `None`
        The file shown: a stack or a raw frame.
    kind : `str` or `None`
        ``"stack"`` or ``"frame"``.
    star : `SelectedStar` or `None`
        The selected star, or `None` when no star is selected.
    set_at : `datetime` or `None`
        When Image Processing sent this selection (UTC).
    """

    target: str | None = None
    path: str | None = None
    kind: ShownFileKind | None = None
    star: SelectedStar | None = None
    set_at: datetime | None = field(default=None)


def _text_or_none(value: Any) -> str | None:
    """Return a text value, or `None` for an empty or missing one.

    Parameters
    ----------
    value : `Any`
        The value sent by the app.

    Returns
    -------
    text : `str` or `None`
        The value as text, or `None`.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number_or_none(value: Any) -> float | None:
    """Return a number value, or `None` when it is missing or not a number.

    Parameters
    ----------
    value : `Any`
        The value sent by the app.

    Returns
    -------
    number : `float` or `None`
        The value as a `float`, or `None`.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class AppSelectionService:
    """Hold the latest selection that Image Processing sent."""

    def __init__(self) -> None:
        """Start with nothing selected."""
        self._lock = threading.Lock()
        self._selection = AppSelection()

    def set_selection(
        self,
        target: str | None = None,
        path: str | None = None,
        kind: str | None = None,
        star: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store what Image Processing has picked now.

        Parameters
        ----------
        target : `str`, optional
            The target's name.
        path : `str`, optional
            The file shown.
        kind : `str`, optional
            ``"stack"`` or ``"frame"``. Any other value is stored as `None`.
        star : `dict`, optional
            The selected star, with any of the keys ``id``, ``name``,
            ``library_id``, ``ra``, ``dec``, ``x`` and ``y``.

        Returns
        -------
        selection : `dict`
            The stored selection, as `get_selection` gives it.
        """
        selected_star = None
        if isinstance(star, dict) and any(star.get(key) is not None for key in ("id", "name", "library_id")):
            selected_star = SelectedStar(
                id=_text_or_none(star.get("id")),
                name=_text_or_none(star.get("name")),
                library_id=_text_or_none(star.get("library_id")),
                ra=_number_or_none(star.get("ra")),
                dec=_number_or_none(star.get("dec")),
                x=_number_or_none(star.get("x")),
                y=_number_or_none(star.get("y")),
            )
        shown_kind: ShownFileKind | None = None
        if kind == "stack":
            shown_kind = "stack"
        elif kind == "frame":
            shown_kind = "frame"
        with self._lock:
            self._selection = AppSelection(
                target=_text_or_none(target),
                path=_text_or_none(path),
                kind=shown_kind,
                star=selected_star,
                set_at=datetime.now(UTC),
            )
        return self.get_selection()

    def current(self) -> AppSelection:
        """Return the latest selection.

        Returns
        -------
        selection : `AppSelection`
            What Image Processing picked last; every field is `None` when
            nothing was sent yet.
        """
        with self._lock:
            return self._selection

    def get_selection(self) -> dict[str, Any]:
        """Return the latest selection as plain data.

        Returns
        -------
        selection : `dict`
            ``target``, ``path``, ``kind``, ``star`` (a `dict` or `None`) and
            ``set_at`` (ISO 8601 text, or `None`).
        """
        selection = self.current()
        star = selection.star
        return {
            "target": selection.target,
            "path": selection.path,
            "kind": selection.kind,
            "star": None
            if star is None
            else {
                "id": star.id,
                "name": star.name,
                "library_id": star.library_id,
                "ra": star.ra,
                "dec": star.dec,
                "x": star.x,
                "y": star.y,
            },
            "set_at": selection.set_at.isoformat() if selection.set_at else None,
        }
