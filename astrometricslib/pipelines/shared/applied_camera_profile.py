"""Record which camera profile a pipeline run used on its quality summary.

Every pipeline's quality summary carries an `AppliedCameraProfile`, so that
a reader can see which camera the numbers assume. A camera that has no profile
gets the generic stand-in, and the summary is flagged so that this is not
missed.
"""

import collections
from collections.abc import Iterable
from typing import Any

from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.models.quality_summary import AppliedCameraProfile, PipelineQualitySummaryBase


def most_common_camera_name(frames: Iterable[Any]) -> str | None:
    """Find the camera that took most of a set of frames.

    Parameters
    ----------
    frames : `Iterable`
        Frame records, each with a ``camera`` attribute.

    Returns
    -------
    camera_name : `str` or `None`
        The most common non-empty camera name, or `None` when no frame names
        a camera.
    """
    counts = collections.Counter(frame.camera for frame in frames if getattr(frame, "camera", None))
    return counts.most_common(1)[0][0] if counts else None


def camera_name_from_header(header: Any) -> str | None:
    """Read the camera name from an image header.

    Parameters
    ----------
    header : `Any`
        The image header.

    Returns
    -------
    camera_name : `str` or `None`
        The header's ``INSTRUME`` or, failing that, its ``CAMERA``, when it is
        non-empty text. `None` when the header has neither, or when the value
        is not text.
    """
    for keyword in ("INSTRUME", "CAMERA"):
        value = header.get(keyword)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def camera_name_for_paths(frames: Iterable[Any], paths: Iterable[str]) -> str | None:
    """Find the camera that took the frames stored at some paths.

    Parameters
    ----------
    frames : `Iterable`
        Frame records, each with ``path`` and ``camera`` attributes.
    paths : `Iterable` [`str`]
        The paths of the frames a run used.

    Returns
    -------
    camera_name : `str` or `None`
        The most common camera among the frames at those paths, or `None`
        when none of them names one.
    """
    wanted = set(paths)
    return most_common_camera_name(frame for frame in frames if frame.path in wanted)


def build_applied_camera_profile(camera_name: str | None) -> AppliedCameraProfile:
    """Describe the camera profile used for a camera.

    Parameters
    ----------
    camera_name : `str` or `None`
        The camera, written in any spelling.

    Returns
    -------
    applied : `AppliedCameraProfile`
        The profile that matches the camera, or the generic stand-in.
    """
    profile = resolve_camera_profile(camera_name)
    return AppliedCameraProfile(
        camera_name=camera_name,
        profile_name=profile.camera_name,
        is_generic_fallback=profile.is_generic_fallback,
        clip_ceiling_adu=profile.clip_ceiling_adu.value,
        clip_ceiling_source=str(profile.clip_ceiling_adu.provenance.kind),
        saturation_threshold_adu=profile.saturation_threshold_adu.value,
        saturation_threshold_source=str(profile.saturation_threshold_adu.provenance.kind),
        saturation_threshold_can_be_reached=profile.saturation_threshold_can_be_reached,
        has_quantum_efficiency_curve=profile.quantum_efficiency is not None,
    )


def record_camera_profile(summary: PipelineQualitySummaryBase, camera_name: str | None) -> None:
    """Put the camera profile used on a quality summary, in place.

    A camera with no profile of its own also flags the summary, because the
    numbers taken from the generic stand-in are assumptions about a made-up
    camera. A run with no camera name at all records nothing and does not flag.

    Parameters
    ----------
    summary : `PipelineQualitySummaryBase`
        The summary to change.
    camera_name : `str` or `None`
        The camera that took the run's frames, when it is known.
    """
    if not camera_name:
        return
    summary.camera_profile = build_applied_camera_profile(camera_name)
    if summary.camera_profile.is_generic_fallback:
        summary.flagged = True
        summary.flag_reasons.append(
            f"camera {camera_name!r} has no camera profile; generic assumptions about the sensor were used"
        )
