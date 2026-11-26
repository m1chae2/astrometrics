"""Tie an analysis run to the one camera and optic of the image it analyzes.

An analysis reads one stack (astrometry, spectroscopy) or a list of raw
frames (photometry, spectral variation, asteroid search). A target can hold
frames from several cameras and optics, and these are never compared or mixed
in one analysis: frames from another camera have another pixel scale, noise
and flux scale. This module answers two questions in one place:

- Which camera and optic does the analysis run on? The stack's own header
  says so (``INSTRUME``, ``FOCALLEN``). The first frame of the target does not:
  it can be a frame from another camera.
- Which of the given frames belong to that camera and optic? The rest are
  left out, and the run reports each one.

The parameter context of the run (grating dispersion, field of view and the
other equipment values) is built from the same camera and optic.
"""

import logging
import os
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from astrometricslib._internal.drivers.camera_profile_store import camera_identity
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.enums import FilterType
from astrometricslib.foundation.errors import AstrometricsError, InvalidArgumentError
from astrometricslib.models.target import FrameRecord, StackConfigurationResult, Target
from astrometricslib.utilities.frame_identity import frame_is_spectral

logger = logging.getLogger(__name__)

# The analysis stages that work on raw frames, not on one stack. The stage
# reads the frames' camera and optic, and the frames of other cameras are left
# out.
FRAME_BASED_PIPELINES = frozenset({"photometry", "spectral_variation", "asteroid_detection"})

# The stages whose images are spectra, so their grating section applies.
SPECTRAL_PIPELINES = frozenset({"spectroscopy", "spectral_variation"})


@dataclass(frozen=True)
class AnalysisConfiguration:
    """The camera and optic one analysis runs on.

    Attributes
    ----------
    camera : `str`
        The camera, as the image or frame spells it.
    focal_length_mm : `float` or `None`
        The optic's focal length in millimeters, or `None` when the image
        does not give one.
    """

    camera: str
    focal_length_mm: float | None

    @property
    def label(self) -> str:
        """Give the short name, such as ``ZWO ASI 533MM Pro@405mm``.

        Returns
        -------
        label : `str`
            The camera, then ``@`` and the focal length when it is known.
        """
        if self.focal_length_mm is None:
            return self.camera
        return f"{self.camera}@{round(float(self.focal_length_mm))}mm"


def stack_configuration(stack_path: str | None, target: Target | None = None) -> AnalysisConfiguration | None:
    """Read the camera and optic a stack was made with.

    The header's ``INSTRUME`` and ``FOCALLEN`` decide. When the file cannot
    be read, the target's recorded stack for that path is used.

    Parameters
    ----------
    stack_path : `str` or `None`
        The stack's path.
    target : `Target`, optional
        The target, for the recorded configuration of a stack whose file
        cannot be read.

    Returns
    -------
    configuration : `AnalysisConfiguration` or `None`
        The camera and optic, or `None` when neither source names a camera.
    """
    if not stack_path:
        return None
    if os.path.exists(stack_path):
        from astrometricslib._internal.drivers.fits_access import read_header

        try:
            header = read_header(stack_path)
        except OSError, ValueError:
            header = None
        camera = str(header.get("INSTRUME") or "").strip() if header is not None else ""
        if camera:
            focal_length = header.get("FOCALLEN")
            return AnalysisConfiguration(camera, float(focal_length) if focal_length else None)
    if target is not None:
        for recorded in target.stacking.stacks_by_configuration.values():
            if recorded.stacked_image == stack_path and recorded.camera:
                return AnalysisConfiguration(recorded.camera, recorded.focal_length_mm)
    return None


def _matching_stack_configurations(
    target: Target,
    camera: str | None,
    focal_length_mm: float | None,
    config: AppConfiguration | None,
) -> list[StackConfigurationResult]:
    """List the target's recorded imaging stacks of one camera and optic.

    Parameters
    ----------
    target : `Target`
        The target.
    camera : `str` or `None`
        The camera: its full name, an alias, or part of its name. `None`
        matches any camera.
    focal_length_mm : `float` or `None`
        The optic's focal length in millimeters, compared after rounding.
        `None` matches any optic.
    config : `AppConfiguration` or `None`
        The config whose camera profiles give the aliases.

    Returns
    -------
    records : `list` [`StackConfigurationResult`]
        The records with a stack file that match, in recorded order.
    """
    return [
        recorded
        for recorded in target.stacking.stacks_by_configuration.values()
        if recorded.stacked_image
        and _camera_is(recorded.camera, camera, config)
        and (
            focal_length_mm is None
            or (
                recorded.focal_length_mm is not None
                and round(float(recorded.focal_length_mm)) == round(float(focal_length_mm))
            )
        )
    ]


def configuration_stack_path(
    target: Target,
    camera: str | None,
    focal_length_mm: float | None,
    config: AppConfiguration | None = None,
) -> str | None:
    """Find the target's recorded imaging stack of one camera and optic.

    A target keeps one imaging stack for each camera and optic it was
    stacked with (``target.stacking.stacks_by_configuration``). This picks
    the one that matches.

    Parameters
    ----------
    target : `Target`
        The target.
    camera : `str` or `None`
        The camera: its full name, an alias, or part of its name. `None`
        matches any camera.
    focal_length_mm : `float` or `None`
        The optic's focal length in millimeters, compared after rounding.
        `None` matches any optic.
    config : `AppConfiguration`, optional
        The config whose camera profiles give the aliases.

    Returns
    -------
    stack_path : `str` or `None`
        The stack of the one matching record. `None` when no record or more
        than one record matches, or when neither a camera nor an optic is
        given.
    """
    if not camera and focal_length_mm is None:
        return None
    matches = _matching_stack_configurations(target, camera, focal_length_mm, config)
    return matches[0].stacked_image if len(matches) == 1 else None


def choose_stack_configuration(
    target: Target,
    camera: str | None,
    focal_length_mm: float | None,
    config: AppConfiguration | None = None,
) -> StackConfigurationResult:
    """Find the one recorded imaging stack of a camera and optic, or say why.

    This is `configuration_stack_path` for a caller that must tell the
    person what went wrong: no match, or more than one, is an error whose
    message lists the target's configurations by their keys (such as
    ``ZWO ASI 533MM Pro@405mm``).

    Parameters
    ----------
    target : `Target`
        The target.
    camera : `str` or `None`
        The camera: its full name, an alias, or part of its name.
    focal_length_mm : `float` or `None`
        The optic's focal length in millimeters, compared after rounding.
    config : `AppConfiguration`, optional
        The config whose camera profiles give the aliases.

    Returns
    -------
    record : `StackConfigurationResult`
        The matching record.

    Raises
    ------
    InvalidArgumentError
        If neither a camera nor an optic is given, or no record or more than
        one matches. ``details["configurations"]`` lists every key.
    """
    keys = sorted(
        recorded.configuration_key
        for recorded in target.stacking.stacks_by_configuration.values()
        if recorded.stacked_image
    )
    details = {"target": target.id, "configurations": keys}
    if not camera and focal_length_mm is None:
        raise InvalidArgumentError(
            "Give a camera, a focal length or both to choose a stack.", details=details
        )
    matches = _matching_stack_configurations(target, camera, focal_length_mm, config)
    if len(matches) == 1:
        return matches[0]
    asked = f"camera {camera!r}" if camera else "any camera"
    if focal_length_mm is not None:
        asked += f" at {focal_length_mm:g} mm"
    if matches:
        raise InvalidArgumentError(
            f"Target {target.id!r} has more than one stack for {asked}: "
            f"{', '.join(sorted(record.configuration_key for record in matches))}. "
            "Give both the camera and the focal length.",
            details=details,
        )
    raise InvalidArgumentError(
        f"Target {target.id!r} has no stack for {asked}. Its configurations are: "
        f"{', '.join(keys) or 'none recorded (stack the target first)'}.",
        details=details,
    )


def _camera_is(recorded_camera: str, wanted: str | None, config: AppConfiguration | None) -> bool:
    """Say whether a recorded camera is the camera a caller asked for.

    Parameters
    ----------
    recorded_camera : `str`
        The camera a stack record names.
    wanted : `str` or `None`
        The camera asked for: a full name, an alias or part of a name.
        `None` or empty matches any camera.
    config : `AppConfiguration`, optional
        The config whose camera profiles give the aliases.

    Returns
    -------
    same : `bool`
        `True` when the names are aliases of one camera, or the wanted name
        is part of the recorded one (case ignored).
    """
    if not wanted:
        return True
    if not recorded_camera:
        return False
    if camera_identity(recorded_camera, config) == camera_identity(wanted, config):
        return True
    return wanted.lower() in recorded_camera.lower()


def frames_configuration(frames: Sequence[FrameRecord]) -> AnalysisConfiguration | None:
    """Give the camera and optic most of a list of frames share.

    Parameters
    ----------
    frames : `Sequence` [`FrameRecord`]
        The frames. A frame with no camera is not counted.

    Returns
    -------
    configuration : `AnalysisConfiguration` or `None`
        The most common camera and rounded focal length, or `None` when no
        frame names a camera.
    """
    counts: Counter[tuple[str, int | None]] = Counter()
    first_seen: dict[tuple[str, int | None], FrameRecord] = {}
    for frame in frames:
        if not frame.camera:
            continue
        key = (frame.camera, round(frame.focal_length_mm) if frame.focal_length_mm else None)
        counts[key] += 1
        first_seen.setdefault(key, frame)
    if not counts:
        return None
    best = counts.most_common(1)[0][0]
    return AnalysisConfiguration(best[0], first_seen[best].focal_length_mm)


def frame_matches_configuration(
    frame: Any, configuration: AnalysisConfiguration, config: AppConfiguration | None = None
) -> bool:
    """Say whether a frame was taken with a configuration's camera and optic.

    Parameters
    ----------
    frame : `Any`
        A frame record with ``camera`` and ``focal_length_mm``.
    configuration : `AnalysisConfiguration`
        The camera and optic to match.
    config : `AppConfiguration`, optional
        The config whose camera profiles give the aliases.

    Returns
    -------
    matches : `bool`
        `True` when the camera is the same camera (aliases count) and, when
        the configuration has a focal length, the frame's focal length
        rounds to the same millimeters. A frame with no focal length does not
        match a configuration that has one.
    """
    frame_camera = getattr(frame, "camera", "") or ""
    if not frame_camera or camera_identity(frame_camera, config) != camera_identity(
        configuration.camera, config
    ):
        return False
    if configuration.focal_length_mm is None:
        return True
    frame_focal_length = getattr(frame, "focal_length_mm", None)
    return bool(frame_focal_length) and round(float(frame_focal_length)) == round(
        float(configuration.focal_length_mm)
    )


def restrict_frames_to_configuration(
    frames: Sequence[FrameRecord],
    configuration: AnalysisConfiguration,
    config: AppConfiguration | None = None,
) -> tuple[list[FrameRecord], list[tuple[FrameRecord, str]]]:
    """Keep the frames of one camera and optic and name the others.

    Parameters
    ----------
    frames : `Sequence` [`FrameRecord`]
        The frames to sort.
    configuration : `AnalysisConfiguration`
        The camera and optic the analysis runs on.
    config : `AppConfiguration`, optional
        The config whose camera profiles give the aliases.

    Returns
    -------
    kept : `list` [`FrameRecord`]
        The frames of the configuration, in the given order.
    left_out : `list` [`tuple` [`FrameRecord`, `str`]]
        Each other frame with the reason it was left out.
    """
    kept: list[FrameRecord] = []
    left_out: list[tuple[FrameRecord, str]] = []
    for frame in frames:
        if frame_matches_configuration(frame, configuration, config):
            kept.append(frame)
            continue
        camera = frame.camera or "Unknown camera"
        if camera_identity(camera, config) == camera_identity(configuration.camera, config):
            reason = (
                f"left out of the analysis: different optic ({_optic_text(frame)}) than {configuration.label}"
            )
        else:
            reason = f"left out of the analysis: different camera ({camera}) than {configuration.label}"
        left_out.append((frame, reason))
    return kept, left_out


def _optic_text(frame: Any) -> str:
    """Name a frame's optic for a message.

    Parameters
    ----------
    frame : `Any`
        A frame record.

    Returns
    -------
    text : `str`
        The focal length in millimeters, or ``no focal length recorded``.
    """
    focal_length = getattr(frame, "focal_length_mm", None)
    return f"{round(float(focal_length))}mm" if focal_length else "no focal length recorded"


def left_out_summary(
    target_id: str, left_out: Sequence[tuple[FrameRecord, str]], configuration: AnalysisConfiguration
) -> list[str]:
    """Write one line for each camera and optic whose frames were left out.

    Parameters
    ----------
    target_id : `str`
        The target, for the start of each line.
    left_out : `Sequence` [`tuple` [`FrameRecord`, `str`]]
        The frames left out with their reasons.
    configuration : `AnalysisConfiguration`
        The camera and optic the analysis runs on.

    Returns
    -------
    lines : `list` [`str`]
        For example ``[M 57] 1 frame from Nikon DSLR DSC D5300 left out:
        different camera than the analyzed stack (ZWO ASI 533MM Pro@405mm)``.
    """
    counts: Counter[str] = Counter()
    for frame, reason in left_out:
        owner = frame.camera or "Unknown camera"
        if "different optic" in reason:
            owner = f"{owner}@{_optic_text(frame)}"
        counts[owner] += 1
    lines = []
    for owner, count in counts.items():
        noun = "frame" if count == 1 else "frames"
        why = "different optic" if "@" in owner else "different camera"
        lines.append(
            f"[{target_id}] {count} {noun} from {owner} left out: {why} than the analyzed stack "
            f"({configuration.label})"
        )
    return lines


def analysis_frames_and_configuration(
    target: Target,
    pipeline_type: str,
    frames: list[FrameRecord] | None,
    path: str | None,
    config: AppConfiguration | None = None,
    stack_path: str | None = None,
) -> tuple[AnalysisConfiguration | None, list[FrameRecord] | None, list[tuple[FrameRecord, str]]]:
    """Choose the camera and optic of one analysis, and its frames.

    A stack-based stage (astrometry, spectroscopy) takes the camera and
    optic of the image it analyzes. A frame-based stage (photometry, spectral
    variation, asteroid search) takes those of the stack it goes with (the
    imaging stack, or the spectral stack for spectral variation), and keeps
    only frames of that camera and optic. A target with stacks for several
    configurations is analyzed for the configuration of its recorded stack
    (``target.stacking.stacked_image``), or of ``stack_path`` when the
    caller analyzes another configuration's stack; frames of the others are
    left out. With no stack, the camera and optic most of the frames share
    are used.

    Parameters
    ----------
    target : `Target`
        The target.
    pipeline_type : `str`
        The analysis, as `PIPELINE_RUNNERS` names it.
    frames : `list` [`FrameRecord`] or `None`
        The frames the caller gave, or `None` for the target's own.
    path : `str` or `None`
        The image a stack-based stage analyzes.
    config : `AppConfiguration`, optional
        The config whose camera profiles give the aliases.
    stack_path : `str`, optional
        For photometry and the asteroid search, the imaging stack whose
        camera and optic the frames must match, when it is not the target's
        recorded stack (for example the stack of another configuration in
        ``target.stacking.stacks_by_configuration``).

    Returns
    -------
    configuration : `AnalysisConfiguration` or `None`
        The camera and optic, or `None` when nothing names them.
    kept_frames : `list` [`FrameRecord`] or `None`
        For a frame-based stage, the frames that belong to the configuration
        (the target's light frames when the caller gave none). `None` for a
        stack-based stage, which the caller leaves as given.
    left_out : `list` [`tuple` [`FrameRecord`, `str`]]
        The frames left out and the reason for each.
    """
    if pipeline_type not in FRAME_BASED_PIPELINES:
        configuration = stack_configuration(path, target) or frames_configuration(
            frames or target.frames or []
        )
        return configuration, None, []

    candidates = list(frames if frames is not None else target.frames or [])
    if pipeline_type == "spectral_variation":
        stack_path = target.spectral_stacking.stacked_image
    elif not stack_path:
        stack_path = target.stacking.stacked_image
    configuration = stack_configuration(stack_path, target)
    if configuration is None:
        spectral = pipeline_type == "spectral_variation"
        configuration = frames_configuration(
            [frame for frame in candidates if frame_is_spectral(frame) == spectral] or candidates
        )
    if configuration is None:
        return None, candidates, []
    # Only the frames this stage would use are sorted and reported. The rest
    # (for example the imaging frames of a spectral stage) stay as given, and
    # the stage's own filters drop them.
    relevant = [frame for frame in candidates if _is_relevant(frame, pipeline_type)]
    irrelevant = [frame for frame in candidates if not _is_relevant(frame, pipeline_type)]
    kept, left_out = restrict_frames_to_configuration(relevant, configuration, config)
    return configuration, kept + irrelevant, left_out


def _is_relevant(frame: Any, pipeline_type: str) -> bool:
    """Say whether a frame is one the stage would analyze.

    Parameters
    ----------
    frame : `Any`
        A frame record.
    pipeline_type : `str`
        A frame-based analysis.

    Returns
    -------
    relevant : `bool`
        Spectral frames for spectral variation, light frames for the
        asteroid search, and imaging frames for photometry.
    """
    if pipeline_type == "spectral_variation":
        return frame_is_spectral(frame)
    if pipeline_type == "asteroid_detection":
        return getattr(frame, "role", "LIGHT") == "LIGHT"
    return not frame_is_spectral(frame)


def context_filter_name(
    pipeline_type: str, frames: Sequence[FrameRecord], configuration: AnalysisConfiguration
) -> str | None:
    """Pick the filter or grating section a run's parameters are read for.

    Parameters
    ----------
    pipeline_type : `str`
        The analysis.
    frames : `Sequence` [`FrameRecord`]
        Frames that may name the filter.
    configuration : `AnalysisConfiguration`
        The camera and optic of the run.

    Returns
    -------
    filter_name : `str` or `None`
        ``SPEC`` for a spectral analysis. Otherwise the filter of the first
        non-spectral frame of the configuration, or `None` when there is none.
    """
    if pipeline_type in SPECTRAL_PIPELINES:
        return "SPEC"
    for frame in frames:
        if frame_matches_configuration(frame, configuration) and not frame_is_spectral(frame):
            try:
                filter_type = FilterType(frame.filter)
            except ValueError:
                return None
            return None if filter_type is FilterType.NONE else filter_type.name
    return None


def report_left_out(
    target_id: str,
    left_out: Sequence[tuple[FrameRecord, str]],
    configuration: AnalysisConfiguration,
    job: Any = None,
) -> None:
    """Log the frames left out, and put the lines in the job's log.

    Parameters
    ----------
    target_id : `str`
        The target.
    left_out : `Sequence` [`tuple` [`FrameRecord`, `str`]]
        The frames left out with their reasons.
    configuration : `AnalysisConfiguration`
        The camera and optic of the run.
    job : `Any`, optional
        The run's job handle, which has an ``info`` method.
    """
    for line in left_out_summary(target_id, left_out, configuration):
        logger.warning(line)
        if job is not None:
            try:
                job.info(line)
            except AstrometricsError, OSError:
                logger.debug("Could not add the left-out line to the job log", exc_info=True)
