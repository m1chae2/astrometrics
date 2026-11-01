"""Names and sizes the pictures that go with a finished stack.

The code lives in `astrometricslib.utilities.stack_preview_path`, so the
`Target` model can name a stack's preview without importing a pipeline. This
module keeps the old import path working.
"""

from astrometricslib.utilities.stack_preview_path import (
    PREVIEW_JPEG_QUALITY,
    PREVIEW_SETTINGS_SUFFIX,
    PREVIEW_SUFFIX,
    PROCESSED_FITS_SUFFIX,
    is_preview_path,
    is_processed_fits_path,
    preview_path_for,
    preview_settings_path_for,
    processed_fits_path_for,
)

__all__ = [
    "PREVIEW_JPEG_QUALITY",
    "PREVIEW_SETTINGS_SUFFIX",
    "PREVIEW_SUFFIX",
    "PROCESSED_FITS_SUFFIX",
    "is_preview_path",
    "is_processed_fits_path",
    "preview_path_for",
    "preview_settings_path_for",
    "processed_fits_path_for",
]
