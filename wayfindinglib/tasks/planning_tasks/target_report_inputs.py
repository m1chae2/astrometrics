"""Purpose: Read what the target dataset report needs.

Description: `build_target_dataset_report` (in ``target_report_tasks.py``)
works on plain data. This module fetches that data and shapes it:

* the target's stored measurements and the capture settings they point at
  (``astrometrics.quality``);
* which capture setting each stack belongs to;
* the frames the stacker set aside (``astrometrics.processing``);
* one `FrameObservation` for each measured frame, with the time and the
  direction the sky model needs;
* the efficiency reference of each setting (the equipment baseline from the
  whole library, else the physical reference);
* the sky model fitted to the equipment's own frames;
* the calibration library's rows;
* the stored night plans of the target.

Only Planning-legal sources are read: the science library through its public
root, the Foundation analytics and models, and the butler. Nothing here
imports Control. Nothing is written, with one exception that is avoided: the
default site is not seeded here (the Foundation reader would store it), so a
missing stored site is rebuilt in memory from the configuration.

How a stack is tied to a capture setting
----------------------------------------
Stack rows carry no capture setting id. The rows of a stack run include one
``stacking.frame_weight`` row for each member frame, and the frame rows carry
the setting. A stack therefore belongs to the setting that most of its member
frames belong to (a tie goes to the lowest setting id). A stack of several
settings is noted. The stacks of an exposure group (``groups/<name>`` or
``<stack>#group-<n>-<seconds>s``) belong to the setting whose exposure
matches the group's exposure to within half a second, when only one does.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from astrometricslib import (
    AstrometricsError,
    CaptureSetting,
    QualityMeasurement,
    SetAsideFrame,
    SubjectLevel,
    Target,
    frame_is_spectral,
    frame_subject_id,
    parse_coordinate_string,
)
from wayfindinglib.analytics.capture_history import normalize_name
from wayfindinglib.analytics.result_quality.capture_overheads import EXPOSURE_MATCH_SECONDS
from wayfindinglib.analytics.result_quality.efficiency import (
    READ_NOISE_METRIC,
    SKY_RATE_METRIC,
    STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC,
    FrameObservation,
    baseline_key,
    baseline_reference,
    build_baseline_nights,
    frame_observations_from_measurements,
    physical_reference_for,
)
from wayfindinglib.analytics.result_quality.observing_values import sky_geometry
from wayfindinglib.analytics.result_quality.sky_model import (
    SkyModel,
    SkyRateSample,
    fit_sky_model,
    predict_sky_rate,
)
from wayfindinglib.data_access.equipment_catalog_reader import list_telescopes
from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
from wayfindinglib.models.quality.efficiency import EfficiencyReference, ScienceGoal
from wayfindinglib.models.session.observation_session import ObservationSession

logger = logging.getLogger(__name__)

SPECTRAL_FRAME_SHARE = 0.5
"""A target counts as a spectroscopy target when more than this share of its
light frames were taken through the spectroscopy filter. Half is the plainest
majority rule. Design estimate, register row WAY-D68."""

EXTENDED_OBJECT_TYPES = frozenset({"messier", "ngc", "ic"})
"""Target types that are extended objects: galaxies, nebulae and clusters
(decision S6). The catalog type is read from the target's id and cannot tell a
galaxy from a cluster, and none of the three is a point source."""

GROUP_STACK_PATTERN = re.compile(r"(?:/groups/|#group-)")
"""Marks the subject id of an exposure-group stack."""

GROUP_EXPOSURE_PATTERN = re.compile(r"#group-\d+-([0-9.]+)s$")
"""Reads the exposure from the subject id of a group with no stack file."""

QUARANTINE_METRICS = (
    "frame.quarantine_roundness",
    "frame.quarantine_sky_median_adu",
    "frame.detected_region_count",
)
"""The frame rows that the stacker's quarantine step stores."""

FRAME_WEIGHT_METRIC = "stacking.frame_weight"
GROUP_EXPOSURE_METRIC = "stacking.group_exposure_s"
DEFAULT_SITE_PROFILE_ID = "default"


@dataclass
class TargetRows:
    """The stored measurements of a target and what they point at.

    Attributes
    ----------
    rows : `list` [`QualityMeasurement`]
        The newest row for each subject, metric and channel, library stacks
        only (rows of subset stacks are left out).
    settings : `dict` [`int`, `CaptureSetting`]
        The capture settings the rows point at, by id.
    notes : `list` [`str`]
        Remarks made while reading, such as a stack of several settings.
    """

    rows: list[QualityMeasurement]
    settings: dict[int, CaptureSetting]
    notes: list[str] = field(default_factory=list)


def read_target_rows(astrometrics: Any, target_id: str) -> TargetRows:
    """Read a target's stored measurements and the settings they use.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle.
    target_id : `str`
        The target.

    Returns
    -------
    target_rows : `TargetRows`
        The rows and their capture settings. A library with no measurements
        gives empty lists.
    """
    rows = list(astrometrics.quality.query(target_id=target_id, library_only=True))
    ids = sorted({
        row.subject.capture_setting_id for row in rows if row.subject.capture_setting_id is not None
    })
    settings = dict(astrometrics.quality.capture_settings(ids=ids)) if ids else {}
    return TargetRows(rows=rows, settings=settings)


def science_goal_for(target: Target) -> tuple[ScienceGoal, str]:
    """Choose the science goal of a target from its type and its frames.

    A target with mostly spectroscopy frames is a spectroscopy target. A
    Messier, NGC or IC object is an extended object. Anything else is a faint
    point source, which is also the answer when the type is unknown (decision
    S6). Photometry targets cannot be told from other stars by name, so they
    take the faint point source goal too.

    Parameters
    ----------
    target : `Target`
        The target.

    Returns
    -------
    goal, reason : `tuple` [`ScienceGoal`, `str`]
        The goal and one sentence saying why.
    """
    lights = [frame for frame in target.frames if str(frame.role).upper().endswith("LIGHT")]
    spectral = [frame for frame in lights if frame_is_spectral(frame)]
    if lights and len(spectral) / len(lights) > SPECTRAL_FRAME_SHARE:
        return ScienceGoal.SLITLESS_SPECTRAL_RESOLUTION, (
            f"{len(spectral)} of {len(lights)} light frames were taken through the spectroscopy filter."
        )
    object_type = getattr(target.object_type, "value", str(target.object_type))
    if object_type in EXTENDED_OBJECT_TYPES:
        return ScienceGoal.EXTENDED_OBJECT, f"The target's catalog type is {object_type}, an extended object."
    return ScienceGoal.FAINT_POINT_SOURCE, (
        f"The target's catalog type is {object_type}, so it is treated as a faint point source."
    )


def target_direction(target: Target) -> tuple[float, float] | None:
    """Read a target's position in degrees.

    Parameters
    ----------
    target : `Target`
        The target.

    Returns
    -------
    direction : `tuple` [`float`, `float`] or `None`
        Right ascension and declination in degrees, or ``None`` when the
        target has no usable position (the library's placeholder position of
        0h 0m 0s counts as none).
    """
    if not target.ra or not target.dec or (target.ra == "0h 0m 0s" and target.dec.startswith("0")):
        return None
    try:
        return (
            float(parse_coordinate_string(target.ra, is_ra=True)),
            float(parse_coordinate_string(target.dec, is_ra=False)),
        )
    except ValueError, TypeError:
        return None


def frame_start_epochs(target: Target) -> dict[str, float]:
    """Find the middle of each frame's exposure, by frame subject id.

    Parameters
    ----------
    target : `Target`
        The target, with its frame records.

    Returns
    -------
    epochs : `dict` [`str`, `float`]
        Seconds since the Unix epoch at the middle of each light frame's
        exposure. A frame with no time is left out.
    """
    epochs: dict[str, float] = {}
    for frame in target.frames:
        if frame.timestamp is None or not str(frame.role).upper().endswith("LIGHT"):
            continue
        try:
            exposure = float(frame.exposure)
        except TypeError, ValueError:
            exposure = 0.0
        epochs[frame_subject_id(frame.path, target.id)] = float(frame.timestamp) + exposure / 2.0
    return epochs


def frame_altitudes(target: Target) -> dict[str, float]:
    """Read the altitude each light frame was taken at, by frame subject id.

    Parameters
    ----------
    target : `Target`
        The target, with its frame records.

    Returns
    -------
    altitudes : `dict` [`str`, `float`]
        The altitude of the pointing, in degrees, from the frame record. A
        frame with none is left out.
    """
    return {
        frame_subject_id(frame.path, target.id): float(frame.altitude_degrees)
        for frame in target.frames
        if frame.altitude_degrees is not None and str(frame.role).upper().endswith("LIGHT")
    }


def frame_sensor_temperatures(target: Target) -> dict[str, float]:
    """Read the sensor temperature of each light frame, by frame subject id.

    Parameters
    ----------
    target : `Target`
        The target, with its frame records.

    Returns
    -------
    temperatures : `dict` [`str`, `float`]
        The temperature in degrees Celsius. A frame with none is left out.
    """
    return {
        frame_subject_id(frame.path, target.id): float(frame.sensor_temperature_c)
        for frame in target.frames
        if frame.sensor_temperature_c is not None and str(frame.role).upper().endswith("LIGHT")
    }


# -----------------------------------------------------------------------------
# Stacks and settings
# -----------------------------------------------------------------------------


def _group_exposure(
    stack_id: str, rows_by_subject: Mapping[str, Sequence[QualityMeasurement]]
) -> float | None:
    """Find the exposure of an exposure-group stack.

    Parameters
    ----------
    stack_id : `str`
        The stack's subject id.
    rows_by_subject : `Mapping` [`str`, `Sequence` [`QualityMeasurement`]]
        The target's stack-level rows, by subject id.

    Returns
    -------
    exposure_seconds : `float` or `None`
        The stored group exposure, else the one written in the id, else
        ``None``.
    """
    for row in rows_by_subject.get(stack_id, ()):
        if row.metric == GROUP_EXPOSURE_METRIC and row.value is not None:
            return float(row.value)
    match = GROUP_EXPOSURE_PATTERN.search(stack_id)
    return float(match.group(1)) if match else None


def assign_stacks_to_settings(
    rows: Iterable[QualityMeasurement],
    settings: Mapping[int, CaptureSetting],
    member_frames_by_stack: Mapping[str, Sequence[str]],
) -> tuple[dict[str, int], list[str]]:
    """Tie each stack to the capture setting its frames belong to.

    Parameters
    ----------
    rows : `Iterable` [`QualityMeasurement`]
        The target's newest rows.
    settings : `Mapping` [`int`, `CaptureSetting`]
        The target's capture settings.
    member_frames_by_stack : `Mapping` [`str`, `Sequence` [`str`]]
        The member frames (their subject ids) of the newest run of each
        stack, from the ``stacking.frame_weight`` rows.

    Returns
    -------
    assignment, notes : `tuple`
        The setting id of each stack subject id that could be placed, and
        one sentence for each stack that holds more than one setting or that
        could not be placed.
    """
    frame_setting: dict[str, int] = {}
    stack_rows: dict[str, list[QualityMeasurement]] = defaultdict(list)
    for row in rows:
        if row.subject.level == SubjectLevel.FRAME and row.subject.capture_setting_id is not None:
            frame_setting[row.subject.subject_id] = row.subject.capture_setting_id
        elif row.subject.level == SubjectLevel.STACK and row.subject.variant is None:
            stack_rows[row.subject.subject_id].append(row)
    assignment: dict[str, int] = {}
    notes: list[str] = []
    for stack_id in sorted(stack_rows):
        if GROUP_STACK_PATTERN.search(stack_id):
            exposure = _group_exposure(stack_id, stack_rows)
            matches = sorted(
                setting_id
                for setting_id, setting in settings.items()
                if exposure is not None
                and setting.exposure_seconds is not None
                and abs(setting.exposure_seconds - exposure) <= EXPOSURE_MATCH_SECONDS
            )
            if len(matches) == 1:
                assignment[stack_id] = matches[0]
            else:
                notes.append(
                    f"The exposure-group stack {stack_id} could not be tied to one capture setting "
                    f"({len(matches)} settings match its exposure)."
                )
            continue
        members = member_frames_by_stack.get(stack_id, ())
        counts = Counter(frame_setting[frame_id] for frame_id in members if frame_id in frame_setting)
        if not counts:
            notes.append(
                f"The stack {stack_id} stored no member frames that carry a capture setting, so its rows "
                "belong to no setting."
            )
            continue
        best = max(sorted(counts), key=lambda setting_id: counts[setting_id])
        assignment[stack_id] = best
        if len(counts) > 1:
            notes.append(
                f"The stack {stack_id} holds frames of {len(counts)} capture settings; its rows are "
                f"assigned to the setting with most frames (id {best})."
            )
    return assignment, notes


# -----------------------------------------------------------------------------
# Frames
# -----------------------------------------------------------------------------


def read_set_aside_frames(astrometrics: Any, target_id: str) -> tuple[list[SetAsideFrame], str | None]:
    """List the frames the stacker set aside for a target.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle.
    target_id : `str`
        The target.

    Returns
    -------
    frames, problem : `tuple`
        The set-aside frames, and a sentence saying why they could not be
        read (``None`` when they were read).
    """
    try:
        report = astrometrics.processing.restore_excluded_frames(target_id, apply=False)
    except (AstrometricsError, OSError) as error:
        return [], f"The set-aside frames could not be listed: {error}"
    return list(report.frames), None


def build_observations(
    target: Target,
    target_rows: TargetRows,
    set_aside_reason_by_frame: Mapping[str, str],
) -> list[FrameObservation]:
    """Turn the target's frame rows into one observation per frame.

    Parameters
    ----------
    target : `Target`
        The target; its frame records give the times, and its position gives
        the direction.
    target_rows : `TargetRows`
        The target's stored rows and settings.
    set_aside_reason_by_frame : `Mapping` [`str`, `str`]
        The reason each set-aside frame was set aside, by frame subject id.

    Returns
    -------
    observations : `list` [`FrameObservation`]
        The frames.
    """
    direction = target_direction(target)
    epochs = frame_start_epochs(target)
    frame_ids = {
        row.subject.subject_id for row in target_rows.rows if row.subject.level == SubjectLevel.FRAME
    }
    return frame_observations_from_measurements(
        target_rows.rows,
        target_rows.settings,
        start_epoch_by_frame={frame_id: epochs[frame_id] for frame_id in frame_ids if frame_id in epochs},
        direction_by_frame=dict.fromkeys(frame_ids, direction) if direction else None,
        set_aside_reason_by_frame=set_aside_reason_by_frame,
    )


# -----------------------------------------------------------------------------
# Library-wide context: references and the sky model
# -----------------------------------------------------------------------------


def stored_site(butler: Any, config: Any) -> SiteProfile:
    """Read the observing site without storing anything.

    Parameters
    ----------
    butler : `DiskButler`
        The storage layer.
    config : `AppConfiguration`
        The configuration, for the site when none is stored.

    Returns
    -------
    site : `SiteProfile`
        The stored default site, else one built in memory from the
        configuration (and the Denver fallback, with a warning in the log, if
        the configuration names none).
    """
    stored = butler.get("site_profile", {"id": DEFAULT_SITE_PROFILE_ID})
    if stored is not None:
        return stored
    from wayfindinglib.data_access.site_profile_reader import resolve_site_location

    location = resolve_site_location(config)
    return SiteProfile(
        id=DEFAULT_SITE_PROFILE_ID,
        name="Default Site",
        latitude_deg=location["latitude"],
        longitude_deg=location["longitude"],
        elevation_m=location["elevation"],
    )


class LibraryContext:
    """What the whole library knows about one camera, read once and kept.

    The equipment baseline and the sky model need the frames of every target
    taken with a camera, not only the target being reported on. They are read
    the first time they are asked for, and kept for the life of this object
    (one report).

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle.
    butler : `DiskButler`
        The storage layer, for the site.
    config : `AppConfiguration`
        The configuration, for the telescopes.
    """

    def __init__(self, astrometrics: Any, butler: Any, config: Any) -> None:
        self._astrometrics = astrometrics
        self._butler = butler
        self._config = config
        self._observations: dict[str, list[FrameObservation]] = {}
        self._rows: dict[str, list[QualityMeasurement]] = {}
        self._baselines: dict[str, tuple[EfficiencyReference | None, str | None]] = {}
        self._models: dict[str, tuple[SkyModel | None, str | None]] = {}
        self._site: SiteProfile | None = None
        self.notes: list[str] = []

    @property
    def site(self) -> SiteProfile:
        """The observing site, read once.

        Returns
        -------
        site : `SiteProfile`
            The site.
        """
        if self._site is None:
            self._site = stored_site(self._butler, self._config)
        return self._site

    def camera_rows(self, camera: str) -> list[QualityMeasurement]:
        """Read the width, sky rate and read noise rows of a camera's frames.

        Parameters
        ----------
        camera : `str`
            The camera name.

        Returns
        -------
        rows : `list` [`QualityMeasurement`]
            The newest row of each frame and metric.
        """
        if camera not in self._rows:
            rows: list[QualityMeasurement] = []
            for metric in (STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC, SKY_RATE_METRIC, READ_NOISE_METRIC):
                try:
                    rows.extend(
                        self._astrometrics.quality.query(
                            metric=metric, level="frame", camera=camera, library_only=True
                        )
                    )
                except (AstrometricsError, sqlite3.Error) as error:
                    self.notes.append(f"The library's {metric} rows for {camera} could not be read: {error}")
            self._rows[camera] = rows
        return self._rows[camera]

    def camera_observations(self, camera: str) -> list[FrameObservation]:
        """Build an observation for every library frame of a camera.

        Parameters
        ----------
        camera : `str`
            The camera name.

        Returns
        -------
        observations : `list` [`FrameObservation`]
            One for each frame with a width or sky rate row. Their times and
            directions are filled in from the frame records of their targets.
        """
        if camera not in self._observations:
            rows = self.camera_rows(camera)
            ids = sorted({row.subject.capture_setting_id for row in rows if row.subject.capture_setting_id})
            settings = dict(self._astrometrics.quality.capture_settings(ids=ids)) if ids else {}
            epochs: dict[str, float] = {}
            directions: dict[str, tuple[float, float]] = {}
            for target_id in sorted({row.subject.target_id for row in rows if row.subject.target_id}):
                try:
                    other = self._astrometrics.targets.get(target_id)
                except AstrometricsError:
                    other = None
                if other is None:
                    continue
                direction = target_direction(other)
                for frame_id, epoch in frame_start_epochs(other).items():
                    epochs[frame_id] = epoch
                    if direction is not None:
                        directions[frame_id] = direction
            self._observations[camera] = frame_observations_from_measurements(
                rows, settings, start_epoch_by_frame=epochs, direction_by_frame=directions
            )
        return self._observations[camera]

    def reference_for(self, setting: CaptureSetting) -> EfficiencyReference | None:
        """Give the efficiency reference of a capture setting.

        The equipment baseline comes first (the equipment's own good nights),
        because it measures a frame against what this equipment can do. When
        the library has too few nights for a baseline, the physical reference
        (diffraction limit and site seeing) is used instead. When neither can
        be built, ``None``, and the reason is added to `notes`.

        Parameters
        ----------
        setting : `CaptureSetting`
            The capture setting.

        Returns
        -------
        reference : `EfficiencyReference` or `None`
            The reference, or ``None``.
        """
        key = baseline_key(setting)
        if key not in self._baselines:
            observations = [
                observation
                for observation in self.camera_observations(setting.camera)
                if baseline_key(observation.capture_setting) == key
            ]
            outcome = baseline_reference(build_baseline_nights(observations), key)
            reference, reason = outcome.reference, outcome.reason
            if reference is None:
                telescope = next(
                    (
                        candidate
                        for candidate in list_telescopes(self._config)
                        if setting.telescope
                        and normalize_name(candidate.name) == normalize_name(setting.telescope)
                    ),
                    None,
                )
                if telescope is None:
                    reason = f"{reason} The telescope {setting.telescope!r} is not in the equipment catalog."
                else:
                    physical = physical_reference_for(telescope, self.site, setting.pixel_scale_arcsec)
                    reference = physical.reference
                    reason = reason if reference is not None else f"{reason} {physical.reason}"
            self._baselines[key] = (reference, None if reference is not None else reason)
            if reference is None:
                self.notes.append(f"No efficiency reference for {key}: {reason}")
        return self._baselines[key][0]

    def sky_model_for(self, setting: CaptureSetting) -> tuple[SkyModel | None, str | None]:
        """Fit the sky model of a capture setting's equipment key.

        Parameters
        ----------
        setting : `CaptureSetting`
            The capture setting.

        Returns
        -------
        model, reason : `tuple`
            The model, or ``None`` and the reason there is none.
        """
        key = baseline_key(setting)
        if key not in self._models:
            samples = [
                SkyRateSample(
                    frame_id=observation.frame_id,
                    night_id=observation.night_id,
                    epoch_seconds=observation.start_epoch_seconds,
                    ra_deg=observation.ra_deg,
                    dec_deg=observation.dec_deg,
                    sky_rate_e_per_s_per_px=observation.sky_rate_e_per_s_per_px,
                )
                for observation in self.camera_observations(setting.camera)
                if baseline_key(observation.capture_setting) == key
                and observation.set_aside_reason is None
                and observation.night_id is not None
                and observation.start_epoch_seconds is not None
                and observation.ra_deg is not None
                and observation.dec_deg is not None
                and observation.sky_rate_e_per_s_per_px
            ]
            site = self.site
            try:
                outcome = fit_sky_model(samples, key, site.latitude_deg, site.longitude_deg, site.elevation_m)
                self._models[key] = (outcome.model, outcome.reason)
            except ValueError as error:
                self._models[key] = (None, f"The sky model could not be fitted: {error}")
        return self._models[key]

    def moonless_sky_rates(self, observations: Sequence[FrameObservation]) -> dict[str, float]:
        """Predict the sky rate each frame would have had without the Moon.

        Parameters
        ----------
        observations : `Sequence` [`FrameObservation`]
            The target's frames.

        Returns
        -------
        rates : `dict` [`str`, `float`]
            The model's moonless rate in electrons per second per pixel, by
            frame id. A frame with no time or direction, or whose setting has
            no sky model, is left out, and so the Moon's share of its loss
            is not split out.
        """
        rates: dict[str, float] = {}
        by_key: dict[str, list[FrameObservation]] = defaultdict(list)
        for observation in observations:
            if (
                observation.set_aside_reason is None
                and observation.start_epoch_seconds is not None
                and observation.ra_deg is not None
                and observation.dec_deg is not None
            ):
                by_key[baseline_key(observation.capture_setting)].append(observation)
        site = self.site
        for group in by_key.values():
            model, _reason = self.sky_model_for(group[0].capture_setting)
            if model is None:
                continue
            geometries = sky_geometry(
                [o.start_epoch_seconds for o in group],
                [o.ra_deg for o in group],
                [o.dec_deg for o in group],
                site.latitude_deg,
                site.longitude_deg,
                site.elevation_m,
            )
            for observation, geometry in zip(group, geometries, strict=True):
                prediction = predict_sky_rate(model, geometry)
                if prediction.moonless_sky_rate_e_per_s_per_px is not None:
                    rates[observation.frame_id] = prediction.moonless_sky_rate_e_per_s_per_px
        return rates


# -----------------------------------------------------------------------------
# Calibration, sessions
# -----------------------------------------------------------------------------


def read_calibration_rows(astrometrics: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Read the calibration library's dark and flat rows.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle.

    Returns
    -------
    darks, flats, problem : `tuple`
        The rows of ``processing.calibration.query(detail="counts")`` and a
        sentence saying why they could not be read (``None`` when they could).
    """
    try:
        answer = astrometrics.processing.calibration.query(detail="counts")
    except (AstrometricsError, OSError) as error:
        return [], [], f"The calibration library could not be read: {error}"
    return list(answer.darks or []), list(answer.flats or []), None


def sessions_of_target(butler: Any, target_id: str) -> list[ObservationSession]:
    """Find the stored night plans that have a queue entry for a target.

    Parameters
    ----------
    butler : `DiskButler`
        The storage layer.
    target_id : `str`
        The target.

    Returns
    -------
    sessions : `list` [`ObservationSession`]
        The stored plans, in storage order.
    """
    return [
        session
        for session in butler.get_all("observation_session")
        if any(entry.target_id == target_id for entry in session.queue)
    ]


def median_or_none(values: Iterable[float | None]) -> float | None:
    """Take the median of the values that are not ``None``.

    Parameters
    ----------
    values : `Iterable` [`float` or `None`]
        The values.

    Returns
    -------
    median : `float` or `None`
        The median, or ``None`` when no value is given.
    """
    numbers = [float(value) for value in values if value is not None]
    return float(np.median(numbers)) if numbers else None


ReferenceProvider = Callable[[CaptureSetting], EfficiencyReference | None]
"""A function that gives the efficiency reference of a capture setting."""
