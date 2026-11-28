"""Purpose: Private `catalog` names of `astrometricslib`.

Description: The private names of the `catalog` group: result models of the
catalog calls. Only `astrometricslib` itself, and the tests of either library,
may import this module. Each name is imported from the module where its code
lives today, so moving the code later changes this file only.
"""

from astrometricslib.models.catalog_queries import (
    OverlayStar,
    ReindexReport,
    TargetCameraCounts,
    TargetNightCounts,
    TargetReindexChange,
    TargetStarCount,
)
from astrometricslib.models.field_centers import FieldCenter, ImagedFieldCenters
from astrometricslib.models.image_deletion import FailedDeletion, ImageDeletionResult
from astrometricslib.models.quality.quality_summary import TargetSessionContribution
from astrometricslib.models.resolution import Resolution
from astrometricslib.models.star_catalog import (
    PeriodicitySearch,
    SpectralClassCount,
    StarCatalogStatistics,
    StarOverlay,
)
from astrometricslib.models.stellar_source import FileItem, GroupedFrameStat, TargetFilesResponse
from astrometricslib.models.target import FitsHeaderEntry, ImageCrop, RenderedImage

__all__ = [
    "FailedDeletion",
    "FieldCenter",
    "FileItem",
    "FitsHeaderEntry",
    "GroupedFrameStat",
    "ImageCrop",
    "ImageDeletionResult",
    "ImagedFieldCenters",
    "OverlayStar",
    "PeriodicitySearch",
    "ReindexReport",
    "RenderedImage",
    "Resolution",
    "SpectralClassCount",
    "StarCatalogStatistics",
    "StarOverlay",
    "TargetCameraCounts",
    "TargetFilesResponse",
    "TargetNightCounts",
    "TargetReindexChange",
    "TargetSessionContribution",
    "TargetStarCount",
]
