"""Purpose: Draw a FITS image, or an array, as a PNG or JPEG picture.

Description: Raw telescope images (FITS files) hold measurements that an
ordinary picture viewer cannot show well. `render_image` scales an image so
faint detail shows and returns the picture's bytes as a `RenderedImage`; it is
the owner of ``Visualization.render``. A stretched view of a whole stack uses
the JPEG the enhancement stage saved beside it, when that picture is current.
`render_newest_capture` draws the newest FITS file in the frames folder, and
`scale_for_display` gives the scaled pixels that a Matplotlib figure draws.
"""

import logging
import os
from dataclasses import dataclass
from io import BytesIO
from typing import Any, NamedTuple

import numpy as np
from PIL import Image

from astrometricslib._internal.drivers.image import AstrometricsImage
from astrometricslib.foundation.errors import InvalidArgumentError, ProcessingError
from astrometricslib.models.target import ImageCrop, RenderedImage, StretchParameters
from astrometricslib.pipelines.shared.image_scaling import ImageScaler
from astrometricslib.pipelines.shared.quarantine_path import QUARANTINE_FOLDER_NAME
from astrometricslib.pipelines.shared.stack_preview_path import (
    PREVIEW_JPEG_QUALITY,
    is_preview_path,
    is_processed_fits_path,
    preview_path_for,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_DIMENSIONS = 2000
"""Longest side of a picture when the caller gives none, in pixels."""

SMALLEST_MAX_DIMENSIONS = 100
"""Smallest longest side a caller may ask for, in pixels."""

LARGEST_MAX_DIMENSIONS = 2000
"""Largest longest side a caller may ask for, in pixels."""

SMALLEST_CROP_SIDE_PX = 16
"""Smallest side of a zoomed piece, in pixels."""


class PngPicture(NamedTuple):
    """A FITS file drawn as a PNG, with how it was drawn.

    Attributes
    ----------
    png_bytes : `bytes`
        The PNG file.
    black, white : `float`
        The pixel values drawn black and white.
    stretch_parameters : `StretchParameters` or `None`
        The automatic stretch, or `None` when none was used.
    full_size : `tuple` [`int`, `int`]
        Width and height of the whole image, in pixels.
    size : `tuple` [`int`, `int`]
        Width and height of the picture, in pixels.
    """

    png_bytes: bytes
    black: float
    white: float
    stretch_parameters: StretchParameters | None
    full_size: tuple[int, int]
    size: tuple[int, int]


# Shared in-memory cache for rendered PNG frames
# The key includes the file's modified time and size, so a picture rewritten at
# the same path (a preview remade without a restack) is drawn again.
_png_cache: dict[tuple[str, int, float | None, float | None, bool, int, int], PngPicture] = {}


def _stretch_used(data: Any, auto_stretched: bool) -> StretchParameters | None:
    """Return the automatic stretch a picture was drawn with.

    Parameters
    ----------
    data : `numpy.ndarray`
        The image data that was drawn.
    auto_stretched : `bool`
        Whether the automatic stretch was asked for (stretch on, no manual
        brightness range).

    Returns
    -------
    parameters : `StretchParameters` or `None`
        The stretch, measured the way `ImageScaler.scale_to_uint8` measures
        it, or `None` when none was used.
    """
    return ImageScaler.autostretch_parameters(data) if auto_stretched else None


# Paths whose saved picture could not be read, so the warning is logged once
# per path and not again each time the viewer redraws it.
_unreadable_preview_paths: set[str] = set()


def _find_moved_frame(path: str) -> str:
    """Find a frame that stacking set aside after its record was made.

    The frame quarantine moves a frame into an ``_excluded`` folder beside it
    and keeps the original path on the frame's record, so a list built from the
    records can still ask for the old path.

    Parameters
    ----------
    path : `str`
        The path that was asked for.

    Returns
    -------
    found_path : `str`
        `path` if that file exists, else the same file name inside the
        ``_excluded`` folder next to it if that file exists, else `path`.
    """
    if os.path.exists(path):
        return path
    moved_path = os.path.join(os.path.dirname(path), QUARANTINE_FOLDER_NAME, os.path.basename(path))
    return moved_path if os.path.exists(moved_path) else path


def _saved_preview_picture(path: str, max_dimensions: int) -> tuple[bytes, tuple[int, int]] | None:
    """Read the stretched JPEG that was saved beside a stack.

    The picture is used only if it is at least as new as the stack, so an old
    picture is never shown for a newer stack. A picture larger than
    `max_dimensions` is shrunk to fit, as the PNG route does.

    Parameters
    ----------
    path : `str`
        Path of the stacked FITS file.
    max_dimensions : `int`
        The longest side the picture may have, in pixels.

    Returns
    -------
    picture : `tuple` [`bytes`, `tuple` [`int`, `int`]] or `None`
        The JPEG file and its width and height, or `None` if there is no
        current preview or it cannot be read.
    """
    if is_processed_fits_path(path) or is_preview_path(path):
        return None
    preview_path = preview_path_for(path)
    if not os.path.exists(preview_path):
        # Only a stack has a saved picture. A single frame, or a stack
        # saved before previews existed, has none: this is normal.
        return None
    try:
        if os.path.getmtime(preview_path) < os.path.getmtime(path):
            return None
        with open(preview_path, "rb") as preview_file:
            jpeg_bytes = preview_file.read()
        with Image.open(BytesIO(jpeg_bytes)) as picture:
            width_px, height_px = picture.size
            largest_side = max(width_px, height_px)
            if largest_side > max_dimensions:
                scale = float(max_dimensions) / largest_side
                width_px, height_px = round(width_px * scale), round(height_px * scale)
                shrunk = picture.resize((width_px, height_px), Image.LANCZOS)
                buffer = BytesIO()
                shrunk.save(buffer, format="JPEG", quality=PREVIEW_JPEG_QUALITY)
                jpeg_bytes = buffer.getvalue()
    except (OSError, ValueError) as error:
        if path not in _unreadable_preview_paths:
            _unreadable_preview_paths.add(path)
            logger.warning("Could not use the preview picture for '%s': %s", path, error)
        return None
    return jpeg_bytes, (int(width_px), int(height_px))


class ImageConverter:
    """A helper class for changing FITS images into PNG format."""

    @staticmethod
    def convert_fits_to_png_with_stats(
        path: str,
        max_dimensions: int = DEFAULT_MAX_DIMENSIONS,
        black: float | None = None,
        white: float | None = None,
        stretch: bool = True,
    ) -> PngPicture:
        """Convert a FITS file to PNG bytes, tracking the brightness range.

        It uses a cache to remember recent images so they load faster
        the next time they are requested.

        Parameters
        ----------
        path : `str`
            The FITS file.
        max_dimensions : `int`, optional
            Longest side of the picture, in pixels.
        black, white : `float`, optional
            A manual brightness range, in pixel values. Both or neither.
        stretch : `bool`, optional
            Brighten faint detail automatically. Defaults to `True`.

        Returns
        -------
        picture : `PngPicture`
            The PNG, the brightness range, the automatic stretch used (or
            `None`) and the sizes.
        """
        try:
            file_status = os.stat(path)
            file_version = (file_status.st_mtime_ns, file_status.st_size)
        except OSError:
            file_version = (0, 0)
        cache_key = (path, int(max_dimensions), black, white, stretch, *file_version)
        if cache_key in _png_cache:
            return _png_cache[cache_key]

        image = AstrometricsImage(path)
        data = image.data

        img8, vmin, vmax = ImageScaler.scale_to_uint8(data, vmin=black, vmax=white, stretch=stretch)
        stretch_parameters = _stretch_used(data, stretch and black is None)

        pil_image = Image.fromarray(img8)
        if pil_image.mode != "L":
            pil_image = pil_image.convert("L")

        width_px, height_px = pil_image.size
        scale = (
            min(1.0, float(max_dimensions) / max(width_px, height_px))
            if max(width_px, height_px) > 0
            else 1.0
        )
        if scale < 1.0:
            pil_image = pil_image.resize((round(width_px * scale), round(height_px * scale)), Image.LANCZOS)

        buffer = BytesIO()
        # Compression level 1 encodes about 2.5 times faster than the default 6
        # and makes the file about 10% bigger. The picture goes to the app on
        # this computer, where the extra size costs almost nothing.
        pil_image.save(buffer, format="PNG", optimize=False, compress_level=1)
        picture = PngPicture(
            buffer.getvalue(),
            float(vmin),
            float(vmax),
            stretch_parameters,
            (int(width_px), int(height_px)),
            (int(pil_image.size[0]), int(pil_image.size[1])),
        )

        # Simple cache pruning: evict oldest entry if cache grows too large
        if len(_png_cache) > 128:
            _png_cache.pop(next(iter(_png_cache)))
        _png_cache[cache_key] = picture
        return picture


@dataclass
class DisplayPixels:
    """An image scaled to 0-255 for display, with how it was scaled.

    Attributes
    ----------
    picture : `numpy.ndarray`
        The 8-bit pixels, cropped when a crop was asked for.
    black : `float`
        The pixel value drawn as black.
    white : `float`
        The pixel value drawn as white.
    stretched : `bool`
        Whether the automatic stretch was used.
    stretch_parameters : `StretchParameters` or `None`
        The automatic stretch, or `None` when none was used.
    full_width : `int`
        Width of the whole image, in pixels.
    full_height : `int`
        Height of the whole image, in pixels.
    crop : `ImageCrop` or `None`
        The piece drawn, in full-image pixels counted from 1, or `None` for
        the whole image.
    """

    picture: np.ndarray
    black: float
    white: float
    stretched: bool
    stretch_parameters: StretchParameters | None
    full_width: int
    full_height: int
    crop: ImageCrop | None


def image_source(image: Any) -> tuple[str | None, np.ndarray | None]:
    """Tell whether an image is a file or an array of pixels.

    Parameters
    ----------
    image : `str`, `os.PathLike`, `FrameRecord` or `numpy.ndarray`
        A FITS file's path, a frame record (its ``path`` is drawn) or the
        pixels themselves (2-D, or color).

    Returns
    -------
    path, pixels : `tuple` [`str` or `None`, `numpy.ndarray` or `None`]
        The file to read, or the pixels; the other is `None`. A file that
        stacking moved into an ``_excluded`` folder is found there.

    Raises
    ------
    InvalidArgumentError
        If the image is none of these.
    """
    if isinstance(image, np.ndarray):
        return None, image
    if isinstance(image, (str, os.PathLike)):
        return _find_moved_frame(os.fspath(image)), None
    frame_path = getattr(image, "path", None)
    if isinstance(frame_path, str) and frame_path:
        return _find_moved_frame(frame_path), None
    raise InvalidArgumentError(
        "Give the image as a path, a frame record or an array of pixels.",
        details={"image_type": type(image).__name__},
    )


def check_brightness(brightness: Any) -> tuple[float | None, float | None]:
    """Check a manual brightness range.

    Parameters
    ----------
    brightness : `tuple` [`float`, `float`] or `None`
        The pixel values to draw black and white, or `None` for none.

    Returns
    -------
    black, white : `tuple` [`float` or `None`, `float` or `None`]
        The two values, or two `None`.

    Raises
    ------
    InvalidArgumentError
        If it is not two numbers with the black value below the white one.
    """
    if brightness is None:
        return None, None
    try:
        black, white = (float(value) for value in brightness)
    except (TypeError, ValueError) as error:
        raise InvalidArgumentError(
            "brightness is two pixel values: (black, white).", details={"brightness": str(brightness)}
        ) from error
    if not black < white:
        raise InvalidArgumentError(
            "The black value of brightness must be below the white value.",
            details={"black": black, "white": white},
        )
    return black, white


def check_crop(crop: Any) -> tuple[float, float, int] | None:
    """Check a zoom request.

    Parameters
    ----------
    crop : `tuple` [`float`, `float`, `int`] or `None`
        The column and row of the piece's center, counted from 1, and its
        side in pixels; or `None` for the whole image.

    Returns
    -------
    crop : `tuple` [`float`, `float`, `int`] or `None`
        The checked values.

    Raises
    ------
    InvalidArgumentError
        If it is not three numbers, or the side is below 1.
    """
    if crop is None:
        return None
    try:
        x_px, y_px, size_px = crop
        checked = (float(x_px), float(y_px), int(size_px))
    except (TypeError, ValueError) as error:
        raise InvalidArgumentError(
            "crop is three numbers: (x_px, y_px, size_px).", details={"crop": str(crop)}
        ) from error
    if checked[2] < 1:
        raise InvalidArgumentError("The side of a crop must be at least 1 pixel.", details={"crop": checked})
    return checked


def scale_for_display(
    path: str | None,
    pixels: np.ndarray | None,
    stretch: bool,
    brightness: tuple[float | None, float | None],
    crop: tuple[float, float, int] | None,
) -> DisplayPixels:
    """Read an image, cut out the zoomed piece and scale it to 0-255.

    Parameters
    ----------
    path : `str` or `None`
        The FITS file, or `None` when ``pixels`` are given.
    pixels : `numpy.ndarray` or `None`
        The pixels themselves (2-D, or color), or `None` to read ``path``.
    stretch : `bool`
        Brighten faint detail automatically. A stack's ``_processed.fits``
        file is already stretched, so it is drawn as it is.
    brightness : `tuple` [`float` or `None`, `float` or `None`]
        The pixel values to draw black and white, or two `None`.
    crop : `tuple` [`float`, `float`, `int`] or `None`
        Column and row of the center of a zoomed piece, counted from 1, and
        its side, in full-image pixels.

    Returns
    -------
    display : `DisplayPixels`
        The scaled pixels and how they were scaled.

    Raises
    ------
    InvalidArgumentError
        If the image holds no 2-D pixels.
    """
    if stretch and path is not None and is_processed_fits_path(path):
        stretch = False
    data = np.asarray(pixels) if pixels is not None else AstrometricsImage(path).data
    if data is None or np.asarray(data).ndim not in (2, 3) or np.asarray(data).size == 0:
        raise InvalidArgumentError("The image has no 2-D pixel data.", details={"path": path})
    full_height, full_width = data.shape[-2:]
    piece = None
    if crop is not None:
        x_px, y_px, size_px = crop
        half = max(SMALLEST_CROP_SIDE_PX, int(size_px)) // 2
        # The center counts from 1; the array counts from 0.
        left = max(0, min(round(x_px - 1.0) - half, full_width - 2 * half))
        top = max(0, min(round(y_px - 1.0) - half, full_height - 2 * half))
        data = data[..., top : top + 2 * half, left : left + 2 * half]
        piece = ImageCrop(
            left_px=left + 1, top_px=top + 1, width_px=int(data.shape[-1]), height_px=int(data.shape[-2])
        )

    black, white = brightness
    picture, black_drawn, white_drawn = ImageScaler.scale_to_uint8(
        data, vmin=black, vmax=white, stretch=stretch
    )
    return DisplayPixels(
        picture=picture,
        black=float(black_drawn),
        white=float(white_drawn),
        stretched=bool(stretch),
        stretch_parameters=_stretch_used(data, stretch and black is None),
        full_width=int(full_width),
        full_height=int(full_height),
        crop=piece,
    )


def _picture_side(max_dimensions: int | None) -> int:
    """Give the longest side a picture may have.

    Parameters
    ----------
    max_dimensions : `int` or `None`
        The side the caller asked for, or `None` for the default.

    Returns
    -------
    side : `int`
        The side, kept between `SMALLEST_MAX_DIMENSIONS` and
        `LARGEST_MAX_DIMENSIONS`.
    """
    asked = DEFAULT_MAX_DIMENSIONS if max_dimensions is None else int(max_dimensions)
    return max(SMALLEST_MAX_DIMENSIONS, min(asked, LARGEST_MAX_DIMENSIONS))


def _render_piece(
    path: str | None,
    pixels: np.ndarray | None,
    side: int,
    stretch: bool,
    brightness: tuple[float | None, float | None],
    crop: tuple[float, float, int] | None,
) -> RenderedImage:
    """Draw an array, or a zoomed piece of a file, as a PNG.

    A big image is shrunk to fit. A small zoomed piece is enlarged to half
    the longest side, so single stars are easy to see.

    Parameters
    ----------
    path : `str` or `None`
        The FITS file, or `None` when ``pixels`` are given.
    pixels : `numpy.ndarray` or `None`
        The pixels to draw instead of reading ``path``.
    side : `int`
        Longest side of the picture, in pixels.
    stretch : `bool`
        Brighten faint detail automatically.
    brightness : `tuple` [`float` or `None`, `float` or `None`]
        A manual brightness range, or two `None`.
    crop : `tuple` [`float`, `float`, `int`] or `None`
        The zoomed piece, or `None`.

    Returns
    -------
    image : `RenderedImage`
        The PNG and how it was drawn.
    """
    display = scale_for_display(path, pixels, stretch, brightness, crop)
    picture = Image.fromarray(display.picture)
    if picture.mode != "L":
        picture = picture.convert("L")
    longest_side = max(picture.size)
    target_side = side if display.crop is None else side // 2
    if longest_side > target_side or (display.crop is not None and longest_side < target_side):
        scale = target_side / longest_side
        picture = picture.resize(
            (round(picture.size[0] * scale), round(picture.size[1] * scale)),
            Image.NEAREST if scale > 1.0 else Image.LANCZOS,
        )
    buffer = BytesIO()
    picture.save(buffer, format="PNG", compress_level=1)
    return RenderedImage(
        picture=buffer.getvalue(),
        path=path,
        black=display.black,
        white=display.white,
        width_px=picture.size[0],
        height_px=picture.size[1],
        full_width_px=display.full_width,
        full_height_px=display.full_height,
        crop=display.crop,
        stretched=display.stretched,
        stretch_parameters=display.stretch_parameters,
    )


def render_image(
    image: Any,
    *,
    stretch: bool = True,
    brightness: tuple[float, float] | None = None,
    crop: tuple[float, float, int] | None = None,
    max_dimensions: int | None = None,
) -> RenderedImage:
    """Draw a FITS file, a frame or an array as a picture. Nothing is saved.

    A stretched view of a whole stack uses the JPEG the enhancement stage
    saved beside it (see `stack_preview_path`), when that picture is
    current. A whole file is otherwise drawn from its FITS data as a PNG,
    with a cache keyed on the file's time and size. An array, or a zoomed
    piece, is drawn the same way without the cache.

    Parameters
    ----------
    image : `str`, `FrameRecord` or `numpy.ndarray`
        The image (see `image_source`).
    stretch : `bool`, optional
        Brighten faint detail automatically. Defaults to `True`.
    brightness : `tuple` [`float`, `float`], optional
        The pixel values to draw black and white. It replaces ``stretch``.
    crop : `tuple` [`float`, `float`, `int`], optional
        Column and row of the center of a zoomed piece, counted from 1, and
        its side, in full-image pixels.
    max_dimensions : `int`, optional
        Longest side of the picture, from 100 to 2000 pixels. Defaults to
        2000.

    Returns
    -------
    image : `RenderedImage`
        The picture and how it was drawn.

    Raises
    ------
    ProcessingError
        If the file cannot be drawn.

    Notes
    -----
    An image, brightness range or crop that is not valid raises
    `InvalidArgumentError`.
    """
    path, pixels = image_source(image)
    black, white = check_brightness(brightness)
    piece = check_crop(crop)
    side = _picture_side(max_dimensions)
    try:
        if pixels is not None or piece is not None:
            return _render_piece(path, pixels, side, stretch, (black, white), piece)
        preview = _saved_preview_picture(path, side) if stretch and black is None else None
        if preview is not None:
            jpeg_bytes, (width_px, height_px) = preview
            return RenderedImage(
                picture=jpeg_bytes,
                media_type="image/jpeg",
                path=path,
                black=0.0,
                white=255.0,
                width_px=width_px,
                height_px=height_px,
            )
        drawn = ImageConverter.convert_fits_to_png_with_stats(
            path, max_dimensions=side, black=black, white=white, stretch=stretch
        )
    except (OSError, ValueError) as error:
        raise ProcessingError(f"Failed to convert FITS to PNG: {error}", details={"path": path}) from error
    return RenderedImage(
        picture=drawn.png_bytes,
        path=path,
        black=drawn.black,
        white=drawn.white,
        width_px=drawn.size[0],
        height_px=drawn.size[1],
        full_width_px=drawn.full_size[0],
        full_height_px=drawn.full_size[1],
        stretched=stretch and black is None,
        stretch_parameters=drawn.stretch_parameters,
    )


def newest_capture_path(config: Any) -> str | None:
    """Find the newest FITS file anywhere in the frames folder.

    Each folder and file is checked on its own, so one that cannot be read
    is skipped and the search goes on.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings that name the frames folder.

    Returns
    -------
    path : `str` or `None`
        The FITS file changed last, or `None` when there is none.
    """
    frames_path = config.get_frames_path()
    if not frames_path or not os.path.isdir(frames_path):
        return None
    latest_path: str | None = None
    latest_mtime = -1.0
    folders = [str(frames_path)]
    while folders:
        current = folders.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            folders.append(entry.path)
                        elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith((
                            ".fits",
                            ".fit",
                            ".fts",
                        )):
                            mtime = entry.stat().st_mtime
                            if mtime > latest_mtime:
                                latest_mtime, latest_path = mtime, entry.path
                    except OSError:
                        continue
        except OSError as scan_error:
            logger.debug("Error scanning %s for the last image: %s", current, scan_error)
    return latest_path


def render_newest_capture(
    config: Any, *, stretch: bool = True, max_dimensions: int | None = None
) -> RenderedImage | None:
    """Draw the newest FITS file in the frames folder.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings that name the frames folder.
    stretch : `bool`, optional
        Brighten faint detail automatically. Defaults to `True`.
    max_dimensions : `int`, optional
        Longest side of the picture, from 100 to 2000 pixels. Defaults to
        2000.

    Returns
    -------
    image : `RenderedImage` or `None`
        The picture, or `None` when there is no FITS file or it cannot be
        drawn (logged; the folder may hold a half-written file).
    """
    path = newest_capture_path(config)
    if path is None:
        return None
    try:
        return render_image(path, stretch=stretch, max_dimensions=max_dimensions)
    except ProcessingError:
        logger.exception("Failed to load the last image %s", path)
        return None
