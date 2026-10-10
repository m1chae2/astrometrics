"""Draw FITS frames for the app's viewer, read their headers, and delete them.

The service is a thin adapter over the library's `Visualization.render_fits`
and `TargetCatalog`. It turns the paths the app sends into paths on this
computer and shapes each picture the way the viewer reads it.
"""

import logging
from typing import Any

from astrometricslib import RenderedImage, resolve_mounted_path

logger = logging.getLogger(__name__)


def _viewer_payload(picture: RenderedImage | None) -> dict[str, Any] | None:
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

    REQ: SR-2.2: The system SHALL store captured images with associated
    metadata (FITS Headers).

    Parameters
    ----------
    target_service : `TargetService`
        The service whose `Astrometrics` handle is shared, so frames are
        found with the same settings as the rest of the app.
    """

    def __init__(self, target_service: Any) -> None:
        self.target_service = target_service

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
        return self._astrometrics.targets.delete_images(resolved_paths, target=target_id)

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
