"""Shared helpers for the tuners that read stored quality rows.

A tuner derives a tuned value from this observatory's own stored data (see
`astrometricslib.foundation.tuning`). The tuners of stacking and astrometry
need the same few things, so they are kept here once:

- the stored quality rows of one metric that belong to the setup's camera,
  each with the capture setting it was taken with;
- the widest gap in a list of numbers (the robust spread is
  `astrometricslib.utilities.robust_statistics.robust_sigma`);
- a check of whether a camera is a colour camera, read from the header of one
  of its frames.

The functions that take plain numbers do no reading, so a test can give them
numbers with a known answer.
"""

import math
from collections.abc import Iterable
from itertools import pairwise
from typing import Any

from astrometricslib._internal.drivers.camera_profile_store import camera_identity
from astrometricslib._internal.drivers.fits_access import frame_uses_color_filter_array
from astrometricslib._internal.drivers.quality_store import QualityStore, quality_database_path
from astrometricslib.models.quality.capture_setting import CaptureSetting
from astrometricslib.models.quality.measurement import QualityMeasurement, SubjectLevel


def widest_gap(values: Iterable[float]) -> tuple[float, float] | None:
    """Find the widest empty stretch between neighboring numbers.

    Parameters
    ----------
    values : `Iterable` [`float`]
        The numbers, in any order.

    Returns
    -------
    gap : `tuple` [`float`, `float`] or `None`
        The two numbers on either side of the widest gap, lower first. `None`
        when there are fewer than two different numbers.
    """
    ordered = sorted(set(values))
    if len(ordered) < 2:
        return None
    pairs = list(pairwise(ordered))
    return max(pairs, key=lambda pair: pair[1] - pair[0])


def camera_rows(
    configuration: Any,
    camera_name: str,
    metric: str,
    *,
    level: SubjectLevel | None = None,
    subject_id_prefix: str | None = None,
) -> list[tuple[QualityMeasurement, CaptureSetting]]:
    """Read the newest stored rows of one metric that a camera took.

    Parameters
    ----------
    configuration : `Any`
        The loaded config, which says where the quality database is.
    camera_name : `str`
        The camera, in any spelling its profile recognizes.
    metric : `str`
        The registered metric name.
    level : `SubjectLevel`, optional
        Only subjects of this level.
    subject_id_prefix : `str`, optional
        Only subjects whose id starts with this text.

    Returns
    -------
    rows : `list` [`tuple` [`QualityMeasurement`, `CaptureSetting`]]
        Each measured row (one with a value) with its capture setting, oldest
        first. Empty when the library has no such rows or no setting of this
        camera. The store is only read.
    """
    store = QualityStore(quality_database_path(configuration))
    identity = camera_identity(camera_name, configuration)
    identity_by_camera: dict[str, str] = {}
    settings: dict[int, CaptureSetting] = {}
    for setting_id, setting in store.get_capture_settings().items():
        if setting.camera not in identity_by_camera:
            identity_by_camera[setting.camera] = camera_identity(setting.camera, configuration)
        if identity_by_camera[setting.camera] == identity:
            settings[setting_id] = setting
    if not settings:
        return []
    found = store.query(metric=metric, level=level, subject_id_prefix=subject_id_prefix)
    return [
        (row, settings[row.subject.capture_setting_id])
        for row in found
        if row.value is not None
        and row.subject.capture_setting_id is not None
        and row.subject.capture_setting_id in settings
    ]


def camera_is_colour(astrometrics: Any, camera_name: str, configuration: Any) -> bool | None:
    """Tell whether a camera is a colour camera, from the header of a frame.

    A colour camera writes a Bayer pattern into the header (``BAYERPAT``).
    The first light frame of the camera whose header can be read decides.

    Parameters
    ----------
    astrometrics : `Any`
        The `astrometricslib.Astrometrics` library, which lists the targets
        and their frames.
    camera_name : `str`
        The camera, in any spelling its profile recognizes.
    configuration : `Any`
        The loaded config, for the camera profiles.

    Returns
    -------
    is_colour : `bool` or `None`
        `True` for a colour camera, `False` for a monochrome one, `None` when
        no frame of the camera has a readable header.
    """
    identity = camera_identity(camera_name, configuration)
    for target in astrometrics.targets.query(include_empty=True).items:
        for frame in target.frames:
            if str(frame.role).upper() != "LIGHT" or camera_identity(frame.camera, configuration) != identity:
                continue
            uses_filter_array = frame_uses_color_filter_array(frame.path)
            if uses_filter_array is not None:
                return uses_filter_array
    return None


def finite_values(values: Iterable[float | None]) -> list[float]:
    """Keep the numbers that are real.

    Parameters
    ----------
    values : `Iterable` [`float` or `None`]
        Numbers that may be missing.

    Returns
    -------
    kept : `list` [`float`]
        The values that are not `None`, not NaN and not infinite.
    """
    return [value for value in values if value is not None and math.isfinite(value)]
