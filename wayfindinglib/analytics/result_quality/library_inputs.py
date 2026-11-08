"""Purpose: Read what the quality analyses need from the library, in one place.

Description: The target dataset report (Planning), tonight's ranking (Planning)
and the night review (Control) all work on the same kind of input: the stored
measurements of frames, the capture setting each frame used, the time and
direction of each frame, the observing site, an efficiency reference for each
setting and a sky model for each piece of equipment. Before this module each
of them read these in its own way, and the copies drifted apart. This module is
the one builder. It is Foundation code, so Planning and Control can both call
it without importing each other.

What is here:

* ``frame_times`` and ``target_direction``: the time and place of a target's
  frames, read from the frame records and the target's position;
* ``set_aside_reasons``: the one definition of which frames the stacker set
  aside (a stored row says the frame was moved to ``_excluded``);
* ``stored_site``: the observing site, read without storing anything;
* ``dominant_setting``: the capture setting that holds most of the exposure;
* ``LibraryInputs``: a reader that keeps what it has read for the life of one
  report, and gives the frame observations of a camera, the efficiency
  references of a setting and the sky models of the equipment.

Nothing here writes. In particular the observing site is never stored: a
missing site is rebuilt in memory from the configuration, and a note says so.

Which reference a setting uses
------------------------------
Two references can exist for one setting. The baseline is the user's own good
nights with the same equipment (needs at least three nights). The physical
reference is the diffraction limit and the site seeing of the telescope named
by the capture setting (not the telescope that is active now).
``reference_for`` gives the baseline when there is one, else the physical
reference. ``reference_choice`` gives both, so a report can state both. When
a night is reviewed, that night is left out of its own baseline.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from astrometricslib import Target
from astrometricslib.drivers import VersionedCache, get_camera_data_versions
from astrometricslib.exceptions import AstrometricsError
from astrometricslib.reports import CaptureSetting, QualityMeasurement, SubjectLevel, observing_night_id
from wayfindinglib.analytics.capture_history import normalize_name
from wayfindinglib.analytics.result_quality.efficiency import (
    READ_NOISE_METRIC,
    SKY_RATE_METRIC,
    STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC,
    FrameObservation,
    assess_efficiency,
    baseline_key,
    baseline_reference,
    build_baseline_nights,
    frame_observations_from_measurements,
    physical_reference_for,
)
from wayfindinglib.analytics.result_quality.observing_values import sky_geometry
from wayfindinglib.analytics.result_quality.science_goal import GoalSource, goal_of_setting
from wayfindinglib.analytics.result_quality.sky_model import (
    SkyModel,
    SkyRateSample,
    fit_sky_model,
    predict_sky_rate,
)
from wayfindinglib.data_access.equipment_catalog_reader import list_telescopes
from wayfindinglib.data_access.site_profile_reader import configured_observer_location, resolve_site_location
from wayfindinglib.models.equipment_and_site.equipment import Telescope
from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
from wayfindinglib.models.quality.efficiency import EfficiencyReference

logger = logging.getLogger(__name__)

SKY_MODEL_CACHE_MAXIMUM_ENTRIES = 16
"""How many fitted sky models the cache keeps. A model is a few small tables,
and a library has a few cameras, so 16 is plenty. Design estimate, register
row WAY-D106."""

_SKY_MODEL_CACHE = VersionedCache(maximum_entries=SKY_MODEL_CACHE_MAXIMUM_ENTRIES)
"""The fitted sky models, by library, equipment key and site. Each is kept
until the finished-night measurements of its camera change
(`get_camera_data_versions` with ``finished_before_night``). A frame of the
night in progress does not change them (register row WAY-D107)."""

CAMERA_OBSERVATIONS_CACHE_MAXIMUM_ENTRIES = 8
"""How many cameras' frame observations the cache keeps. A library has a few
cameras and one camera's observations are a few thousand small records, so 8
stay well under a few megabytes. Design estimate, register row WAY-D107."""

_CAMERA_OBSERVATIONS_CACHE = VersionedCache(maximum_entries=CAMERA_OBSERVATIONS_CACHE_MAXIMUM_ENTRIES)
"""The frame observations of each camera, by library and camera. Each is
kept until the finished-night measurements of its camera change; the night in
progress is not read (register row WAY-D107)."""

DEFAULT_SITE_PROFILE_ID = "default"
"""The id of the stored default site."""

SITE_AGREEMENT_DEG = 0.01
"""Two site positions closer than this in latitude and longitude count as the
same place. 0.01 degree is about 1 km, far finer than anything the sky model
or the airmass depends on. Design estimate, register row WAY-D82."""

NO_REASON_RECORDED = "set aside, reason not recorded"
"""The reason given to a frame in an ``_excluded`` folder that no stored row
and no manifest explains."""

SET_ASIDE_MARKER = "_excluded"
"""Text in a row's ``action_taken`` that says the stacker moved the frame to
its ``_excluded`` folder (``"frame moved to _excluded/"``). Design estimate,
register row WAY-D82."""

ReferenceProvider = Callable[[CaptureSetting], EfficiencyReference | None]
"""A function that gives the efficiency reference of a capture setting."""


# -----------------------------------------------------------------------------
# Frames and targets
# -----------------------------------------------------------------------------


def frame_exposure_seconds(exposure_text: str | float | None) -> float | None:
    """Read a frame record's exposure as seconds.

    Parameters
    ----------
    exposure_text : `str` or `float` or `None`
        The frame record's ``exposure`` field.

    Returns
    -------
    seconds : `float` or `None`
        The exposure, or ``None`` when it is not a positive number.
    """
    if exposure_text is None:
        return None
    try:
        seconds = float(exposure_text)
    except TypeError, ValueError:
        return None
    return seconds if seconds > 0.0 and math.isfinite(seconds) else None


def frame_times(target: Target) -> dict[str, float]:
    """Give the middle of each light frame's exposure.

    Parameters
    ----------
    target : `Target`
        The library target.

    Returns
    -------
    times : `dict` [`str`, `float`]
        Seconds since the Unix epoch, by frame subject id. A frame with no
        time stamp, and a frame that is not a light frame, is left out.
    """
    times: dict[str, float] = {}
    for frame in target.frames:
        if not str(frame.role).upper().endswith("LIGHT") or frame.timestamp is None:
            continue
        half = (frame_exposure_seconds(frame.exposure) or 0.0) / 2.0
        times[target.frame_subject_id(frame)] = float(frame.timestamp) + half
    return times


def target_direction(target: Target) -> tuple[float, float] | None:
    """Read a target's position in degrees.

    Parameters
    ----------
    target : `Target`
        The library target.

    Returns
    -------
    direction : `tuple` [`float`, `float`] or `None`
        Right ascension and declination, or ``None`` when the target's
        position is unverified, is the library's placeholder (0h 0m 0s), or
        cannot be read.
    """
    if target.position_unverified or not target.ra or not target.dec:
        return None
    if target.ra == "0h 0m 0s" and target.dec.startswith("0"):
        return None
    try:
        return (
            float(target.ra_deg),
            float(target.dec_deg),
        )
    except ValueError, TypeError:
        return None


def times_and_directions(
    targets: Iterable[Target],
) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
    """Give the time and the direction of every light frame of some targets.

    Parameters
    ----------
    targets : `Iterable` [`Target`]
        The library targets.

    Returns
    -------
    epochs, directions : `tuple`
        The middle of each frame's exposure and the (right ascension,
        declination) it points at, both by frame subject id. A frame of a
        target with no usable position has a time and no direction.
    """
    epochs: dict[str, float] = {}
    directions: dict[str, tuple[float, float]] = {}
    for target in targets:
        direction = target_direction(target)
        for frame_id, epoch in frame_times(target).items():
            epochs[frame_id] = epoch
            if direction is not None:
                directions[frame_id] = direction
    return epochs, directions


def set_aside_reasons(
    rows: Iterable[QualityMeasurement],
    listed_frames: Iterable[Any] = (),
) -> dict[str, str]:
    """Find the frames the stacker set aside, and why.

    The stacker's quarantine step moves a frame to an ``_excluded`` folder.
    Three places can say so, and this reads them in order of trust. The first
    is the library's own listing of the ``_excluded`` folders
    (``Astrometrics.quality.set_aside_frames``), which already prefers the
    stored rows, then the folder's manifest, then the folder alone. The
    second is a stored row whose ``action_taken`` says the frame was moved.
    The quarantine measurements (roundness, sky median) are stored for kept
    frames too, so only that wording marks a frame as set aside.

    Parameters
    ----------
    rows : `Iterable` [`QualityMeasurement`]
        Frame-level rows.
    listed_frames : `Iterable` [`SetAsideFrameRecord`], optional
        The listing of the target's ``_excluded`` folders. Left out, only the
        rows are read.

    Returns
    -------
    reasons : `dict` [`str`, `str`]
        The reason by frame subject id (by file path for a listed frame that
        has no subject id). The reason is the kind (``clouded`` or
        ``trailed``) when it is known, else the recorded words, else a
        statement that no reason was recorded.
    """
    reasons: dict[str, str] = {}
    for record in listed_frames:
        key = record.subject_id or record.path
        reasons.setdefault(key, record.kind or record.reason or record.action_taken or NO_REASON_RECORDED)
    for row in rows:
        if (
            row.subject.level == SubjectLevel.FRAME
            and row.action_taken
            and SET_ASIDE_MARKER in row.action_taken
        ):
            reasons.setdefault(row.subject.subject_id, row.action_taken)
    return reasons


def dominant_setting(observations: Sequence[FrameObservation]) -> CaptureSetting | None:
    """Find the capture setting with the most exposure time.

    Parameters
    ----------
    observations : `Sequence` [`FrameObservation`]
        The frames.

    Returns
    -------
    setting : `CaptureSetting` or `None`
        The setting, or ``None`` when no frame has an exposure time. A tie
        goes to the setting met first.
    """
    totals: dict[CaptureSetting, float] = defaultdict(float)
    for observation in observations:
        seconds = observation.effective_exposure_seconds
        if seconds:
            totals[observation.capture_setting] += seconds
    return max(totals, key=lambda setting: totals[setting]) if totals else None


def build_target_observations(
    target: Target,
    rows: Sequence[QualityMeasurement],
    settings: Mapping[int, CaptureSetting],
    set_aside_reason_by_frame: Mapping[str, str] | None = None,
) -> list[FrameObservation]:
    """Turn a target's frame rows into one observation per frame.

    Parameters
    ----------
    target : `Target`
        The target; its frame records give the times, and its position gives
        the direction.
    rows : `Sequence` [`QualityMeasurement`]
        The target's stored rows.
    settings : `Mapping` [`int`, `CaptureSetting`]
        The capture settings by id.
    set_aside_reason_by_frame : `Mapping` [`str`, `str`], optional
        The reason each set-aside frame was set aside, by frame subject id.

    Returns
    -------
    observations : `list` [`FrameObservation`]
        The frames.
    """
    epochs, directions = times_and_directions([target])
    frame_ids = {row.subject.subject_id for row in rows if row.subject.level == SubjectLevel.FRAME}
    return frame_observations_from_measurements(
        rows,
        settings,
        start_epoch_by_frame={frame_id: epochs[frame_id] for frame_id in frame_ids if frame_id in epochs},
        direction_by_frame={
            frame_id: directions[frame_id] for frame_id in frame_ids if frame_id in directions
        }
        or None,
        set_aside_reason_by_frame=set_aside_reason_by_frame,
    )


# -----------------------------------------------------------------------------
# The site
# -----------------------------------------------------------------------------


def stored_site(butler: Any, config: Any, notes: list[str] | None = None) -> SiteProfile:
    """Read the observing site without storing anything.

    The configured location wins when it is set. A stored default profile
    is the fallback for a configuration with no location. It once shadowed
    the configuration (the copy came out Denver while the configuration said
    45.76 N, -110.74 E), so the configuration is read first now.

    Parameters
    ----------
    butler : `DiskButler`
        The storage layer.
    config : `AppConfiguration`
        The configuration, for the site.
    notes : `list` [`str`], optional
        Where to add a remark when the stored profile and the configuration
        disagree.

    Returns
    -------
    site : `SiteProfile`
        The configured site (keeping the stored profile's name, seeing and
        avoidance zones when there is one), else the stored default site,
        else one built from the Denver fallback (with a warning in the log).
    """
    stored = butler.get("site_profile", {"id": DEFAULT_SITE_PROFILE_ID})
    configured = configured_observer_location(config)
    if configured is None:
        if stored is not None:
            return stored
        configured = resolve_site_location(config)
    elif stored is not None:
        differs = (
            abs(stored.latitude_deg - configured["latitude"]) > SITE_AGREEMENT_DEG
            or abs(stored.longitude_deg - configured["longitude"]) > SITE_AGREEMENT_DEG
        )
        if differs and notes is not None:
            notes.append(
                f"The stored site profile ({stored.latitude_deg:.2f}, {stored.longitude_deg:.2f}) disagrees "
                f"with the configured location ({configured['latitude']:.2f}, "
                f"{configured['longitude']:.2f}). "
                "The configured location was used."
            )
        return stored.model_copy(
            update={
                "latitude_deg": configured["latitude"],
                "longitude_deg": configured["longitude"],
                "elevation_m": configured["elevation"],
            }
        )
    return SiteProfile(
        id=DEFAULT_SITE_PROFILE_ID,
        name="Default Site",
        latitude_deg=configured["latitude"],
        longitude_deg=configured["longitude"],
        elevation_m=configured["elevation"],
    )


def telescope_of(setting: CaptureSetting, config: Any) -> Telescope | None:
    """Find the telescope that a capture setting names.

    Parameters
    ----------
    setting : `CaptureSetting`
        The capture setting.
    config : `AppConfiguration`
        The configuration that holds the equipment catalog.

    Returns
    -------
    telescope : `Telescope` or `None`
        The catalog telescope whose name matches the setting's telescope (case,
        spaces and punctuation ignored), or ``None``. The telescope that is
        active now is not used: an old frame was taken with the optic it names.
    """
    if not setting.telescope:
        return None
    wanted = normalize_name(setting.telescope)
    return next(
        (candidate for candidate in list_telescopes(config) if normalize_name(candidate.name) == wanted), None
    )


# -----------------------------------------------------------------------------
# What the whole library knows
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ReferenceChoice:
    """The efficiency references that exist for one capture setting.

    Attributes
    ----------
    used : `EfficiencyReference` or `None`
        The reference efficiency is measured against: the baseline when there
        is one, else the physical reference, else ``None``.
    baseline : `EfficiencyReference` or `None`
        The user's own good nights with this equipment.
    baseline_reason : `str` or `None`
        Why there is no baseline.
    physical : `EfficiencyReference` or `None`
        The telescope's diffraction limit and the site's seeing.
    physical_reason : `str` or `None`
        Why there is no physical reference.
    """

    used: EfficiencyReference | None
    baseline: EfficiencyReference | None
    baseline_reason: str | None
    physical: EfficiencyReference | None
    physical_reason: str | None


class _IncompleteReadError(Exception):
    """A read of a camera's frames failed in part.

    Raised inside the cache builder so the cache keeps nothing, and caught by
    `LibraryInputs.camera_observations`, which uses the partial result once.

    Parameters
    ----------
    observations : `list` [`FrameObservation`]
        What could be read.
    """

    def __init__(self, observations: list[FrameObservation]) -> None:
        """Hold the partial result."""
        super().__init__("The frames of a camera could only be read in part.")
        self.observations = observations


@dataclass
class LibraryInputs:
    """What the whole library knows about the equipment, read once and kept.

    The equipment baseline and the sky model need the frames of every target
    taken with a camera, not only the target being reported on. They are read
    the first time they are asked for, and kept for the life of this object
    (one report, one ranking or one night review).

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle.
    butler : `DiskButler`
        The storage layer, for the site.
    config : `AppConfiguration`
        The configuration, for the telescopes and the site.
    known_site : `SiteProfile`, optional
        A site the caller already has. It is used instead of reading one.
    now : `float`, optional
        The moment that decides which observing night is not finished yet, as
        seconds since the Unix epoch. Defaults to the present, read each time
        it is needed.

    Attributes
    ----------
    notes : `list` [`str`]
        Remarks made while reading, such as a missing reference or site.
    """

    astrometrics: Any
    butler: Any
    config: Any
    known_site: SiteProfile | None = None
    notes: list[str] = field(default_factory=list)
    now: float | None = None
    _targets: dict[str, Target] | None = field(default=None, init=False, repr=False)
    _observations: dict[str, list[FrameObservation]] = field(default_factory=dict, init=False, repr=False)
    _rows: dict[str, list[QualityMeasurement]] = field(default_factory=dict, init=False, repr=False)
    _choices: dict[tuple[str, str | None], ReferenceChoice] = field(
        default_factory=dict, init=False, repr=False
    )
    _models: dict[str, tuple[SkyModel | None, str | None]] = field(
        default_factory=dict, init=False, repr=False
    )
    _camera_versions: dict[tuple[str, str], tuple[str | int, ...] | None] = field(
        default_factory=dict, init=False, repr=False
    )
    _site: SiteProfile | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """Take a site the caller gave."""
        self._site = self.known_site

    @property
    def site(self) -> SiteProfile:
        """The observing site, read once and never stored.

        Returns
        -------
        site : `SiteProfile`
            The site. When none is stored, the note says where it came from.
        """
        if self._site is None:
            self._site = stored_site(self.butler, self.config, self.notes)
            if self.butler.get("site_profile", {"id": DEFAULT_SITE_PROFILE_ID}) is None:
                source = (
                    "the configuration"
                    if configured_observer_location(self.config) is not None
                    else "the built-in default site (Denver), because the configuration names none"
                )
                self.notes.append(
                    f"No observing site is stored, so the site was taken from {source}. "
                    "Altitudes, airmass and the Moon's place depend on it."
                )
        return self._site

    def set_aside_reasons(self, target_id: str, rows: Iterable[QualityMeasurement]) -> dict[str, str]:
        """Find a target's set-aside frames, from the listing and the rows.

        Parameters
        ----------
        target_id : `str`
            The target.
        rows : `Iterable` [`QualityMeasurement`]
            The target's frame rows.

        Returns
        -------
        reasons : `dict` [`str`, `str`]
            The reason by frame subject id (see `set_aside_reasons`). A
            listing that cannot be read leaves a note and falls back to the
            rows.
        """
        try:
            # A target already listed spares the library another listing.
            known = self._targets.get(target_id) if self._targets else None
            listed = self.astrometrics.quality.set_aside_frames(known or target_id)
        except (AstrometricsError, OSError, sqlite3.Error) as error:
            self.notes.append(f"The set-aside frames of {target_id} could not be listed: {error}")
            listed = []
        return set_aside_reasons(rows, listed)

    def targets(self) -> dict[str, Target]:
        """List the library's targets once.

        Returns
        -------
        targets : `dict` [`str`, `Target`]
            The targets by id.
        """
        if self._targets is None:
            self._targets = {target.id: target for target in self.astrometrics.targets.list()}
        return self._targets

    def target(self, target_id: str) -> Target | None:
        """Find one target, from the listing when it has been read.

        Parameters
        ----------
        target_id : `str`
            The target.

        Returns
        -------
        target : `Target` or `None`
            The target, or ``None`` when the library has none by that id.
        """
        if self._targets is not None:
            return self._targets.get(target_id)
        try:
            return self.astrometrics.targets.get(target_id)
        except AstrometricsError:
            return None

    @property
    def unfinished_night_id(self) -> str:
        """Name the observing night that is not finished yet.

        The camera-wide inputs (`camera_observations`, the efficiency
        baseline and the sky model) read only the frames of nights before
        this one, because they describe what the camera usually does and a
        night in progress is not representative (register row WAY-D107).
        """
        return observing_night_id(time.time() if self.now is None else self.now)

    def camera_rows(self, camera: str) -> list[QualityMeasurement]:
        """Read the width, sky rate and read noise rows of a camera's frames.

        Only finished nights are read. A frame of the night in progress
        (`unfinished_night_id`) is left out. A row with no night is kept.

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
                        self.astrometrics.quality.query(
                            metric=metric, level="frame", camera=camera, library_only=True
                        ).rows
                    )
                except (AstrometricsError, sqlite3.Error) as error:
                    self.notes.append(f"The library's {metric} rows for {camera} could not be read: {error}")
            unfinished = self.unfinished_night_id
            self._rows[camera] = [
                row
                for row in rows
                if row.subject.observing_night_id is None or row.subject.observing_night_id < unfinished
            ]
        return self._rows[camera]

    def camera_observations(self, camera: str) -> list[FrameObservation]:
        """Build an observation for every library frame of a camera.

        Only finished nights are read: frames of the night in progress
        (`unfinished_night_id`) are left out.

        Parameters
        ----------
        camera : `str`
            The camera name.

        Returns
        -------
        observations : `list` [`FrameObservation`]
            One for each frame with a width, sky rate or read noise row. Their
            times and directions are filled in from the frame records of their
            targets.
        """
        if camera not in self._observations:
            try:
                library = str(self.config.get_library_path())
            except AttributeError, OSError, TypeError, ValueError:
                library = None
            if library is None:
                observations = self._read_camera_observations(camera)
            else:
                try:
                    # The observations are frozen records, so the cache hands
                    # out the same ones; the list is copied below.
                    observations = _CAMERA_OBSERVATIONS_CACHE.get_or_build(
                        ("camera_observations", library, camera),
                        lambda: self.camera_data_version(camera),
                        lambda: self._read_camera_observations(camera, keep_only_complete=True),
                        copy_result=False,
                    )
                except _IncompleteReadError as incomplete:
                    observations = incomplete.observations
            self._observations[camera] = list(observations)
        return self._observations[camera]

    def camera_data_version(self, camera: str) -> tuple[str | int, ...] | None:
        """Work out the version of the data an equipment baseline reads.

        Parameters
        ----------
        camera : `str`
            The camera name.

        Returns
        -------
        version : `tuple` or `None`
            Changes when a measurement of a finished night or a calibration
            record of this camera is written, and not when a frame of the
            night in progress or another camera's record is (see
            `get_camera_data_versions`). `None` when it cannot be read. It is
            read once for each camera and night in the life of this object.
        """
        night = self.unfinished_night_id
        key = (camera, night)
        if key not in self._camera_versions:
            # One object serves one report, so the version is read once for it,
            # and the sky model and the camera's frames both use that reading.
            versions = get_camera_data_versions(self.config, [camera], finished_before_night=night)
            self._camera_versions[key] = None if versions is None else versions[camera]
        return self._camera_versions[key]

    def _read_camera_observations(
        self, camera: str, *, keep_only_complete: bool = False
    ) -> list[FrameObservation]:
        """Read the observations of a camera's frames from the library.

        Parameters
        ----------
        camera : `str`
            The camera name.
        keep_only_complete : `bool`, optional
            When true and a read failed (a note was added), raise
            `_IncompleteReadError` with the partial result, so the cache keeps
            nothing.

        Returns
        -------
        observations : `list` [`FrameObservation`]
            One for each frame with a width, sky rate or read noise row.

        Raises
        ------
        _IncompleteReadError
            If ``keep_only_complete`` is true and a read failed.
        """
        notes_before = len(self.notes)
        rows = self.camera_rows(camera)
        ids = sorted({row.subject.capture_setting_id for row in rows if row.subject.capture_setting_id})
        settings = dict(self.astrometrics.quality.capture_settings(ids=ids)) if ids else {}
        owners = (
            self.target(target_id)
            for target_id in sorted({row.subject.target_id for row in rows if row.subject.target_id})
        )
        epochs, directions = times_and_directions(owner for owner in owners if owner is not None)
        observations = frame_observations_from_measurements(
            rows, settings, start_epoch_by_frame=epochs, direction_by_frame=directions
        )
        if keep_only_complete and len(self.notes) > notes_before:
            raise _IncompleteReadError(observations)
        return observations

    def reference_choice(
        self, setting: CaptureSetting, exclude_night_id: str | None = None
    ) -> ReferenceChoice:
        """Find the efficiency references that exist for a capture setting.

        The baseline is the equipment's own good nights, so it measures a
        frame against what this equipment can do. The physical reference
        (diffraction limit and site seeing) comes from the telescope the
        setting names.

        Parameters
        ----------
        setting : `CaptureSetting`
            The capture setting.
        exclude_night_id : `str`, optional
            A night left out of the baseline. A night under review is not
            part of its own yardstick.

        Returns
        -------
        choice : `ReferenceChoice`
            Both references where they exist, and the one in use.
        """
        key = baseline_key(setting)
        cache_key = (key, exclude_night_id)
        if cache_key in self._choices:
            return self._choices[cache_key]
        observations = [
            observation
            for observation in self.camera_observations(setting.camera)
            if baseline_key(observation.capture_setting) == key
            and (exclude_night_id is None or observation.night_id != exclude_night_id)
        ]
        baseline = baseline_reference(build_baseline_nights(observations), key)
        telescope = telescope_of(setting, self.config)
        if telescope is None:
            physical_reference, physical_reason = (
                None,
                (f"The telescope {setting.telescope!r} is not in the equipment catalog."),
            )
        else:
            outcome = physical_reference_for(telescope, self.site, setting.pixel_scale_arcsec)
            physical_reference, physical_reason = outcome.reference, outcome.reason
        used = baseline.reference or physical_reference
        choice = ReferenceChoice(
            used=used,
            baseline=baseline.reference,
            baseline_reason=None if baseline.reference is not None else baseline.reason,
            physical=physical_reference,
            physical_reason=None if physical_reference is not None else physical_reason,
        )
        self._choices[cache_key] = choice
        if used is None:
            self.notes.append(
                f"No efficiency reference for {key}: {choice.baseline_reason} {choice.physical_reason}"
            )
        return choice

    def reference_for(
        self, setting: CaptureSetting, exclude_night_id: str | None = None
    ) -> EfficiencyReference | None:
        """Give the efficiency reference of a capture setting.

        Parameters
        ----------
        setting : `CaptureSetting`
            The capture setting.
        exclude_night_id : `str`, optional
            A night left out of the baseline.

        Returns
        -------
        reference : `EfficiencyReference` or `None`
            The baseline, else the physical reference, else ``None`` (and the
            reason is added to `notes`).
        """
        return self.reference_choice(setting, exclude_night_id).used

    def reference_provider(self, exclude_night_id: str | None = None) -> ReferenceProvider:
        """Give a function that finds the reference of a setting.

        Parameters
        ----------
        exclude_night_id : `str`, optional
            A night left out of every baseline.

        Returns
        -------
        reference_for : `Callable`
            The function the summaries in ``aggregates`` expect.
        """

        def reference_for(setting: CaptureSetting) -> EfficiencyReference | None:
            """Give the reference for a capture setting.

            Parameters
            ----------
            setting : `CaptureSetting`
                The setting.

            Returns
            -------
            reference : `EfficiencyReference` or `None`
                The reference, or ``None``.
            """
            return self.reference_for(setting, exclude_night_id)

        return reference_for

    def reference_notes(
        self,
        settings: Iterable[CaptureSetting],
        observations: Sequence[FrameObservation],
        goal: GoalSource,
        exclude_night_id: str | None = None,
    ) -> list[str]:
        """State which reference each setting uses, and the other one.

        Parameters
        ----------
        settings : `Iterable` [`CaptureSetting`]
            The capture settings to describe.
        observations : `Sequence` [`FrameObservation`]
            The frames.
        goal : `ScienceGoal` or `Callable`
            The goal, or a function that gives the goal of a capture setting
            (user decision U2: the goal is chosen for each setting).
        exclude_night_id : `str`, optional
            A night left out of the baseline.

        Returns
        -------
        notes : `list` [`str`]
            One sentence for each setting that has a reference. When both the
            baseline and the physical reference exist, it gives the efficiency
            against the one not in use as well; otherwise it says why the
            other does not exist.
        """
        notes: list[str] = []
        for setting in settings:
            choice = self.reference_choice(setting, exclude_night_id)
            if choice.used is None:
                continue
            if choice.used is choice.baseline:
                other, other_reason, other_kind = choice.physical, choice.physical_reason, "physical"
            else:
                other, other_reason, other_kind = choice.baseline, choice.baseline_reason, "baseline"
            head = (
                f"Efficiency of {baseline_key(setting)} is measured against the {choice.used.name} reference."
            )
            if other is None:
                notes.append(f"{head} The {other_kind} reference is not available: {other_reason}")
                continue
            kept = [o for o in observations if o.capture_setting == setting and o.set_aside_reason is None]
            eta = assess_efficiency(kept, goal_of_setting(goal, setting), other).result.eta
            text = f"{eta:.2f}" if eta is not None else "not found"
            notes.append(
                f"{head} Against the {other.name} reference the same frames reach an efficiency of {text}."
            )
        return notes

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
            site = self.site
            try:
                cache_key = (
                    "sky_model",
                    str(self.config.get_library_path()),
                    key,
                    site.latitude_deg,
                    site.longitude_deg,
                    site.elevation_m,
                )
            except AttributeError, OSError, TypeError, ValueError:
                cache_key = None
            # The fit reads every frame of the camera in the library, so it is
            # the same for every target and is kept until its camera changes.
            version = None if cache_key is None else (lambda: self.camera_data_version(setting.camera))
            self._models[key] = _SKY_MODEL_CACHE.get_or_build(
                cache_key, version, lambda: self._fit_sky_model(setting, key)
            )
        return self._models[key]

    def _fit_sky_model(self, setting: CaptureSetting, key: str) -> tuple[SkyModel | None, str | None]:
        """Fit the sky model of an equipment key from the camera's frames.

        Parameters
        ----------
        setting : `CaptureSetting`
            A capture setting of the equipment, which names the camera.
        key : `str`
            The equipment key of `setting`.

        Returns
        -------
        model, reason : `tuple`
            The model, or ``None`` and the reason there is none.
        """
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
        except ValueError as error:
            return None, f"The sky model could not be fitted: {error}"
        return outcome.model, outcome.reason

    def all_sky_models(self) -> tuple[dict[str, SkyModel], list[str]]:
        """Fit a sky model for each piece of equipment in the library.

        Returns
        -------
        models, unavailable : `tuple`
            The fitted models by equipment key, and one sentence for each key
            that could not be fitted, with the reason.
        """
        representatives: dict[str, CaptureSetting] = {}
        for setting in self.astrometrics.quality.capture_settings().values():
            representatives.setdefault(baseline_key(setting), setting)
        models: dict[str, SkyModel] = {}
        unavailable: list[str] = []
        for key, setting in sorted(representatives.items()):
            model, reason = self.sky_model_for(setting)
            if model is not None:
                models[key] = model
            else:
                unavailable.append(f"{key}: {reason}")
        return models, unavailable

    def moonless_sky_rates(self, observations: Sequence[FrameObservation]) -> dict[str, float]:
        """Predict the sky rate each frame would have had without the Moon.

        Parameters
        ----------
        observations : `Sequence` [`FrameObservation`]
            The frames.

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


__all__ = [
    "DEFAULT_SITE_PROFILE_ID",
    "SET_ASIDE_MARKER",
    "LibraryInputs",
    "ReferenceChoice",
    "ReferenceProvider",
    "build_target_observations",
    "dominant_setting",
    "frame_exposure_seconds",
    "frame_times",
    "set_aside_reasons",
    "stored_site",
    "target_direction",
    "telescope_of",
    "times_and_directions",
]
