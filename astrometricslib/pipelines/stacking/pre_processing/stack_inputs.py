"""Decides whether a stack on disk is what a new run would make again.

Stacking a target rebuilds every stack it has: one for each filter, and one for
the spectra. Adding frames to one of them used to rebuild all of them, even
though the others would come out pixel for pixel the same. This module lets
the stacker skip a stack when nothing that goes into it has changed.

A stack depends on four things:

1. The frames: which files, and whether any file was replaced.
2. The calibration frames (darks, biases and flats) the library picks for them.
3. The settings that change the pixels: rejection, weighting, trimming and
   the frame filters.
4. The stacking code itself.

Each time a stack is made, the stacker writes a record of these inputs next to
it (``<stack name>_inputs.json``) and a fingerprint, a hash of the record. At
the next run it builds the record again. If the fingerprint matches and the
stack file is still there, the stack is skipped. If not, the record shows what
changed, and the stacker logs it. The stacker also keeps the record with the
stack's other files, so a kept previous stack carries the record it was made
with.

The skip is always safe to override: ``force=True`` in the API, or
``--force-restack`` in the batch script (which sets
`FORCE_RESTACK_ENVIRONMENT_VARIABLE` so that worker processes see it too). When
the inputs cannot be read, for example a frame file that cannot be reached,
nothing is skipped.
"""

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from astrometricslib.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib.foundation.errors import AstrometricsError

logger = logging.getLogger(__name__)

__all__ = [
    "FORCE_RESTACK_ENVIRONMENT_VARIABLE",
    "SETTINGS_THAT_CHANGE_A_STACK",
    "SETTINGS_THAT_DO_NOT_CHANGE_A_STACK",
    "STACKING_ALGORITHM_VERSION",
    "STACK_INPUTS_SUFFIX",
    "RestackDecision",
    "build_stack_inputs_record",
    "decide_whether_to_restack",
    "describe_changes",
    "fingerprint_of",
    "inputs_file_path",
    "read_stack_inputs",
    "restack_is_forced",
    "write_stack_inputs",
]

# Raise this by one whenever a change to the stacking code, or to how its
# calibration frames are chosen, would give a different stack from the same
# frames and settings. Without it, a fix to the stacker would never reach a
# stack whose frames had not changed, because the skip would call the old stack
# up to date. It is part of the fingerprint. It is a plain counter because
# nothing can tell by itself that a code change alters a stack: a person has to
# say so. Raised to 1 when the skip was added, together with the star-based
# alignment of exposure groups (2026-10-03). Raised to 2 when a lone flat began
# to have the bias subtracted and a lone light began to be calibrated.
# Raised to 3 when adaptive rejection began to use a floor and a looser low
# bound, which changes the rejection limits of every small stack.
STACKING_ALGORITHM_VERSION = 3

# What follows a stack's own name (without .fits) in the name of its record.
STACK_INPUTS_SUFFIX = "_inputs.json"

# When this environment variable is set to a non-empty value other than "0",
# every stack is rebuilt. The batch script's ``--force-restack`` sets it. An
# environment variable is used because the batch runs each target in a worker
# process, and a worker starts with its parent's environment but not with
# changes made to the parent's memory.
FORCE_RESTACK_ENVIRONMENT_VARIABLE = "ASTROMETRICS_FORCE_RESTACK"

# The configuration getters (methods of `AppConfiguration`) whose value changes
# the pixels of a stack. Their current values go into the record.
SETTINGS_THAT_CHANGE_A_STACK = (
    "get_stack_weight",
    "get_stack_rejection_sigma_mode",
    "get_stack_rejection_sigma",
    "get_stack_rejection_sigma_floor",
    "get_stack_rejection_low_extra_sigma",
    "get_stack_generate_rejmap",
    "get_exposure_group_gain_tolerance",
    "get_stack_filter_wfwhm_percentile",
    "get_stack_filter_round_percentile",
    "get_trim_noisy_stack_edges_enabled",
    "get_quarantine_bad_frames_enabled",
    "get_background_homogeneity_check_enabled",
)

# The getters the stacking code reads that do not change a stack's pixels. A
# test scans the stacking code for every getter it reads and fails when one is
# on neither list, so a new setting cannot slip through unnoticed.
#
# - Where files live, which program runs, whether the old stack is kept and
#   whether an unchanged stack is skipped.
# - How many jobs run at once.
# - The fewest frames a master calibration frame should have. It only decides
#   whether the stack is flagged, and never what the pixels hold.
# - Which camera and optic are the preferred ones. They decide which stack the
#   target marks as preferred, which is set again when a stack is skipped.
# - The picture settings. They shape the preview picture and its stretched
#   FITS, made by a separate step after the stack, and never the stack itself.
SETTINGS_THAT_DO_NOT_CHANGE_A_STACK = (
    "get_stacks_path",
    "get_frames_path",
    "get_logs_path",
    "get_siril_executable",
    "get_auto_open_siril_gui",
    "get_keep_previous_stack_enabled",
    "get_skip_unchanged_stacks_enabled",
    "get_max_concurrent_jobs",
    "get_minimum_calibration_frames",
    "get_primary_camera_name",
    "get_primary_focal_length_mm",
    "get_graxpert_executable",
    "get_cosmic_clarity_denoise_executable",
    "get_cosmic_clarity_denoise_path",
    "get_cosmic_clarity_denoise_enabled",
    "get_cosmic_clarity_denoise_strength",
    "get_preview_star_tone_enabled",
)


@dataclass
class RestackDecision:
    """Whether to rebuild a stack, and why.

    Attributes
    ----------
    skip : `bool`
        `True` when the stack on disk is up to date and should be kept.
    reasons : `list` [`str`]
        Plain sentences saying why the stack is rebuilt, empty when `skip`.
    """

    skip: bool
    reasons: list[str] = field(default_factory=list)


def restack_is_forced() -> bool:
    """Say whether the environment asks for every stack to be rebuilt.

    Returns
    -------
    forced : `bool`
        `True` when `FORCE_RESTACK_ENVIRONMENT_VARIABLE` is set to something
        other than an empty string or ``"0"``.
    """
    return os.environ.get(FORCE_RESTACK_ENVIRONMENT_VARIABLE, "") not in ("", "0")


def inputs_file_path(stack_path: str) -> str:
    """Give the path of the record of a stack's inputs.

    Parameters
    ----------
    stack_path : `str`
        The stack's FITS file.

    Returns
    -------
    path : `str`
        The record's path, next to the stack.
    """
    return os.path.splitext(stack_path)[0] + STACK_INPUTS_SUFFIX


def _file_state(path: str) -> list[Any]:
    """Describe a file by its size and modification time.

    A raw frame's name does not say whether the file was replaced, and every
    frame of one camera has the same size, so the modification time (in
    nanoseconds) is what notices a swapped file.

    Returns
    -------
    state : `list`
        ``[path, size, modification time]``, with ``None`` for both numbers
        when the file cannot be read.
    """
    try:
        status = os.stat(path)
    except OSError:
        return [path, None, None]
    return [path, status.st_size, status.st_mtime_ns]


def _calibration_files(frames: list[Any], library: Any) -> dict[str, list[list[Any]]]:
    """List the calibration frames the library picks for these frames.

    The stacker picks a batch's calibration from its first frame's camera,
    gain, offset, telescope and filter, and from the batch's most common
    exposure. The lookups here use every combination found in the frames, so
    they cover whatever the batches use. A few extra frames in the list only
    mean a stack is rebuilt when calibration it did not use changes, never that
    a changed one is missed. The lookups also leave out the binning and the
    sensor temperature, so they list the calibration frames of every binning
    and every temperature slot. A frame added at another temperature may or
    may not change a given stack's pick, and it is cheaper to rebuild than to
    miss it.

    Parameters
    ----------
    frames : `list`
        The frames to stack.
    library : `CalibrationLibrary`
        The library that picks the calibration frames.

    Returns
    -------
    files : `list`
        ``{"darks": ..., "biases": ..., "flats": ...}``, each a sorted list of
        file states (see `_file_state`).
    """
    combinations = {
        (
            getattr(frame, "telescope", "Unknown"),
            getattr(frame, "camera", "Unknown"),
            str(getattr(frame, "iso", "800")),
            str(getattr(frame, "offset", "0")),
            getattr(frame, "filter", None),
            str(getattr(frame, "exposure", "1.0")),
        )
        for frame in frames
    }
    found: dict[str, set[str]] = {"darks": set(), "biases": set(), "flats": set()}
    for telescope, camera, iso, offset, filter_type, exposure in combinations:
        found["darks"].update(
            library.get_dark_frames(camera=camera, iso=iso, offset=offset, exposure=exposure)
        )
        found["biases"].update(library.get_bias_frames(camera=camera, iso=iso, offset=offset))
        found["flats"].update(
            library.get_flat_frames(
                telescope=telescope, camera=camera, filter_type=filter_type, iso=iso, offset=offset
            )
        )
    return {kind: [_file_state(path) for path in sorted(paths)] for kind, paths in found.items()}


def _stacking_settings(configuration: Any, options: dict[str, Any]) -> dict[str, Any]:
    """Collect the values of every setting that changes a stack.

    Parameters
    ----------
    configuration : `AppConfiguration`
        The configuration to read the settings from.
    options : `dict`
        The options this run was given directly (rejection limits, frame
        filters, weighting), which override the configuration.

    Returns
    -------
    settings : `dict`
        The configuration values by getter name, then ``"options"`` for the
        direct options. Everything is a plain JSON value.
    """
    settings: dict[str, Any] = {}
    for name in SETTINGS_THAT_CHANGE_A_STACK:
        value = getattr(configuration, name)()
        settings[name] = list(value) if isinstance(value, tuple) else value
    settings["options"] = {
        key: list(value) if isinstance(value, tuple) else value for key, value in sorted(options.items())
    }
    return settings


def build_stack_inputs_record(
    frames: list[Any], configuration: Any, options: dict[str, Any], library: Any = None
) -> dict[str, Any] | None:
    """Write down everything a stack is made from.

    Parameters
    ----------
    frames : `list`
        The frames that will be stacked, after any frames have been set aside.
    configuration : `AppConfiguration`
        The configuration, for the settings that change a stack.
    options : `dict`
        The options this run was given directly (see `_stacking_settings`),
        plus anything else that decides the stack, such as whether it is
        spectral.
    library : `CalibrationLibrary`, optional
        The calibration library. One is opened from `configuration` when left
        out.

    Returns
    -------
    record : `dict` or `None`
        The record, or `None` when it cannot be built (the caller then does
        not skip). The frames are listed in path order, so the order they were
        given in does not matter.
    """
    try:
        if library is None:
            from astrometricslib.drivers.calibration_library import CalibrationLibrary

            library = CalibrationLibrary(app_config=configuration)
        return {
            "algorithm_version": STACKING_ALGORITHM_VERSION,
            "settings": _stacking_settings(configuration, options),
            "frames": sorted(_file_state(getattr(frame, "path", "")) for frame in frames),
            "calibration": _calibration_files(frames, library),
        }
    except (AstrometricsError, *FITS_READ_ERRORS) as error:
        logger.warning("Could not record the inputs of a stack, so it will not be skipped: %s", error)
        return None


def fingerprint_of(record: dict[str, Any]) -> str:
    """Hash a record of a stack's inputs.

    Returns
    -------
    fingerprint : `str`
        A hexadecimal SHA-256 hash that is the same for equal records.
    """
    text = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_stack_inputs(stack_path: str, record: dict[str, Any]) -> None:
    """Save the record of a stack's inputs next to the stack.

    A failure to write is logged and does not stop the stack: the stack is
    then simply rebuilt next time.

    Parameters
    ----------
    stack_path : `str`
        The stack that was just made.
    record : `dict`
        What `build_stack_inputs_record` returned for it.
    """
    try:
        with open(inputs_file_path(stack_path), "w", encoding="utf-8") as record_file:
            json.dump({"fingerprint": fingerprint_of(record), "record": record}, record_file, indent=1)
    except OSError as error:
        logger.warning("Could not save the inputs of '%s': %s", stack_path, error)


def read_stack_inputs(stack_path: str) -> dict[str, Any] | None:
    """Read the record saved with a stack.

    Parameters
    ----------
    stack_path : `str`
        The stack whose record to read.

    Returns
    -------
    saved : `dict` or `None`
        ``{"fingerprint": ..., "record": ...}``, or `None` when there is no
        record or it cannot be read.
    """
    try:
        with open(inputs_file_path(stack_path), encoding="utf-8") as record_file:
            saved = json.load(record_file)
    except OSError, ValueError:
        return None
    if not isinstance(saved, dict) or "fingerprint" not in saved or "record" not in saved:
        return None
    return saved


def describe_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Say in plain words how two records of a stack's inputs differ.

    Parameters
    ----------
    old, new : `dict`
        Records from `build_stack_inputs_record`.

    Returns
    -------
    reasons : `list` [`str`]
        One sentence for each kind of change, empty when they are the same.
    """
    reasons = []
    if old.get("algorithm_version") != new.get("algorithm_version"):
        reasons.append("the stacking code changed")
    old_frames = {entry[0]: entry for entry in old.get("frames", [])}
    new_frames = {entry[0]: entry for entry in new.get("frames", [])}
    added = len(new_frames.keys() - old_frames.keys())
    removed = len(old_frames.keys() - new_frames.keys())
    replaced = sum(
        1 for path in old_frames.keys() & new_frames.keys() if old_frames[path] != new_frames[path]
    )
    for count, wording in ((added, "added"), (removed, "removed"), (replaced, "replaced")):
        if count:
            reasons.append(f"{count} frame(s) {wording}")
    for kind in ("darks", "biases", "flats"):
        if old.get("calibration", {}).get(kind) != new.get("calibration", {}).get(kind):
            reasons.append(f"the {kind[:-1]} frames changed")
    old_settings = old.get("settings", {})
    new_settings = new.get("settings", {})
    changed = sorted(
        key
        for key in old_settings.keys() | new_settings.keys()
        if old_settings.get(key) != new_settings.get(key)
    )
    if changed:
        reasons.append("settings changed: " + ", ".join(changed))
    return reasons


def decide_whether_to_restack(
    stack_path: str, record: dict[str, Any] | None, *, force: bool = False, enabled: bool = True
) -> RestackDecision:
    """Decide whether the stack at a path has to be rebuilt.

    Parameters
    ----------
    stack_path : `str`
        Where the stack is (or will be) written.
    record : `dict` or `None`
        The record for this run, from `build_stack_inputs_record`. `None`
        means it could not be built.
    force : `bool`, optional
        `True` to rebuild whatever the record says.
    enabled : `bool`, optional
        `False` when the setting ``skip_unchanged_stacks_enabled`` is off.

    Returns
    -------
    decision : `RestackDecision`
        To skip only when a stack file exists, it has a saved record, and that
        record's fingerprint equals this run's. Otherwise to rebuild, with the
        reasons.
    """
    if force or restack_is_forced():
        return RestackDecision(False, ["a restack was asked for"])
    if not enabled:
        return RestackDecision(False, ["skipping unchanged stacks is turned off"])
    if record is None:
        return RestackDecision(False, ["the stack's inputs could not be recorded"])
    if not os.path.isfile(stack_path):
        return RestackDecision(False, ["there is no stack yet"])
    saved = read_stack_inputs(stack_path)
    if saved is None:
        return RestackDecision(False, ["the stack on disk has no record of its inputs"])
    if saved["fingerprint"] == fingerprint_of(record):
        return RestackDecision(True)
    reasons = describe_changes(saved["record"], record)
    return RestackDecision(False, reasons or ["the inputs changed"])
