"""Draw FITS frames for the app's viewer, read their headers, and delete them.

The service is a thin adapter over the library's `Visualization.render_fits`
and `TargetCatalog`. It turns the paths the app sends into paths on this
computer and shapes each picture the way the viewer reads it.
"""

import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from astrometricslib.drivers import is_path_inside, resolve_mounted_path, scratch_directory
from astrometricslib.exceptions import InvalidArgumentError
from backend.services.data.position_mapping import PROCESSED_PICTURE_ENDING, CannotMapError, PositionMapper

logger = logging.getLogger(__name__)

# How many images' detection lists the service keeps. A list of a few
# thousand stars is a few hundred kilobytes; this covers the images a person
# flips between while looking for missed stars.
DETECTION_CACHE_SIZE = 8


def _detection_source(resolved: str) -> str:
    """Pick the file the Detections overlay searches for a shown image.

    The viewer shows a stack as its stretched ``_processed`` picture, but the
    astrometry run finds stars on the linear stack beside it. The stretch
    changes the noise, so searching the picture would give a different list.
    The stack is used when it is there and has the same size, so the markers
    still land on the picture's pixels.

    Parameters
    ----------
    resolved : `str`
        The real path of the shown image.

    Returns
    -------
    path : `str`
        The linear stack, or ``resolved`` itself.
    """
    base, extension = os.path.splitext(resolved)
    if not base.endswith(PROCESSED_PICTURE_ENDING):
        return resolved
    stack = base[: -len(PROCESSED_PICTURE_ENDING)] + extension
    if not os.path.isfile(stack):
        return resolved
    from astropy.io import fits

    try:
        shown = fits.getheader(resolved)
        linear = fits.getheader(stack)
    except OSError:
        return resolved
    same_size = all(shown.get(key) == linear.get(key) for key in ("NAXIS1", "NAXIS2"))
    return stack if same_size else resolved


def _viewer_payload(picture: Any | None) -> dict[str, Any] | None:
    """Shape a rendered picture the way the app's viewer reads it.

    The viewer reads the picture as ``imageData``; the last-image panel
    reads it as ``image_data``. Both keys are sent.

    Parameters
    ----------
    picture : `RenderedImage` or `None`
        The picture the library drew.

    Returns
    -------
    payload : `dict` or `None`
        The picture with its brightness range, header and path, or `None`
        when there is no picture.
    """
    if picture is None:
        return None
    payload = picture.model_dump(by_alias=True)
    payload["image_data"] = payload["imageData"]
    return payload


class ImageService:
    """Draw FITS frames, read FITS headers and delete frame files.

    Parameters
    ----------
    target_service : `TargetService`
        The service whose `Astrometrics` handle is shared, so frames are
        found with the same settings as the rest of the app.
    """

    def __init__(self, target_service: Any) -> None:
        self.target_service = target_service
        self._position_mapper = PositionMapper(self._allowed_image_roots, self._registration_record_roots)
        # The detection lists by real path, each with the file's modified
        # time and size when it was measured, newest last.
        self._detection_cache: OrderedDict[str, tuple[float, int, dict[str, Any]]] = OrderedDict()
        self._detection_cache_lock = threading.Lock()

    @property
    def _astrometrics(self) -> Any:
        """The library handle the target service holds.

        Returns
        -------
        astrometrics : `Astrometrics`
            The handle whose sub-APIs draw and find frames.
        """
        return self.target_service.astrometrics

    def get_light_frame_data_by_id(
        self, target_id: str, iso: str, exposure: str, index: int = 0, stretch: bool = True
    ) -> dict[str, Any] | None:
        """Draw one of a target's frames, chosen by gain, exposure and order.

        Parameters
        ----------
        target_id : `str`
            The target whose frame to draw.
        iso : `str`
            The gain or ISO of the frame.
        exposure : `str`
            The exposure length of the frame.
        index : `int`, optional
            Which matching frame, by order. Defaults to 0.
        stretch : `bool`, optional
            Brighten faint detail automatically. Defaults to `True`.

        Returns
        -------
        result : `dict` or `None`
            The picture as a data URL, with its brightness range.
        """
        picture = self._astrometrics.visualization.render_fits(
            target=target_id, iso=iso, exposure=exposure, index=index, stretch=stretch, kind="data_url"
        )
        return _viewer_payload(picture)

    def get_target_frame_by_id(self, target_id: str, iso: str, exposure: str, index: int = 0) -> str:
        """Find the path of a target's frame by gain, exposure and order.

        Returns
        -------
        path : `str`
            Filesystem path of the matching frame.
        """
        return self._astrometrics.targets.get_frame(target_id, iso, exposure, index)

    def get_fits_header_data(self, path: str) -> list[dict[str, str]]:
        """Extract the FITS header as a list of key/value/comment entries.

        Parameters
        ----------
        path : `str`
            Filesystem path to the FITS file.

        Returns
        -------
        header_entries : `list`
            One dictionary per header card, with ``"key"``, ``"value"``,
            and ``"comment"`` entries.
        """
        return self._astrometrics.targets.get_header(resolve_mounted_path(path))

    def _allowed_image_roots(self) -> list[str]:
        """List the folders a star measurement may read images from.

        Returns
        -------
        roots : `list` [`str`]
            The real paths of the library folder, the frames and stacks
            folders when the settings name them, and the scratch folder.
        """
        config = self.target_service.config
        roots = [Path(config.get_library_path()), Path(scratch_directory())]
        for getter in ("get_frames_path", "get_stacks_path"):
            method = getattr(config, getter, None)
            if callable(method):
                roots.append(Path(method()))
        return [os.path.realpath(root) for root in roots]

    def _registration_record_roots(self) -> list[str]:
        """List the folders searched for stack registration records.

        Returns
        -------
        roots : `list` [`str`]
            The stacks folder when the settings name one, and the ``stacks``
            folder inside the library folder.
        """
        config = self.target_service.config
        roots = [Path(config.get_library_path()) / "stacks"]
        method = getattr(config, "get_stacks_path", None)
        if callable(method):
            roots.append(Path(method()))
        return list(dict.fromkeys(os.path.realpath(root) for root in roots))

    def map_position(self, from_path: str, x: float, y: float, to_path: str) -> dict[str, Any]:
        """Find where a pixel of one image is on another image.

        The viewer uses this to follow a measured star when the image on
        screen changes. The ways to follow it, in order, are the sky position
        (both images plate solved), the registration record of a stack
        (frame to stack, stack to frame, frame to frame, or those chained
        with the sky), and the same pixel as a guess.

        Parameters
        ----------
        from_path, to_path : `str`
            The two FITS images. Both must be inside the library folders or
            the scratch folder.
        x, y : `float`
            The pixel on the first image, 1-based (as in DS9).

        Returns
        -------
        result : `dict`
            ``x`` and ``y`` (1-based, on the second image), ``method`` ("sky",
            "stack alignment", "stack alignment + sky" or "same pixel") and
            ``note``. When the position cannot be followed, ``x`` and ``y`` are
            `None`, ``method`` is "none" and ``note`` says why.

        Raises
        ------
        InvalidArgumentError
            If an image is outside the allowed folders.
        """
        resolved = [os.path.realpath(resolve_mounted_path(path)) for path in (from_path, to_path)]
        roots = self._allowed_image_roots()
        if not all(any(is_path_inside(root, path) for root in roots) for path in resolved):
            raise InvalidArgumentError("The image must be inside the library folders.")
        try:
            mapped = self._position_mapper.map_position(resolved[0], float(x), float(y), resolved[1])
        except CannotMapError as error:
            return {"x": None, "y": None, "method": "none", "note": str(error)}
        return {"x": mapped.x, "y": mapped.y, "method": mapped.method, "note": mapped.note}

    def measure_star_profile(
        self,
        path: str,
        x: float,
        y: float,
        radius_px: float | None = None,
        cut_angle_degrees: float = 0.0,
        cut_half_length_px: float | None = None,
        search_radius_px: float | None = None,
    ) -> dict[str, Any]:
        """Measure the profile of the star at a clicked position.

        Parameters
        ----------
        path : `str`
            The FITS image. It must be inside the library folders or the
            scratch folder.
        x, y : `float`
            The clicked position in 1-based pixels (as in DS9).
        radius_px : `float`, optional
            How far from the star's center the profile reaches. The star's
            width sets it when left out.
        cut_angle_degrees : `float`, optional
            The direction of the straight cut, from +y toward +x.
        cut_half_length_px : `float`, optional
            How far the cut reaches each way from the center.
        search_radius_px : `float`, optional
            Move the click to the nearest star peak within this many image
            pixels. Left out, the click is not moved.

        Returns
        -------
        profile : `dict`
            The measurement: the refined center, the sky, the pixels of the
            radial profile, the Moffat and Gaussian fits, and the cut.

        Raises
        ------
        InvalidArgumentError
            If the image is outside the allowed folders.
        """
        resolved = os.path.realpath(resolve_mounted_path(path))
        if not any(is_path_inside(root, resolved) for root in self._allowed_image_roots()):
            raise InvalidArgumentError("The image must be inside the library folders.")
        result = self._astrometrics.quality.measure_star_profile(
            resolved,
            x,
            y,
            radius_px=radius_px,
            cut_angle_degrees=cut_angle_degrees,
            cut_half_length_px=cut_half_length_px,
            search_radius_px=search_radius_px,
        )
        return result.model_dump()

    def measure_detections(self, path: str) -> dict[str, Any]:
        """List the stars the astrometry run's star finder detects in an image.

        The viewer's Detections overlay draws one marker for each. The result
        is kept for the file's modified time and size, so turning the overlay
        off and on again, or opening another panel on the same image, does not
        search the image twice. A changed file is searched again. A stack's
        ``_processed`` picture is searched on the linear stack beside it, as
        the astrometry run does (see `_detection_source`).

        Parameters
        ----------
        path : `str`
            The FITS image. It must be inside the library folders or the
            scratch folder.

        Returns
        -------
        detections : `dict`
            The measurement: the image size, the detections (each with
            ``x``, ``y`` in 1-based pixels, ``flux`` and ``merged``), their
            count, the merged count, and the kernel width used.

        Raises
        ------
        InvalidArgumentError
            If the image is outside the allowed folders.
        """
        resolved = os.path.realpath(resolve_mounted_path(path))
        if not any(is_path_inside(root, resolved) for root in self._allowed_image_roots()):
            raise InvalidArgumentError("The image must be inside the library folders.")
        resolved = _detection_source(resolved)
        return self._measure_with_cache(
            resolved,
            self._detection_cache,
            self._detection_cache_lock,
            lambda: self._astrometrics.quality.measure_detections(resolved).model_dump(),
        )

    @staticmethod
    def _measure_with_cache(
        resolved: str,
        cache: OrderedDict[str, tuple[float, int, dict[str, Any]]],
        lock: threading.Lock,
        measure: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        """Return a file's measurement, reusing the last while it is unchanged.

        Parameters
        ----------
        resolved : `str`
            The real path of the file.
        cache : `collections.OrderedDict`
            The kept answers by path, each with the file's modified time and
            size, newest last.
        lock : `threading.Lock`
            Guards ``cache``.
        measure : `Callable`
            Measures the file when there is no kept answer.

        Returns
        -------
        result : `dict`
            The measurement as plain data.
        """
        try:
            status = os.stat(resolved)
            stamp = (status.st_mtime, status.st_size)
        except OSError:
            stamp = None
        if stamp is not None:
            with lock:
                cached = cache.get(resolved)
                if cached is not None and cached[:2] == stamp:
                    cache.move_to_end(resolved)
                    return cached[2]
        result = measure()
        if stamp is not None:
            with lock:
                cache[resolved] = (stamp[0], stamp[1], result)
                cache.move_to_end(resolved)
                while len(cache) > DETECTION_CACHE_SIZE:
                    cache.popitem(last=False)
        return result

    def delete_images(self, paths: list[str], target_id: str | None = None) -> dict[str, Any]:
        """Delete files from disk and remove them from the target's frames.

        Parameters
        ----------
        paths : `list`
            Filesystem paths of the images to delete.
        target_id : `str`, optional
            If provided, matching frame entries are also removed from
            this target's frame list.

        Returns
        -------
        result : `dict`
            ``deleted`` (the paths removed) and ``failed`` (each path that
            could not be removed, with the reason).
        """
        resolved_paths = [resolve_mounted_path(path) for path in paths]
        result = self._astrometrics.targets.delete_images(resolved_paths, target=target_id).model_dump()
        if target_id is None:
            self._forget_measurements_of_deleted(result["deleted"])
        return result

    def _forget_measurements_of_deleted(self, deleted_paths: list[str]) -> None:
        """Forget the measurements of frames deleted without a target.

        The library forgets the measurements itself when it is given the
        target. When the caller names none, the target is read from the
        library's folder layout (``lights/<target>/...``), so a deleted
        frame never leaves its numbers behind. A path that is not under a
        ``lights`` folder, or whose target is not in the library, is left.

        Parameters
        ----------
        deleted_paths : `list` [`str`]
            The paths the library reports as deleted.
        """
        subject_ids: list[str] = []
        for path in deleted_paths:
            parts = os.path.normpath(path).split(os.sep)
            positions = [i for i, part in enumerate(parts) if part.lower() == "lights" and i + 2 < len(parts)]
            if not positions:
                continue
            target = self._astrometrics.targets.get(parts[positions[-1] + 1])
            if target is not None:
                subject_ids.append(target.frame_subject_id(path))
        if subject_ids:
            self._astrometrics.quality.forget(subject_ids)

    def get_last_image(self, stretch: bool = True) -> dict[str, Any] | None:
        """Find the most recently created FITS file and draw it.

        Parameters
        ----------
        stretch : `bool`, optional
            Whether to apply the stretch/normalization before rendering.

        Returns
        -------
        result : `dict` or `None`
            The picture as a data URL, or `None` if no FITS file exists.
        """
        return _viewer_payload(self._astrometrics.visualization.get_last_captured_image(stretch=stretch))

    def convert_fits(self, path: str, maxdim: int = 2000, stretch: bool = True) -> dict[str, Any] | None:
        """Draw a FITS file, named by its path, for the viewer.

        Parameters
        ----------
        path : `str`
            The FITS file.
        maxdim : `int`, optional
            Longest side of the picture, in pixels. Defaults to 2000.
        stretch : `bool`, optional
            Brighten faint detail automatically. Defaults to `True`.

        Returns
        -------
        result : `dict` or `None`
            The picture as a data URL, with its brightness range and FITS
            header.
        """
        picture = self._astrometrics.visualization.render_fits(
            resolve_mounted_path(path), kind="data_url", max_dimensions=maxdim, stretch=stretch
        )
        return _viewer_payload(picture)
