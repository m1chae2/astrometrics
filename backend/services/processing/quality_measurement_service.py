"""Purpose: Run the frame measurement job for the app and announce its result.

Description: The science library measures a target's light frames and stores
the numbers in the quality database
(`astrometrics.processing.record_frame_measurements`). The library also
guards that work: only one run measures a target at a time, and only one run
reads frames at a time in the whole process, so this service does not have to.
It does three small things.

- When the app asks for a measurement, it starts the library call on a
  background thread (so the request returns at once) and announces the result.
- When the app asks for the noise-against-frame-count series, each night's
  contribution or the odd–even difference noise
  (`astrometrics.processing.measure_noise_growth`,
  `measure_night_contributions` and `measure_odd_even_difference_noise`), it
  starts that library call the same way. All of them stack some of a target's
  frames into the target's ``_subsets`` folder, never over the library
  stack, so they take minutes and run only when asked. None starts by itself
  after a stack. The series includes the odd–even difference noise unless the
  caller turns it off.
- When a frame sync or an ingestion has already measured the frames it added,
  it announces the summaries of those runs.

An announcement is the ``quality:measured`` event sent to every connected app
window, so the screens that show quality numbers can read them again. Every
announcement comes from the summary of the run that stored the rows, never
from a second look at the database.
"""

import logging
import threading
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol

from astrometricslib import AstrometricsError

logger = logging.getLogger(__name__)


class MeasurementAnnouncer(Protocol):
    """The part of the quality measurement service that announces results."""

    def announce_summaries(self, summaries: Iterable[Mapping[str, Any]]) -> None:
        """Announce the summaries of measurement runs that already finished.

        Parameters
        ----------
        summaries : `Iterable` [`Mapping` [`str`, `Any`]]
            The summaries as plain dicts (snake_case keys), as the frame sync
            returns them under ``measurements``.
        """
        ...


class QualityMeasurementPort(MeasurementAnnouncer, Protocol):
    """Everything the ingestion needs from the quality measurement service."""

    def start_frame_measurements(self, target_id: str, limit: int | None = None) -> dict[str, Any]:
        """Start measuring a target's light frames in the background.

        Parameters
        ----------
        target_id : `str`
            The target whose frames to measure.
        limit : `int`, optional
            Measure at most this many frames, newest first.

        Returns
        -------
        result : `dict` [`str`, `Any`]
            ``started`` and ``target_id``.
        """
        ...


class QualityMeasurementService:
    """Start the quality jobs and announce what they stored.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library, which does the measuring and the guarding.
    socket_manager : `SocketManager`, optional
        Sends events to the app windows. Nothing is announced without it.
    suggestion_trigger : `Callable` [[`list` [`str`]], `None`], optional
        Called with the target id after rows are announced, so the package
        suggestions can be refreshed. It must not raise; a failure is logged
        and ignored here too.
    """

    def __init__(
        self,
        astrometrics: Any,
        socket_manager: Any = None,
        suggestion_trigger: Callable[[list[str]], None] | None = None,
    ) -> None:
        self._astrometrics = astrometrics
        self._socket_manager = socket_manager
        self._suggestion_trigger = suggestion_trigger

    def start_frame_measurements(self, target_id: str, limit: int | None = None) -> dict[str, Any]:
        """Start measuring a target's light frames in the background.

        Parameters
        ----------
        target_id : `str`
            The target whose frames to measure.
        limit : `int`, optional
            Measure at most this many frames, newest first.

        Returns
        -------
        result : `dict` [`str`, `Any`]
            ``started`` (always `True`: the thread was started) and
            ``target_id``. The library decides whether the run goes ahead. If
            this target is already being measured the run returns at once and
            measures nothing. The result of a run that measured arrives as the
            ``quality:measured`` event.
        """

        def run() -> None:
            """Measure the frames; the top of a background thread."""
            try:
                summary = self._astrometrics.processing.record_frame_measurements(target_id, limit=limit)
            except AstrometricsError as error:
                logger.warning(
                    "Measuring the frames of %s failed (%s): %s", target_id, error.code, error.message
                )
                return
            except Exception:  # the top of a background thread: log it, never lose it
                logger.exception("Measuring the frames of %s failed", target_id)
                return
            if summary.already_running:
                logger.info("Measuring the frames of %s was skipped: %s", target_id, "; ".join(summary.notes))
                return
            self._announce(
                summary.target_id,
                summary.frames_measured,
                summary.rows_written,
                list(summary.observing_night_ids),
            )

        threading.Thread(target=run, name=f"quality-measure-{target_id}", daemon=True).start()
        return {"started": True, "target_id": target_id}

    def start_noise_growth(
        self,
        target_id: str,
        maximum_steps: int = 4,
        include_night_contributions: bool = False,
        include_odd_even_difference: bool = True,
    ) -> dict[str, Any]:
        """Start the noise-against-frame-count series in the background.

        Nothing starts this by itself after a stack; a caller asks for it.

        Parameters
        ----------
        target_id : `str`
            The target whose library stack to take frames from.
        maximum_steps : `int`, optional
            The most subset stacks to make. Defaults to 4.
        include_night_contributions : `bool`, optional
            Also stack the library stack's frames without each night. Costs
            one more stack per night.
        include_odd_even_difference : `bool`, optional
            Also measure the noise of the difference of two halves of the
            library stack's frames. Costs about one library stack's time.
            Defaults to `True`.

        Returns
        -------
        result : `dict` [`str`, `Any`]
            ``started`` (always `True`: the thread was started) and
            ``target_id``. A failure is logged. The rows the run stored are
            announced as the ``quality:measured`` event.
        """

        def work() -> tuple[int, list[str]]:
            """Run the series and count what it stored.

            Returns
            -------
            counts : `tuple` [`int`, `list` [`str`]]
                The rows written and the observing nights they belong to.
            """
            result = self._astrometrics.processing.measure_noise_growth(
                target_id,
                maximum_steps=maximum_steps,
                include_night_contributions=include_night_contributions,
                include_odd_even_difference=include_odd_even_difference,
            )
            rows = result.rows_written
            nights = {night for subset in result.subsets for night in subset.observing_night_ids}
            if result.night_contributions is not None:
                rows += result.night_contributions.rows_written
                nights.update(entry.night_id for entry in result.night_contributions.nights)
            if result.odd_even_difference is not None:
                rows += result.odd_even_difference.rows_written
            return rows, sorted(nights)

        return self._start_subset_job(target_id, "noise-growth", work)

    def start_odd_even_difference_noise(self, target_id: str) -> dict[str, Any]:
        """Start the odd–even difference noise measurement in the background.

        The library stack's frames are split into two halves, each half is
        stacked, and the noise of their difference is stored beside the
        stack's own noise. It costs about one library stack's time. Nothing
        starts it by itself after a stack; a caller asks for it.

        Parameters
        ----------
        target_id : `str`
            The target whose library stack to take frames from.

        Returns
        -------
        result : `dict` [`str`, `Any`]
            ``started`` (always `True`: the thread was started) and
            ``target_id``. A failure is logged. The rows the run stored are
            announced as the ``quality:measured`` event.
        """

        def work() -> tuple[int, list[str]]:
            """Run the measurement and count what it stored.

            Returns
            -------
            counts : `tuple` [`int`, `list` [`str`]]
                The rows written and the observing nights they belong to (the
                rows belong to the library stack, so no night is listed).
            """
            result = self._astrometrics.processing.measure_odd_even_difference_noise(target_id)
            return result.rows_written, []

        return self._start_subset_job(target_id, "odd–even difference", work)

    def start_night_contributions(self, target_id: str) -> dict[str, Any]:
        """Start each night's contribution measurement in the background.

        Parameters
        ----------
        target_id : `str`
            The target whose library stack to take frames from.

        Returns
        -------
        result : `dict` [`str`, `Any`]
            ``started`` (always `True`: the thread was started) and
            ``target_id``. A failure is logged. The rows the run stored are
            announced as the ``quality:measured`` event.
        """

        def work() -> tuple[int, list[str]]:
            """Run the night contributions and count what they stored.

            Returns
            -------
            counts : `tuple` [`int`, `list` [`str`]]
                The rows written and the observing nights they belong to.
            """
            result = self._astrometrics.processing.measure_night_contributions(target_id)
            return result.rows_written, sorted(entry.night_id for entry in result.nights)

        return self._start_subset_job(target_id, "night-contributions", work)

    def _start_subset_job(
        self, target_id: str, name: str, work: Callable[[], tuple[int, list[str]]]
    ) -> dict[str, Any]:
        """Run one subset-stack job on a background thread and announce it.

        Parameters
        ----------
        target_id : `str`
            The target.
        name : `str`
            A short name for the thread and the log.
        work : `Callable`
            Runs the library call and returns the rows written and the
            observing nights they belong to.

        Returns
        -------
        result : `dict` [`str`, `Any`]
            ``started`` and ``target_id``.
        """

        def run() -> None:
            """Run the job; the top of a background thread."""
            try:
                rows_written, night_ids = work()
            except AstrometricsError as error:
                logger.warning(
                    "The %s run for %s failed (%s): %s", name, target_id, error.code, error.message
                )
                return
            except Exception:  # the top of a background thread: log it, never lose it
                logger.exception("The %s run for %s failed", name, target_id)
                return
            self._announce(target_id, 0, rows_written, night_ids)

        threading.Thread(target=run, name=f"quality-{name}-{target_id}", daemon=True).start()
        return {"started": True, "target_id": target_id}

    def announce_summaries(self, summaries: Iterable[Mapping[str, Any]]) -> None:
        """Announce the summaries of measurement runs that already finished.

        A frame sync or an ingestion measures the frames it adds inside the
        library and returns the summary of each run. Each summary that stored
        rows is announced; the others (nothing measured, or the run was
        skipped because another run had the target) send nothing.

        Parameters
        ----------
        summaries : `Iterable` [`Mapping` [`str`, `Any`]]
            The summaries as plain dicts with the keys ``target_id``,
            ``frames_measured``, ``rows_written`` and ``observing_night_ids``.
        """
        for summary in summaries:
            self._announce(
                str(summary["target_id"]),
                int(summary.get("frames_measured", 0)),
                int(summary.get("rows_written", 0)),
                list(summary.get("observing_night_ids", [])),
            )

    def _announce(
        self, target_id: str, frames_measured: int, rows_written: int, observing_night_ids: list[str]
    ) -> None:
        """Send the ``quality:measured`` event when rows were stored.

        The event is declared in `backend.public_interface.UI_EVENTS`.

        Parameters
        ----------
        target_id : `str`
            The target whose frames were measured.
        frames_measured : `int`
            How many frames the rows belong to.
        rows_written : `int`
            How many rows were stored. Nothing is sent when this is 0.
        observing_night_ids : `list` [`str`]
            The observing nights of the frames measured, sorted.
        """
        if rows_written <= 0:
            return
        if self._socket_manager is not None:
            self._socket_manager.broadcast_ui_event_sync(
                "quality:measured",
                {
                    "targetId": target_id,
                    "framesMeasured": frames_measured,
                    "rowsWritten": rows_written,
                    "observingNightIds": observing_night_ids,
                },
            )
        if self._suggestion_trigger is not None:
            try:
                self._suggestion_trigger([target_id])
            except Exception:  # a refresh trigger must never fail an announcement: log it
                logger.exception("Could not ask for a suggestion refresh after measuring %s", target_id)
