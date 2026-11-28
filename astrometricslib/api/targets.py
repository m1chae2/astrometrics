"""Main interface for the targets in the catalog.

`TargetCatalog` is `Astrometrics.targets`. It creates, reads, changes and
deletes targets, turns a name into a sky position, and counts frames by
camera and by night. Its `frames` child (`TargetFrames`) works on a target's
frame records. The catalog keeps the targets in memory and works out which
ones changed, so a save writes only those. Each method is a thin wrapper of
one owner function in `astrometricslib.pipelines.shared`, which it names.
"""

import logging
from typing import Any, Literal

from astrometricslib._internal.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib._internal.renames import MovedNames
from astrometricslib.api._target_frames import TargetFrames
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.foundation.jobs.runner import background_job
from astrometricslib.models.catalog_queries import (
    TargetCameraCounts,
    TargetNightCounts,
    TargetRecordVersions,
)
from astrometricslib.models.field_centers import ImagedFieldCenters
from astrometricslib.models.observatory_configuration import ObservatoryConfiguration
from astrometricslib.models.page import Page
from astrometricslib.models.resolution import Resolution
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.shared.frame_scanning import classify_and_sort_fits_files
from astrometricslib.pipelines.shared.target_sessions import derive_target_sessions

logger = logging.getLogger(__name__)

__all__ = [
    "TargetCatalog",
    "TargetFrames",
    "classify_and_sort_fits_files",
    "derive_target_sessions",
    "frame_is_spectral",
]


class TargetCatalog(MovedNames):
    """Create, read, change and delete targets.

    A target is a place on the sky that the observatory images. It anchors
    all the data about that place: the raw frames, the stacks and the
    analysis results. Use `get` or `resolve` to turn a name into a `Target`,
    `query` to list targets, and `frames` to work on a target's frames.
    Methods that act on a target take the `Target` itself.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. They give the frames folder and the
        configured cameras.
    storage : `AbstractCatalogAccess`
        The database the target catalog is read from and saved to.
    drivers : `Drivers`, optional
        The outside services to use. `create` with ``from_catalog`` and
        `resolve` ask the SIMBAD driver for a position. The built-in driver
        is used when omitted.
    """

    def __init__(
        self,
        config: AppConfiguration,
        storage: AbstractCatalogAccess,
        *,
        drivers: Drivers | None = None,
    ) -> None:
        self._config = config
        self._drivers = drivers or Drivers()
        self._catalog_access = storage
        from astrometricslib.pipelines.shared import target_records

        # The write counter is read before the targets, so a write made while
        # they are read is noticed by the next refresh.
        version = target_records._stored_targets_version(self)
        self._targets: list = storage.get("target_catalog", {}) or []
        self._touched_target_ids: set = set()
        self._saved_fingerprints: dict[str, str] = {}
        target_records.remember_stored_state(self, self._targets)
        target_records.record_listed_state(self, version)
        self.frames = TargetFrames(self)
        """A target's frame records (`TargetFrames`)."""

    # -- Create, read, change, delete ---------------------------------------

    def get(self, target_id: str, required: bool = False) -> Target | None:
        """Find one target by its id, allowing small differences in spelling.

        The target is brought up to date with storage first, so a target
        that another program added or changed is seen. Only that target's
        row is read, and nothing is read when nothing was written since the
        last read. Unsaved edits to the held target are kept.

        Owner: astrometricslib.pipelines.shared.target_records.find_target

        Parameters
        ----------
        target_id : `str`
            The target's id. Underscores, spaces and capital letters may
            differ from the stored id.
        required : `bool`, optional
            Raise `NotFoundError` instead of returning `None` when no target
            matches. Defaults to `False`.

        Returns
        -------
        target : `Target` or `None`
            The matching target, or `None` if no target matches and
            ``required`` is `False`.

        Notes
        -----
        Raises `NotFoundError` if ``required`` is `True` and no target matches.
        Raises `InvalidArgumentError` if ``target_id`` is not a non-empty
        string.
        """
        from astrometricslib.pipelines.shared.target_records import find_target

        return find_target(self, target_id, required=required)

    def create(self, name: str, from_catalog: bool = False) -> Target:
        """Create a target, find its frames on disk, and save it.

        The frames folders are scanned for folders named after the target,
        so frames that are already on disk are listed straight away. A target
        that already has the id is returned as it is.

        Owner:
            astrometricslib.pipelines.shared.target_records.create_named_target

        Parameters
        ----------
        name : `str`
            The id of the target to create. With ``from_catalog``, a catalog
            name such as ``"M 57"`` or ``"Vega"``.
        from_catalog : `bool`, optional
            Look the name up in the SIMBAD catalog and store the position it
            gives (ICRS, J2000) on the new target, without a lookup when the
            id is taken. Defaults to `False`, which makes a bare target with
            no position. Planets, the Moon, the Sun, comets and asteroids
            have no fixed position, so they are refused. A name the catalog
            does not know (or cannot reach, which logs a warning) raises
            `NotFoundError`.

        Returns
        -------
        target : `Target`
            The new target, or the existing one if the id is taken.
        """
        from astrometricslib.pipelines.shared.target_records import create_named_target

        return create_named_target(self, name, self._drivers.simbad_or_default() if from_catalog else None)

    def add(self, target: Target) -> Target:
        """Add a `Target` object to the catalog and save it.

        Owner: astrometricslib.pipelines.shared.target_records.add_target

        Parameters
        ----------
        target : `Target`
            The target to add. When the catalog already holds a target with
            its id, that one is kept and saved.

        Returns
        -------
        target : `Target`
            The target the catalog holds under the id.

        Raises
        ------
        InvalidArgumentError
            If ``target`` is not a `Target`.
        """
        from astrometricslib.pipelines.shared.target_records import add_target

        if not isinstance(target, Target):
            raise InvalidArgumentError("add takes a Target; use create for a name.")
        return add_target(self, target)

    def update(self, target: Target, **changes: Any) -> Target:
        """Change fields of a target, save it, and return it.

        ``processed_image=path`` sets the target's finished picture (a
        ``.jpg``, ``.png`` or ``.tiff``). With no changes, it saves the
        target as it is, for example after a pipeline run changed it. A new
        ``ra`` or ``dec`` typed in by a person is trusted, so it clears
        ``position_unverified``.

        Owner: astrometricslib.pipelines.shared.target_records.update_target

        Parameters
        ----------
        target : `Target`
            The target to change.
        **changes : `Any`
            Field name -> new value, such as ``common_name="Ring Nebula"`` or
            ``preview_bright_object=PreviewBrightObject(mode="off")``.

        Returns
        -------
        target : `Target`
            The changed target.

        Notes
        -----
        Raises `InvalidArgumentError` if a name is not a field of `Target`, or
        a value does not fit.
        Raises `NotFoundError` if the catalog does not hold the target.
        """
        from astrometricslib.pipelines.shared.target_records import update_target

        return update_target(self, target, changes)

    def delete(self, target: Target) -> None:
        """Remove a target from the catalog.

        The frame files on disk are left alone.

        Owner: astrometricslib.pipelines.shared.target_records.delete_target

        Parameters
        ----------
        target : `Target`
            The target to remove.

        Notes
        -----
        Raises `NotFoundError` if the catalog does not hold the target.
        """
        from astrometricslib.pipelines.shared.target_records import delete_target

        delete_target(self, target)

    def resolve(self, name: str) -> Resolution | None:
        """Find where a named object is: a target, a star, or SIMBAD.

        Looks among the library's targets (id or common name) first, then
        its stars, and asks the SIMBAD catalog only when the library does
        not know the name, so the call can use the network.

        Owner: astrometricslib.pipelines.shared.name_resolution.resolve_name

        Parameters
        ----------
        name : `str`
            A target id or common name, a star name, or a catalog name.

        Returns
        -------
        resolution : `Resolution` or `None`
            The position (ICRS degrees) and who knew the name, with the
            library's `Target` or star when it knew it. `None` when no one
            knows the name, or SIMBAD cannot be reached (a warning is
            logged).
        """
        from astrometricslib.pipelines.shared.name_resolution import resolve_name

        return resolve_name(self, self._catalog_access, name, self._drivers.simbad_or_default())

    # -- Lists and counts -------------------------------------------------

    def query(
        self,
        *,
        text: str | None = None,
        camera: str | None = None,
        configuration: ObservatoryConfiguration | None = None,
        ra_deg: float | None = None,
        dec_deg: float | None = None,
        radius_deg: float | None = None,
        object_type: Literal["solar_system", "messier", "ngc", "ic", "comet", "calibration", "star"]
        | None = None,
        order_by: Literal["newest", "name", "separation"] | None = None,
        include_empty: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> Page[Target]:
        """List the targets that match every filter given.

        Reads what other programs saved first. Nothing is changed. A target
        holds its frames, so a page of targets can be large; ask for the
        frames of one target with ``frames.query``.

        Owner: astrometricslib.pipelines.shared.target_overview.query_targets

        Parameters
        ----------
        text : `str`, optional
            Only targets whose id or common name contains this text,
            ignoring case.
        camera : `str`, optional
            Only targets with light frames from this camera (part of the
            name, ignoring case).
        configuration : `ObservatoryConfiguration`, optional
            Only targets with light frames taken with this equipment.
        ra_deg, dec_deg, radius_deg : `float`, optional
            Only targets inside this circle on the sky (ICRS, degrees). The
            three go together.
        object_type : `str`, optional
            Only targets of this kind, read from their names (see
            `Target.object_type`).
        order_by : `str`, optional
            ``"newest"`` (by the last light frame taken), ``"name"`` or
            ``"separation"`` (nearest the circle's center first). Left out,
            the stored order.
        include_empty : `bool`, optional
            Also list targets with no frames, such as the placeholders the
            calibration folders create. Off by default.
        limit : `int` or `None`, optional
            The most targets to return. `None` (the default) returns every
            match.
        offset : `int`, optional
            How many matches to skip first. Defaults to 0.

        Returns
        -------
        targets : `Page` [`Target`]
            The matching targets.

        Notes
        -----
        Raises `InvalidArgumentError` if a circle is incomplete,
        ``order_by="separation"`` has no circle, a choice is unknown, or
        ``limit`` or ``offset`` is out of range.
        """
        from astrometricslib.pipelines.shared.target_overview import query_targets

        return query_targets(
            self._look_at_every_target(),
            self._config,
            text=text,
            camera=camera,
            configuration=configuration,
            ra_deg=ra_deg,
            dec_deg=dec_deg,
            radius_deg=radius_deg,
            object_type=object_type,
            order_by=order_by,
            include_empty=include_empty,
            limit=limit,
            offset=offset,
        )

    def get_data_version(self, target: Target | None = None) -> str:
        """Give a short text that changes when one target's own data change.

        Owner:
            astrometricslib.pipelines.shared.data_version_texts.get_target_data_version

        The target's own data are its stored record (frames and stacks), its
        stored quality measurements and its calibration records. A result
        about the target can keep its answer while this text stays the same.
        Data of other targets do not change it. With no target, the text
        changes whenever any target record is written. The text is opaque:
        compare it with an earlier one.

        Parameters
        ----------
        target : `Target`, optional
            The target. Every target record when omitted.

        Returns
        -------
        version : `str`
            The text.
        """
        from astrometricslib.pipelines.shared.data_version_texts import (
            get_kind_data_version,
            get_target_data_version,
        )

        if target is None:
            return get_kind_data_version(self._config, "targets")
        return get_target_data_version(self._config, target)

    def get_record_versions(self) -> TargetRecordVersions:
        """Give a short text for each target that changes when its record does.

        Owner:
            astrometricslib.pipelines.shared.data_version_texts.get_target_record_versions

        It reads every stored target record once and decodes none, so a
        result built from every target's frames can read again only the
        targets whose record changed. A stored quality measurement does not
        change a record's text. Each text is opaque: compare it with an
        earlier one.

        Returns
        -------
        versions : `TargetRecordVersions`
            ``versions``: each stored target's id and its text.
        """
        from astrometricslib.pipelines.shared.data_version_texts import get_target_record_versions

        return get_target_record_versions(self._config)

    def count_by_camera(self) -> TargetCameraCounts:
        """Count the light frames each camera took, overall and per target.

        Owner:
            astrometricslib.pipelines.shared.target_overview.count_frames_by_camera

        Returns
        -------
        counts : `TargetCameraCounts`
            ``frames_by_camera`` (each camera name as the headers spell it)
            and ``camera_index`` (for each target, the light frames from
            each configured camera and the newest frame time).
        """
        from astrometricslib.pipelines.shared.target_overview import count_frames_by_camera

        return count_frames_by_camera(self._look_at_every_target(), self._config)

    def count_by_night(self) -> TargetNightCounts:
        """Count how many targets have frames from each observing night.

        Owner: astrometricslib.pipelines.shared.target_overview.count_nights

        Returns
        -------
        counts : `TargetNightCounts`
            ``targets_by_night``: night (``YYYY-MM-DD``) to target count, in
            date order.
        """
        from astrometricslib.pipelines.shared.target_overview import count_nights

        return count_nights(self._look_at_every_target())

    @background_job("diagnostics", grace_period_seconds=20.0)
    def measure_field_centers(self, max_frames_per_target: int = 12) -> ImagedFieldCenters:
        """List the distinct sky positions the library has imaged.

        Reads the position from the FITS header of the first few frames of
        every target. On a large library kept on a slow drive this takes
        about half a minute.

        Owner:
            astrometricslib.pipelines.astrometry.utilities.catalog_seeding.measure_field_centers

        Parameters
        ----------
        max_frames_per_target : `int`, optional
            How many frames of each target to read. Defaults to 12.

        Returns
        -------
        centers : `ImagedFieldCenters`
            One entry per distinct position, with its coordinates and the
            targets that share it.
        """
        from astrometricslib.pipelines.astrometry.utilities.catalog_seeding import measure_field_centers

        # A read, not a pipeline run: it records no job of its own.
        return measure_field_centers(
            self._look_at_every_target(), max_frames_per_target=max_frames_per_target
        )

    # -- Private ------------------------------------------------------------

    def _save(self) -> None:
        """Write every target that changed back to storage."""
        from astrometricslib.pipelines.shared import target_records

        target_records.save_targets(self)

    def _look_at_every_target(self) -> list[Target]:
        """Read every target without marking any as changed.

        Returns
        -------
        targets : `list` [`Target`]
            Every target, after reading what other programs saved. Looking
            is not editing, so none is saved later because of this read.
        """
        from astrometricslib.pipelines.shared import target_records

        return target_records.look_at_targets(self)
