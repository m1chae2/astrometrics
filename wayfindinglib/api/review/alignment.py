"""Purpose: `ReviewAlignment`, the pointing records (``review.alignment``).

Description: The plate solves that checked the mount's pointing, grouped
into one session per target; the nights that recorded them; the pointing
model of one night; and the polar alignment. Only `record_external` writes:
it stores the syncs and polar alignment runs other programs (such as Ekos)
made through the mount. Each method is a thin wrapper of one owner
function.
"""

from __future__ import annotations

from astrometricslib.arguments import Page, TimeInput
from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.models.session.alignment_records import AlignmentRecords
from wayfindinglib.models.session.telemetry import (
    AlignmentSessionSummary,
    ExternalAlignmentPoll,
    MountPointingModel,
    PolarAlignmentStatus,
)

__all__ = ["ReviewAlignment"]


class ReviewAlignment(ControlChild):
    """The alignment records, reached as ``wayfinder.review.alignment``."""

    def query(
        self,
        *,
        night: str | None = None,
        since: TimeInput | None = None,
        until: TimeInput | None = None,
        limit: int | None = None,
        include: list[str] | None = None,
    ) -> AlignmentRecords:
        """Read the stored alignment attempts, grouped by target.

        Every plate solve that checked the pointing is an attempt: the
        rounds of ``control.mount.slew(center=True)``, the syncs other
        programs sent, and the solves read back from downloaded frames.

        Owner:
            wayfindinglib.tasks.control_tasks.alignment_history.query_alignment_records

        Parameters
        ----------
        night : `str`, optional
            Only this observing night, such as ``"2026-09-24"``. Every night
            when omitted.
        since, until : `str`, `datetime` or `astropy.time.Time`, optional
            Only attempts from this time to that one. Text without a time
            zone is UTC.
        limit : `int`, optional
            Keep only the newest this many attempts. `None` (the default)
            keeps every one.
        include : `list` [`str`], optional
            ``["tracking"]`` adds a tracking row for each library target
            imaged in the time covered, from the guiding samples near its
            frames, so a night's view shows when each target was tracked.

        Returns
        -------
        records : `AlignmentRecords`
            The attempts, oldest first; the same attempts grouped into one
            `AlignmentTargetSession` per target, with jitter and drift
            rates; and the latest polar alignment run of the night (of any
            night when no night is given). A status the attempt model does
            not know reads as ``aligned``.

        Notes
        -----
        Raises `InvalidArgumentError` if `limit` is below 1, a time cannot
        be read, or `include` names an unknown part.
        """
        from wayfindinglib.tasks.control_tasks.alignment_history import query_alignment_records

        return query_alignment_records(
            self._context, night=night, since=since, until=until, limit=limit, include=include
        )

    def query_nights(self, *, limit: int | None = None, offset: int = 0) -> Page[AlignmentSessionSummary]:
        """List the nights with alignment or guiding data, newest first.

        Owner:
            wayfindinglib.tasks.control_tasks.alignment_history.query_alignment_nights

        Parameters
        ----------
        limit : `int`, optional
            The most nights to return. `None` (the default) returns every
            night the log database summarizes (its 50 newest).
        offset : `int`, optional
            How many nights to skip first. Defaults to 0.

        Returns
        -------
        nights : `Page` [`AlignmentSessionSummary`]
            One summary per night: its syncs and targets, its times, the
            mean pointing error, the latest polar alignment errors, the mean
            pointing and the tracking jitter.
        """
        from wayfindinglib.tasks.control_tasks.alignment_history import query_alignment_nights

        return query_alignment_nights(self._context, limit=limit, offset=offset)

    def get_pointing_model(self, night: str) -> MountPointingModel:
        """Fit the mount's pointing model to one night's plate solves.

        A model that mixes several nights' polar alignments is not
        meaningful, so it takes one night.

        Owner:
            wayfindinglib.tasks.control_tasks.night_history.get_pointing_model

        Parameters
        ----------
        night : `str`
            The observing night, such as ``"2026-09-24"``.

        Returns
        -------
        model : `MountPointingModel`
            The fitted model.

        Notes
        -----
        Raises `ConfigurationError` if no observer location is known.
        """
        from wayfindinglib.tasks.control_tasks.night_history import get_pointing_model

        return get_pointing_model(self._context, night)

    def get_polar_alignment(self) -> PolarAlignmentStatus:
        """Read the polar alignment, live if a run is going.

        Owner:
            wayfindinglib.tasks.control_tasks.external_alignment.read_polar_alignment

        Returns
        -------
        status : `PolarAlignmentStatus`
            The run in progress or just finished on the INDI session;
            otherwise the most recent stored run; otherwise an idle status
            with no numbers.
        """
        from wayfindinglib.tasks.control_tasks.external_alignment import read_polar_alignment

        return read_polar_alignment(self._context)

    def record_external(self) -> ExternalAlignmentPoll:
        """Store the alignment that other programs did through the mount.

        KStars, Ekos and similar programs plate solve and sync the mount,
        and run the Polar Alignment Assistant, on their own. The INDI
        session queues what it sees. This stores the queued plate-solve
        syncs as alignment attempts and the polar alignment measurement as a
        polar alignment record, so `query` can show them. Call it from time
        to time while another program is aligning. It moves nothing.

        Owner:
            wayfindinglib.tasks.control_tasks.external_alignment.poll_external_alignment

        Returns
        -------
        poll : `ExternalAlignmentPoll`
            How many syncs and whether a polar alignment run were stored.
        """
        from wayfindinglib.tasks.control_tasks.external_alignment import poll_external_alignment

        return poll_external_alignment(self._context)
