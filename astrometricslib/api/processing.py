"""Main interfaces for processing images and checking their quality.

`ProcessingPipelines` stacks a target's frames, makes the preview picture of a
stack again, and runs the analysis stages (astrometry, photometry,
spectroscopy and the asteroid search). A stack chooses its darks, biases
and flats from the calibration library, `astrometrics.calibration`
(`CalibrationCatalog`, in `calibration.py`), which the root object builds
once and shares with it.

Quality reads are not here. They start at `astrometrics.quality`
(`QualityRecords`, in `quality.py`).

The stacking methods of `ProcessingPipelines` are written in
`_processing_stacking.py` and its preview methods in `_processing_preview.py`,
as functions that take the `ProcessingPipelines` as ``self``; the class binds
them as its own methods.

The methods here check their arguments and hand the work to `pipelines/`.
They raise an error from `astrometricslib.foundation.errors` when a request
cannot be met, and return a typed result otherwise.
"""

import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from astrometricslib._internal.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib._internal.renames import MovedNames
from astrometricslib.api import _processing_preview as _preview
from astrometricslib.api import _processing_stacking as _stacking
from astrometricslib.api.calibration import CalibrationCatalog
from astrometricslib.foundation.arguments import check_choice, check_limit, reject_unused_arguments
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.foundation.jobs.runner import background_job
from astrometricslib.models.plate_solution import PlateSolution
from astrometricslib.models.processing_results import (
    ProcessTargetResult,
)
from astrometricslib.models.stack_identification import StackIdentification
from astrometricslib.models.target import Target
from astrometricslib.pipelines.analysis_stage_names import (
    ANALYSIS_STAGES,
    DEFAULT_ANALYSIS_STAGES,
    SELECTABLE_ANALYSIS_STAGES,
)
from astrometricslib.pipelines.shared.analysis_configuration import (
    configuration_stack_path,
)
from astrometricslib.pipelines.shared.api_arguments import resolve_target
from astrometricslib.pipelines.shared.camera_passes import resolve_processing_frames
from astrometricslib.utilities.parallel_batch import BatchRunSummary

if TYPE_CHECKING:
    from astrometricslib.api.targets import TargetCatalog


__all__ = ["ProcessingPipelines"]


class ProcessingPipelines(MovedNames):
    """Stack targets, run the analysis stages and measure noise.

    It also remakes the preview pictures of stacks.

    Every method that takes a target accepts its id or the `Target`. An id
    that names no target, or a stack that does not exist, raises
    `NotFoundError`. An unknown ``kind`` raises `InvalidArgumentError`.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    storage : `AbstractCatalogAccess`
        The database the library reads and writes. The analysis stages
        record stars in it.
    targets : `TargetCatalog`, optional
        The target catalog. It turns target names into targets and saves a
        target after a stack. One is built from ``storage`` when omitted.
    calibration : `CalibrationCatalog`, optional
        The calibration library to share (``astrometrics.calibration``). The
        stack chooses its darks, biases and flats from it. One is built when
        omitted.
    drivers : `Drivers`, optional
        The plate solver, stacking program and SIMBAD driver to use. Any
        left out is the built-in one (Astrometry.net, Siril, astroquery).
        They reach `stack`, `plate_solve`, `identify_stack`, and the
        astrometry, photometry and spectroscopy stages of `process_target`
        for one target.
        `process_target` for several targets runs in worker processes, which
        cannot receive them, so it refuses to start when any were given.
    """

    # The three stage lists are described by these comments, not in the
    # Attributes section above: the API reference documents class
    # attributes from their comments, and listing them in both places
    # describes each twice.

    #: Every stage `process_target` can run, in the order run.
    ANALYSIS_STAGES = ANALYSIS_STAGES
    #: The stages the app and its AI tools offer by name: all but the
    #: experimental asteroid search.
    SELECTABLE_ANALYSIS_STAGES = SELECTABLE_ANALYSIS_STAGES
    #: The stages `process_target` runs for one target when none are named.
    DEFAULT_ANALYSIS_STAGES = DEFAULT_ANALYSIS_STAGES

    def __init__(
        self,
        config: AppConfiguration,
        storage: AbstractCatalogAccess,
        *,
        targets: TargetCatalog | None = None,
        calibration: CalibrationCatalog | None = None,
        drivers: Drivers | None = None,
    ) -> None:
        self._config = config
        self._storage = storage
        self._drivers = drivers or Drivers()
        self._target_catalog = targets
        self._calibration = calibration or CalibrationCatalog(config, storage)

    @property
    def _targets(self) -> TargetCatalog:
        """The target catalog, built from the storage when first needed.

        Returns
        -------
        targets : `TargetCatalog`
            The shared target catalog.
        """
        if self._target_catalog is None:
            from astrometricslib.api.targets import TargetCatalog

            self._target_catalog = TargetCatalog(self._config, self._storage)
        return self._target_catalog

    def _resolve(self, target: str | Target) -> Target:
        """Turn a target name into the `Target` it names.

        Returns
        -------
        target : `Target`
            The target.
        """
        if isinstance(target, Target):
            return target
        return resolve_target(self._targets, target)

    def acquire_job_slot(self) -> AbstractContextManager:
        """Limit how many heavy jobs can run at the same time.

        Analyzing and stacking images use a lot of CPU power and memory.
        This makes sure the computer is not overwhelmed, by limiting how
        many jobs run at once. Analysis and stacking share one slot pool;
        both draw on ``max_concurrent_jobs``.

        Returns
        -------
        slot : `AbstractContextManager`
            Enter to block until a slot is free, then hold it for the
            job's duration.
        """
        from astrometricslib.foundation.storage.process_locks import acquire_resource_slot

        return acquire_resource_slot(self._config, "job", self._config.get_max_concurrent_jobs())

    # -- Stacking (`_processing_stacking.py`) --------------------------------

    stack = _stacking.stack
    _choose_frames = _stacking._choose_frames
    predict_stack = _stacking.predict_stack
    measure_noise = _stacking.measure_noise
    _library_stack_path = _stacking._library_stack_path
    _subset_context = _stacking._subset_context
    find_stack_configuration = _stacking.find_stack_configuration
    stack_summary = _stacking.stack_summary
    predict_stack_weights = staticmethod(_stacking.predict_stack_weights)
    open_in_siril = _stacking.open_in_siril
    stop_stacking_programs = _stacking.stop_stacking_programs

    # -- The preview picture (`_processing_preview.py`) ----------------------

    remake_preview = _preview.remake_preview
    list_preview_bright_objects = _preview.list_preview_bright_objects
    set_preview_bright_object = _preview.set_preview_bright_object
    _listed_bright_object = _preview._listed_bright_object

    # -- Analysis stages ----------------------------------------------------

    @background_job("process_target", grace_period_seconds=8.0)
    def process_target(
        self,
        target: str | Target | list[str | Target] | None,
        stages: list[str] | None = None,
        astrometry: dict[str, Any] | None = None,
        photometry: dict[str, Any] | None = None,
        spectroscopy: dict[str, Any] | None = None,
        asteroids: dict[str, Any] | None = None,
        camera_id: str | None = None,
        focal_length_mm: float | None = None,
        on_item_complete: Callable[[str, dict, int, int], None] | None = None,
        run_prerequisites: bool = True,
        spectral_variation: dict[str, Any] | None = None,
        on_stages_planned: Callable[[tuple[str, ...], tuple[str, ...]], None] | None = None,
    ) -> ProcessTargetResult | BatchRunSummary:
        """Run the analysis stages for one target, or the pipeline for many.

        For one target, the chosen ``stages`` run in this process, always
        in the order astrometry, photometry, spectroscopy, spectral
        variation, asteroids.
        Photometry measures only the frames of one camera and one optic:
        those of the target's imaging stack, or, with no stack, the camera
        the batch would give the target to and the primary optic
        (``[Observatory.Telescope] focal_length_mm``). Pass ``camera_id`` or
        ``focal_length_mm`` to choose differently. When they name one of the
        target's other stacks (``stacking.stacks_by_configuration``, one per
        camera and optic), that stack is analyzed: astrometry solves it
        (unless ``astrometry={"path": ...}`` names another image) and
        photometry measures its frames. The results replace the target's
        saved astrometry and photometry summaries, which hold one run each.
        Spectroscopy is skipped (with a ``{"status": "skipped", ...}``
        entry, not an error) when the target has no spectral data. Each
        stage's own options are a separate dictionary, so an option meant
        for one stage never reaches another. Nothing is saved; save the
        target afterwards.

        Photometry and spectroscopy need astrometry. With
        ``run_prerequisites`` (the default), astrometry is added to the run
        when its saved result is missing or older than the imaging stack.
        It runs on the image named in ``astrometry={"path": ...}`` or, when
        none is named, on the imaging stack. If it cannot run (no stack and
        no image) or it fails, photometry and spectroscopy are skipped, not
        run on a guess, and the reason, ``needs astrometry: ...``, is in
        ``stages_skipped``. ``prerequisites_run`` lists the stages added.

        The ``"spectral_variation"`` stage follows each star's spectrum
        across the target's raw spectral frames, one observing session at a
        time. It needs spectroscopy first (added when its result is missing
        or older than the spectral stack), keeps its summary in
        ``quality.spectroscopy_variation`` and leaves the stack's spectra as
        they are. A session with fewer than two frames is skipped, with the
        reason in the result.

        For a list of targets, or `None` for every target, each target runs
        the full pipeline in its own worker process: stack the frames of
        ``camera_id``, plate solve, measure star brightness, and extract
        spectra when there are spectroscopy frames. Each target is saved
        after each stage.

        Arguments used by each form (any other argument is refused):

        - one target: ``stages``, ``astrometry``, ``photometry``,
          ``spectroscopy``, ``spectral_variation``, ``asteroids``,
          ``run_prerequisites``, ``on_stages_planned``, and optionally
          ``camera_id`` and ``focal_length_mm``.
        - several targets: ``camera_id`` (needed), ``focal_length_mm``,
          ``on_item_complete``. The full pipeline always runs astrometry
          before photometry, so ``run_prerequisites=False`` is refused.

        Parameters
        ----------
        target : `str`, `Target`, `list` or `None`
            One target (its id or the `Target`), a list of them, or `None`
            for every target in the library.
        stages : `list` [`str`], optional
            For one target, which of ``"astrometry"``, ``"photometry"``,
            ``"spectroscopy"`` and ``"asteroids"`` to run. Defaults to the
            first three. The asteroid search needs a stack. It is
            experimental: its false-positive rate is not measured on real
            data, so a person should check its results.
        astrometry : `dict`, optional
            Options for the astrometry stage: ``path`` (the image to plate
            solve; the stack by default).
        photometry : `dict`, optional
            Options for the photometry stage: ``frames``, ``filter_name``,
            ``use_astrometry_seed`` and ``max_workers``.
        spectroscopy : `dict`, optional
            Options for the spectroscopy stage: ``path`` and ``limit`` (a
            cap on how many stars to process, the brightest first, at least
            1; left out or `None`, every usable identified star is
            processed. The user removed the top-10 default on 2026-11-01).
        asteroids : `dict`, optional
            Options for the asteroid search, which is experimental: its
            false-positive rate is not measured on real data, so a person
            should check its results. The option is ``moving_object_config`` (a
            dictionary of settings, such as
            ``{"detection_threshold_sigma": 4.0}``; a key left out keeps its
            default value, and a key that names no setting raises
            `InvalidArgumentError`).
        camera_id : `str`, optional
            Only frames from this camera are processed (the full name, or
            part of it). Needed for several targets. For one target, it
            defaults to the camera of the target's imaging stack, and with
            ``focal_length_mm`` it can name another stack of the target.
        focal_length_mm : `float`, optional
            Only frames at this focal length are processed. For one target,
            it defaults to the optic of the target's imaging stack, else the
            config's primary focal length.
        on_item_complete : `Callable`, optional
            For several targets, called as ``(target_id, result,
            completed_count, total_count)`` after each target finishes.
        run_prerequisites : `bool`, optional
            For one target, whether to add astrometry before photometry or
            spectroscopy when its result is missing or out of date.
            Defaults to `True`. Pass `False` only when astrometry has just
            run on this stack, or to run a stage alone on purpose.
        spectral_variation : `dict`, optional
            Options for the spectral variation stage: ``frames`` (the raw
            spectral frames to follow; the selected camera's by default) and
            ``max_workers``.
        on_stages_planned : `Callable`, optional
            For one target, called once before the first stage runs, as
            ``(requested, added)``: the stages asked for and the
            prerequisites that will be added, in running order.

        Returns
        -------
        result : `ProcessTargetResult` or `BatchRunSummary`
            For one target, each stage's result keyed by stage name, the
            prerequisites added (``prerequisites_run``), the stages skipped
            because a prerequisite could not run (``stages_skipped``), and
            the target's quality summaries afterwards (``quality``). For
            several, which targets succeeded, failed or were skipped.

        Raises
        ------
        InvalidArgumentError
            If a stage or stage option is unknown, the spectroscopy ``limit``
            is below 1, or an argument the form does not use is given. Also
            if several targets are asked for while `Astrometrics` was given
            drivers, because the worker processes cannot receive them.
        """
        from astrometricslib.pipelines import tasks

        if target is None or isinstance(target, list):
            reject_unused_arguments(
                "several targets",
                {"several targets": ("camera_id", "focal_length_mm", "on_item_complete")},
                {
                    "stages": stages is not None,
                    "astrometry": astrometry is not None,
                    "photometry": photometry is not None,
                    "spectroscopy": spectroscopy is not None,
                    "asteroids": asteroids is not None,
                    "spectral_variation": spectral_variation is not None,
                    "run_prerequisites": not run_prerequisites,
                    "on_stages_planned": on_stages_planned is not None,
                },
            )
            if not camera_id:
                raise InvalidArgumentError("Processing several targets needs a camera_id.")
            if self._drivers.any_chosen:
                raise InvalidArgumentError(
                    "Processing several targets runs each target in its own worker process, which cannot "
                    "receive the drivers given to Astrometrics(...). Process the targets one at a time, or "
                    "build Astrometrics without drivers."
                )
            from astrometricslib.pipelines.target_batch import process_targets_in_parallel

            if target is None:
                target_ids = [item.id for item in self._targets.query(detail="objects", limit=None).objects]
            else:
                target_ids = [self._resolve(item).id for item in target]
            return process_targets_in_parallel(
                self._config,
                target_ids,
                camera_id=camera_id,
                focal_length_mm=focal_length_mm,
                on_item_complete=on_item_complete,
                measured_at=datetime.now(UTC),
            )

        reject_unused_arguments(
            "one target",
            {"one target": ("camera_id", "focal_length_mm")},
            {
                "on_item_complete": on_item_complete is not None,
            },
        )
        chosen_stages = tuple(stages) if stages is not None else DEFAULT_ANALYSIS_STAGES
        for stage in chosen_stages:
            check_choice("stages", stage, ANALYSIS_STAGES)
        stage_options = {
            "astrometry": astrometry,
            "photometry": photometry,
            "spectroscopy": spectroscopy,
            "spectral_variation": spectral_variation,
            "asteroids": asteroids,
        }
        for stage, options in stage_options.items():
            unknown = sorted(set(options or {}) - tasks.STAGE_OPTIONS[stage])
            if unknown:
                raise InvalidArgumentError(
                    f"Unknown {stage} option(s): {', '.join(unknown)}. "
                    f"Choose from: {', '.join(sorted(tasks.STAGE_OPTIONS[stage]))}."
                )
        check_limit((spectroscopy or {}).get("limit"), 1, None, name="spectroscopy limit")
        from astrometricslib.pipelines.asteroid_detection.runner import moving_object_config_from_options

        moving_object_config_from_options((asteroids or {}).get("moving_object_config"))
        resolved = self._resolve(target)
        # A camera or optic that names one of the target's stacks (one per
        # camera and optic) analyzes that stack: astrometry solves it and
        # photometry measures its frames.
        configuration_stack = configuration_stack_path(resolved, camera_id, focal_length_mm, self._config)
        if configuration_stack and os.path.realpath(configuration_stack) == os.path.realpath(
            resolved.stacking.stacked_image or ""
        ):
            configuration_stack = None
        if configuration_stack and not (astrometry or {}).get("path"):
            stage_options["astrometry"] = {**(astrometry or {}), "path": configuration_stack}
        frame_selection = None
        if any(stage in chosen_stages for stage in ("photometry", "spectroscopy", "spectral_variation")):
            frame_selection = resolve_processing_frames(
                resolved, self._config, camera_id, focal_length_mm, stack_path=configuration_stack
            )
        return tasks.run_target_stages(
            resolved,
            chosen_stages,
            {stage: options or {} for stage, options in stage_options.items()},
            self._storage,
            drivers=self._drivers,
            frame_selection=frame_selection,
            measured_at=datetime.now(UTC),
            run_prerequisites=run_prerequisites,
            on_stages_planned=on_stages_planned,
        )

    # -- Reading and changing a target's stacks and set-aside frames --------

    def plate_solve(self, image_path: str) -> PlateSolution | None:
        """Work out where on the sky an image points, from its stars.

        Plate solving matches the pattern of stars in the image against a
        star catalog. The result is a World Coordinate System (WCS): the
        mapping from each pixel to a sky position.

        Parameters
        ----------
        image_path : `str`
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
            pattern cannot be matched, `PlateSolveFailedError` is raised
            instead.
        """
        from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS, read_header
        from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier
        from astrometricslib.pipelines.shared.header_wcs import describe_plate_solution

        _, wcs = StarIdentifier(config=self._config, drivers=self._drivers).process_image(
            image_path, attempt_plate_solving=True
        )
        if wcs is None:
            return None
        if wcs.pixel_shape:
            width, height = wcs.pixel_shape
        else:
            try:
                header = read_header(image_path, first_hdu_only=True)
            except FITS_READ_ERRORS:
                return None
            width, height = int(header.get("NAXIS1", 0)), int(header.get("NAXIS2", 0))
        return describe_plate_solution(image_path, wcs, int(width), int(height))

    def identify_stack(
        self,
        stack_path: str,
        *,
        progress: Callable[[float, str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> StackIdentification:
        """Name every star the stack's own star finder detected.

        The stack's saved plate solution is used when it is real (header
        ``PLTSOLVD``); otherwise the stack is solved and the solution is
        written into the stack and its viewer copy. The stack's detections
        are matched to the Gaia stars of the cache tiles under it (filled to
        the depth the equipment predicts) and, when online, to SIMBAD, whose
        most familiar designation wins (proper name, Bayer, Flamsteed, HD,
        HIP, TYC, then ``Gaia DR3 <id>``; among equals, alphabetical). The
        other SIMBAD names of each star are its ``aliases``. A star the
        library already has a record of (``Gaia DR3 <id>``) gains the names
        it lacks as aliases; no record is created or otherwise changed. The
        result is saved next to the stack; see `get_stack_identification`.
        Raw frames are refused.

        Parameters
        ----------
        stack_path : `str`
            The imaging stack.
        progress : `Callable`, optional
            Called with the share done (0 to 1) and a sentence.
        should_stop : `Callable`, optional
            Returns True to stop before the next step or Gaia tile; the call
            then raises an `AstrometricsError` that says it was stopped.

        Returns
        -------
        identification : `StackIdentification`
            Fields: ``path``, ``width``, ``height``, ``solution_reused``,
            ``stars`` (each with ``id``, ``name``, ``kind`` -- ``matched``,
            ``unmatched`` or ``catalog_only`` --, ``x`` and ``y`` in 1-based
            pixels (the center of the first pixel is 1, 1), ``ra_deg``,
            ``dec_deg``, ``g_mag``, ``gaia_source_id``, ``name_source`` and
            ``aliases``), the three counts, ``gaia_depth``,
            ``catalog_only_limit``, ``gaia_tiles_missing``, ``simbad_used``,
            ``notes`` and ``alias_records_updated``.
        """
        from astrometricslib.pipelines.astrometry.processing.stack_identification import identify_stack_stars

        return identify_stack_stars(
            stack_path, config=self._config, drivers=self._drivers, progress=progress, should_stop=should_stop
        )

    def get_stack_identification(self, stack_path: str) -> StackIdentification | None:
        """Read the saved result of `identify_stack` for an unchanged stack.

        Parameters
        ----------
        stack_path : `str`
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

        return read_stack_identification(stack_path)
