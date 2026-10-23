"""Describe the exposure groups of a stack, and keep a record of them.

For every stack this builds one entry per exposure length (how many frames it
held, whether a dark was applied, whether a star is clipped at that length) and
the exposure that would keep the brightest star below the ceiling. For a stack
made from several exposure lengths it also writes those entries, and how each
group stack was lined up and weighted, to a manifest file in the ``groups``
folder next to the combined stack.

The entries are plain dictionaries with the field names of
`astrometricslib.models.quality.quality_summary.ExposureGroupSummary`, so the
stage can put them straight into the quality summary.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.fits_access import read_data
from astrometricslib.models.quality.gate_result import GateResult, failed_gate, passed_gate, unchecked_gate
from astrometricslib.pipelines.stacking.post_processing.exposure_saturation import (
    DEFAULT_STAR_FWHM_PIXELS,
    FrameSaturation,
    checked_estimate_range,
    group_is_saturated,
    measure_frame_saturation,
    recommend_stack_exposure_seconds,
)
from astrometricslib.pipelines.stacking.processing.exposure_groups import (
    CLIPPED_FRAME_ZERO_FRACTION,
    FRAMES_SAMPLED_PER_GROUP,
    GroupGainMeasurement,
)

logger = logging.getLogger(__name__)

# The folder, next to a combined stack, that keeps each exposure group's own
# stack and the manifest describing them.
GROUPS_FOLDER_NAME = "groups"

# The name of the gate that checks that each group's brightness is one scale
# factor of the reference group's (see `exposure_group_linearity_gate`).
EXPOSURE_GROUP_LINEARITY_GATE_NAME = "exposure_group_linearity"
RAW_FRAME_CLIPPING_GATE_NAME = "raw_frame_clipping"
# The name of the gate that fails when a whole exposure group is missing from
# the combined stack (see `exposure_groups_combined_gate`).
EXPOSURE_GROUPS_COMBINED_GATE_NAME = "exposure_groups_combined"


def _attribute(frame: Any, name: str) -> Any:
    """Read one field of a frame record or a dictionary of one.

    Returns
    -------
    value : `Any`
        The field's value, or `None` when it is missing.
    """
    return frame.get(name) if isinstance(frame, dict) else getattr(frame, name, None)


def measure_group_saturation(
    frames: list[Any], sample_count: int = FRAMES_SAMPLED_PER_GROUP
) -> list[FrameSaturation]:
    """Measure saturation in a few raw frames spread through an exposure group.

    Parameters
    ----------
    frames : `list`
        The frame records (or dictionaries of them) of one exposure group.
    sample_count : `int`, optional
        How many frames to read, spread evenly through the group.

    Returns
    -------
    measurements : `list` [`FrameSaturation`]
        One per frame that could be read. Frames that cannot be read are
        skipped, so this may be empty.
    """
    if not frames:
        return []
    positions = sorted({
        round(position) for position in np.linspace(0, len(frames) - 1, min(sample_count, len(frames)))
    })
    measurements = []
    for position in positions:
        frame = frames[position]
        path = _attribute(frame, "path")
        if not path:
            continue
        fwhm = _attribute(_attribute(frame, "measurements"), "measured_fwhm_px") or DEFAULT_STAR_FWHM_PIXELS
        try:
            data = np.asarray(read_data(str(path)))
        except OSError as read_error:
            logger.warning("Could not read %s to measure saturation: %s", path, read_error)
            continue
        ceiling_adu = resolve_camera_profile(_attribute(frame, "camera")).clip_ceiling_adu.value
        measurements.append(measure_frame_saturation(data, ceiling_adu, float(fwhm)))
    return measurements


@dataclass(frozen=True)
class GroupPeak:
    """How close the brightest star of an exposure group is to the ceiling.

    This replaces the single exposure the stage used to recommend (decision 18
    of the quality-metrics plan: the library measures and never advises). It
    states what was seen at the exposure that was used. A reader who wants to
    know what another exposure would do can scale the peak, which grows in
    proportion to the exposure. The field names are those of
    `ExposureGroupSummary`, so `build_group_summary` puts them straight into
    the group's entry.

    Parameters
    ----------
    brightest_star_peak_fraction : `float` or `None`
        The median, over the sampled frames that gave a peak, of the star's
        peak above the sky divided by the room between the sky and the
        ceiling. 1 means the peak reaches the ceiling. A value above 1 comes
        from a clipped star whose peak was estimated.
    brightest_star_peak_above_sky_adu : `float` or `None`
        The median of those peaks, in ADU.
    peak_sky_adu : `float` or `None`
        The median sky level of those frames, in ADU.
    peak_clip_ceiling_adu : `float` or `None`
        The median ceiling of those frames, in ADU: the camera profile's clip
        ceiling for a frame whose star is not clipped, and the highest value
        the frame itself reached for a clipped one.
    peak_linearity_limit_adu : `float` or `None`
        The camera profile's photometric linearity limit, or `None` when the
        profile has none (or could not be read).
    peak_frames_with_peak : `int`
        How many sampled frames gave a peak.
    peak_frames_estimated : `int`
        How many of those peaks were estimated from the wings of a clipped
        star instead of measured.
    peak_frames_clipped : `int`
        How many sampled frames had a clipped star, whether or not its peak
        could be estimated.
    peak_frames_sampled : `int`
        How many frames were read.
    peak_frames_on_profile_ceiling : `int`
        How many of the frames that gave a peak took their ceiling from the
        camera profile (their star is not clipped). The others reached the
        ceiling themselves.
    peak_largest_estimate_to_room_ratio : `float` or `None`
        The largest peak-to-room ratio among the estimated peaks, which says
        how far beyond the ceiling the farthest estimate reaches. `None` when
        no peak was estimated.
    peak_clip_ceiling_provenance : `str` or `None`
        How the camera profile's clip ceiling was obtained (``measured``,
        ``datasheet`` or ``assumed``), or `None` when the profile could not be
        read.
    peak_linearity_limit_provenance : `str` or `None`
        The same for the linearity limit; `None` when there is no limit.
    peak_checked_range_room_multiples : `float`
        The largest estimated peak, in multiples of the room, whose growth
        with the exposure was checked for the group's camera (see
        `exposure_saturation.checked_estimate_range`). 0 when the camera has
        no checked range or its profile could not be read.
    """

    brightest_star_peak_fraction: float | None
    brightest_star_peak_above_sky_adu: float | None
    peak_sky_adu: float | None
    peak_clip_ceiling_adu: float | None
    peak_linearity_limit_adu: float | None
    peak_frames_with_peak: int
    peak_frames_estimated: int
    peak_frames_clipped: int
    peak_frames_sampled: int
    peak_frames_on_profile_ceiling: int = 0
    peak_largest_estimate_to_room_ratio: float | None = None
    peak_clip_ceiling_provenance: str | None = None
    peak_linearity_limit_provenance: str | None = None
    peak_checked_range_room_multiples: float = 0.0


def summarize_group_peak(
    saturation: list[FrameSaturation],
    linearity_limit_adu: float | None = None,
    clip_ceiling_provenance: str | None = None,
    linearity_limit_provenance: str | None = None,
    checked_range_room_multiples: float = 0.0,
) -> GroupPeak:
    """Summarize the brightest star's peak over the sampled frames of a group.

    Parameters
    ----------
    saturation : `list` [`FrameSaturation`]
        The sampled frames' saturation (see `measure_group_saturation`).
    linearity_limit_adu : `float`, optional
        The camera profile's photometric linearity limit, passed on.
    clip_ceiling_provenance : `str`, optional
        How the profile's clip ceiling was obtained, passed on.
    linearity_limit_provenance : `str`, optional
        How the profile's linearity limit was obtained, passed on.
    checked_range_room_multiples : `float`, optional
        The largest checked estimate size for the camera, passed on.

    Returns
    -------
    peak : `GroupPeak`
        The medians over the frames that gave a peak above the sky and a
        ceiling above the sky. The numbers are `None` when no frame did.
    """
    clipped = sum(frame.saturated for frame in saturation)
    usable = [
        frame
        for frame in saturation
        if frame.peak_above_sky_adu and frame.peak_above_sky_adu > 0 and frame.ceiling_adu > frame.sky_adu
    ]
    if not usable:
        return GroupPeak(
            None,
            None,
            None,
            None,
            linearity_limit_adu,
            0,
            0,
            clipped,
            len(saturation),
            peak_clip_ceiling_provenance=clip_ceiling_provenance,
            peak_linearity_limit_provenance=linearity_limit_provenance,
            peak_checked_range_room_multiples=checked_range_room_multiples,
        )
    fractions = [frame.peak_above_sky_adu / (frame.ceiling_adu - frame.sky_adu) for frame in usable]
    estimated_fractions = [
        fraction for fraction, frame in zip(fractions, usable, strict=True) if frame.peak_is_estimated
    ]
    return GroupPeak(
        brightest_star_peak_fraction=float(np.median(fractions)),
        brightest_star_peak_above_sky_adu=float(np.median([frame.peak_above_sky_adu for frame in usable])),
        peak_sky_adu=float(np.median([frame.sky_adu for frame in usable])),
        peak_clip_ceiling_adu=float(np.median([frame.ceiling_adu for frame in usable])),
        peak_linearity_limit_adu=linearity_limit_adu,
        peak_frames_with_peak=len(usable),
        peak_frames_estimated=len(estimated_fractions),
        peak_frames_clipped=clipped,
        peak_frames_sampled=len(saturation),
        peak_frames_on_profile_ceiling=sum(not frame.saturated for frame in usable),
        peak_largest_estimate_to_room_ratio=float(max(estimated_fractions)) if estimated_fractions else None,
        peak_clip_ceiling_provenance=clip_ceiling_provenance,
        peak_linearity_limit_provenance=linearity_limit_provenance,
        peak_checked_range_room_multiples=checked_range_room_multiples,
    )


def group_peak_fields(frames: list[Any], saturation: list[FrameSaturation]) -> dict[str, Any]:
    """Build the peak fields of one exposure group's entry.

    The camera profile is looked up for the linearity limit and for how the
    profile's numbers were obtained. A profile that cannot be read gives no
    limit and no provenance (the rows built from them are then not measured),
    is logged, and never stops the stack.

    Parameters
    ----------
    frames : `list`
        The group's frame records (or dictionaries of them). The first one's
        camera gives the profile.
    saturation : `list` [`FrameSaturation`]
        The sampled frames' saturation.

    Returns
    -------
    fields : `dict`
        The fields of `GroupPeak`, as plain numbers and text.
    """
    limit = None
    limit_provenance = None
    ceiling_provenance = None
    checked_range = 0.0
    if frames:
        try:
            profile = resolve_camera_profile(_attribute(frames[0], "camera"))
            ceiling_provenance = profile.clip_ceiling_adu.provenance.kind.value
            checked_range = checked_estimate_range(profile.camera_name)
            if profile.photometric_linearity_limit_adu is not None:
                limit = float(profile.photometric_linearity_limit_adu.value)
                limit_provenance = profile.photometric_linearity_limit_adu.provenance.kind.value
        except Exception:  # a by-product: no profile error may stop a stack
            logger.exception("The camera profile of an exposure group could not be read for its peak values")
            limit = limit_provenance = ceiling_provenance = None
            checked_range = 0.0
    return asdict(
        summarize_group_peak(saturation, limit, ceiling_provenance, limit_provenance, checked_range)
    )


def build_group_summary(
    exposure_seconds: float,
    frames: list[Any],
    diagnostics: dict[str, Any],
    saturation: list[FrameSaturation],
    stack_path: str | None = None,
    clipped_at_zero: bool = False,
    alignment_shift_pixels: list[float] | None = None,
    left_out_reason: str | None = None,
    gain_measurement: GroupGainMeasurement | None = None,
) -> dict[str, Any]:
    """Build the entry describing one exposure group.

    Parameters
    ----------
    exposure_seconds : `float`
        The group's exposure length.
    frames : `list`
        The frames submitted to the group.
    diagnostics : `dict`
        What the driver reported for the group's run.
    saturation : `list` [`FrameSaturation`]
        The sampled frames' saturation.
    stack_path : `str`, optional
        Where the group's own stack is kept.
    clipped_at_zero : `bool`, optional
        Whether the group's raw frames are clipped at zero.
    alignment_shift_pixels : `list` [`float`], optional
        The (rows, columns) shift that lined the group up with the reference.
    left_out_reason : `str`, optional
        Why the group is not in the combined image. A group with a reason
        reports ``frames_stacked`` as 0.
    gain_measurement : `GroupGainMeasurement`, optional
        The brightness scale measured for the group: the gain, the bright-end
        ratio (brightest quarter of the stars, or brightest pixels on the pixel
        fallback) and their disagreement. Leave it out for a group that was
        never compared with the reference.

    Returns
    -------
    summary : `dict`
        The fields of `ExposureGroupSummary`.
    """
    calibration = diagnostics.get("calibration_applied") or {}
    gain_fields: dict[str, Any] = {
        "gain_mid_range": None,
        "gain_bright_end_ratio": None,
        "gain_disagreement": None,
        "gain_nonlinear": False,
    }
    if gain_measurement is not None:
        gain_fields = {
            "gain_mid_range": float(gain_measurement.gain),
            "gain_bright_end_ratio": None
            if gain_measurement.bright_end_ratio is None
            else float(gain_measurement.bright_end_ratio),
            "gain_disagreement": None
            if gain_measurement.disagreement is None
            else float(gain_measurement.disagreement),
            "gain_nonlinear": bool(gain_measurement.nonlinear),
        }
    return {
        "exposure_seconds": float(exposure_seconds),
        "frames_submitted": len(frames),
        # Counts frames in the combined stack, so a group that was left out
        # holds none, even if its own stack was built.
        "frames_stacked": 0 if left_out_reason else int(diagnostics.get("images_stacked") or 0),
        "dark_applied": bool(calibration.get("dark", False)),
        "saturated": group_is_saturated(saturation),
        "clipped_at_zero": bool(clipped_at_zero),
        "stack_path": stack_path,
        "alignment_shift_pixels": alignment_shift_pixels,
        **gain_fields,
        "left_out_reason": left_out_reason,
        **group_peak_fields(frames, saturation),
    }


def exposure_group_linearity_gate(groups: list[dict[str, Any]], tolerance: float) -> GateResult:
    """Judge whether each exposure group is one scale factor of the reference.

    Each group is put on the reference group's brightness scale using one gain.
    For an imaging stack the gain is the median aperture-flux ratio of the
    isolated, unsaturated stars the two groups share
    (`measure_group_gains_with_stars`), and the same ratio over the faintest
    quarter and over the brightest quarter of those stars is the check. A group
    with too few such stars, and every group of a spectral stack, uses the
    pixel method instead (`measure_group_gains`): the gain is the median ratio
    over mid-range pixels, and the ratio over the brightest pixels is the
    check. If the camera is linear the check ratios agree with the gain; if the
    bright end of a group is compressed near full well or clipped they do not.
    A group whose check ratio sits more than `tolerance` from the gain is left
    out of the combined image (see `_stack_exposure_groups`), and this gate
    fails with a sentence that names the ratios. The sentence is the same text
    the stage lists as the group's flag reason, so the two never disagree.

    Parameters
    ----------
    groups : `list` [`dict`]
        The entries from `build_group_summary`, one per exposure group.
    tolerance : `float`
        The allowed distance of the faint-quarter or bright-quarter star ratio
        (the bright-end pixel ratio on the pixel fallback) from the gain, as a
        fraction of the gain (0.05 is 5%; see
        documentation/library_design/validation/stacking.md, STK-20).

    Returns
    -------
    result : `GateResult`
        Failed when any group is nonlinear, with the largest difference as the
        measured value. Passed when at least one group was compared with the
        reference and all agree. Not checked when no group was compared, for
        example when the stack has one exposure length.
    """
    limit_source = "configured exposure_group_gain_tolerance (design choice, not validated)"
    compared = [group for group in groups if group.get("gain_disagreement") is not None]
    nonlinear = [group for group in groups if group.get("gain_nonlinear")]
    if nonlinear:
        worst = max(float(group["gain_disagreement"]) for group in nonlinear)
        sentences = [
            f"the {group['exposure_seconds']:g} s exposure group {group['left_out_reason']}"
            for group in nonlinear
            if group.get("left_out_reason")
        ]
        return failed_gate(
            EXPOSURE_GROUP_LINEARITY_GATE_NAME,
            "; ".join(sentences),
            measured_value=worst,
            limit=tolerance,
            limit_source=limit_source,
        )
    if not compared:
        return unchecked_gate(
            EXPOSURE_GROUP_LINEARITY_GATE_NAME,
            "no exposure group was compared with a reference group",
            limit_source,
        )
    return passed_gate(
        EXPOSURE_GROUP_LINEARITY_GATE_NAME,
        measured_value=max(float(group["gain_disagreement"]) for group in compared),
        limit=tolerance,
        limit_source=limit_source,
    )


def exposure_groups_combined_gate(groups: list[dict[str, Any]]) -> GateResult:
    """Judge whether every exposure group went into the combined stack.

    A stack of several exposure lengths is built one length at a time. A
    length can fail to stack, fail to line up, or be nonlinear, and is then
    left out. The combined stack is still written, from the groups that
    remain, so without this gate a reader sees a full-looking stack that holds
    only part of the data. Any dropped group that had frames fails the gate.

    Parameters
    ----------
    groups : `list` [`dict`]
        The entries from `build_group_summary`, one per exposure group.

    Returns
    -------
    result : `GateResult`
        Failed when any group with frames is missing from the combined stack;
        the sentence names each group, why, and how many frames were lost,
        and the measured value is the share of submitted frames lost. Passed
        when several groups were all combined. Not checked when the stack has
        fewer than two groups.
    """
    limit_source = "any dropped exposure group fails (design choice: no group should vanish silently)"
    if len(groups) < 2:
        return unchecked_gate(
            EXPOSURE_GROUPS_COMBINED_GATE_NAME,
            "the stack has one exposure group or none, so there was nothing to combine",
            limit_source,
        )
    submitted = sum(int(group.get("frames_submitted") or 0) for group in groups)
    dropped = [group for group in groups if group.get("left_out_reason") and group.get("frames_submitted")]
    if not dropped:
        return passed_gate(EXPOSURE_GROUPS_COMBINED_GATE_NAME, 0.0, 0.0, limit_source)
    lost = sum(int(group["frames_submitted"]) for group in dropped)
    names = "; ".join(
        f"{group['exposure_seconds']:g} s ({group['frames_submitted']} frames: {group['left_out_reason']})"
        for group in dropped
    )
    return failed_gate(
        EXPOSURE_GROUPS_COMBINED_GATE_NAME,
        f"the combined stack holds {submitted - lost} of {submitted} frames; left out: {names}",
        measured_value=lost / submitted,
        limit=0.0,
        limit_source=limit_source,
    )


def raw_frame_clipping_gate(diagnostics: dict[str, Any]) -> GateResult:
    """Judge whether the raw frames of the stack are clipped at zero.

    A frame taken at a camera offset below its read noise cannot show the
    negative side of the noise, so a share of its pixels read exactly zero and
    the faint sky above them reads too high (see `CLIPPED_FRAME_ZERO_FRACTION`
    for the measurements). The share is measured on a few raw frames of every
    exposure group (see `measure_group_frames`). A stack of several exposure
    groups takes the faint pixels of a clipped group from the groups that are
    not clipped; a stack with no such group cannot.

    The gate framework has only passed, failed and not checked, so a stack
    where some but not all groups are clipped fails too. Its sentence says
    that the longer groups replace the faint pixels, so a reader can tell it
    from the case with nothing to replace them.

    Parameters
    ----------
    diagnostics : `dict`
        The stacking run's diagnostics. The measured zero shares are read from
        ``"exposure_group_frame_zero_fractions"``: a list of
        ``{"exposure_seconds", "frame_zero_fraction"}`` entries, one per
        stacked group.

    Returns
    -------
    result : `GateResult`
        Not checked when no zero share was measured. Passed when no group is
        above `CLIPPED_FRAME_ZERO_FRACTION`. Failed otherwise. The measured
        value is the largest zero share of any group.
    """
    limit_source = (
        "share of raw pixels at exactly zero; 0.2 sits between the clipped 1 s and the clean 2 s "
        "groups of the Vega session (ASI533MM Pro, offset 0)"
    )
    measured = diagnostics.get("exposure_group_frame_zero_fractions") or []
    if not measured:
        return unchecked_gate(
            RAW_FRAME_CLIPPING_GATE_NAME,
            "the raw frames could not be read to count their zero pixels",
            limit_source,
        )
    largest = max(float(entry["frame_zero_fraction"]) for entry in measured)
    clipped = [
        entry for entry in measured if float(entry["frame_zero_fraction"]) > CLIPPED_FRAME_ZERO_FRACTION
    ]
    if not clipped:
        return passed_gate(RAW_FRAME_CLIPPING_GATE_NAME, largest, CLIPPED_FRAME_ZERO_FRACTION, limit_source)
    exposures = ", ".join(f"{entry['exposure_seconds']:g} s" for entry in clipped)
    if len(clipped) == len(measured):
        detail = (
            f"raw frames clipped at zero in the {exposures} exposure group(s), up to {largest:.0%} of "
            "pixels: every group is clipped, so nothing can replace the faint pixels, which read too high"
        )
    else:
        detail = (
            f"raw frames clipped at zero in the {exposures} exposure group(s), up to {largest:.0%} of "
            "pixels: the longer groups replace the faint pixels of the clipped ones"
        )
    return failed_gate(
        RAW_FRAME_CLIPPING_GATE_NAME,
        detail,
        measured_value=largest,
        limit=CLIPPED_FRAME_ZERO_FRACTION,
        limit_source=limit_source,
    )


def recommended_exposure_for_groups(
    exposure_seconds: list[float], saturation_by_group: list[list[FrameSaturation]]
) -> float | None:
    """Recommend one exposure for a stack.

    Returns
    -------
    recommended : `float` or `None`
        The recommended exposure in seconds, rounded to two decimals, or
        `None` when it cannot be worked out.
    """
    recommendation = recommend_stack_exposure_seconds(exposure_seconds, saturation_by_group)
    return None if recommendation is None else round(float(recommendation), 2)


def write_group_manifest(
    final_path: str, entries: list[dict[str, Any]], details: list[dict[str, Any]]
) -> str:
    """Write the manifest of a combined stack's exposure groups.

    Parameters
    ----------
    final_path : `str`
        The combined stack's path. The manifest goes in the ``groups`` folder
        next to it, named after the stack.
    entries : `list` [`dict`]
        The group entries (see `build_group_summary`).
    details : `list` [`dict`]
        Per-group numbers that are not part of the public summary: the weight,
        brightness gain and frame noise used to combine it, in the same order.

    Returns
    -------
    manifest_path : `str`
        Where the manifest was written.
    """
    directory = os.path.join(os.path.dirname(final_path), GROUPS_FOLDER_NAME)
    os.makedirs(directory, exist_ok=True)
    stem = os.path.splitext(os.path.basename(final_path))[0]
    manifest_path = os.path.join(directory, f"{stem}_manifest.json")
    with open(manifest_path, "w") as manifest_file:
        json.dump(
            {
                "combined_stack": os.path.basename(final_path),
                "groups": [{**entry, **detail} for entry, detail in zip(entries, details, strict=True)],
            },
            manifest_file,
            indent=2,
        )
    return manifest_path


def groups_directory(final_path: str) -> str:
    """Give the folder that keeps a combined stack's group stacks.

    Returns
    -------
    directory : `str`
        The ``groups`` folder next to `final_path`, created if it is missing.
    """
    directory = os.path.join(os.path.dirname(final_path), GROUPS_FOLDER_NAME)
    os.makedirs(directory, exist_ok=True)
    return directory
