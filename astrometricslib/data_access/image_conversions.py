"""Purpose: Image Conversions.

Description: Extracted astronomical target image processing, conversion of
FITS arrays to raw base64 encoded PNG data URLs, and file exclusions.
The pure numpy-scaling logic (ImageScaler) lives in
tasks/target_tasks/image_scaling_tasks.py instead -- everything in this
module reads or writes files on disk.
"""

import base64
import glob
import logging
import os
from io import BytesIO
from typing import Any

from PIL import Image

from astrometricslib.tasks.target_tasks.image_scaling_tasks import ImageScaler
from astrometricslib.utilities.exceptions import AstroLibError
from astrometricslib.utilities.image import AstrometricsImage

logger = logging.getLogger(__name__)

# Shared in-memory cache for rendered PNG frames
_png_cache: dict[tuple[str, int, float | None, float | None, str, bool], tuple[bytes, float, float]] = {}


class ImageConverter:
    """Stateless utility for FITS conversion, scaling, and PNG output."""

    @staticmethod
    def convert_fits_to_png_with_stats(
        path: str,
        max_dimensions: int = 2000,
        center: float | None = None,
        width: float | None = None,
        cmap: str = "gray",
        stretch: bool = True,
    ) -> tuple[bytes, float, float]:
        """Perform standard FITS scaling and return raw PNG bytes and stats.

        Includes an in-memory cache to speed up repeated queries.

        Returns
        -------
        result : `tuple`
            `(png_bytes, vmin, vmax)`.
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
        """Convert a FITS file to a base64 PNG data URL with scale values.

        Returns
        -------
        result : `dict`
            Contains `image_data`, `min`, `max`.

        Raises
        ------
        AstroLibError
            If FITS-to-PNG conversion fails for any reason.
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
    """Retrieve a frame path for a target by ISO, exposure, and index.

    Parameters
    ----------
    target : `astrometricslib.models.target.Target`
        The target whose frames are searched.
    iso : `str`
        The ISO/gain setting to match.
    exposure : `str`
        The exposure length to match.
    index : `int`, optional
        Which matching frame to return, by order. Defaults to 0.

    Returns
    -------
    frame_path : `str`
        The path of the matching frame.

    Raises
    ------
    ValueError
        If no frame matches the requested ISO/exposure/index.
    """

    def to_float(val: Any) -> float | None:
        """Convert a value to float safely, returning `None` on failure.

        Returns
        -------
        result : `float` or `None`
            The converted float value, or `None` if `val` cannot be
            converted.
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
    """Convert a FITS file at the given path to a base64 PNG data URL.

    Parameters
    ----------
    path : `str`
        Path to the FITS file to convert.
    max_dimensions : `int`, optional
        Maximum output dimension in pixels. Defaults to 2000.
    stretch : `bool`, optional
        Whether to apply the stretch/normalization before rendering.
        Defaults to `True`.

    Returns
    -------
    png_data : `dict[str, Any]`
        The base64-encoded PNG and scale metadata.
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
    """Perform standard FITS scaling and return raw PNG bytes and stats.

    Parameters
    ----------
    path : `str`
        Path to the FITS file to convert.
    max_dimensions : `int`, optional
        Maximum output dimension in pixels. Defaults to 2000.
    center : `float`, optional
        Stretch center override.
    width : `float`, optional
        Stretch width override.
    cmap : `str`, optional
        Matplotlib colormap name. Defaults to ``"gray"``.
    stretch : `bool`, optional
        Whether to apply the stretch/normalization before rendering.
        Defaults to `True`.

    Returns
    -------
    result : `tuple[bytes, float, float]`
        A tuple ``(png_bytes, min_value, max_value)`` of the raw
        PNG bytes and the scale bounds used to render them.
    """
    return ImageConverter.convert_fits_to_png_with_stats(
        path, max_dimensions=max_dimensions, center=center, width=width, cmap=cmap, stretch=stretch
    )


def get_fits_header(path: str) -> list[dict[str, str]]:
    """Extract the main FITS header card entries from the file.

    Parameters
    ----------
    path : `str`
        Path to the FITS file to read.

    Returns
    -------
    header_cards : `list[dict[str, str]]`
        The FITS primary header's card entries.

    Raises
    ------
    FileNotFoundError
        If `path` does not exist on disk.
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
    """Find a light frame record and scale it to base64 PNG data.

    Parameters
    ----------
    target : `astrometricslib.models.target.Target`
        The target whose frames are searched.
    iso : `str`
        The ISO/gain setting to match.
    exposure : `str`
        The exposure length to match.
    index : `int`, optional
        Which matching frame to use, by order. Defaults to 0.
    stretch : `bool`, optional
        Whether to apply the stretch/normalization before rendering.
        Defaults to `True`.

    Returns
    -------
    light_frame_data : `dict[str, Any]`
        The scaled base64 PNG data and associated metadata.

    Raises
    ------
    FileNotFoundError
        If no frame matches the request, or the matched frame's path
        does not exist on disk.
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
    """Locate and return the most recently modified FITS image file.

    Parameters
    ----------
    config : `astrometricslib.utilities.config_loader.AppConfiguration`
        Application configuration, used to resolve the frames directory.
    stretch : `bool`, optional
        Whether to apply the stretch/normalization before rendering.
        Defaults to `True`.

    Returns
    -------
    image_data : `dict[str, Any]` or `None`
        The scaled base64 PNG data for the most recently modified
        FITS file, or `None` if no FITS file is found or conversion
        fails.
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
    """Remove files from disk and the target's frame list if present.

    Parameters
    ----------
    paths : `list` [`str`]
        File paths to delete.
    target_catalog : `astrometricslib.api.targets.TargetCatalog`, optional
        Catalog to update when `target_id` is also given.
    target_id : `str`, optional
        If given (with `target_catalog`), remove matching frame
        records from this target's frame list.

    Returns
    -------
    result : `dict[str, Any]`
        A summary of the deletion outcome.
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
