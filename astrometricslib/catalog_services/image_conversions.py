"""Image Conversions.

This module helps turn raw astronomical telescope images (FITS files)
into standard pictures (PNGs) that can be shown on a webpage or app.
"""

import base64
import glob
import logging
import os
from io import BytesIO
from typing import Any

from PIL import Image

from astrometricslib.catalog_services.utilities.image_scaling import ImageScaler
from astrometricslib.image_processing.image import AstrometricsImage
from astrometricslib.utilities.exceptions import AstroLibError

logger = logging.getLogger(__name__)

# Shared in-memory cache for rendered PNG frames
_png_cache: dict[tuple[str, int, float | None, float | None, str, bool], tuple[bytes, float, float]] = {}


class ImageConverter:
    """A helper class for changing FITS images into PNG format."""

    @staticmethod
    def convert_fits_to_png_with_stats(
        path: str,
        max_dimensions: int = 2000,
        center: float | None = None,
        width: float | None = None,
        cmap: str = "gray",
        stretch: bool = True,
    ) -> tuple[bytes, float, float]:
        """Convert a FITS file to PNG bytes, tracking the brightness range.

        It uses a cache to remember recent images so they load faster
        the next time you ask for them.

        Returns
        -------
        result : `tuple`
            A tuple containing `(png_bytes, minimum_brightness,
            maximum_brightness)`.
        """
        cache_key = (path, int(max_dimensions), center, width, cmap, stretch)
        if cache_key in _png_cache:
            return _png_cache[cache_key]

        image = AstrometricsImage(path)
        data = image.data

        vmin = (center - width / 2.0) if center is not None and width is not None else None
        vmax = (center + width / 2.0) if center is not None and width is not None else None

        img8, vmin, vmax = ImageScaler.scale_to_uint8(data, vmin=vmin, vmax=vmax, stretch=stretch)

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
        pil_image.save(buffer, format="PNG", optimize=False)
        png_bytes = buffer.getvalue()

        # Simple cache pruning: evict oldest entry if cache grows too large
        if len(_png_cache) > 128:
            _png_cache.pop(next(iter(_png_cache)))
        _png_cache[cache_key] = (png_bytes, vmin, vmax)
        return png_bytes, vmin, vmax

    @classmethod
    def convert_fits_to_base64_png(
        cls, path: str, max_dimensions: int = 2000, stretch: bool = True
    ) -> dict[str, Any] | None:
        """Convert a FITS image to text so it can be sent over the internet.

        Returns
        -------
        result : `dict`
            A dictionary with the image text (`image_data`), minimum
            brightness (`min`), maximum brightness (`max`), and file headers.

        Raises
        ------
        AstroLibError
            If there is any problem changing the image.
        """
        try:
            png_bytes, vmin, vmax = cls.convert_fits_to_png_with_stats(
                path, max_dimensions=max_dimensions, stretch=stretch
            )
            base64_string = base64.b64encode(png_bytes).decode("utf-8")

            # Extract FITS headers to bundle them in the output
            headers = []
            try:
                headers = get_fits_header(None, path)
            except Exception as e:
                logger.warning(f"Could not extract headers during PNG conversion: {e}")

            return {
                "image_data": f"data:image/png;base64,{base64_string}",
                "min": float(vmin),
                "max": float(vmax),
                "headers": headers,
            }
        except Exception as error:
            logger.error(f"Failed to convert FITS to PNG: {error}")
            raise AstroLibError(f"Failed to convert FITS to PNG: {error}") from error


def get_frame(target: Any, iso: str, exposure: str, index: int = 0) -> str:
    """Find a specific frame for a target based on its camera settings.

    Parameters
    ----------
    target : `Target`
        The astronomical target we are looking at.
    iso : `str`
        The camera ISO or gain setting.
    exposure : `str`
        The exposure time in seconds.
    index : `int`, optional
        If there are multiple matching frames, this picks which one to
        return (0 is the first). Defaults to 0.

    Returns
    -------
    frame_path : `str`
        The file path to the matching image.

    Raises
    ------
    ValueError
        If it can't find an image with those settings.
    """

    def to_float(val: Any) -> float | None:
        """Safely convert a value to a decimal number.

        Returns
        -------
        result : `float` or `None`
            The decimal number, or None if it's not a valid number.
        """
        try:
            return float(val)
        except ValueError, TypeError:
            return None

    target_iso_val = to_float(iso)
    target_exposure_val = to_float(exposure)

    if hasattr(target, "frames") and target.frames:
        matches = []
        for frame in target.frames:
            # ISO Match
            frame_iso_val = to_float(frame.iso)
            if target_iso_val is not None and frame_iso_val is not None:
                iso_match = abs(target_iso_val - frame_iso_val) < 0.1
            else:
                iso_match = str(frame.iso).strip() == str(iso).strip()

            # Exposure Match
            frame_exposure_val = to_float(frame.exposure)
            if target_exposure_val is not None and frame_exposure_val is not None:
                exposure_match = abs(target_exposure_val - frame_exposure_val) < 0.001
            else:
                exposure_match = str(frame.exposure).strip() == str(exposure).strip()

            if iso_match and exposure_match:
                matches.append(frame)

        if matches:
            safe_index = max(0, min(index, len(matches) - 1))
            return matches[safe_index].path

    raise ValueError(f"No frame found for ISO={iso} Exposure={exposure} Index={index}")


def convert_fits_to_png(path: str, max_dimensions: int = 2000, stretch: bool = True) -> dict[str, Any] | None:
    """Convert an image file to a text-based format for webpages.

    Parameters
    ----------
    path : `str`
        The file path to the image.
    max_dimensions : `int`, optional
        The maximum size (width or height) in pixels. Defaults to 2000.
    stretch : `bool`, optional
        Whether to adjust the image brightness so it's easier to see. Defaults
        to True.

    Returns
    -------
    png_data : `dict`
        A dictionary with the converted image and its brightness settings.
    """
    return ImageConverter.convert_fits_to_base64_png(path, max_dimensions=max_dimensions, stretch=stretch)


def convert_fits_to_png_with_stats(
    path: str,
    max_dimensions: int = 2000,
    center: float | None = None,
    width: float | None = None,
    cmap: str = "gray",
    stretch: bool = True,
) -> tuple[bytes, float, float]:
    """Convert a FITS image to raw PNG data and return the brightness range.

    Parameters
    ----------
    path : `str`
        The file path to the image.
    max_dimensions : `int`, optional
        The maximum size (width or height) in pixels. Defaults to 2000.
    center : `float`, optional
        The middle value for adjusting brightness.
    width : `float`, optional
        The range of values around the center to show.
    cmap : `str`, optional
        The color scheme to use (like "gray"). Defaults to "gray".
    stretch : `bool`, optional
        Whether to adjust the brightness. Defaults to True.

    Returns
    -------
    result : `tuple`
        The image data as bytes, followed by the lowest and highest
        brightness values used.
    """
    return ImageConverter.convert_fits_to_png_with_stats(
        path, max_dimensions=max_dimensions, center=center, width=width, cmap=cmap, stretch=stretch
    )


def get_fits_header(path: str) -> list[dict[str, str]]:
    """Get the text information (header) saved inside a FITS file.

    Parameters
    ----------
    path : `str`
        The file path to the image.

    Returns
    -------
    header_cards : `list` of `dict`
        A list of information pieces, where each has a 'key', 'value',
        and 'comment'.

    Raises
    ------
    FileNotFoundError
        If the file doesn't exist.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")

    from astropy.io import fits

    with fits.open(path, memmap=False) as hdulist:
        primary_header = hdulist[0].header
        header_list = []
        for key in primary_header.keys():
            if not key:
                continue
            value = primary_header[key]
            comment = primary_header.comments[key]
            header_list.append({"key": key, "value": str(value), "comment": str(comment) if comment else ""})
    return header_list


def get_light_frame_data(
    target: Any, iso: str, exposure: str, index: int = 0, stretch: bool = True
) -> dict[str, Any]:
    """Find a specific telescope image and convert it for web display.

    Parameters
    ----------
    target : `Target`
        The target to look for.
    iso : `str`
        The camera ISO or gain.
    exposure : `str`
        The exposure time.
    index : `int`, optional
        Which matching frame to use if there are multiple. Defaults to 0.
    stretch : `bool`, optional
        Whether to adjust the brightness so things are easier to see.
        Defaults to True.

    Returns
    -------
    light_frame_data : `dict`
        A dictionary with the target ID, brightness stats, and the image data.

    Raises
    ------
    FileNotFoundError
        If the image can't be found or doesn't exist.
    """
    path = get_frame(target, iso, exposure, index)
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"Frame not found: {path}")

    png_bytes, vmin, vmax = convert_fits_to_png_with_stats(path, max_dimensions=2000, stretch=stretch)
    base64_string = base64.b64encode(png_bytes).decode("utf-8")
    return {
        "id": target.id,
        "min": float(vmin),
        "max": float(vmax),
        "image_data": f"data:image/png;base64,{base64_string}",
    }


def get_last_captured_image(config: Any, stretch: bool = True) -> dict[str, Any] | None:
    """Find the newest telescope image and convert it for web display.

    Parameters
    ----------
    config : `AppConfiguration`
        Application settings to know where to look for images.
    stretch : `bool`, optional
        Whether to adjust the image brightness. Defaults to True.

    Returns
    -------
    image_data : `dict` or `None`
        A dictionary with the image data and details, or None if no
        images could be found.
    """
    frames_path = config.get_frames_path()
    pattern = os.path.join(frames_path, "**", "*.fit*")
    files = glob.glob(pattern, recursive=True)

    if not files:
        return None

    files.sort(key=os.path.getmtime, reverse=True)
    latest_path = files[0]

    try:
        png_bytes, vmin, vmax = convert_fits_to_png_with_stats(
            latest_path, max_dimensions=2000, stretch=stretch
        )
        base64_string = base64.b64encode(png_bytes).decode("utf-8")
        parent_folder = os.path.basename(os.path.dirname(latest_path))

        return {
            "id": parent_folder,
            "min": float(vmin),
            "max": float(vmax),
            "image_data": f"data:image/png;base64,{base64_string}",
            "path": latest_path,
        }
    except Exception as error:
        logger.error(f"Failed to load last image: {error}")
        return None


def delete_images(
    paths: list[str], target_catalog: Any = None, target_id: str | None = None
) -> dict[str, Any]:
    """Delete specific image files and update the records.

    Parameters
    ----------
    paths : `list` of `str`
        The file paths of the images to delete.
    target_catalog : `TargetCatalog`, optional
        The database containing our targets, to remove the image records.
    target_id : `str`, optional
        The specific target to remove the images from.

    Returns
    -------
    result : `dict`
        A summary of which files were successfully deleted and which failed.
    """
    deleted_files = []
    failed_files = []

    for path in paths:
        try:
            if os.path.exists(path):
                os.remove(path)
                deleted_files.append(path)
            else:
                deleted_files.append(path)
        except Exception as error:
            failed_files.append({"path": path, "reason": str(error)})

    if target_catalog and target_id:
        target = target_catalog.get(target_id)
        if target:
            target.frames = [frame for frame in target.frames if frame.path not in deleted_files]
            target_catalog.save()

    return {"deleted": deleted_files, "failed": failed_files}
