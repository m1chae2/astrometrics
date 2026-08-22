"""Image ingestion, FITS header parsing, and frame retrieval service."""

import logging
import os
from typing import Any

from astrometricslib import FilterType, FrameRecord, Target

logger = logging.getLogger(__name__)


class ImageService:
    """Handle file ingestion, FITS header parsing, and FrameRecord creation.

    REQ: SR-2.2: The system SHALL store captured images with associated
    metadata (FITS Headers).
    """

    def __init__(self, target_service=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self._imaging_api = None
        self.target_service = target_service

    @property
    def imaging_api(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """The lazily-constructed ProcessingPipelines astrometrics.

        Returns
        -------
        imaging_api : `ProcessingPipelines`
            the astrometricslib high-level interface used for FITS
            conversion, header reading, and image deletion.
            Constructed on first access.
        """
        if self._imaging_api is None:
            from astrometricslib import ProcessingPipelines, get_configuration

            self._imaging_api = ProcessingPipelines(get_configuration())
        return self._imaging_api

    @property
    def visualization_api(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """The `Visualization` astrometrics used for FITS-to-PNG rendering.

        Reuses `target_service.astrometrics.visualization` when a
        `target_service` was injected, since `Visualization`
        needs the full `Astrometrics` astrometrics (not just config) to
        resolve frame paths through `Astrometrics.targets`.

        Returns
        -------
        visualization_api : `Visualization`
            the astrometricslib high-level interface used for FITS
            rendering.
        """
        if self.target_service is not None:
            return self.target_service.astrometrics.visualization
        from astrometricslib import Astrometrics

        return Astrometrics().visualization

    @property
    def target_registry_api(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """The `TargetCatalog` used for frame lookup and deletion.

        Reuses `target_service.astrometrics.targets` when a
        `target_service` was injected, since `TargetCatalog` needs the
        full `Astrometrics` astrometrics (not just config) to construct.

        Returns
        -------
        target_registry_api : `TargetCatalog`
            the astrometricslib high-level interface used for frame
            path resolution and image deletion.
        """
        if self.target_service is not None:
            return self.target_service.astrometrics.targets
        from astrometricslib import Astrometrics

        return Astrometrics().targets

    @staticmethod
    def get_filter_type(header: dict[str, Any]) -> FilterType:
        """Extract the FilterType from a FITS header, or infer it.

        Parameters
        ----------
        header : `dict`
            FITS header key/value mapping. The ``"FILTER"`` entry, if
            present, is matched against known filter name tokens.

        Returns
        -------
        filter_type : `FilterType`
            The matched filter type, or `FilterType.NONE` if no token
            matches.
        """
        filter_str = str(header.get("FILTER", "")).upper()

        mapping = {
            "SPECTROSCOPY": FilterType.SPEC,
            "SPEC": FilterType.SPEC,
            "H-ALPHA": FilterType.Ha,
            "HA": FilterType.Ha,
            "OIII": FilterType.OIII,
            "SII": FilterType.SII,
            "RED": FilterType.R,
            "GREEN": FilterType.G,
            "BLUE": FilterType.B,
            "L": FilterType.L,
            "R": FilterType.R,
            "G": FilterType.G,
            "B": FilterType.B,
            "NONE": FilterType.NONE,
        }

        # Sort by length descending to match most specific terms first
        sorted_keys = sorted(mapping.keys(), key=len, reverse=True)
        for key in sorted_keys:
            if key in filter_str:
                return mapping[key]

        return FilterType.NONE

    def create_frame_record(self, path: str, camera: str | None = None) -> FrameRecord:
        """Parse a file and returns a standard FrameRecord.

        Parameters
        ----------
        path : `str`
            Filesystem path to the frame file.
        camera : `str`, optional
            Camera identifier used to disambiguate header parsing.

        Returns
        -------
        record : `FrameRecord`
            The parsed frame record.

        Raises
        ------
        FileNotFoundError
            If ``path`` does not exist on disk.
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f"{path} not found")
        return self.imaging_api.create_frame_record(path, camera)

    def add_frame_to_target(self, target: Target, path: str) -> None:
        """Parse the frame at path and add it to the Target's collections.

        No-op if a frame with the same path is already tracked.
        """
        try:
            # Check if already tracked in modern frames
            record = next((f for f in target.frames if f.path == path), None)

            if not record:
                record = self.create_frame_record(path)
                target.frames.append(record)

        except Exception as e:
            logger.error(f"Error adding frame {path} to target {target.id}: {e}")

    def scan_target_directory(self, target: Target, frames_root_path: str) -> None:
        """Recursively scan the filesystem for a target's frames.

        Hydrates the Target object with any discovered frames.
        """
        self.imaging_api.scan_target_directory(target, frames_root_path)

    def get_target_frame(self, target, iso: str, exposure: str, index: int = 0) -> str:  # ruff: ignore[missing-type-function-argument]
        """Retrieve a frame path for a target based on ISO/Exposure/Index.

        Returns
        -------
        path : `str`
            Filesystem path of the matching frame.
        """
        return self.target_registry_api.get_frame(target, iso, exposure, index)

    def convert_fits_to_png_with_stats(
        self,
        path: str,
        maxdim: int = 2000,
        center: float | None = None,
        width: float | None = None,
        cmap: str = "gray",
        stretch: bool = True,
    ) -> tuple[bytes, float, float]:
        """Convert a FITS file to a PNG, returning pixel statistics.

        Parameters
        ----------
        path : `str`
            Filesystem path to the FITS file.
        maxdim : `int`, optional
            Maximum output dimension in pixels, preserving aspect ratio.
        center : `float`, optional
            Stretch center value. If `None`, computed automatically.
        width : `float`, optional
            Stretch width value. If `None`, computed automatically.
        cmap : `str`, optional
            Matplotlib colormap name used to render the PNG.
        stretch : `bool`, optional
            Whether to apply the stretch/normalization before rendering.

        Returns
        -------
        result : `tuple`
            A ``(png_bytes, minimum_value, maximum_value)`` tuple, where
            ``png_bytes`` is the encoded image and the min/max are the
            pixel statistics used for the stretch.
        """
        return self.visualization_api.convert_fits_to_png_with_stats(
            path, max_dimensions=maxdim, center=center, width=width, cmap=cmap, stretch=stretch
        )

    def get_light_frame_data_by_id(
        self, target_id: str, iso: str, exposure: str, index: int = 0, stretch: bool = True
    ) -> dict[str, Any]:
        """Resolve the target object then call `get_light_frame_data`.

        Returns
        -------
        result : `dict`
            Rendered light frame payload from the ImagingAPI astrometrics.

        Raises
        ------
        ValueError
            If no target exists for ``target_id``.
        """
        target = self.target_service.get_target(target_id)
        if not target:
            raise ValueError(f"Target not found: {target_id}")
        return self.get_light_frame_data(target, iso=iso, exposure=exposure, index=index, stretch=stretch)

    def get_target_frame_by_id(self, target_id: str, iso: str, exposure: str, index: int = 0) -> str:
        """Resolve the target object then call `get_target_frame`.

        Returns
        -------
        path : `str`
            Filesystem path of the matching frame.

        Raises
        ------
        ValueError
            If no target exists for ``target_id``.
        """
        target = self.target_service.get_target(target_id)
        if not target:
            raise ValueError(f"Target not found: {target_id}")
        return self.get_target_frame(target, iso=iso, exposure=exposure, index=index)

    def get_light_frame_data(
        self,
        target,  # ruff: ignore[missing-type-function-argument]
        iso: str,
        exposure: str,
        index: int = 0,
        stretch: bool = True,
    ) -> dict[str, Any]:
        """Resolve a matching frame and return its rendered light data.

        Parameters
        ----------
        target : `Target`
            Target whose frames are searched for a match.
        iso : `str`
            ISO value to match, as recorded on the frame.
        exposure : `str`
            Exposure value to match, as recorded on the frame.
        index : `int`, optional
            Index into the matching frames when more than one matches.
        stretch : `bool`, optional
            Whether to apply the stretch/normalization before rendering.

        Returns
        -------
        result : `dict`
            Rendered light frame payload from the ImagingAPI astrometrics.
        """
        return self.visualization_api.get_light_frame_data(
            target, iso=iso, exposure=exposure, index=index, stretch=stretch
        )

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
        return self.target_registry_api.get_header(path)

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
            Deletion result payload from the ImagingAPI astrometrics.
        """
        return self.target_registry_api.delete_images(paths, target_id=target_id)

    def get_last_image(self, stretch: bool = True) -> dict[str, Any] | None:
        """Find the most recently created FITS file and convert it to PNG.

        Parameters
        ----------
        stretch : `bool`, optional
            Whether to apply the stretch/normalization before rendering.

        Returns
        -------
        result : `dict` or `None`
            Base64-encoded PNG payload, or `None` if no FITS file exists.
        """
        return self.visualization_api.get_last_captured_image(stretch=stretch)

    def convert_fits(self, path: str, maxdim: int = 2000, stretch: bool = True) -> dict[str, Any] | None:
        """Convert FITS file by path to base64 PNG with min/max stats.

        Returns
        -------
        result : `dict` or `None`
            Base64-encoded PNG payload with min/max pixel statistics,
            or `None` if the FITS file could not be converted.
        """
        return self.visualization_api.convert_fits_to_png(path, max_dimensions=maxdim, stretch=stretch)
