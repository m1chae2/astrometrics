"""Keep the observatory configurations and match each frame to its own.

An observatory configuration is one set of equipment used together over a
range of dates (see `astrometricslib.models.observatory_configuration`). This
module owns the work behind `astrometrics.observatories.configurations`:
reading, listing, creating and renaming configurations, and two rules.

Seeding. Libraries made before configurations existed have none. The
settings file and the frames already say which equipment was used, so
`seed_configurations` creates the missing ones:

- one for each setup in the settings (a camera paired with an optic), or,
  with no setups, one for each listed camera on the active telescope;
- one for each group of frames that share a camera and a focal length (the
  key that already groups frames into stacks, such as
  ``ZWO ASI533MM Pro@405mm``) and that no configuration covers yet. It
  starts on the group's first observing night.

A configuration is only ever added, never changed, by seeding, so running it
again does nothing new. Its cost is one pass over the frames already in
memory, plus one header read for each new frame group whose camera the
settings do not describe.

Matching. A frame belongs to the configuration with its camera (any
spelling the camera profiles know) and focal length (to the millimeter) that
was in use on the frame's observing night. When several are, the one that
started last wins. The id of the match is stored on the frame record
(`FrameRecord.configuration_id`).
"""

import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from astrometricslib._internal.drivers.camera_profile_store import camera_identity
from astrometricslib._internal.drivers.observatory_configuration_store import ObservatoryConfigurationStore
from astrometricslib.foundation.camera_names import normalize_camera_name
from astrometricslib.foundation.errors import (
    ConfigurationError,
    ConflictError,
    InvalidArgumentError,
    NotFoundError,
)
from astrometricslib.foundation.run_bases import PLATE_SCALE_CONSTANT
from astrometricslib.models.observatory_configuration import (
    Camera,
    FilterWheel,
    Mount,
    ObservatoryConfiguration,
    Telescope,
    configuration_key_for,
)
from astrometricslib.models.page import Page
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.utilities.observing_night import observing_night_id

logger = logging.getLogger(__name__)

EDITABLE_FIELDS = frozenset({"name", "notes", "valid_from", "valid_until"})
"""The fields `update_configuration` may change. Any other change is new
equipment, which is a new configuration."""

CameraIdentity = Callable[[str], str]
"""Turns a camera name into text that is the same for every spelling."""


def _night_of(frame: FrameRecord) -> date | None:
    """Give the observing night a frame was taken on.

    Parameters
    ----------
    frame : `FrameRecord`
        The frame.

    Returns
    -------
    night : `date` or `None`
        The local date on which the night began, or `None` when the frame
        has no start time.
    """
    if frame.timestamp is None:
        return None
    return date.fromisoformat(observing_night_id(frame.timestamp))


def _camera_identities(configuration: ObservatoryConfiguration, identity: CameraIdentity) -> set[str]:
    """Give every identity of a configuration's main camera.

    Parameters
    ----------
    configuration : `ObservatoryConfiguration`
        The configuration.
    identity : `CameraIdentity`
        Reduces a camera name to its identity.

    Returns
    -------
    identities : `set` [`str`]
        The identity of the camera's name and of each alias.
    """
    return {identity(name) for name in (configuration.camera.name, *configuration.camera.aliases) if name}


def _frame_group(frame: FrameRecord, identity: CameraIdentity) -> tuple[str, int] | None:
    """Give the camera identity and whole focal length of a frame.

    Parameters
    ----------
    frame : `FrameRecord`
        The frame.
    identity : `CameraIdentity`
        Reduces a camera name to its identity.

    Returns
    -------
    group : `tuple` [`str`, `int`] or `None`
        The camera identity and focal length in whole millimeters, or `None`
        when the frame records no focal length or no camera.
    """
    focal_length = frame.focal_length_mm
    camera = (frame.camera or "").strip()
    if not focal_length or focal_length <= 0 or not camera or camera == "Unknown":
        return None
    return identity(camera), round(float(focal_length))


def _memoized_identity(config: Any) -> CameraIdentity:
    """Build a camera-identity function that remembers its answers.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings that hold the camera profiles.

    Returns
    -------
    identity : `CameraIdentity`
        `camera_identity` with a cache, for one pass over many frames.
    """
    known: dict[str, str] = {}

    def identity(camera_name: str) -> str:
        """Give the identity of one camera name.

        Parameters
        ----------
        camera_name : `str`
            The name.

        Returns
        -------
        identity : `str`
            Its identity, from the cache when it was asked before.
        """
        if camera_name not in known:
            try:
                known[camera_name] = camera_identity(camera_name, config)
            except ConfigurationError:
                # Settings without camera profiles still compare names,
                # ignoring case, spaces and punctuation.
                known[camera_name] = normalize_camera_name(camera_name)
        return known[camera_name]

    return identity


class ConfigurationIndex:
    """Find the configuration of a frame quickly.

    Parameters
    ----------
    configurations : `Iterable` [`ObservatoryConfiguration`]
        Every configuration to match against.
    identity : `CameraIdentity`
        Reduces a camera name to its identity.
    """

    def __init__(self, configurations: Iterable[ObservatoryConfiguration], identity: CameraIdentity) -> None:
        self._identity = identity
        self._by_group: dict[tuple[str, int], list[ObservatoryConfiguration]] = {}
        for configuration in configurations:
            self.add(configuration)

    def add(self, configuration: ObservatoryConfiguration) -> None:
        """Make one more configuration findable.

        Parameters
        ----------
        configuration : `ObservatoryConfiguration`
            The configuration.
        """
        focal_length = round(configuration.telescope.focal_length_mm)
        for camera in _camera_identities(configuration, self._identity):
            self._by_group.setdefault((camera, focal_length), []).append(configuration)

    def covers(self, group: tuple[str, int]) -> bool:
        """Say whether any configuration has a camera and focal length.

        Parameters
        ----------
        group : `tuple` [`str`, `int`]
            The camera identity and whole focal length.

        Returns
        -------
        covered : `bool`
            `True` when at least one configuration has them.
        """
        return bool(self._by_group.get(group))

    def match(self, frame: FrameRecord) -> ObservatoryConfiguration | None:
        """Find the configuration a frame was taken with.

        Parameters
        ----------
        frame : `FrameRecord`
            The frame.

        Returns
        -------
        configuration : `ObservatoryConfiguration` or `None`
            The configuration with the frame's camera and focal length that
            was in use on its night (the one that started last when several
            were), or `None` when there is none.
        """
        group = _frame_group(frame, self._identity)
        if group is None:
            return None
        candidates = self._by_group.get(group, [])
        night = _night_of(frame)
        if night is not None:
            candidates = [candidate for candidate in candidates if candidate.is_valid_on(night)]
        if not candidates:
            return None
        return max(candidates, key=lambda candidate: (candidate.valid_from or date.min, candidate.id))


def frame_matches(frame: FrameRecord, configuration: ObservatoryConfiguration, config: Any = None) -> bool:
    """Say whether a frame was taken with a configuration.

    A frame that has a stored configuration id is compared by id. A frame
    without one (its library was not seeded yet) is compared by camera,
    focal length and night.

    Parameters
    ----------
    frame : `FrameRecord`
        The frame.
    configuration : `ObservatoryConfiguration`
        The configuration.
    config : `AppConfiguration`, optional
        The settings that hold the camera profiles.

    Returns
    -------
    matches : `bool`
        `True` when the frame belongs to the configuration.
    """
    if frame.configuration_id is not None:
        return frame.configuration_id == configuration.id
    index = ConfigurationIndex([configuration], _memoized_identity(config))
    return index.match(frame) is not None


def get_configuration(
    store: ObservatoryConfigurationStore, configuration_id: str
) -> ObservatoryConfiguration | None:
    """Read one stored configuration.

    Parameters
    ----------
    store : `ObservatoryConfigurationStore`
        The store.
    configuration_id : `str`
        The configuration's id.

    Returns
    -------
    configuration : `ObservatoryConfiguration` or `None`
        The configuration, or `None` when no configuration has that id.
    """
    return store.get(configuration_id)


def _as_date(value: date | datetime | str | None) -> date | None:
    """Turn a time into a date.

    Parameters
    ----------
    value : `date`, `datetime`, `str` or `None`
        A date, a time, or an ISO 8601 text such as ``"2026-10-03"``.

    Returns
    -------
    day : `date` or `None`
        The date, or `None` for `None`.

    Raises
    ------
    InvalidArgumentError
        If a text cannot be read as a date.
    """
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        raise InvalidArgumentError(f"{value!r} is not a date such as 2026-10-03.") from None


def query_configurations(
    store: ObservatoryConfigurationStore,
    *,
    camera: Camera | None = None,
    telescope: Telescope | None = None,
    time: date | datetime | str | None = None,
    text: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    config: Any = None,
) -> Page[ObservatoryConfiguration]:
    """List stored configurations.

    Parameters
    ----------
    store : `ObservatoryConfigurationStore`
        The store.
    camera : `Camera`, optional
        Only configurations whose main camera is this one (any spelling the
        camera profiles know).
    telescope : `Telescope`, optional
        Only configurations with a telescope of this name.
    time : `date`, `datetime` or `str`, optional
        Only configurations in use on this date.
    text : `str`, optional
        Only configurations whose name, key or notes contain this text,
        ignoring case.
    limit : `int` or `None`, optional
        Most configurations to return. `None` (the default) returns every
        match.
    offset : `int`, optional
        How many matches to skip first. Defaults to 0.
    config : `AppConfiguration`, optional
        The settings that hold the camera profiles.

    Returns
    -------
    page : `Page` [`ObservatoryConfiguration`]
        The matches, oldest start date first.

    Raises
    ------
    InvalidArgumentError
        If `limit` is below 1, `offset` is below 0 or `time` is not a date.
    """
    if limit is not None and limit < 1:
        raise InvalidArgumentError("limit must be at least 1, or None for every match.")
    if offset < 0:
        raise InvalidArgumentError("offset must be 0 or more.")
    day = _as_date(time)
    identity = _memoized_identity(config)
    wanted_camera = (
        {identity(name) for name in (camera.name, *camera.aliases)} if camera is not None else None
    )
    needle = text.lower() if text else None
    matches = []
    for configuration in store.all():
        if wanted_camera is not None and not wanted_camera & _camera_identities(configuration, identity):
            continue
        if telescope is not None and configuration.telescope.name != telescope.name:
            continue
        if day is not None and not configuration.is_valid_on(day):
            continue
        if needle is not None:
            haystack = (
                f"{configuration.name}\n{configuration.configuration_key}\n{configuration.notes}".lower()
            )
            if needle not in haystack:
                continue
        matches.append(configuration)
    return Page.of(matches, offset=offset, limit=limit)


def create_configuration(
    store: ObservatoryConfigurationStore, configuration: ObservatoryConfiguration
) -> ObservatoryConfiguration:
    """Store a new configuration.

    Parameters
    ----------
    store : `ObservatoryConfigurationStore`
        The store.
    configuration : `ObservatoryConfiguration`
        The configuration. An empty id gets a new one; an empty name gets
        the camera-and-focal-length key.

    Returns
    -------
    created : `ObservatoryConfiguration`
        The stored configuration, with its id and revision 1.

    Notes
    -----
    Raises `ConflictError` if a configuration with that id is already
    stored.
    """
    created = configuration.model_copy(
        update={
            "id": configuration.id or uuid.uuid4().hex,
            "name": configuration.name or configuration.configuration_key,
            "revision": 1,
        }
    )
    store.insert(created)
    logger.info("Stored observatory configuration %r (%s).", created.name, created.configuration_key)
    return created


def update_configuration(
    store: ObservatoryConfigurationStore, configuration: ObservatoryConfiguration, **changes: Any
) -> ObservatoryConfiguration:
    """Change a stored configuration's name, notes or dates.

    The revision of `configuration` must be the stored one, so a change based
    on an old copy is refused.

    Parameters
    ----------
    store : `ObservatoryConfigurationStore`
        The store.
    configuration : `ObservatoryConfiguration`
        The configuration as read.
    **changes
        New values for any of ``name``, ``notes``, ``valid_from`` and
        ``valid_until``.

    Returns
    -------
    updated : `ObservatoryConfiguration`
        The saved configuration, with its revision one higher.

    Raises
    ------
    InvalidArgumentError
        If a change names another field (new equipment is a new
        configuration: use `create_configuration`), or the new values are not
        valid (an end before the start).
    NotFoundError
        If the configuration is not stored.

    Notes
    -----
    Raises `ConflictError` if it was changed since it was read.
    """
    other_fields = sorted(set(changes) - EDITABLE_FIELDS)
    if other_fields:
        raise InvalidArgumentError(
            f"An observatory configuration's {', '.join(other_fields)} cannot change: a change of equipment "
            "is a new configuration. Create one instead, and set valid_until on this one.",
            details={"fields": other_fields},
        )
    if not configuration.id:
        raise NotFoundError("This observatory configuration is not stored yet; create it first.")
    try:
        updated = ObservatoryConfiguration.model_validate({
            **configuration.model_dump(),
            **changes,
            "revision": configuration.revision + 1,
        })
    except ValueError as problem:
        raise InvalidArgumentError(f"The change is not valid: {problem}") from None
    store.replace(updated, expected_revision=configuration.revision)
    return updated


@dataclass
class SeedResult:
    """What one seeding pass did.

    Attributes
    ----------
    created : `list` [`ObservatoryConfiguration`]
        The configurations it created.
    changed_targets : `list` [`Target`]
        The targets whose frames got a new configuration id; the caller
        saves them.
    """

    created: list[ObservatoryConfiguration] = field(default_factory=list)
    changed_targets: list[Target] = field(default_factory=list)


def _settings_configurations(config: Any) -> list[ObservatoryConfiguration]:
    """Describe the equipment pairings the settings file lists.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings.

    Returns
    -------
    configurations : `list` [`ObservatoryConfiguration`]
        One unstored configuration per setup; with no setups, one for each
        listed camera on the active telescope (the settings then allow any
        camera with it). A pairing whose camera or telescope the settings do
        not fully describe is left out.
    """
    telescopes = {telescope.name: telescope for telescope in config.get_telescopes()}
    cameras = config.get_cameras()
    identity = _memoized_identity(config)

    def camera_named(name: str | None) -> Camera | None:
        """Find a listed camera by any spelling of its name.

        Parameters
        ----------
        name : `str` or `None`
            The camera's name.

        Returns
        -------
        camera : `Camera` or `None`
            The listed camera, or `None` when none has that name.
        """
        if not name:
            return None
        wanted = identity(name)
        return next((camera for camera in cameras if identity(camera.name) == wanted), None)

    pairings = []
    setups = config.get_observatory_setups()
    if setups.optics and setups.setups:
        for setup in setups.setups:
            telescope = telescopes.get(setup.optic_name)
            camera = camera_named(setup.camera_name)
            if telescope is None or camera is None:
                continue
            pairings.append(
                ObservatoryConfiguration(
                    name=setup.name,
                    telescope=telescope,
                    camera=camera,
                    mount=Mount(name=setup.mount_name) if setup.mount_name else None,
                    filter_wheel=(
                        FilterWheel(name=setup.name, filter_names=setup.filter_names)
                        if setup.filter_names
                        else None
                    ),
                )
            )
        return pairings
    active_name = config.get_value("Observatory.Telescope", "active_telescope")
    telescope = telescopes.get(active_name or "") or next(iter(telescopes.values()), None)
    if telescope is not None:
        pairings.extend(ObservatoryConfiguration(telescope=telescope, camera=camera) for camera in cameras)
    return pairings


def _camera_from_frames(frames: list[FrameRecord], config: Any) -> Camera | None:
    """Describe a camera from the settings or from one of its frames.

    Parameters
    ----------
    frames : `list` [`FrameRecord`]
        Frames of one camera and focal length, oldest first.
    config : `AppConfiguration`
        The settings.

    Returns
    -------
    camera : `Camera` or `None`
        The camera from the settings when they list it; otherwise built from
        the first readable frame header (pixel size from ``XPIXSZ``, or from
        the recorded pixel scale and focal length; sensor size from
        ``NAXIS1`` and ``NAXIS2``; a binned frame's values are turned back
        into unbinned ones). `None` when neither says enough.
    """
    name = frames[0].camera.strip()
    identity = _memoized_identity(config)
    listed = next(
        (camera for camera in config.get_cameras() if identity(camera.name) == identity(name)), None
    )
    if listed is not None:
        return listed
    from astrometricslib._internal.drivers.fits_access import read_header

    for frame in frames[:3]:
        try:
            header = read_header(frame.path)
        except OSError, ValueError:
            continue
        pixel_size_um = header.get("XPIXSZ")
        if not pixel_size_um and frame.pixel_scale_arcsec and frame.focal_length_mm:
            pixel_size_um = frame.pixel_scale_arcsec * frame.focal_length_mm / PLATE_SCALE_CONSTANT
        width, height = header.get("NAXIS1"), header.get("NAXIS2")
        binning = frame.binning or 1
        try:
            return Camera(
                id=name,
                name=name,
                pixel_size_um=float(pixel_size_um) / binning,
                sensor_width_px=int(width) * binning,
                sensor_height_px=int(height) * binning,
            )
        except TypeError, ValueError:
            continue
    logger.debug(
        "No configuration for camera %r: neither the settings nor a frame header give its size.", name
    )
    return None


def seed_configurations(
    store: ObservatoryConfigurationStore, config: Any, targets: Iterable[Target]
) -> SeedResult:
    """Create the configurations the settings and frames imply; match frames.

    See the module description for the rule. Running it again changes
    nothing unless new frames or settings appeared.

    Parameters
    ----------
    store : `ObservatoryConfigurationStore`
        The store.
    config : `AppConfiguration`
        The settings.
    targets : `Iterable` [`Target`]
        The targets whose frames to match; each frame's
        `FrameRecord.configuration_id` is set in place.

    Returns
    -------
    result : `SeedResult`
        The configurations created and the targets whose frames changed.
    """
    identity = _memoized_identity(config)
    index = ConfigurationIndex(store.all(), identity)
    result = SeedResult()

    def add(configuration: ObservatoryConfiguration) -> None:
        """Store a new configuration and make it findable.

        Parameters
        ----------
        configuration : `ObservatoryConfiguration`
            The configuration to store. One that another program stored
            first under the same id is skipped.
        """
        try:
            created = create_configuration(store, configuration)
        except ConflictError:
            return
        index.add(created)
        result.created.append(created)

    for pairing in _settings_configurations(config):
        group = (identity(pairing.camera.name), round(pairing.telescope.focal_length_mm))
        if not index.covers(group):
            add(pairing)

    target_list = list(targets)
    groups: dict[tuple[str, int], list[FrameRecord]] = {}
    for target in target_list:
        for frame in target.frames:
            group = _frame_group(frame, identity)
            if group is not None and not index.covers(group):
                groups.setdefault(group, []).append(frame)
    for frames in groups.values():
        frames.sort(key=lambda frame: frame.timestamp or 0.0)
        camera = _camera_from_frames(frames, config)
        if camera is None:
            continue
        focal_length_mm = float(frames[0].focal_length_mm or 0.0)
        telescope_name = frames[0].telescope.strip()
        if not telescope_name or telescope_name == "Unknown":
            telescope_name = f"{round(focal_length_mm)} mm optic"
        first_night = next((night for night in map(_night_of, frames) if night is not None), None)
        add(
            ObservatoryConfiguration(
                name=configuration_key_for(camera.name, focal_length_mm),
                telescope=Telescope(id=telescope_name, name=telescope_name, focal_length_mm=focal_length_mm),
                camera=camera,
                valid_from=first_night,
                notes="Created from the frames already in the library.",
            )
        )

    for target in target_list:
        changed = False
        for frame in target.frames:
            match = index.match(frame)
            match_id = match.id if match is not None else None
            if frame.configuration_id != match_id:
                frame.configuration_id = match_id
                changed = True
        if changed:
            result.changed_targets.append(target)
    return result


def scan_configurations(
    store: ObservatoryConfigurationStore, config: Any, catalog: Any
) -> Page[ObservatoryConfiguration]:
    """Seed the configurations of every target's frames, and save the frames.

    Runs `seed_configurations` over every target of the catalog and saves
    the targets whose frames got a new configuration id.

    Parameters
    ----------
    store : `ObservatoryConfigurationStore`
        The store.
    config : `AppConfiguration`
        The settings.
    catalog : `TargetCatalog`
        The target catalog whose targets are matched and saved.

    Returns
    -------
    created : `Page` [`ObservatoryConfiguration`]
        The configurations this scan created; empty when it found nothing
        new.
    """
    from astrometricslib.pipelines.shared import target_records

    result = seed_configurations(store, config, catalog._look_at_every_target())
    if result.changed_targets:
        catalog._touched_target_ids.update(target.id for target in result.changed_targets)
        target_records.save_targets(catalog)
    return Page.of(result.created)
