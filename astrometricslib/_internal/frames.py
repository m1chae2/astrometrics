"""Purpose: Private `frames` names of `astrometricslib`.

Description: The private names of the `frames` group: frame and target helpers.
Only `astrometricslib` itself, and the tests of either library, may import this
module. Each name is imported from the module where its code lives today, so
moving the code later changes this file only.
"""

from astrometricslib._internal.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.models.applied_camera_profile import AppliedCameraProfile
from astrometricslib.models.camera_profile import CameraProfile
from astrometricslib.models.stellar_source import has_catalog_magnitude
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.shared.frame_scanning import classify_and_sort_fits_files
from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject_id
from astrometricslib.pipelines.shared.quality.frame_selection import (
    FrameSelection,
    parse_iso_time,
    select_library_frames,
)
from astrometricslib.pipelines.shared.stack_preview_path import preview_path_for
from astrometricslib.pipelines.shared.target_sessions import TargetSession, derive_target_sessions
from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string
from astrometricslib.utilities.target_object_category import target_object_category

__all__ = [
    "AppliedCameraProfile",
    "CameraProfile",
    "FrameSelection",
    "TargetSession",
    "classify_and_sort_fits_files",
    "derive_target_sessions",
    "frame_is_spectral",
    "frame_subject_id",
    "has_catalog_magnitude",
    "parse_coordinate_string",
    "parse_iso_time",
    "preview_path_for",
    "resolve_camera_profile",
    "select_library_frames",
    "target_object_category",
]
