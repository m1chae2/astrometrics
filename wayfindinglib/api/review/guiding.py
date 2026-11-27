"""Purpose: `ReviewGuiding`, the recorded guiding (``review.guiding``).

Description: One night's guiding analysis, the recorded guiding runs, the
guiding spectrum (what the guider left uncorrected) and the mount's own
unguided error, worked out from the stored guide logs. Only
`record_spectrum` writes: it keeps the spectrum as the mount's periodic
error model. Each method is a thin wrapper of one owner function.
"""

from __future__ import annotations

from astrometricslib.arguments import Page
from astrometricslib.jobs import background_job
from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.models.session.session_quality import GuidingSessionAnalysis
from wayfindinglib.models.session.telemetry import GuidingSpectrumAnalysis
from wayfindinglib.models.session.unguided_mount_error import UnguidedMountError

__all__ = ["ReviewGuiding"]


class ReviewGuiding(ControlChild):
    """The recorded guiding, reached as ``wayfinder.review.guiding``."""

    @background_job("diagnostics", grace_period_seconds=20.0)
    def get_night(self, night: str) -> GuidingSessionAnalysis:
        """Analyse one night's guiding.

        The guide signal, lost frames, the guiding error on each axis and
        the recommendations these lead to. The night is judged only against
        the equipment's earlier nights.

        Owner:
            wayfindinglib.tasks.control_tasks.night_history.get_guiding_analysis

        Parameters
        ----------
        night : `str`
            The observing night, such as ``"2026-09-24"``.

        Returns
        -------
        analysis : `GuidingSessionAnalysis`
            The night's guiding analysis.

        Notes
        -----
        Raises `NotFoundError` if nothing is recorded for the night.
        """
        from wayfindinglib.tasks.control_tasks.night_history import get_guiding_analysis

        return get_guiding_analysis(self._context, night)

    def query_runs(
        self, *, night: str | None = None, limit: int | None = None, offset: int = 0
    ) -> Page[GuidingRunSummary]:
        """List the recorded guiding runs, with each log header's settings.

        Owner:
            wayfindinglib.tasks.control_tasks.night_history.query_guiding_runs

        Parameters
        ----------
        night : `str`, optional
            Only the runs of this observing night.
        limit : `int`, optional
            The most runs to return. `None` (the default) returns every run.
        offset : `int`, optional
            How many runs to skip first. Defaults to 0.

        Returns
        -------
        runs : `Page` [`GuidingRunSummary`]
            The runs, oldest first.
        """
        from wayfindinglib.tasks.control_tasks.night_history import query_guiding_runs

        return query_guiding_runs(self._context, night=night, limit=limit, offset=offset)

    def measure_spectrum(
        self, night: str | None = None, *, exclude_nights: list[str] | None = None
    ) -> GuidingSpectrumAnalysis:
        """Work out the guiding spectrum from the samples, storing nothing.

        It shows what the guider left uncorrected, not the mount's unguided
        error (use `measure_unguided_error` for that). `record_spectrum`
        runs the same analysis and keeps it as the mount's model.

        Owner:
            wayfindinglib.tasks.control_tasks.mount_error_tasks.measure_guiding_spectrum

        Parameters
        ----------
        night : `str`, optional
            Use only this observing night. Every recorded night when omitted.
        exclude_nights : `list` [`str`], optional
            Nights to leave out, for a leave-one-out check.

        Returns
        -------
        analysis : `GuidingSpectrumAnalysis`
            The repeating-error size, the dominant periods and the Dec
            backlash estimate. Its ``message`` says when there were too few
            samples.
        """
        from wayfindinglib.tasks.control_tasks.mount_error_tasks import measure_guiding_spectrum

        return measure_guiding_spectrum(self._context, night, exclude_nights)

    def record_spectrum(self, night: str | None = None) -> GuidingSpectrumAnalysis:
        """Work out the guiding spectrum and keep it as the mount's model.

        The same analysis as `measure_spectrum`. The result is saved for the
        active telescope, where the guider's status and the guiding loop
        read it as the mount's periodic error and backlash. To bring new
        guide logs in first, run ``control.remote.sync_logs``.

        Owner:
            wayfindinglib.tasks.control_tasks.mount_error_tasks.record_guiding_spectrum

        Parameters
        ----------
        night : `str`, optional
            Use only this observing night. Every recorded night when omitted.

        Returns
        -------
        analysis : `GuidingSpectrumAnalysis`
            The saved spectrum.
        """
        from wayfindinglib.tasks.control_tasks.mount_error_tasks import record_guiding_spectrum

        return record_guiding_spectrum(self._context, night)

    def measure_unguided_error(
        self, night: str | None = None, *, exclude_nights: list[str] | None = None
    ) -> UnguidedMountError:
        """Measure the mount's own error from the recorded guide logs.

        A guide log holds the error the guide star showed and every
        correction the guider sent. Adding the corrections back (signed
        pulse length times the guide rate) gives the error the mount would
        have made unguided. This fits, for each guiding run, the mount's
        periodic error in right ascension (period, amplitude and phase, each
        with its uncertainty) and, on both axes, the slow drift and the
        random jitter that remain. It then pools the runs. Nothing is
        stored.

        Amplitudes are zero to peak in arcseconds on the sky, periods are in
        seconds, phases are in radians, drifts are in arcseconds per minute.
        The size of the reconstructed error rests on the guide rate the log
        states. The random terms include the guide star's measurement noise,
        so they are upper bounds on the mount's own jitter.

        Owner:
            wayfindinglib.tasks.control_tasks.mount_error_tasks.measure_unguided_mount_error

        Parameters
        ----------
        night : `str`
            Measure only this observing night. Every recorded night when
            omitted.
        exclude_nights : `list` [`str`], optional
            Nights to leave out, for a leave-one-out check.

        Returns
        -------
        result : `UnguidedMountError`
            ``runs`` (one entry per guiding run with samples and guide
            rates), ``summary`` (the pooled repeating error and the pooled
            drift and random terms) and ``excluded_nights``. A run too short
            or too sparse for a repeating-error fit says so in its ``notes``.
        """
        from wayfindinglib.tasks.control_tasks.mount_error_tasks import measure_unguided_mount_error

        return measure_unguided_mount_error(self._context, night, exclude_nights)
