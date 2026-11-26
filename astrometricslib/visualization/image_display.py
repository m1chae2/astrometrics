"""Purpose: Draw the app's star overlays on a picture of one image.

Description: `Visualization.render_fits` draws a frame or a stack as a PNG
(``kind="image"``) or as a Matplotlib figure (``kind="figure"``). With
``overlays`` it also marks what Image Processing shows, read from the files
saved next to a stack:

* ``"detections"``: the stars the astrometry run's star finder detects, read
  through `measure_detections_in_file`, which uses the stack's
  ``<stack>_detections.json`` file when it is still valid.
* ``"stars"``: the named stars of the stack, read from its
  ``<stack>_identification.json`` file. As in Image Processing's Astrometry
  overlay, only stars that have a record in the library with photometry (a
  light curve) are drawn and named.

This module finds the marks once (`collect_overlay_marks`) and draws them
either on a Matplotlib figure (`draw_image_figure`) or on a PNG
(`draw_marks_on_picture`), so both kinds show the same marks in the same
colors. The figure is made with `matplotlib.pyplot`, so a Jupyter notebook
shows it in the page and the app's Command Console opens it in a figure
window. Nothing is saved.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING, Any

from astrometricslib.foundation.errors import InvalidArgumentError

if TYPE_CHECKING:
    from matplotlib.figure import Figure

    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.models.target import ViewableImage
    from astrometricslib.pipelines.shared.image_conversions import DisplayPixels

__all__ = [
    "IMAGE_OVERLAYS",
    "OverlayMark",
    "catalog_keys_of_star",
    "check_overlays",
    "collect_overlay_marks",
    "draw_image_figure",
    "draw_marks_on_picture",
    "figure_name",
]

IMAGE_OVERLAYS = ("detections", "stars")
"""The overlays that can be drawn on a FITS file."""

UNNAMED_STAR = "Unnamed star"
"""The name a stack's star gets when no catalog star is close to it."""

GAIA_NAME_PREFIX = "Gaia DR3 "
"""How a library star found only in Gaia is named: this text and its number."""

_STACK_STAR_ID = re.compile(r"^(detection|gaia)-\d+$")
"""The ids a stack's star list gives its stars, which are not library ids."""

OVERLAY_COLORS = {"detections": "cyan", "stars": "yellow"}
"""The color of each overlay's circles and names."""

DETECTION_MARKER_SIZE = 60.0
"""Area of a detection's circle on a figure, in points squared (about 9
points across)."""

STAR_MARKER_SIZE = 140.0
"""Area of a named star's circle on a figure, in points squared (13 points
across)."""

MARKER_RADIUS_PIXELS = {"detections": 5, "stars": 7}
"""Radius of each overlay's circle on a PNG, in picture pixels. These match
the figure's circles at its usual size."""


@dataclass(frozen=True)
class OverlayMark:
    """One circle to draw on an image, with an optional name.

    Attributes
    ----------
    overlay : `str`
        Which overlay the mark belongs to: ``"detections"`` or ``"stars"``.
    column : `float`
        Column of the mark, in full-frame pixels, where the first pixel's
        center is 0.
    row : `float`
        Row of the mark, in full-frame pixels, where the first pixel's
        center is 0.
    label : `str` or `None`
        The name written beside the circle, or `None` for none.
    """

    overlay: str
    column: float
    row: float
    label: str | None = None


def catalog_keys_of_star(star: Any) -> list[str]:
    """List the library ids a stack's named star may have.

    This follows the app's rule (``catalogKeysOfStar`` in the viewer): the
    Gaia name ``Gaia DR3 <number>`` when the star has a Gaia number, then
    its name unless it is ``Unnamed star``, then its own id unless it is a
    stack-only id such as ``detection-12``.

    Parameters
    ----------
    star : `IdentifiedStar`
        One star of a stack's saved identification.

    Returns
    -------
    keys : `list` [`str`]
        The possible library ids, most specific first, with no repeats.
    """
    keys: list[str] = []
    gaia_source_id = getattr(star, "gaia_source_id", None)
    if gaia_source_id:
        keys.append(f"{GAIA_NAME_PREFIX}{gaia_source_id}")
    name = (getattr(star, "name", "") or "").strip()
    if name and name != UNNAMED_STAR and name not in keys:
        keys.append(name)
    own_id = (getattr(star, "id", "") or "").strip()
    if own_id and not _STACK_STAR_ID.match(own_id) and own_id not in keys:
        keys.append(own_id)
    return keys


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


def _photometry_star_ids(stars: StellarCatalog, keys: list[str], target_id: str | None) -> set[str]:
    """Find which library ids name stars that have photometry.

    Parameters
    ----------
    stars : `StellarCatalog`
        The library's star catalog.
    keys : `list` [`str`]
        The possible library ids of the stack's stars.
    target_id : `str` or `None`
        The target the stack belongs to. With one, only that target's stars
        count, as in Image Processing. With none, any library star counts.

    Returns
    -------
    ids : `set` [`str`]
        The ids that have photometry.
    """
    if target_id:
        answer = stars.query(target_id=target_id, has_photometry=True, detail="ids", limit=None)
    elif keys:
        answer = stars.query(ids=keys, has_photometry=True, detail="ids", limit=None)
    else:
        return set()
    return set(answer.ids or [])


def collect_overlay_marks(
    path: str, overlays: Sequence[str], stars: StellarCatalog | None, target_id: str | None
) -> tuple[list[OverlayMark], list[str]]:
    """Find the marks of the chosen overlays on a FITS file.

    Parameters
    ----------
    path : `str`
        The FITS file (a frame or a stack).
    overlays : `Sequence` [`str`]
        The checked overlay names (see `check_overlays`).
    stars : `StellarCatalog` or `None`
        The library's star catalog, used to keep only the named stars with
        photometry. Without it, the ``"stars"`` overlay draws nothing.
    target_id : `str` or `None`
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
        notes.append(_named_star_marks(path, stars, target_id, marks))
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
    # The detections are in 1-based pixels (the first pixel's center is 1, 1);
    # the marks use 0-based pixels (the first pixel's center is 0, 0).
    marks.extend(OverlayMark("detections", star.x - 1.0, star.y - 1.0) for star in measurement.detections)
    return f"{len(measurement.detections):,} detections"


def _named_star_marks(
    path: str, stars: StellarCatalog | None, target_id: str | None, marks: list[OverlayMark]
) -> str:
    """Add the stack's named stars that have photometry to the marks.

    Parameters
    ----------
    path : `str`
        The stack.
    stars : `StellarCatalog` or `None`
        The library's star catalog.
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

    if stars is None:
        return "no star names (no star catalog)"
    identification = read_stack_identification(path)
    if identification is None:
        return "no star names (identify the stack first)"
    keys_by_star = [(star, catalog_keys_of_star(star)) for star in identification.stars]
    all_keys = list(dict.fromkeys(key for _star, keys in keys_by_star for key in keys))
    with_photometry = _photometry_star_ids(stars, all_keys, target_id)
    if not with_photometry:
        return "no stars with photometry yet (run Analyze Target)"
    shown = 0
    for star, keys in keys_by_star:
        if any(key in with_photometry for key in keys):
            marks.append(OverlayMark("stars", star.x, star.y, star.name))
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
        viewer, and the axes count full-frame pixels even for a crop.
    """
    import matplotlib.pyplot as plt

    box = display.crop_box or {
        "left": 0,
        "top": 0,
        "width": display.full_width,
        "height": display.full_height,
    }
    # Each pixel is drawn centered on its whole-number position.
    left, right = box["left"] - 0.5, box["left"] + box["width"] - 0.5
    top, bottom = box["top"] - 0.5, box["top"] + box["height"] - 0.5
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


def draw_marks_on_picture(
    picture: ViewableImage, marks: Sequence[OverlayMark], notes: Sequence[str]
) -> ViewableImage:
    """Draw marks on a rendered PNG, in the overlays' colors.

    Parameters
    ----------
    picture : `ViewableImage`
        The PNG and its description, from `render_viewable_image`. The
        description gives the crop and the picture size, which place each
        mark.
    marks : `Sequence` [`OverlayMark`]
        The marks to draw, in full-frame pixels.
    notes : `Sequence` [`str`]
        One note per overlay. They are added to the description under
        ``"overlays"``.

    Returns
    -------
    picture : `ViewableImage`
        A color PNG with the marks, and the description with the notes.
    """
    from PIL import Image, ImageDraw

    description = dict(picture.description)
    full = description["full_frame_size"]
    box = description.get("crop") or {"left": 0, "top": 0, "width": full["width"], "height": full["height"]}
    size = description["picture_size"]
    scale_x = size["width"] / box["width"]
    scale_y = size["height"] / box["height"]
    image = Image.open(BytesIO(picture.png_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    for mark in marks:
        # A source pixel's center at c lands at (c + 0.5) * scale - 0.5 once
        # the piece is resized.
        x = (mark.column - box["left"] + 0.5) * scale_x - 0.5
        y = (mark.row - box["top"] + 0.5) * scale_y - 0.5
        radius = MARKER_RADIUS_PIXELS[mark.overlay]
        if not (-radius <= x <= size["width"] + radius and -radius <= y <= size["height"] + radius):
            continue
        color = OVERLAY_COLORS[mark.overlay]
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=color, width=1)
        if mark.label:
            draw.text((x + radius + 2, y - radius - 10), mark.label, fill=color)
    buffer = BytesIO()
    image.save(buffer, format="PNG", compress_level=1)
    description["overlays"] = list(notes)
    return picture.model_copy(update={"png_bytes": buffer.getvalue(), "description": description})


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
