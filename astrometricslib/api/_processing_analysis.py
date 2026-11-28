"""Purpose: The analysis pipelines of the processing sub-API.

Description: Four objects, one per analysis pipeline, each reached from
``Astrometrics.processing``:

* `AstrometryPipeline` (``processing.astrometry``): plate solve a target's
  stack and record its stars (`run`), solve one image in memory (`solve`),
  name the detected stars of a stack (`identify`, `get_identification`) and
  read the stars an astrometry run named (`get_named_stars`).
* `PhotometryPipeline` (``processing.photometry``): measure star brightness
  across a target's frames (`run`).
* `SpectroscopyPipeline` (``processing.spectroscopy``): extract the spectra
  of a spectral stack (`run`) and follow them across the raw frames
  (`record_variation`).
* `AsteroidPipeline` (``processing.asteroids``): the experimental search
  for moving objects (`run`).

There is no call that runs every pipeline: a script, and the app's Analyze
Target button, call each pipeline in turn. Astrometry is the core layer:
photometry and spectroscopy only read its plate solution and never run it.
Without a current solution they are skipped, with the reason
``needs astrometry: ...``, so run ``processing.astrometry.run`` first. (Until
issue 182 they ran astrometry themselves when its result was missing or out
of date.) Every run records a job. Each method is a thin wrapper of one
owner function, which it names.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from astrometricslib._internal.renames import MovedNames
from astrometricslib.api._target_frames import _check_target
from astrometricslib.models.plate_solution import PlateSolution
from astrometricslib.models.processing_results import ProcessTargetResult
from astrometricslib.models.stack_identification import StackIdentification
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import Target
from astrometricslib.pipelines.pipeline_runs import StackChoice
from astrometricslib.pipelines.shared.frame_choice import FrameChoice

if TYPE_CHECKING:
    from astrometricslib.api.processing import ProcessingPipelines

__all__ = ["AsteroidPipeline", "AstrometryPipeline", "PhotometryPipeline", "SpectroscopyPipeline"]


class _Pipeline(MovedNames):
    """The shared part of the analysis pipeline objects.

    Parameters
    ----------
    pipelines : `ProcessingPipelines`
        The processing sub-API the pipeline belongs to.
    """

    def __init__(self, pipelines: ProcessingPipelines) -> None:
        self._pipelines = pipelines


class AstrometryPipeline(_Pipeline):
    """Plate solve stacks and images, and name their stars."""

    def run(self, target: Target, stack: StackChoice | None = None) -> ProcessTargetResult:
        """Plate solve a target's stack and record the stars it names.

        The result replaces the target's saved astrometry summary, which
        holds one run. Save the target afterwards with
        ``targets.update(target)``.

        Owner: astrometricslib.pipelines.pipeline_runs.run_astrometry

        Parameters
        ----------
        target : `Target`
            The target.
        stack : `StackConfigurationResult` or `str`, optional
            The stack to solve: one from ``processing.stacking.get_stack``,
            or the path of a stack or image. Left out, the main imaging
            stack.

        Returns
        -------
        result : `ProcessTargetResult`
            The astrometry result under ``results["astrometry"]`` and the
            target's quality summaries afterwards.
        """
        from astrometricslib.pipelines.pipeline_runs import run_astrometry

        pipelines = self._pipelines
        return run_astrometry(
            _check_target(target),
            stack,
            storage=pipelines._storage,
            config=pipelines._config,
            drivers=pipelines._drivers,
            measured_at=datetime.now(UTC),
        )

    def solve(self, path: str) -> PlateSolution | None:
        """Work out where on the sky an image points, from its stars.

        Plate solving matches the pattern of stars in the image against a
        star catalog. The result is a World Coordinate System (WCS): the
        mapping from each pixel to a sky position. Nothing is written.

        Owner:
            astrometricslib.pipelines.astrometry.processing.star_identifier.solve_image

        Parameters
        ----------
        path : `str`
            The FITS file to solve.

        Returns
        -------
        solution : `PlateSolution` or `None`
            Fields: ``path``, ``ra_deg`` and ``dec_deg`` (the image center,
            ICRS degrees), ``pixel_scale_arcsec``, ``rotation_deg`` (where the
            image's +y axis points, degrees from north toward east),
            ``width``, ``height`` and ``wcs_header`` (the sky map's header
            cards; ``astropy.wcs.WCS(solution.wcs_header)`` rebuilds the
            mapping). `None` when the image cannot be read or too few stars
            are found to try a solve. When a solve is tried and the star
            pattern cannot be matched, `PlateSolveFailedError` is raised.
        """
        from astrometricslib.pipelines.astrometry.processing.star_identifier import solve_image

        return solve_image(path, config=self._pipelines._config, drivers=self._pipelines._drivers)

    def identify(self, path: str) -> StackIdentification:
        """Name every star the stack's own star finder detected.

        The stack's saved plate solution is used when it is real (header
        ``PLTSOLVD``); otherwise the stack is solved and the solution is
        written into the stack and its viewer copy. The detections are
        matched to the Gaia stars of the cache tiles under the stack (filled
        to the depth the equipment predicts) and, when online, to SIMBAD,
        whose most familiar designation wins (proper name, Bayer, Flamsteed,
        HD, HIP, TYC, then ``Gaia DR3 <id>``). The other SIMBAD names of each
        star are its ``aliases``. A star the library already has a record of
        gains the names it lacks as aliases. The result is saved next to the
        stack; see `get_identification`. Raw frames are refused. Run inside a
        job context (``astrometricslib.jobs.running_as_job``), the call
        reports its progress to the job, and a run given up on stops before
        its next step.

        Owner:
            astrometricslib.pipelines.astrometry.processing.stack_identification.identify_stack

        Parameters
        ----------
        path : `str`
            The imaging stack.

        Returns
        -------
        identification : `StackIdentification`
            Fields: ``path``, ``width``, ``height``, ``solution_reused``,
            ``stars`` (each with ``id``, ``name``, ``kind`` -- ``matched``,
            ``unmatched`` or ``catalog_only`` --, ``x`` and ``y`` in 1-based
            pixels, ``ra_deg``, ``dec_deg``, ``g_mag``, ``gaia_source_id``,
            ``name_source`` and ``aliases``), the three counts,
            ``gaia_depth``, ``catalog_only_limit``, ``gaia_tiles_missing``,
            ``simbad_used``, ``notes`` and ``alias_records_updated``.
        """
        from astrometricslib.pipelines.astrometry.processing.stack_identification import identify_stack

        return identify_stack(path, config=self._pipelines._config, drivers=self._pipelines._drivers)

    def get_identification(self, path: str) -> StackIdentification | None:
        """Read the saved result of `identify` for an unchanged stack.

        Owner:
            astrometricslib.pipelines.astrometry.processing.stack_identification.read_stack_identification

        Parameters
        ----------
        path : `str`
            The imaging stack.

        Returns
        -------
        identification : `StackIdentification` or `None`
            The saved result, or `None` when there is none or the stack
            changed since.
        """
        from astrometricslib.pipelines.astrometry.processing.stack_identification import (
            read_stack_identification,
        )

        return read_stack_identification(path)

    def get_named_stars(self, path: str) -> list[StellarObject]:
        """Read the stars an astrometry run named on a stack.

        The astrometry run saves the stars it named beside the stack. The
        list is read only while the stack is unchanged since then. Nothing
        is stored.

        Owner:
            astrometricslib.pipelines.shared.solved_star_list.read_solved_stars

        Parameters
        ----------
        path : `str`
            The stack.

        Returns
        -------
        stars : `list` [`StellarObject`]
            The named stars, with their names, sky and pixel positions and
            catalog details. Empty when the stack has no saved list, or the
            stack changed after the list was saved.
        """
        from astrometricslib.pipelines.shared.solved_star_list import read_solved_stars

        return read_solved_stars(path)


class PhotometryPipeline(_Pipeline):
    """Measure star brightness across a target's frames."""

    def run(
        self, target: Target, frames: FrameChoice | None = None, stack: StackChoice | None = None
    ) -> ProcessTargetResult:
        """Measure the brightness of a target's stars, night by night.

        Photometry names its stars with the stack's plate solution. It reads
        astrometry's result and never runs astrometry: run
        ``processing.astrometry.run(target)`` first (or with ``stack``, for
        another stack). When the solution is missing, older than the stack,
        or the stack could not be solved, photometry is skipped, not failed:
        ``results["photometry"]`` is ``{"status": "skipped", ...}`` and
        ``stages_skipped["photometry"]`` holds the reason ``needs
        astrometry: <why>; run processing.astrometry.run(target) first``,
        which the job also lists as a warning. (Until issue 182 this call
        ran astrometry itself first.) Only frames of one camera and one optic
        are measured: those of the analyzed stack. The result replaces the
        target's saved photometry summary. Save the target afterwards with
        ``targets.update(target)``.

        Owner: astrometricslib.pipelines.pipeline_runs.run_photometry

        Parameters
        ----------
        target : `Target`
            The target.
        frames : `FrameChoice`, optional
            The frames to measure, such as
            ``targets.frames.query(target, filter_name="V")``. Frames of more
            than one camera or optic are refused, with each group and its
            count. Left out, the frames of the analyzed stack's camera and
            optic.
        stack : `StackConfigurationResult` or `str`, optional
            The stack to analyze: another configuration's stack from
            ``processing.stacking.get_stack``, or the path of an image
            astrometry solved when the target has no stack. Its plate
            solution must already be there. Left out, the main stack.

        Returns
        -------
        result : `ProcessTargetResult`
            The photometry result under ``results["photometry"]``, or the
            skipped entry and its reason in ``stages_skipped``.
            ``prerequisites_run`` stays empty.
        """
        from astrometricslib.pipelines.pipeline_runs import run_photometry

        pipelines = self._pipelines
        return run_photometry(
            _check_target(target),
            frames,
            stack,
            storage=pipelines._storage,
            config=pipelines._config,
            drivers=pipelines._drivers,
            measured_at=datetime.now(UTC),
        )


class SpectroscopyPipeline(_Pipeline):
    """Extract spectra from spectral stacks and follow them across frames."""

    def run(
        self, target: Target, stack: StackChoice | None = None, *, max_stars: int | None = None
    ) -> ProcessTargetResult:
        """Extract and classify the spectra of a target's spectral stack.

        Spectroscopy takes its field center and star names from a plate
        solution and never runs astrometry. The solution is the target's
        solved imaging stack's of the same camera and optic, else the
        spectral stack's own when ``processing.astrometry.run(target,
        spectral_stack)`` has solved it. With neither, spectroscopy is
        skipped, not failed: ``stages_skipped["spectroscopy"]`` holds the
        reason ``needs astrometry: <why>; run ... first``, which the job
        also lists as a warning. (Until issue 182 this call ran astrometry
        on the imaging stack first.) A target with no spectral data is
        skipped, with a ``{"status": "skipped", ...}`` entry, not an error.
        Save the target afterwards with ``targets.update(target)``.

        Owner: astrometricslib.pipelines.pipeline_runs.run_spectroscopy

        Parameters
        ----------
        target : `Target`
            The target.
        stack : `str`, optional
            The spectral stack or raw spectral frame. Left out, the target's
            spectral stack.
        max_stars : `int`, optional
            The most stars to process, brightest first, at least 1. Left
            out, every usable identified star.

        Returns
        -------
        result : `ProcessTargetResult`
            The spectroscopy result under ``results["spectroscopy"]``, or
            the skipped entry and its reason in ``stages_skipped``.
        """
        from astrometricslib.pipelines.pipeline_runs import run_spectroscopy

        pipelines = self._pipelines
        return run_spectroscopy(
            _check_target(target),
            stack,
            max_stars=max_stars,
            storage=pipelines._storage,
            config=pipelines._config,
            drivers=pipelines._drivers,
            measured_at=datetime.now(UTC),
        )

    def record_variation(self, target: Target, frames: FrameChoice | None = None) -> ProcessTargetResult:
        """Follow each star's spectrum across a target's raw spectral frames.

        One observing night at a time; a night with fewer than two frames is
        skipped, with the reason in the result. Spectroscopy runs first when
        its result is missing or older than the spectral stack; when it has
        no plate solution to read, both are skipped with the reason
        ``needs spectroscopy: needs astrometry: ...``. The summary
        is kept in ``quality.spectroscopy_variation``, and the stack's
        spectra stay as they are.

        Owner:
            astrometricslib.pipelines.pipeline_runs.record_spectral_variation

        Parameters
        ----------
        target : `Target`
            The target.
        frames : `FrameChoice`, optional
            The raw spectral frames to follow, such as
            ``targets.frames.query(target, filter_name="SPEC")``. Left out,
            the spectral frames of the analyzed camera.

        Returns
        -------
        result : `ProcessTargetResult`
            The result under ``results["spectral_variation"]``.
        """
        from astrometricslib.pipelines.pipeline_runs import record_spectral_variation

        pipelines = self._pipelines
        return record_spectral_variation(
            _check_target(target),
            frames,
            storage=pipelines._storage,
            config=pipelines._config,
            drivers=pipelines._drivers,
            measured_at=datetime.now(UTC),
        )


class AsteroidPipeline(_Pipeline):
    """Search a target's frames for moving objects (experimental)."""

    def run(self, target: Target) -> ProcessTargetResult:
        """Search a target's stack and frames for asteroids.

        The search is experimental: its false-positive rate is not measured
        on real data, so a person should check what it finds. It needs a
        stack.

        Owner: astrometricslib.pipelines.pipeline_runs.run_asteroid_search

        Parameters
        ----------
        target : `Target`
            The target.

        Returns
        -------
        result : `ProcessTargetResult`
            The search's result under ``results["asteroids"]``.
        """
        from astrometricslib.pipelines.pipeline_runs import run_asteroid_search

        pipelines = self._pipelines
        return run_asteroid_search(
            _check_target(target),
            storage=pipelines._storage,
            config=pipelines._config,
            measured_at=datetime.now(UTC),
        )
