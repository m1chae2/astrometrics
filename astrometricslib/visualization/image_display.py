"""Purpose: Draw one image as a Matplotlib figure, with the star overlays.

Description: `plot_image` is the owner of ``Visualization.plot_image``. It
draws a frame, a stack or an array as a figure at full resolution. With
``overlays`` it also marks what Image Processing shows, read from the files
saved next to a stack:

* ``"detections"``: the stars the astrometry run's star finder detects, read
  through `measure_detections_in_file`, which uses the stack's
  ``<stack>_detections.json`` file when it is still valid.
* ``"stars"``: the named stars of the stack, read from its
  ``<stack>_identification.json`` file. As in Image Processing's Astrometry
  overlay, only stars whose library id (``library_id``, set by the
  identification) is a record with photometry (a light curve) are drawn and
  named.

This module finds the marks (`collect_overlay_marks`) and draws them on the
figure (`draw_image_figure`). The axes count pixels from 1, as FITS and DS9
do. The figure is made with `matplotlib.pyplot`, so a Jupyter notebook shows
it in the page and the app's Command Console opens it in a figure window.
Nothing is saved.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from astrometricslib.foundation.errors import InvalidArgumentError

if TYPE_CHECKING:
    from matplotlib.figure import Figure

    from astrometricslib.pipelines.shared.image_conversions import DisplayPixels

__all__ = [
    "IMAGE_FIGURE_SIZE",
    "IMAGE_OVERLAYS",
    "OverlayMark",
    "check_overlays",
    "collect_overlay_marks",
    "draw_image_figure",
    "figure_name",
    "plot_image",
]

IMAGE_FIGURE_SIZE = (10.0, 8.0)
"""Size of the figure `plot_image` draws, in inches."""

IMAGE_OVERLAYS = ("detections", "stars")
"""The overlays that can be drawn on a FITS file."""

OVERLAY_COLORS = {"detections": "cyan", "stars": "yellow"}
"""The color of each overlay's circles and names."""

DETECTION_MARKER_SIZE = 60.0
"""Area of a detection's circle on a figure, in points squared (about 9
points across)."""

STAR_MARKER_SIZE = 140.0
"""Area of a named star's circle on a figure, in points squared (13 points
across)."""


@dataclass(frozen=True)
class OverlayMark:
    """One circle to draw on an image, with an optional name.

    Attributes
    ----------
    overlay : `str`
        Which overlay the mark belongs to: ``"detections"`` or ``"stars"``.
    column : `float`
        Column of the mark, in full-frame pixels, where the first pixel's
        center is 1.
    row : `float`
        Row of the mark, in full-frame pixels, where the first pixel's
        center is 1.
    label : `str` or `None`
        The name written beside the circle, or `None` for none.
    """

    overlay: str
    column: float
    row: float
    label: str | None = None


def check_overlays(overlays: Sequence[str] | str | None) -> tuple[str, ...]:
    """Check the overlay names a caller gave.

    Parameters
    ----------
    overlays : `Sequence` [`str`], `str` or `None`
        The overlay names, one name, or `None` for none.

    Returns
    -------
    overlays : `tuple` [`str`, ...]
        The names, in the order given.

    Raises
    ------
    InvalidArgumentError
        If a name is not one of `IMAGE_OVERLAYS`.
    """
    if overlays is None:
        return ()
    names = (overlays,) if isinstance(overlays, str) else tuple(overlays)
    unknown = [name for name in names if name not in IMAGE_OVERLAYS]
    if unknown:
        raise InvalidArgumentError(
            f"overlays must be chosen from: {', '.join(IMAGE_OVERLAYS)}.",
            details={"overlays": list(names), "choices": list(IMAGE_OVERLAYS)},
        )
    return names


def _photometry_star_names(storage: Any, keys: list[str], target_id: str | None) -> dict[str, str]:
    """Find the library stars with photometry and the names the lists show.

    The overlay labels a star with its library record's name, so a star reads
    the same on the image as in the Astronomy Manager's lists.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage, which holds the star records.
    keys : `list` [`str`]
        The library ids of the stack's stars.
    target_id : `str` or `None`
        The target the stack belongs to. With one, only that target's stars
        count, as in Image Processing. With none, any library star counts.

    Returns
    -------
    names : `dict` [`str`, `str`]
        Each id that has photometry, mapped to its record's name (its id
        when the record has no name).
    """
    from astrometricslib.pipelines.shared.star_catalog_queries import get_star

    names: dict[str, str] = {}
    for key in keys:
        star = get_star(storage, key)
        if star is None or star.id != key or not star.has_photometry:
            continue
        if target_id and target_id not in (star.target_ids or []):
            continue
        names[key] = str(star.name or key)
    return names


def collect_overlay_marks(
    path: str, overlays: Sequence[str], storage: Any, target_id: str | None = None
) -> tuple[list[OverlayMark], list[str]]:
    """Find the marks of the chosen overlays on a FITS file.

    Parameters
    ----------
    path : `str`
        The FITS file (a frame or a stack).
    overlays : `Sequence` [`str`]
        The checked overlay names (see `check_overlays`).
    storage : `AbstractCatalogAccess` or `None`
        The catalog storage, used to keep only the named stars with
        photometry. Without it, the ``"stars"`` overlay draws nothing.
    target_id : `str` or `None`, optional
        The target the stack belongs to. With it, only that target's stars
        with photometry are named, as in Image Processing.

    Returns
    -------
    marks : `list` [`OverlayMark`]
        Every mark, in full-frame pixels.
    notes : `list` [`str`]
        One note per overlay: how many marks it drew, or why it drew none.
    """
    marks: list[OverlayMark] = []
    notes: list[str] = []
    if "detections" in overlays:
        notes.append(_detection_marks(path, marks))
    if "stars" in overlays:
        notes.append(_named_star_marks(path, storage, target_id, marks))
    return marks, notes


def _detection_marks(path: str, marks: list[OverlayMark]) -> str:
    """Add the star finder's detections to the marks.

    Parameters
    ----------
    path : `str`
        The FITS file.
    marks : `list` [`OverlayMark`]
        The list to add to.

    Returns
    -------
    note : `str`
        How many detections were found, or why none were.
    """
    from astrometricslib.pipelines.astrometry.processing.detections import measure_detections_in_file

    try:
        measurement = measure_detections_in_file(path)
    except InvalidArgumentError as error:
        return f"no detections ({error})"
    if not measurement.detections:
        return "no stars detected"
    # The detections and the marks both count pixels from 1.
    marks.extend(OverlayMark("detections", star.x, star.y) for star in measurement.detections)
    return f"{len(measurement.detections):,} detections"


def _named_star_marks(path: str, storage: Any, target_id: str | None, marks: list[OverlayMark]) -> str:
    """Add the stack's named stars that have photometry to the marks.

    Parameters
    ----------
    path : `str`
        The stack.
    storage : `AbstractCatalogAccess` or `None`
        The catalog storage, which holds the star records.
    target_id : `str` or `None`
        The stack's target, if known.
    marks : `list` [`OverlayMark`]
        The list to add to.

    Returns
    -------
    note : `str`
        How many named stars were found, or why none were.
    """
    from astrometricslib.pipelines.astrometry.processing.stack_identification import read_stack_identification

    if storage is None:
        return "no star names (no star catalog)"
    identification = read_stack_identification(path)
    if identification is None:
        return "no star names (identify the stack first)"
    library_ids = list(dict.fromkeys(star.library_id for star in identification.stars if star.library_id))
    with_photometry = _photometry_star_names(storage, library_ids, target_id)
    if not with_photometry:
        return "no stars with photometry yet (run Analyze Target)"
    shown = 0
    for star in identification.stars:
        if star.library_id in with_photometry:
            # The identification and the marks both count pixels from 1.
            marks.append(OverlayMark("stars", star.x, star.y, with_photometry[star.library_id]))
            shown += 1
    return f"{shown:,} {'star' if shown == 1 else 'stars'} with photometry"


def draw_image_figure(
    display: DisplayPixels,
    name: str,
    marks: Sequence[OverlayMark],
    notes: Sequence[str],
    figsize: tuple[float, float],
) -> Figure:
    """Draw scaled pixels and their marks as a Matplotlib figure.

    Parameters
    ----------
    display : `DisplayPixels`
        The 8-bit pixels and the crop they were cut with.
    name : `str`
        What the title calls the image: the file name, or ``"array"``.
    marks : `Sequence` [`OverlayMark`]
        The marks to draw, in full-frame pixels.
    notes : `Sequence` [`str`]
        One note per overlay, added to the title.
    figsize : `tuple` [`float`, `float`]
        Figure size in inches.

    Returns
    -------
    figure : `matplotlib.figure.Figure`
        The figure. The first row of pixels is at the top, as in the app's
        viewer, and the axes count full-frame pixels from 1, even for a
        crop.
    """
    import matplotlib.pyplot as plt

    crop = display.crop
    first_column, first_row = (crop.left_px, crop.top_px) if crop is not None else (1, 1)
    width = crop.width_px if crop is not None else display.full_width
    height = crop.height_px if crop is not None else display.full_height
    # Each pixel is drawn centered on its whole-number position.
    left, right = first_column - 0.5, first_column + width - 0.5
    top, bottom = first_row - 0.5, first_row + height - 0.5
    figure, axes = plt.subplots(figsize=figsize)
    style: dict[str, Any] = {
        "origin": "upper",
        "interpolation": "nearest",
        "extent": (left, right, bottom, top),
    }
    if display.picture.ndim == 2:
        axes.imshow(display.picture, cmap="gray", vmin=0, vmax=255, **style)
    else:
        axes.imshow(display.picture, **style)
    marker_styles = (("detections", DETECTION_MARKER_SIZE, 0.7), ("stars", STAR_MARKER_SIZE, 1.0))
    for overlay, size, width in marker_styles:
        chosen = [mark for mark in marks if mark.overlay == overlay]
        if not chosen:
            continue
        axes.scatter(
            [mark.column for mark in chosen],
            [mark.row for mark in chosen],
            s=size,
            facecolors="none",
            edgecolors=OVERLAY_COLORS[overlay],
            linewidths=width,
            label=overlay,
        )
        for mark in chosen:
            if mark.label:
                axes.annotate(
                    mark.label,
                    (mark.column, mark.row),
                    xytext=(6, 6),
                    textcoords="offset points",
                    color=OVERLAY_COLORS[overlay],
                    fontsize=7,
                )
    # Marks outside a crop must not widen the view.
    axes.set_xlim(left, right)
    axes.set_ylim(bottom, top)
    axes.set_xlabel("x (pixels)")
    axes.set_ylabel("y (pixels)")
    axes.set_title(name if not notes else f"{name}\n{'; '.join(notes)}", fontsize=10)
    figure.tight_layout()
    return figure


def figure_name(path: str | None) -> str:
    """Name an image for a figure's title.

    Parameters
    ----------
    path : `str` or `None`
        The FITS file, or `None` for an array.

    Returns
    -------
    name : `str`
        The file name, or ``"array"``.
    """
    return os.path.basename(path) if path is not None else "array"


def plot_image(
    image: Any,
    *,
    storage: Any,
    overlays: Sequence[str] | str | None = None,
    crop: tuple[float, float, int] | None = None,
    stretch: bool = True,
    brightness: tuple[float, float] | None = None,
) -> Figure:
    """Draw a FITS file, a frame or an array as a figure, with overlays.

    Parameters
    ----------
    image : `str`, `FrameRecord` or `numpy.ndarray`
        The image (see `image_conversions.image_source`).
    storage : `AbstractCatalogAccess`
        The catalog storage, which names the stars of the ``"stars"``
        overlay.
    overlays : `Sequence` [`str`], `str` or `None`, optional
        What to mark on a file: ``"detections"``, ``"stars"`` or both.
    crop : `tuple` [`float`, `float`, `int`], optional
        Column and row of the center of a zoomed piece, counted from 1, and
        its side, in full-image pixels.
    stretch : `bool`, optional
        Brighten faint detail automatically. Defaults to `True`.
    brightness : `tuple` [`float`, `float`], optional
        The pixel values to draw black and white. It replaces ``stretch``.

    Returns
    -------
    figure : `matplotlib.figure.Figure`
        The figure, with what each overlay drew (or why it drew nothing) in
        its title.

    Raises
    ------
    InvalidArgumentError
        If overlays are asked for on an array, which has no saved
        detections or names.
    """
    from astrometricslib.pipelines.shared import image_conversions

    names = check_overlays(overlays)
    path, pixels = image_conversions.image_source(image)
    if names and path is None:
        raise InvalidArgumentError(
            "An array has no saved detections or names, so it takes no overlays.",
            details={"overlays": list(names)},
        )
    display = image_conversions.scale_for_display(
        path,
        pixels,
        stretch,
        image_conversions.check_brightness(brightness),
        image_conversions.check_crop(crop),
    )
    marks, notes = collect_overlay_marks(path, names, storage) if names and path is not None else ([], [])
    return draw_image_figure(display, figure_name(path), marks, notes, IMAGE_FIGURE_SIZE)
