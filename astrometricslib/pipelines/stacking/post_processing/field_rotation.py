"""Builds the rotation of each frame about the registration reference.

When Siril lines the frames of a stack up, it fits a transform for each frame
that moves the frame's stars onto the reference frame's stars. The transform is
a 3 x 3 matrix (a homography). Its top left 2 x 2 part holds the rotation. A
mount whose polar axis is not exactly on the sky's pole turns the field slowly
as the night goes on, so the frame-to-frame change of this angle is the raw
material for a polar-alignment estimate. This file only stores the angle of
each frame. The conversion to a polar-alignment error belongs to the
`wayfindinglib` library (plan section 2.1), and nothing here judges the value.

The angle is the rotation of the polar decomposition of the 2 x 2 part: the
part is written as a rotation followed by a stretch, and the rotation's angle
is ``atan2(h10 - h01, h00 + h11)``. Unlike ``atan2(h10, h00)`` it does not
change when the fit has a small shear. It is given only when the determinant
is positive (the transform does not mirror the image).

The angle is in Siril's image coordinates: x counts along the columns and y
counts up from the last row of the array (y = height - 1 - row; checked on real
frames, see `pipelines/astrometry/post_processing/target_clipping.py`). A
positive angle turns the frame's x axis toward +y, which is counterclockwise
when the array is drawn with its first row at the top. It is the angle of the
transform that moves the frame onto the reference, so the orientation of the
frame relative to the reference has the opposite sign. The angle lies in
(-180, 180] degrees. A meridian flip turns the field by about 180 degrees, so
across a flip the stored angle jumps by about 360 degrees at the wrap. A reader
unwraps the series before fitting a drift.

A row names the run's registration reference frame and the image parity (the
sign of the determinant of the CD matrix of the stack's plate solution, or
"unknown" when the stack has none) in its reason. The angle depends on which
frame is the reference, so values compare only within one run (``run_id``); a
restack that picks another reference changes them.

A frame's time is not repeated in the row. The row is about the frame (the same
subject as the frame's weight), and the time is the timestamp of the frame
record, joined by the subject id. That timestamp is the frame header's
``DATE-OBS`` (``DATE`` when there is none): by the FITS convention the start of
the exposure.

A spectral stack is registered by shift only, so its frames have no rotation.
A sequence whose frames are all the identity transform was already registered
before Siril read it, so its zero angles say nothing. A frame that is not in
Siril's table at all (Siril was not given it, or dropped it) gets a row that is
not measured, with the reason.
"""

import logging
import math
import os
import warnings
from collections.abc import Sequence
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning

from astrometricslib.drivers.fits_access import FITS_READ_ERRORS, read_header
from astrometricslib.models.quality.measurement import MeasurementStatus, QualityMeasurement
from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
)
from astrometricslib.pipelines.shared.registration_record import RegistrationRecord, is_already_registered
from astrometricslib.pipelines.stacking.metrics import (
    STACKING_FRAME_REGISTRATION_ROTATION_DEG,
    STACKING_METRICS,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

__all__ = ["field_rotation_measurements", "image_parity_text", "rotation_from_homography"]

_SPEC = next(spec for spec in STACKING_METRICS if spec.name == STACKING_FRAME_REGISTRATION_ROTATION_DEG)

SPECTRAL_REASON = "a spectral stack is registered by shift only, so no rotation is fitted"
UNMATCHED_REASON = (
    "Siril's registration results could not be matched to the frames (their lists differ in length), "
    "so no frame's rotation can be named"
)
MULTI_GROUP_REASON = (
    "the stack joins several exposure groups, each lined up by Siril on a reference of its own, so no one "
    "list of transforms belongs to the stack"
)
ALREADY_REGISTERED_REASON = (
    "every frame of the sequence has the identity transform, so the frames were registered before "
    "Siril read them and the zero angles say nothing"
)
DROPPED_REASON = "Siril could not register this frame, so it has no transform"
NOT_IN_TABLE_REASON = (
    "Siril's registration table has no entry for this frame (it was not given to Siril: the file was "
    "unreadable, its size differed from the others or its camera was not the one stacked)"
)
MIRRORED_REASON = (
    "the transform reverses the image (the determinant of its 2 x 2 part is not positive), so a "
    "rotation angle is not defined"
)
PARITY_UNKNOWN = "unknown"


def rotation_from_homography(homography: Sequence[float]) -> float | None:
    """Take the rotation out of the 2 x 2 part of a registration transform.

    The 2 x 2 part A of the transform is split into a rotation followed by a
    stretch (the polar decomposition, A = R S with S symmetric). For a matrix
    with a positive determinant the rotation's angle is
    ``atan2(A[1][0] - A[0][1], A[0][0] + A[1][1])``. A small shear in the
    fit moves the stretch S and leaves this angle alone, which is why it is
    used instead of ``atan2(h10, h00)``.

    Parameters
    ----------
    homography : `Sequence` [`float`]
        The nine numbers Siril saves for the frame, in row order.

    Returns
    -------
    angle_deg : `float` or `None`
        The angle in degrees, in (-180, 180]. It reaches the wrap at +-180
        degrees when the field has turned half a circle (a meridian flip).
        `None` when the matrix is not finite or its determinant is not
        positive.
    """
    h00, h01, _, h10, h11 = (float(value) for value in (*homography[:3], *homography[3:5]))
    determinant = h00 * h11 - h01 * h10
    if not (math.isfinite(determinant) and determinant > 0.0):
        return None
    return math.degrees(math.atan2(h10 - h01, h00 + h11))


def image_parity_text(stack_path: str) -> str:
    """Say which way the stack's plate solution mirrors the sky.

    Parameters
    ----------
    stack_path : `str`
        The stack. Its header is read.

    Returns
    -------
    parity : `str`
        ``"+1"`` or ``"-1"``, the sign of the determinant of the CD matrix of
        the stack's celestial coordinate solution, or ``"unknown"`` when the
        header has none or cannot be read. A north-up, east-left image has
        ``-1``.
    """
    try:
        header = read_header(stack_path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FITSFixedWarning)
            wcs = WCS(fits.Header(header))
        if not wcs.has_celestial:
            return PARITY_UNKNOWN
        determinant = float(np.linalg.det(np.asarray(wcs.celestial.pixel_scale_matrix, dtype=float)))
    except (*FITS_READ_ERRORS, *DATA_ERRORS):
        return PARITY_UNKNOWN
    if not math.isfinite(determinant) or determinant == 0.0:  # ruff: ignore[float-equality-comparison] -- exact zero of a matrix determinant, not a measurement
        return PARITY_UNKNOWN
    return "+1" if determinant > 0.0 else "-1"


def field_rotation_measurements(
    target_frames: Sequence[Any],
    record: RegistrationRecord | None,
    context: PipelineRunContext,
    *,
    is_spectral: bool,
    stack_path: str = "",
    missing_reason: str = UNMATCHED_REASON,
) -> list[QualityMeasurement]:
    """Build the rotation row of each frame of a stacking run.

    Parameters
    ----------
    target_frames : `Sequence`
        The frames given to the stacking program (frame records).
    record : `RegistrationRecord` or `None`
        Siril's registration of the run (see `build_registration_record`), or
        `None` when it could not be matched to the frames.
    context : `PipelineRunContext`
        The run's shared facts.
    is_spectral : `bool`
        Whether the stack is spectral.
    stack_path : `str`, optional
        The finished stack, whose header gives the image parity. Left out, the
        parity is "unknown".
    missing_reason : `str`, optional
        The reason given to every frame when ``record`` is `None`.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        One row for each frame given to the stacking program. A frame Siril
        registered has its rotation in degrees, with the registration
        reference frame and the image parity in the reason. A frame Siril
        dropped or never read, a spectral stack and a sequence of identity
        transforms give not-measured rows with the reason. When the
        registration results cannot be matched to the frames, every frame gets
        a not-measured row.
    """
    if is_spectral:
        return [_missing(frame, context, SPECTRAL_REASON) for frame in target_frames]
    if record is None:
        return [_missing(frame, context, missing_reason) for frame in target_frames]
    already_registered = is_already_registered(record)
    reference_text = (
        "not identified with certainty"
        if record.reference_frame is None
        else os.path.basename(record.reference_frame)
    )
    parity = image_parity_text(stack_path) if stack_path else PARITY_UNKNOWN
    provenance = (
        f"polar-decomposition angle of Siril's transform onto the reference frame {reference_text}; "
        f"image parity (sign of det(CD) of the stack's plate solution): {parity}"
    )
    entry_by_path = {
        path: (homography, flag)
        for path, homography, flag in zip(
            record.light_paths, record.homographies, record.included, strict=True
        )
    }
    rows: list[QualityMeasurement] = []
    for frame in target_frames:
        entry = entry_by_path.get(frame.path)
        if entry is None:
            rows.append(_missing(frame, context, NOT_IN_TABLE_REASON))
            continue
        homography, flag = entry
        if flag is False:
            rows.append(_missing(frame, context, DROPPED_REASON))
        elif already_registered:
            rows.append(_missing(frame, context, ALREADY_REGISTERED_REASON))
        else:
            angle = rotation_from_homography(homography)
            if angle is None:
                rows.append(_missing(frame, context, MIRRORED_REASON))
                continue
            rows.append(
                build_measurement(
                    _SPEC,
                    angle,
                    context,
                    frame_subject(frame.path, context.target_id, frame.timestamp),
                    provenance=provenance,
                    status=MeasurementStatus.MEASURED,
                )
            )
    return rows


def _missing(frame: Any, context: PipelineRunContext, reason: str) -> QualityMeasurement:
    """Build a not-measured rotation row for one frame.

    Parameters
    ----------
    frame : `Any`
        The frame record.
    context : `PipelineRunContext`
        The run's shared facts.
    reason : `str`
        Why there is no rotation.

    Returns
    -------
    measurement : `QualityMeasurement`
        The row.
    """
    return build_measurement(
        _SPEC, None, context, frame_subject(frame.path, context.target_id, frame.timestamp), reason=reason
    )
