"""Measures a camera setting's gain and read noise from the library.

`measure_sensor_noise_from_library` is the glue between the calibration
library and the measurement in `pre_processing/sensor_noise.py`. It finds
the flat and bias frames for one camera setting, loads them, pairs the flats by
the time they were taken (``DATE-OBS``, at most `MAXIMUM_PAIR_GAP_SECONDS`
apart, so a pair never spans two sessions) and the biases in file-name order,
and runs the measurement. Flats above the camera profile's photometric
linearity limit are refused. With ``save=True`` it also writes the result to
the camera profile, with the spread, the per-pair values and the robust read
noise.

It lives beside `calibration_ingest.py`, the other code that reads the
calibration library, and not under `pipelines/shared/quality/`, because it is
a calibration job: it writes a camera profile, and quality code only reads.

Choices this module makes for the caller, each recorded in the result's notes:

- A camera setting can have flats through several telescopes or filters.
  Gain belongs to the sensor, not the optics, so any group works, but the
  frames of two groups differ in exposure and level. The group with the most
  frames is used, and the others are named in the notes. The caller can name
  a telescope or filter to choose another.
- Frames that cannot be read are skipped and counted in the notes. Flat
  frames with no ``DATE-OBS`` cannot be paired by time and are left out and
  counted in the notes.
"""

import logging
import math
import os
from datetime import date

from astrometricslib.drivers.calibration_library import CalibrationLibrary, FlatGroup
from astrometricslib.drivers.camera_profile_store import resolve_camera_profile, save_sensor_setting_noise
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.camera_profile import (
    ProvenancedValue,
    ProvenanceKind,
    SensorSettingNoise,
    ValueProvenance,
)
from astrometricslib.models.quality.measurement import MeasurementStatus
from astrometricslib.models.sensor_noise_measurement import SensorNoiseMeasurement
from astrometricslib.pipelines.stacking.exact_bias_frames import exact_setting_bias_frames
from astrometricslib.pipelines.stacking.pre_processing.sensor_noise import (
    EXPOSURE_RELATIVE_TOLERANCE,
    MAXIMUM_PAIR_GAP_SECONDS,
    MAXIMUM_TEMPERATURE_SPREAD_C,
    CalibrationFrame,
    load_calibration_frame,
    make_unmeasured_result,
    measure_sensor_noise,
    pair_by_observation_time,
    pair_consecutive,
)
from astrometricslib.utilities.camera_setting_text import (
    canonical_binning_setting,
    canonical_gain_setting,
    canonical_offset_setting,
)

logger = logging.getLogger(__name__)


def _load_frames(paths: list[str], notes: list[str], kind: str) -> list[CalibrationFrame]:
    """Read frames in file-name order, skipping repeats and unreadable files.

    Returns
    -------
    frames : `list` [`CalibrationFrame`]
        The frames that could be read.
    """
    unique_paths = list({os.path.realpath(path): path for path in sorted(paths)}.values())
    frames = []
    unreadable = 0
    for path in unique_paths:
        frame = load_calibration_frame(path)
        if frame is None:
            unreadable += 1
        else:
            frames.append(frame)
    if unreadable:
        notes.append(f"{unreadable} {kind} frame(s) could not be read and were skipped")
    return frames


def _choose_flat_group(groups: list[FlatGroup], notes: list[str]) -> FlatGroup:
    """Pick the flat group with the most frames and note the others.

    Returns
    -------
    group : `FlatGroup`
        The largest group (the first one on a tie).
    """
    chosen = max(groups, key=lambda group: len(group.paths))
    others = [group for group in groups if group is not chosen]
    if others:
        listed = "; ".join(f"{g.telescope} / {g.filter} ({len(g.paths)} frames)" for g in others)
        notes.append(
            f"used the flats of {chosen.telescope} / {chosen.filter} ({len(chosen.paths)} frames); "
            f"other flat groups at this setting were not used: {listed}"
        )
    return chosen


def _keep_near(
    frames: list[CalibrationFrame], temperature_c: float | None, exposure_seconds: float | None
) -> list[CalibrationFrame]:
    """Keep the frames at the wanted temperature and exposure.

    Returns
    -------
    kept : `list` [`CalibrationFrame`]
        The frames within `MAXIMUM_TEMPERATURE_SPREAD_C` of the temperature
        and within `EXPOSURE_RELATIVE_TOLERANCE` of the exposure. A test left
        as `None` keeps every frame.
    """
    kept = []
    for frame in frames:
        if temperature_c is not None and (
            frame.temperature_c is None
            or abs(frame.temperature_c - temperature_c) > MAXIMUM_TEMPERATURE_SPREAD_C
        ):
            continue
        if exposure_seconds is not None and (
            frame.exposure_seconds is None
            or not math.isclose(frame.exposure_seconds, exposure_seconds, rel_tol=EXPOSURE_RELATIVE_TOLERANCE)
        ):
            continue
        kept.append(frame)
    return kept


def save_measurement(
    config: AppConfiguration, result: SensorNoiseMeasurement, camera_id: str
) -> SensorNoiseMeasurement:
    """Write a measured gain and read noise to the camera profile.

    Parameters
    ----------
    config : `AppConfiguration`
        The configuration whose file is rewritten (after a backup).
    result : `SensorNoiseMeasurement`
        A result with status ``measured``.
    camera_id : `str`
        The camera whose profile gets the entry.

    Returns
    -------
    saved_result : `SensorNoiseMeasurement`
        The same result with ``saved`` and ``backup_path`` filled in.
    """
    provenance = ValueProvenance(kind=ProvenanceKind.MEASURED, source=result.provenance or "")
    entry = SensorSettingNoise(
        gain_setting=result.gain_setting,
        offset=result.offset,
        binning=result.binning,
        gain_e_per_adu=ProvenancedValue(value=result.gain_e_per_adu, provenance=provenance),
        read_noise_e=ProvenancedValue(value=result.read_noise_e, provenance=provenance),
        temperature_c=result.temperature_c,
        gain_spread_e_per_adu=result.gain_spread_e_per_adu,
        pair_gains_e_per_adu=tuple(result.pair_gains_e_per_adu),
        read_noise_adu_robust=result.read_noise_adu_robust,
        pair_read_noises_adu=tuple(result.pair_read_noises_adu),
    )
    backup_path = save_sensor_setting_noise(config, camera_id, entry)
    return result.model_copy(
        update={"saved": True, "backup_path": None if backup_path is None else str(backup_path)}
    )


def measure_sensor_noise_from_library(
    library: CalibrationLibrary,
    config: AppConfiguration,
    camera_id: str,
    gain: object,
    offset: object,
    binning: object = "1x1",
    *,
    temperature_c: float | None = None,
    exposure_seconds: float | None = None,
    telescope: str | None = None,
    filter_type: str | None = None,
    measured_on: date,
    save: bool = False,
) -> SensorNoiseMeasurement:
    """Measure one camera setting's gain and read noise from the library.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The library that lists the flat and bias frames.
    config : `AppConfiguration`
        The configuration, rewritten only when ``save`` is `True`.
    camera_id : `str`
        The camera whose frames to use.
    gain : `object`
        The camera gain setting (or ISO).
    offset : `object`
        The camera offset.
    binning : `object`, optional
        The binning, such as ``"1x1"``.
    temperature_c : `float`, optional
        Use only frames within `MAXIMUM_TEMPERATURE_SPREAD_C` of this sensor
        temperature.
    exposure_seconds : `float`, optional
        Use only flats of this exposure.
    telescope : `str`, optional
        Use only flats taken through this telescope.
    filter_type : `str`, optional
        Use only flats taken through this filter.
    save : `bool`, optional
        Write a measured result to the camera profile. Defaults to `False`.
    measured_on : `datetime.date`
        The date for the provenance sentence.

    Returns
    -------
    result : `SensorNoiseMeasurement`
        The measurement, or a ``not_measured`` result with the reason.
    """
    gain_setting = canonical_gain_setting(gain)
    offset_setting = canonical_offset_setting(offset)
    binning_setting = canonical_binning_setting(binning)
    setting = (gain_setting, offset_setting, binning_setting)
    notes: list[str] = []

    groups = [
        group
        for group in library.list_flat_groups(
            telescope=telescope,
            camera=camera_id,
            filter_type=filter_type,
            gain=gain_setting,
            offset=offset_setting,
        )
        if canonical_binning_setting(group.binning) == binning_setting
    ]
    if not groups:
        return make_unmeasured_result(
            camera_id, setting, "the calibration library has no flats at this setting", 0, 0
        )
    flat_group = _choose_flat_group(groups, notes)

    bias_selection = exact_setting_bias_frames(
        library, camera_id, gain_setting, offset_setting, binning_setting
    )
    if not bias_selection.paths:
        return make_unmeasured_result(camera_id, setting, bias_selection.reason, len(flat_group.paths), 0)

    flat_frames = _keep_near(_load_frames(flat_group.paths, notes, "flat"), temperature_c, exposure_seconds)
    bias_frames = _keep_near(_load_frames(bias_selection.paths, notes, "bias"), temperature_c, None)
    flat_pairing = pair_by_observation_time(flat_frames)
    if flat_pairing.undated_count:
        notes.append(
            f"{flat_pairing.undated_count} flat frame(s) have no readable DATE-OBS and were not used, "
            "because flats are paired by the time they were taken"
        )
    if flat_pairing.unpaired_count:
        notes.append(
            f"{flat_pairing.unpaired_count} flat frame(s) had no partner within "
            f"{MAXIMUM_PAIR_GAP_SECONDS:g} s and were not used"
        )
    # The profile's linearity limit is the pixel value above which the sensor
    # no longer responds in proportion to the light (see `CameraProfile`).
    limit = resolve_camera_profile(camera_id, config).photometric_linearity_limit_adu
    result = measure_sensor_noise(
        flat_pairing.pairs,
        pair_consecutive(bias_frames),
        camera_id=camera_id,
        measured_on=measured_on,
        linearity_limit_adu=None if limit is None else limit.value,
    )
    result = result.model_copy(
        update={
            "notes": [*notes, *result.notes],
            "gain_setting": setting[0],
            "offset": setting[1],
            "binning": setting[2],
        }
    )
    if save and result.status == MeasurementStatus.MEASURED:
        result = save_measurement(config, result, camera_id)
    return result
