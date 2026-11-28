"""Purpose: `TargetFrames`, the frame records of the library's targets.

Description: `TargetFrames` is ``Astrometrics.targets.frames``. It chooses a
target's frames, adds new files and rescans the folders, reads a frame's
header and its display rotation, groups frames into nights, deletes frames,
and reports and restores the frames the stacker set aside. Each method is a
thin wrapper of one owner function, which it names: most are in
`astrometricslib.pipelines.shared.target_frames`, and the ones that read the
stacks (the query, the rotation and the stack status) in
`astrometricslib.pipelines.stacking`.
"""

from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING, Literal

from astropy.io import fits

from astrometricslib._internal.renames import MovedNames
from astrometricslib.foundation.arguments import TimeInput
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.catalog_queries import ReindexReport
from astrometricslib.models.image_deletion import ImageDeletionResult
from astrometricslib.models.observatory_configuration import ObservatoryConfiguration
from astrometricslib.models.page import Page
from astrometricslib.models.quality.excluded_frames import SetAsideFrameRecord
from astrometricslib.models.quality.frame_status import FrameStatusReport
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.target_sessions import TargetSession

if TYPE_CHECKING:
    from astrometricslib.api.targets import TargetCatalog

__all__ = ["TargetFrames"]


def _check_target(target: Target) -> Target:
    """Refuse a subject that is not a `Target`.

    Parameters
    ----------
    target : `Target`
        The subject a method was given.

    Returns
    -------
    target : `Target`
        The same target.

    Raises
    ------
    InvalidArgumentError
        If ``target`` is not a `Target` (an id goes through
        ``targets.get`` first).
    """
    if not isinstance(target, Target):
        raise InvalidArgumentError("Pass the Target itself; turn an id into one with targets.get(id).")
    return target


class TargetFrames(MovedNames):
    """Choose, add, scan, delete and restore a target's frames.

    Parameters
    ----------
    catalog : `TargetCatalog`
        The target catalog the frames belong to.
    """

    def __init__(self, catalog: TargetCatalog) -> None:
        self._catalog = catalog

    def query(
        self,
        target: Target,
        *,
        night: str | date | None = None,
        since: TimeInput | None = None,
        until: TimeInput | None = None,
        filter_name: str | None = None,
        first_file: str | None = None,
        last_file: str | None = None,
        configuration: ObservatoryConfiguration | None = None,
        gain: float | None = None,
        exposure_s: float | None = None,
        status: Literal["included", "flagged", "not_stacked", "set_aside"] | None = None,
        order_by: Literal["time", "newest", "name"] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Page[FrameRecord]:
        """Choose a target's light frames.

        Only light frames that have a start time are kept. Spectroscopy
        frames are left out unless ``filter_name`` asks for one. The page is
        what a pipeline's ``frames=`` argument takes.

        Owner: astrometricslib.pipelines.stacking.frame_queries.query_frames

        Parameters
        ----------
        target : `Target`
            The target.
        night : `str` or `date`, optional
            Only frames of this observing night, ``YYYY-MM-DD`` (the local
            date after a 12-hour shift).
        since, until : `str`, `datetime` or `Time`, optional
            Only frames that began from ``since`` to ``until``; ISO 8601 text
            with no offset means UTC.
        filter_name : `str`, optional
            Only frames whose filter matches this text, ignoring case.
        first_file, last_file : `str`, optional
            Only frames whose file name sorts from ``first_file`` to
            ``last_file``. A short bound such as ``"013"`` is matched against
            the frame number at the end of the name.
        configuration : `ObservatoryConfiguration`, optional
            Only frames taken with this equipment (from
            ``astrometrics.observatories.configurations``).
        gain : `float`, optional
            Only frames with this gain (or ISO) setting.
        exposure_s : `float`, optional
            Only frames of this exposure length, in seconds.
        status : `str`, optional
            Only frames in this state of the newest stack: ``"included"``,
            ``"flagged"`` (stacked, with stretched stars), ``"not_stacked"``
            or ``"set_aside"`` (moved into an `_excluded` folder).
        order_by : `str`, optional
            ``"time"`` (oldest first, the default), ``"newest"`` or
            ``"name"``.
        limit : `int` or `None`, optional
            The most frames to return. `None` (the default) returns every
            match.
        offset : `int`, optional
            How many matches to skip first. Defaults to 0.

        Returns
        -------
        frames : `Page` [`FrameRecord`]
            The matching frames.

        Notes
        -----
        Raises `InvalidArgumentError` if a time cannot be read, a choice is
        unknown, or ``limit`` or ``offset`` is out of range.
        """
        from astrometricslib.pipelines.stacking.frame_queries import query_frames

        return query_frames(
            _check_target(target),
            self._catalog._config,
            night=night,
            since=since,
            until=until,
            filter_name=filter_name,
            first_file=first_file,
            last_file=last_file,
            configuration=configuration,
            gain=gain,
            exposure_s=exposure_s,
            status=status,
            order_by=order_by,
            limit=limit,
            offset=offset,
        )

    def get_newest(self, target: Target) -> FrameRecord | None:
        """Give the target's light frame taken last.

        Owner: astrometricslib.pipelines.shared.target_frames.newest_frame

        Parameters
        ----------
        target : `Target`
            The target.

        Returns
        -------
        frame : `FrameRecord` or `None`
            The light frame with the latest start time, or `None` when the
            target has none.
        """
        from astrometricslib.pipelines.shared.target_frames import newest_frame

        return newest_frame(_check_target(target))

    def add(
        self,
        target: Target,
        paths: Sequence[str],
        *,
        camera: str | None = None,
        configuration: ObservatoryConfiguration | None = None,
    ) -> Page[FrameRecord]:
        """Bring files into the library as frames of a target, and save it.

        A file or folder that is not yet where the library keeps light
        frames (``lights/<target>/<telescope>/<camera>``) is sorted into
        place: a light frame goes under the target, a dark, bias or flat
        frame to the calibration folders, and frames set aside by the
        stacker are skipped. A file already in place stays where it is.
        Each light frame is then indexed from its header and matched to its
        observatory configuration. A finished picture (``.jpg``, ``.png``)
        is not a frame: set it with ``targets.update(target,
        processed_image=path)``.

        Owner: astrometricslib.pipelines.shared.target_frames.add_frame_files

        Parameters
        ----------
        target : `Target`
            The target the light frames belong to.
        paths : `Sequence` [`str`]
            FITS files, or folders to search.
        camera : `str`, optional
            The camera name, instead of the one in each header.
        configuration : `ObservatoryConfiguration`, optional
            The equipment the frames were taken with; its telescope names the
            library folder light frames are moved into. Left out, the
            settings' main telescope.

        Returns
        -------
        frames : `Page` [`FrameRecord`]
            The records of the light frames added or updated.

        Notes
        -----
        Raises `InvalidArgumentError` if ``paths`` is one string or names a
        picture.
        Raises `NotFoundError` if a path does not exist.
        """
        from astrometricslib.pipelines.shared.target_frames import add_frame_files

        return add_frame_files(
            self._catalog, _check_target(target), paths, camera=camera, configuration=configuration
        )

    def scan(self, target: Target | None = None, prune_missing: bool = False) -> ReindexReport:
        """Read the frames folder again and index what changed, then save.

        With a target, its folders are read: new frames are added, the
        header of each listed frame whose file changed is read again, and
        the total exposure is recomputed. Without one, every target folder
        under the frames folder's ``lights`` folder is read, and a target is
        created for each folder that has none; run inside a job (see
        ``astrometricslib.jobs.running_as_job``), the job's progress counts
        the folders.

        Owner: astrometricslib.pipelines.shared.target_frames.scan_frames

        Parameters
        ----------
        target : `Target`, optional
            The target. `None` (the default) means every target folder.
        prune_missing : `bool`, optional
            Also remove the records of files that no longer exist. Defaults
            to `False`.

        Returns
        -------
        report : `ReindexReport`
            Each target's frame count before and after.
        """
        from astrometricslib.pipelines.shared.target_frames import scan_frames

        return scan_frames(
            self._catalog, None if target is None else _check_target(target), prune_missing=prune_missing
        )

    def get_header(self, path: str) -> fits.Header:
        """Read the first header of a FITS file.

        This reads the first block (HDU 0) of the file, where cameras and
        capture software write their notes. It does not read the pixels.

        Owner: astrometricslib.pipelines.shared.target_frames.read_frame_header

        Parameters
        ----------
        path : `str`
            The FITS file.

        Returns
        -------
        header : `astropy.io.fits.Header`
            A copy of the file's first header, used like a dictionary, for
            example ``header.get("FILTER")``.

        Notes
        -----
        Raises `NotFoundError` if the file does not exist.

        A file that is cut off or damaged raises one of
        `astrometricslib.exceptions.FITS_READ_ERRORS`.
        """
        from astrometricslib.pipelines.shared.target_frames import read_frame_header

        return read_frame_header(path)

    def get_rotation(self, target: Target, frame: FrameRecord | str) -> int:
        """Say how far to turn a frame on screen to match the target's stack.

        Frames taken on the two sides of a meridian flip are upside down from
        each other. A viewer turns each raw frame clockwise by this many
        degrees so it looks the same way round as the target's imaging
        stack. The turn is for display only; it is 0 when the stack has no
        plate solution or the file cannot be read.

        Owner:
            astrometricslib.pipelines.stacking.post_processing.display_turn.frame_rotation

        Parameters
        ----------
        target : `Target`
            The target.
        frame : `FrameRecord` or `str`
            The frame, or the path of one of the target's frames or stacks.

        Returns
        -------
        turn_deg : `int`
            0, 90, 180 or 270 degrees, clockwise.

        Notes
        -----
        Raises `InvalidArgumentError` if the file is not one of the target's
        frames or stacks.
        """
        from astrometricslib.pipelines.stacking.post_processing.display_turn import frame_rotation

        return frame_rotation(_check_target(target), frame)

    def get_nights(self, target: Target, night: str | date | None = None) -> Page[TargetSession]:
        """Group a target's frames into sessions, by night, gain and offset.

        Frames go in the same session when they were taken on the same night
        (noon to noon, local time) with the same gain and offset. A change of
        filter or exposure length does not start a new session. Frames with
        no start time are ignored.

        Owner: astrometricslib.pipelines.shared.target_frames.target_nights

        Parameters
        ----------
        target : `Target`
            The target.
        night : `str` or `date`, optional
            Only the sessions of this night, ``YYYY-MM-DD``.

        Returns
        -------
        sessions : `Page` [`TargetSession`]
            The sessions, ordered by night.
        """
        from astrometricslib.pipelines.shared.target_frames import target_nights

        return target_nights(_check_target(target), night)

    def delete(
        self, target: Target | None, frames: Page[FrameRecord] | Sequence[FrameRecord] | Sequence[str]
    ) -> ImageDeletionResult:
        """Delete frame files, their records and their stored measurements.

        Each file is removed from disk (one already gone counts as removed),
        its record is removed from its target, which is saved, and the
        quality measurements stored for it are removed, so its numbers do
        not stay behind. Stacks and runs are never touched.

        Owner: astrometricslib.pipelines.shared.target_frames.delete_frames

        Parameters
        ----------
        target : `Target` or `None`
            The target the frames belong to. `None` finds each frame's
            target from its ``lights/<target>`` folder.
        frames : `Page` or `Sequence` [`FrameRecord` or `str`]
            The frames, or their paths.

        Returns
        -------
        result : `ImageDeletionResult`
            ``deleted`` (the paths removed) and ``failed`` (each path that
            could not be removed, with the reason).


        Raises
        ------
        InvalidArgumentError
            If ``frames`` is one string, or ``target`` is not a `Target`.
        """
        from astrometricslib.pipelines.shared.target_frames import delete_frames

        if isinstance(frames, str):
            raise InvalidArgumentError("frames must be a list of frames or paths, not one string.")
        return delete_frames(self._catalog, None if target is None else _check_target(target), list(frames))

    def restore(
        self, target: Target, frames: Page[FrameRecord] | Sequence[FrameRecord] | Sequence[str]
    ) -> Page[FrameRecord]:
        """Move set-aside frames back into their target.

        Each frame must lie in one of the target's `_excluded` folders, as
        ``query(target, status="set_aside")`` lists them. A frame whose
        original place is taken, or that has no record of where it came
        from, stays where it is. The target is scanned again afterwards, and
        the next stack may set the frames aside again.

        Owner:
            astrometricslib.pipelines.stacking.frame_status.restore_set_aside_frames

        Parameters
        ----------
        target : `Target`
            The target.
        frames : `Page` or `Sequence` [`FrameRecord` or `str`]
            The set-aside frames, or their paths.

        Returns
        -------
        restored : `Page` [`FrameRecord`]
            The target's records of the frames that went back.

        Raises
        ------
        InvalidArgumentError
            If a frame is not in one of the target's `_excluded` folders.
        """
        from astrometricslib.pipelines.stacking.frame_status import restore_set_aside_frames

        if isinstance(frames, str):
            raise InvalidArgumentError("frames must be a list of frames or paths, not one string.")
        return restore_set_aside_frames(self._catalog, _check_target(target), list(frames))

    def get_statuses(self, target: Target) -> FrameStatusReport:
        """Say what state each frame of a target is in, for a status column.

        Each frame is ``included``, ``flagged`` (in the stack, but its stars
        are stretched: at least the warning level and kept by the
        the set-aside check, with a reason giving the numbers), ``not_stacked``
        (taken after the newest stack file was written, or there is no
        stack) or ``set_aside`` (moved into an `_excluded` folder, with the
        stacker's kind and reason). Nothing is written.

        Owner:
            astrometricslib.pipelines.stacking.frame_status.report_frame_statuses

        Parameters
        ----------
        target : `Target`
            The target.

        Returns
        -------
        report : `FrameStatusReport`
            ``frames`` (each with ``path``, ``name``, ``state``, ``kind``,
            ``reason``, ``date``, ``exposure_s``, ``elongation``,
            ``original_path``, ``restorable`` and ``restore_note``) and
            ``stacked_at``.
        """
        from astrometricslib.pipelines.stacking.frame_status import report_frame_statuses

        return report_frame_statuses(_check_target(target), self._catalog._config)

    def get_set_aside(self, target: Target) -> Page[SetAsideFrameRecord]:
        """List the frames the stacker set aside, and why.

        The reason comes from the frame's stored quality rows when they
        record the action taken, else from the folder's
        `excluded_frames.json` file, else it is empty; ``reason_source``
        says which. Nothing is moved, measured or written.

        Owner:
            astrometricslib.pipelines.stacking.frame_status.list_set_aside_frames_with_reasons

        Parameters
        ----------
        target : `Target`
            The target.

        Returns
        -------
        frames : `Page` [`SetAsideFrameRecord`]
            One entry per frame file in an `_excluded` folder.
        """
        from astrometricslib.pipelines.stacking.frame_status import list_set_aside_frames_with_reasons

        return Page.of(list_set_aside_frames_with_reasons(_check_target(target), self._catalog._config))
