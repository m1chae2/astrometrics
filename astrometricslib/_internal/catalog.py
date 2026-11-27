"""Purpose: Private `catalog` names of `astrometricslib`.

Description: The private names of the `catalog` group: result models of the
catalog calls. Only `astrometricslib` itself, and the tests of either library,
may import this module. Each name is imported from the module where its code
lives today, so moving the code later changes this file only.
"""

from astrometricslib.models.catalog_queries import (
    OverlayStar,
    ReindexReport,
    TargetQueryResult,
    TargetReindexChange,
    TargetStarCount,
)
from astrometricslib.models.field_centers import FieldCenter, ImagedFieldCenters
from astrometricslib.models.image_deletion import FailedDeletion, ImageDeletionResult
from astrometricslib.models.quality.quality_summary import TargetSessionContribution
from astrometricslib.models.star_catalog import (
    PeriodicitySearch,
    SpectralClassCount,
    StarCatalogStatistics,
    StarOverlay,
)
from astrometricslib.models.stellar_source import FileItem, GroupedFrameStat, TargetFilesResponse
from astrometricslib.models.target import FitsHeaderEntry, RenderedImage, ViewableImage

__all__ = [
    "FailedDeletion",
    "FieldCenter",
    "FileItem",
    "FitsHeaderEntry",
    "GroupedFrameStat",
    "ImageDeletionResult",
    "ImagedFieldCenters",
    "OverlayStar",
    "PeriodicitySearch",
    "ReindexReport",
    "RenderedImage",
    "SpectralClassCount",
    "StarCatalogStatistics",
    "StarOverlay",
    "TargetFilesResponse",
    "TargetQueryResult",
    "TargetReindexChange",
    "TargetSessionContribution",
    "TargetStarCount",
    "ViewableImage",
]
