"""Draw FITS frames for the app's viewer, measure stars on them, delete them.

The service is a thin adapter over the library's `Visualization.render`,
`QualityRecords` and `TargetCatalog`. It turns the paths the app sends into
paths on this computer, refuses a path outside the folders the app may read
(`image_paths.resolve_allowed_image`), and shapes each picture the way the
viewer reads it (`ViewerImage`): the library gives the picture's bytes, and
this service turns them into a data URL and adds the file's FITS header. A
frame's header alone is read through ``target:get_frame_header``
(`TargetService.get_frame_header`).
"""

import base64
import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib.drivers import resolve_mounted_path
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from backend.services.data.image_paths import PROCESSED_PICTURE_ENDING, resolve_allowed_image

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


class ViewerImage(BaseModel):
    """A picture the way the app's viewer reads it.

    Attributes
    ----------
    id : `str`
        What the picture shows, such as the target id or the frame's folder.
    min, max : `float`
        The brightness range shown, in pixel values (0 and 255 for a JPEG).
    image_data : `str`
        The picture as a ``data:image/png;base64,...`` (or JPEG) URL.
    headers : `list` [`dict` [`str`, `str`]]
        The FITS header of the file that was drawn: ``key``, ``value`` and
        ``comment`` of each card.
    path : `str` or `None`
        The file that was drawn.
    stretch_parameters : `dict` [`str`, `float`] or `None`
        The automatic stretch the picture was drawn with (``blackPoint``,
        ``whitePoint``, ``midtones``), or `None` when it was drawn without
        one.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default="", alias="id")
    min: float = Field(alias="min")
    max: float = Field(alias="max")
    image_data: str = Field(alias="imageData")
    headers: list[dict[str, str]] = Field(default_factory=list, alias="headers")
    path: str | None = Field(default=None, alias="path")
    stretch_parameters: dict[str, float] | None = Field(default=None, alias="stretchParameters")


def _viewer_payload(
    picture: Any | None, image_id: str, read_header: Callable[[str], Any]
) -> dict[str, Any] | None:
    """Shape a rendered picture the way the app's viewer reads it.

    The viewer reads the picture as ``imageData``; the last-image panel
    reads it as ``image_data``. Both keys are sent.

    Parameters
    ----------
    picture : `RenderedImage` or `None`
        The picture the library drew.
    image_id : `str`
        What the picture shows, copied into the reply.
    read_header : `Callable` [[`str`], `astropy.io.fits.Header`]
        Reads a file's FITS header (the library's
        ``targets.frames.get_header``).

    Returns
    -------
    payload : `dict` or `None`
        The picture with its brightness range, header and path, or `None`
        when there is no picture.
    """
    if picture is None:
        return None
    headers: list[dict[str, str]] = []
    if picture.path:
        try:
            header = read_header(picture.path)
            headers = [
                {"key": key, "value": str(header[key]), "comment": str(header.comments[key] or "")}
                for key in header.keys()
                if key
            ]
        except (OSError, ValueError, NotFoundError) as error:
            logger.warning("Could not read the header of %s while drawing it: %s", picture.path, error)
    stretch = picture.stretch_parameters
    encoded = base64.b64encode(picture.picture).decode("ascii")
    payload = ViewerImage(
        id=image_id,
        min=picture.black,
        max=picture.white,
        image_data=f"data:{picture.media_type};base64,{encoded}",
        headers=headers,
        path=picture.path,
        stretch_parameters=None if stretch is None else stretch.model_dump(by_alias=True),
    ).model_dump(by_alias=True)
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
        self, target_id: str, gain: float | None, exposure_s: float, index: int = 0, stretch: bool = True
    ) -> dict[str, Any] | None:
        """Draw one of a target's frames, chosen by gain, exposure and order.

        Parameters
        ----------
        target_id : `str`
            The target whose frame to draw.
        gain : `float` or `None`
            The gain or ISO of the frame, or `None` for a frame whose gain
            is not known (any gain then matches).
        exposure_s : `float`
            The exposure length of the frame, in seconds.
        index : `int`, optional
            Which matching frame, by order. Defaults to 0.
        stretch : `bool`, optional
            Brighten faint detail automatically. Defaults to `True`.

        Returns
        -------
        result : `dict` or `None`
            The picture as a data URL, with its brightness range.

        Notes
        -----
        A gain or exposure that is not a number raises
        `InvalidArgumentError`, and no matching frame `NotFoundError`.
        """
        path = self.get_target_frame_by_id(target_id, gain, exposure_s, index)
        picture = self._astrometrics.visualization.render(path, stretch=stretch)
        # The reply uses the stored target id, whatever spelling the app sent.
        shown_id = self.target_service.subjects.target(target_id).id
        return _viewer_payload(picture, shown_id, self._astrometrics.targets.frames.get_header)

    def get_target_frame_by_id(
        self, target_id: str, gain: float | None, exposure_s: float, index: int = 0
    ) -> str:
        """Find the path of a target's frame by gain, exposure and order.

        Parameters
        ----------
        target_id : `str`
            The target whose frame to find.
        gain : `float` or `None`
            The gain or ISO of the frame, or `None` for a frame whose gain
            is not known (any gain then matches).
        exposure_s : `float`
            The exposure length of the frame, in seconds.
        index : `int`, optional
            Which matching frame, by order. Defaults to 0.

        Returns
        -------
        path : `str`
            Filesystem path of the matching frame. An index past the last
            match gives the last one.

        Raises
        ------
        InvalidArgumentError
            If the gain or exposure is not a number.
        NotFoundError
            If no light frame has that gain and exposure.
        """
        target = self.target_service.subjects.target(target_id)
        try:
            gain_number = None if gain is None else float(gain)
            exposure_number = float(exposure_s)
        except (TypeError, ValueError) as error:
            raise InvalidArgumentError(
                "The gain and exposure must be numbers.", details={"gain": gain, "exposure_s": exposure_s}
            ) from error
        matches = self._astrometrics.targets.frames.query(
            target, gain=gain_number, exposure_s=exposure_number
        )
        if not len(matches):
            raise NotFoundError(
                f"No frame found for gain {gain}, exposure {exposure_s} s, index {index}",
                details={"target": target.id, "gain": gain, "exposure_s": exposure_s, "index": index},
            )
        return matches[max(0, min(index, len(matches) - 1))].path

    def _resolve(self, path: str) -> str:
        """Give the real path of an image the app sent, in the allowed folders.

        Parameters
        ----------
        path : `str`
            The path as the app sent it.

        Returns
        -------
        resolved : `str`
            The real path on this computer.

        Notes
        -----
        A path outside the library folders and the scratch folder raises
        `InvalidArgumentError` (`image_paths.resolve_allowed_image`).
        """
        return resolve_allowed_image(path, self.target_service.config)

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
            The library's answer (`quality.frames.map_positions`, for one
            point): ``x`` and ``y`` (1-based, on the second image), ``method``
            ("sky", "stack alignment", "stack alignment + sky", "star
            matching", "same pixel" or "none") and ``note``. When the
            position cannot be followed, ``x`` and ``y`` are `None`,
            ``method`` is "none" and ``note`` says why.

        Notes
        -----
        An image outside the allowed folders raises `InvalidArgumentError`
        (`image_paths.resolve_allowed_image`), and a missing image file the
        library's `NotFoundError`.
        """
        source, destination = self._resolve(from_path), self._resolve(to_path)
        mapping = self._astrometrics.quality.frames.map_positions(
            source, destination, points=[(float(x), float(y))]
        )
        (point,) = mapping.points
        return {"x": point.mapped_x, "y": point.mapped_y, "method": mapping.method, "note": mapping.note}

    def measure_star_profile(
        self,
        path: str,
        x: float,
        y: float,
        radius_px: float | None = None,
        cut_angle_deg: float = 0.0,
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
        cut_angle_deg : `float`, optional
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

        Notes
        -----
        A path outside the library folders and the scratch folder raises
        `InvalidArgumentError` (`image_paths.resolve_allowed_image`).
        """
        resolved = self._resolve(path)
        result = self._astrometrics.quality.detections.measure_profile(
            resolved,
            (x, y),
            radius_px=radius_px,
            cut_angle_deg=cut_angle_deg,
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

        Notes
        -----
        A path outside the library folders and the scratch folder raises
        `InvalidArgumentError` (`image_paths.resolve_allowed_image`).
        """
        resolved = _detection_source(self._resolve(path))
        return self._measure_with_cache(
            resolved,
            self._detection_cache,
            self._detection_cache_lock,
            lambda: self._astrometrics.quality.detections.measure_detections(resolved).model_dump(),
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
        target = None if target_id is None else self._astrometrics.targets.get(target_id)
        return self._astrometrics.targets.frames.delete(target, resolved_paths).model_dump()

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
        picture = self._astrometrics.visualization.render_newest(stretch=stretch)
        # The panel names the picture after the folder the frame is in.
        folder = (
            os.path.basename(os.path.dirname(picture.path)) if picture is not None and picture.path else ""
        )
        return _viewer_payload(picture, folder, self._astrometrics.targets.frames.get_header)

    def convert_fits(self, path: str, maxdim: int = 2000, stretch: bool = True) -> dict[str, Any] | None:
        """Draw a FITS file, named by its path, for the viewer.

        Parameters
        ----------
        path : `str`
            The FITS file. It must be inside the library folders or the
            scratch folder.
        maxdim : `int`, optional
            Longest side of the picture, in pixels. Defaults to 2000.
        stretch : `bool`, optional
            Brighten faint detail automatically. Defaults to `True`.

        Returns
        -------
        result : `dict` or `None`
            The picture as a data URL, with its brightness range and FITS
            header.

        Notes
        -----
        A path outside the library folders and the scratch folder raises
        `InvalidArgumentError` (`image_paths.resolve_allowed_image`).
        """
        picture = self._astrometrics.visualization.render(
            self._resolve(path), max_dimensions=maxdim, stretch=stretch
        )
        return _viewer_payload(picture, "", self._astrometrics.targets.frames.get_header)
