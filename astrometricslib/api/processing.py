"""Main interfaces for processing images and checking their quality.

`ProcessingPipelines` stacks a target's frames, makes the preview picture of a
stack again, and runs the analysis stages (astrometry, photometry,
spectroscopy and the asteroid search). It holds one child that it builds
once and shares: `CalibrationCatalog` keeps track of the dark, bias and
flat frames that remove a camera's noise from the raw frames.

Quality reads are not here. They start at `astrometrics.quality`
(`QualityRecords`, in `quality.py`).

The methods here check their arguments and hand the work to `pipelines/`.
They raise an error from `astrometricslib.foundation.errors` when a request
cannot be met, and return a typed result otherwise.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from astrometricslib.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.foundation.jobs.runner import background_job, registered_job
from astrometricslib.models.calibration_ingest import CalibrationIngestReport, FlatSetAssessment
from astrometricslib.models.catalog_queries import CalibrationQueryResult
from astrometricslib.models.processing_results import PreviewRemakeResult, ProcessTargetResult, StackResult
from astrometricslib.models.quality.excluded_frames import RestoreReport
from astrometricslib.models.quality.frame_measurement_summary import FrameMeasurementSummary
from astrometricslib.models.quality.quality_reports import (
    StackSummary,
)
from astrometricslib.models.sensor_noise_measurement import SensorNoiseMeasurement
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.api_arguments import (
    STACK_KINDS,
    StackKind,
    TargetLookup,
    TimeInput,
    check_choice,
    make_frame_selection,
    reject_unused_arguments,
    resolve_target,
)
from astrometricslib.pipelines.shared.camera_passes import resolve_processing_frames
from astrometricslib.pipelines.stacking.calibration_ingest import (
    assess_flat_group,
    build_ingest_report,
    flatten_frame_index,
)
from astrometricslib.pipelines.stacking.sensor_noise_run import measure_sensor_noise_from_library
from astrometricslib.utilities.parallel_batch import BatchRunSummary

if TYPE_CHECKING:
    from astrometricslib.api.targets import TargetCatalog

__all__ = ["CalibrationCatalog", "ProcessingPipelines"]

_CalibrationKind = Literal["dark", "bias", "flat"]
_CALIBRATION_KINDS: tuple[str, ...] = ("dark", "bias", "flat")

CALIBRATION_DETAILS = ("counts", "target_match", "target_frames")
_CALIBRATION_ARGUMENTS = {
    "counts": ("kind",),
    "target_match": ("target", "camera_id"),
    "target_frames": ("target",),
}


class CalibrationCatalog:
    """The library of calibration frames: darks, biases and flats.

    Cameras add noise to every picture. Calibration frames are pictures
    taken with the lens cap on (darks and biases) or of an evenly lit
    surface (flats). They map that noise so the stacker can remove it. This
    class keeps track of those files so they can be applied to real frames.
    A target id that names no target raises `NotFoundError`.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. They give the calibration index file.
    storage : `AbstractCatalogAccess`
        The database the library reads and writes.
    targets : `TargetLookup`, optional
        The target catalog, used to turn a target name into a `Target`.
    """

    def __init__(
        self, config: AppConfiguration, storage: AbstractCatalogAccess, *, targets: TargetLookup | None = None
    ) -> None:
        self._config = config
        self._storage = storage
        self._targets = targets
        self._library: Any = None

    @property
    def library(self) -> Any:
        """The calibration-data model, built the first time it is used.

        Returns
        -------
        library : `CalibrationLibrary`
            The underlying calibration library model.
        """
        if self._library is None:
            from astrometricslib.drivers.calibration_library import CalibrationLibrary

            self._library = CalibrationLibrary(app_config=self._config)
        return self._library

    def query(
        self,
        kind: _CalibrationKind | None = None,
        detail: Literal["counts", "target_match", "target_frames"] = "counts",
        target: str | Target | None = None,
        camera_id: str | None = None,
        refresh: bool = False,
    ) -> CalibrationQueryResult:
        """Report on the calibration library. Nothing is written.

        Arguments used by each detail (any other argument is refused):

        - ``"counts"``: ``kind``.
        - ``"target_match"``: ``target`` (needed) and ``camera_id``.
        - ``"target_frames"``: ``target`` (needed).

        ``refresh`` works with every detail.

        Parameters
        ----------
        kind : `str`, optional
            ``"dark"``, ``"bias"`` or ``"flat"``. Leave it out for all three.
        detail : `str`, optional
            ``"counts"`` (default): how many frames the library holds, per
            camera, setting and exposure (or filter, for flats).
            ``"target_match"``: a target's light frames grouped by filter,
            gain and exposure, each with how many darks match it.
            ``"target_frames"``: a target's light frames counted by
            telescope, camera, gain, exposure and filter.
        target : `str` or `Target`, optional
            The target, for the target details.
        camera_id : `str`, optional
            For ``"target_match"``, only frames from this camera.
        refresh : `bool`, optional
            Read the calibration index from disk again first, so files
            another program added are counted. This replaces the copy in
            memory; it writes nothing.

        Returns
        -------
        answer : `CalibrationQueryResult`
            The rows for the chosen detail.

        Raises
        ------
        InvalidArgumentError
            If the detail or kind is unknown, an argument the detail does
            not use is given, or a needed target is missing.
        """
        from astrometricslib.pipelines.shared.quality import frame_statistics

        check_choice("detail", detail, CALIBRATION_DETAILS)
        if kind is not None:
            check_choice("kind", kind, _CALIBRATION_KINDS)
        reject_unused_arguments(
            detail,
            _CALIBRATION_ARGUMENTS,
            {"kind": kind is not None, "target": target is not None, "camera_id": camera_id is not None},
        )
        if refresh:
            self.library.load_library()
        if detail == "counts":
            stats = self.library.get_stats()
            wanted = {"dark": "darks", "bias": "biases", "flat": "flats"}
            rows = {key: stats.get(key, []) for key in wanted.values()}
            if kind is not None:
                rows = {wanted[kind]: rows[wanted[kind]]}
            return CalibrationQueryResult(detail=detail, kind=kind, **rows)
        if target is None:
            raise InvalidArgumentError(f"detail={detail!r} needs a target.")
        resolved = resolve_target(self._targets, target)
        if detail == "target_frames":
            lights = frame_statistics.get_frame_stats(resolved)["lights"]
            return CalibrationQueryResult(detail=detail, target_id=resolved.id, lights=lights)
        groups = frame_statistics.get_frame_stats_grouped(resolved, self, camera_id)
        return CalibrationQueryResult(detail=detail, target_id=resolved.id, groups=groups)

    def save(self) -> None:
        """Write the calibration library to its JSON file on disk."""
        self.library.save_library()

    def add(self, image_file: str, kind: _CalibrationKind, **kwargs: Any) -> None:
        """Add a calibration frame of the given kind (dark, bias or flat).

        Parameters
        ----------
        image_file : `str`
            Path to the calibration frame FITS file.
        kind : {"dark", "bias", "flat"}
            The calibration frame kind.
        **kwargs
            Passed to the kind's own adder; ``flat`` accepts ``telescope``.
        """
        check_choice("kind", kind, _CALIBRATION_KINDS)
        method = getattr(self.library, f"add_{kind}_frame")
        method(image_file, **kwargs)

    def get(self, kind: _CalibrationKind, **kwargs: Any) -> list[str]:
        """Find the calibration frame files of the given kind.

        Parameters
        ----------
        kind : {"dark", "bias", "flat"}
            The calibration frame kind.
        **kwargs
            Passed to the kind's own lookup. Common keys are ``camera``,
            ``exposure`` (darks only), ``telescope`` and ``filter_type``
            (flats only), and ``validate_paths``.

        Returns
        -------
        frames : `list` [`str`]
            Matching frame file paths.
        """
        check_choice("kind", kind, _CALIBRATION_KINDS)
        method = getattr(self.library, f"get_{kind}_frames")
        return method(**kwargs)

    def refresh(self, kind: _CalibrationKind, prune_missing: bool = False) -> CalibrationIngestReport:
        """Rescan one kind of calibration frame and report what was found.

        Use this after new calibration frames have been downloaded. The
        report says how many frames were found, which groups they were
        filed under and, for flats, whether each new set is good enough.

        Parameters
        ----------
        kind : {"dark", "bias", "flat"}
            The calibration frame kind.
        prune_missing : `bool`, optional
            If `True`, remove index entries whose files no longer
            exist on disk. Defaults to `False`.

        Returns
        -------
        report : `CalibrationIngestReport`
            The frames added and removed, per group, and the assessment of
            each flat set that gained frames.
        """
        check_choice("kind", kind, _CALIBRATION_KINDS)
        before = flatten_frame_index(getattr(self.library, f"{kind}_frames"))
        method = getattr(self.library, f"refresh_{kind}_frames")
        method(prune_missing=prune_missing)
        after = flatten_frame_index(getattr(self.library, f"{kind}_frames"))
        flat_groups = self.library.list_flat_groups() if kind == "flat" else ()
        return build_ingest_report(kind, before, after, flat_groups)

    @background_job("diagnostics", grace_period_seconds=20.0)
    def assess_flats(
        self,
        telescope: str | None = None,
        camera_id: str | None = None,
        filter_type: str | None = None,
        gain: float | None = None,
        offset: float | None = None,
        register_job: bool = True,
    ) -> list[FlatSetAssessment]:
        """Check whether the flats in the library are good enough to use.

        Each set (one telescope, camera, filter, gain and offset) is
        measured on its own with the check the stacker runs: how many
        frames, how bright, how noisy the master flat will be, and any
        problems. Leave an argument out to include every value of it.

        Parameters
        ----------
        telescope : `str`, optional
            Only this telescope's flats.
        camera_id : `str`, optional
            Only this camera's flats.
        filter_type : `str`, optional
            Only flats for this filter, such as ``"L"`` or ``"SPEC"``.
        gain : `float`, optional
            Only flats at this gain.
        offset : `float`, optional
            With `gain`, only flats at this camera offset.
        register_job : `bool`, optional
            Record the run in the job list. Defaults to `True`.

        Returns
        -------
        assessments : `list` [`FlatSetAssessment`]
            One assessment per matching set. Empty if no flats match.
        """
        with registered_job(enabled=register_job, job_type="diagnostics", target_id="calibration"):
            groups = self.library.list_flat_groups(
                telescope=telescope, camera=camera_id, filter_type=filter_type, gain=gain, offset=offset
            )
            return [assess_flat_group(group) for group in groups]

    @background_job("diagnostics", grace_period_seconds=20.0)
    def measure_sensor_noise(
        self,
        camera_id: str,
        gain: str | float,
        offset: str | float,
        binning: str = "1x1",
        temperature: float | None = None,
        exposure_seconds: float | None = None,
        telescope: str | None = None,
        filter_type: str | None = None,
        save: bool = False,
        register_job: bool = True,
    ) -> SensorNoiseMeasurement:
        """Measure a camera setting's gain and read noise.

        The gain (electrons per ADU, where ADU is one step of the stored
        pixel value) comes from pairs of flat frames, and the read noise
        from pairs of bias frames, all taken at exactly this setting. The
        frames are paired in file-name order. Reading never writes: the
        camera profile changes only when ``save`` is `True` and the
        measurement succeeded. If it cannot be made (too few pairs, or the
        frames do not share one setting, exposure or temperature), the result
        has status ``not_measured`` and a reason, and nothing is written.
        Saving raises `ConfigurationError` when the camera has no profile or
        its profile still holds an unmigrated gain.

        Parameters
        ----------
        camera_id : `str`
            The camera whose frames to use.
        gain : `str` or `float`
            The camera gain setting (or ISO).
        offset : `str` or `float`
            The camera offset.
        binning : `str`, optional
            The binning, such as ``"1x1"``. Defaults to ``"1x1"``.
        temperature : `float`, optional
            Use only frames within 1 degree C of this sensor temperature.
        exposure_seconds : `float`, optional
            Use only flats of this exposure. Needed when the setting's flats
            were taken at several exposures.
        telescope : `str`, optional
            Use only flats taken through this telescope.
        filter_type : `str`, optional
            Use only flats taken through this filter.
        save : `bool`, optional
            Write a measured result into the camera profile, with a backup
            of the config first. Defaults to `False`.
        register_job : `bool`, optional
            Record the run in the job list. Defaults to `True`.

        Returns
        -------
        measurement : `SensorNoiseMeasurement`
            The gain, read noise, spreads, counts and a source sentence for
            the profile, or the reason nothing was measured.
        """
        with registered_job(enabled=register_job, job_type="diagnostics", target_id="calibration"):
            return measure_sensor_noise_from_library(
                self.library,
                self._config,
                camera_id,
                gain,
                offset,
                binning,
                temperature_c=temperature,
                exposure_seconds=exposure_seconds,
                telescope=telescope,
                filter_type=filter_type,
                save=save,
                measured_on=datetime.now(UTC).date(),
            )


class _TargetsOnDemand:
    """Find targets through a `ProcessingPipelines`, building its catalog late.

    Parameters
    ----------
    pipelines : `ProcessingPipelines`
        The pipelines whose target catalog to use.
    """

    def __init__(self, pipelines: ProcessingPipelines) -> None:
        self._pipelines = pipelines

    def get(self, target_id: str, refresh: bool = False) -> Target | None:
        """Find one target by its id.

        Parameters
        ----------
        target_id : `str`
            The target's id, matched loosely.
        refresh : `bool`, optional
            Read the stored targets first.

        Returns
        -------
        target : `Target` or `None`
            The target, or `None` when no target has that id.
        """
        return self._pipelines._targets.get(target_id, refresh=refresh)


class ProcessingPipelines:
    """Stack targets, remake their previews, and run the analysis stages.

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
        The calibration library to share. One is built when omitted.
    drivers : `Drivers`, optional
        The plate solver, stacking program and SIMBAD driver to use. Any
        left out is the built-in one (Astrometry.net, Siril, astroquery).
        They reach `stack`, the astrometry and spectroscopy stages of
        `process_target` for one target, and `run_spectroscopy_by_session`.
        `process_target` for several targets runs in worker processes, which
        cannot receive them, so it refuses to start when any were given.

    Attributes
    ----------
    calibration : `CalibrationCatalog`
        Tracking, querying, and refreshing dark, bias, and flat
        calibration frames.
    """

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
        lookup = targets if targets is not None else _TargetsOnDemand(self)
        self.calibration = calibration or CalibrationCatalog(config, storage, targets=lookup)

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

    # -- Stacking ----------------------------------------------------------

    @background_job("stacking", grace_period_seconds=8.0)
    def stack(
        self,
        target: str | Target,
        frames: list[FrameRecord] | None = None,
        filter_name: str | None = None,
        first_file: str | None = None,
        last_file: str | None = None,
        since: TimeInput = None,
        until: TimeInput = None,
        plan_only: bool = False,
        kind: StackKind = "imaging",
        camera_id: str | None = None,
        force: bool = False,
        denoise: bool | None = None,
        denoise_strength: float | None = None,
        star_toning: bool | None = None,
        rejection_sigma: tuple[float, float] | None = None,
        filter_wfwhm: str | None = None,
        filter_round: str | None = None,
        stack_weight: str | None = None,
        generate_rejmap: bool | None = None,
        output_file: str | None = None,
        log_file: str | None = None,
        register_job: bool = True,
    ) -> StackResult:
        """Stack a target's light frames, choosing which frames go in.

        This runs the same stacking stage as the Stack button in the app:
        it sets aside bad frames, stacks, trims the noisy edges, records the
        quality summary and the preview, and saves the target. Imaging and
        spectroscopy frames are never mixed, so ``kind`` picks one, and
        frames from two cameras are never stacked together. An unchanged
        stack is kept unless ``force`` is set. A target, camera or frame
        choice that matches nothing raises `NotFoundError`; a stacking stage
        that finishes without making a stack raises `ProcessingError`.

        Parameters
        ----------
        target : `str` or `Target`
            The target to stack.
        frames : `list` [`FrameRecord`], optional
            The exact frames to stack. Cannot be combined with the frame
            choices below.
        filter_name : `str`, optional
            Only frames of this filter, such as ``"L"`` or ``"Luminance"``.
        first_file : `str`, optional
            Only frames from this file onward. A bare number such as
            ``"013"`` means frame 013.
        last_file : `str`, optional
            Only frames up to this file or number.
        since : `str`, `datetime` or `Time`, optional
            Only frames taken at or after this time (UTC if no offset).
        until : `str`, `datetime` or `Time`, optional
            Only frames taken at or before this time.
        plan_only : `bool`, optional
            Only report which frames would be stacked. Nothing is stacked
            or saved.
        kind : `str`, optional
            ``"imaging"`` (default) or ``"spectral"`` frames.
        camera_id : `str`, optional
            Only frames from this camera, such as ``"ASI 533MM"``. Chosen
            before any other frame check. A target with frames from more
            than one camera needs this; without it the answer is an error
            that names the cameras and how many frames each took.
        force : `bool`, optional
            Rebuild even if nothing changed since the stack on disk.
        denoise : `bool`, optional
            For this run's preview picture: `False` skips Cosmic Clarity,
            `True` runs it even if the settings have it switched off. Left
            out, the settings apply. Never written to the settings.
        denoise_strength : `float`, optional
            Cosmic Clarity's denoise strength for this run, from 0 to 1.
        star_toning : `bool`, optional
            Turn the preview's star toning on or off for this run.
        rejection_sigma : `tuple` [`float`, `float`], optional
            Low and high sigma-clipping bounds for pixel rejection.
        filter_wfwhm : `str`, optional
            Siril's weighted-FWHM frame filter.
        filter_round : `str`, optional
            Siril's star-roundness frame filter.
        stack_weight : `str`, optional
            How Siril weights each frame.
        generate_rejmap : `bool`, optional
            Also write the rejection maps.
        output_file : `str`, optional
            Where to write the stack instead of the usual place.
        log_file : `str`, optional
            Where to write Siril's log.
        register_job : `bool`, optional
            Record the run in the job list. Defaults to `True`. When the
            caller already runs a job, the stack's provenance is recorded
            against that job.

        Returns
        -------
        result : `StackResult`
            The frames chosen and, unless ``plan_only``, the stack's path,
            flags and quality summary (``quality_summary``). To compare
            it with the previous stack, use
            `Astrometrics.quality.measure_stack`.

        Raises
        ------
        InvalidArgumentError
            If ``kind`` or ``denoise_strength`` is out of range, a time
            cannot be read, ``frames`` is combined with a frame choice, or
            the frames come from more than one camera.
        """
        from astrometricslib.pipelines.stacking.frame_measurement_run import FrameQualityRecording
        from astrometricslib.pipelines.stacking.post_processing.stack_preview import PreviewSettings
        from astrometricslib.pipelines.stacking.stack_request import (
            SirilOptions,
            check_one_camera,
            choose_frames_to_stack,
            run_stack,
        )

        check_choice("kind", kind, STACK_KINDS)
        if denoise_strength is not None and not 0.0 <= denoise_strength <= 1.0:
            raise InvalidArgumentError("denoise_strength must be between 0 and 1.")
        resolved = self._resolve(target)
        if frames is not None:
            choices = (filter_name, first_file, last_file, since, until, camera_id)
            if any(value is not None for value in choices):
                raise InvalidArgumentError(
                    "Pass frames, or choose them with filter_name, first_file, last_file, since, until "
                    "and camera_id, not both."
                )
            if not frames:
                raise InvalidArgumentError("frames is empty: there is nothing to stack.")
            check_one_camera(resolved, frames)
            chosen = list(frames)
        else:
            selection = make_frame_selection(
                filter_name, first_file, last_file, since, until, kind == "spectral"
            )
            chosen = choose_frames_to_stack(resolved, kind, selection, camera_id)
        return run_stack(
            resolved,
            chosen,
            kind=kind,
            plan_only=plan_only,
            force=force,
            preview_settings=PreviewSettings(
                denoise=denoise, denoise_strength=denoise_strength, star_toning=star_toning
            ),
            siril=SirilOptions(
                rejection_sigma=rejection_sigma,
                filter_wfwhm=filter_wfwhm,
                filter_round=filter_round,
                stack_weight=stack_weight,
                generate_rejmap=generate_rejmap,
                output_file=output_file,
                log_file=log_file,
            ),
            register_job=register_job,
            stacking_slot=self.acquire_stacking_slot,
            save_targets=self._targets.save,
            drivers=self._drivers,
            measured_at=datetime.now(UTC),
            quality_recording=FrameQualityRecording(self._config, self.calibration.library),
        )

    @background_job("preview", grace_period_seconds=20.0)
    def remake_preview(
        self,
        target: str | Target,
        kind: StackKind = "imaging",
        denoise: bool | None = None,
        denoise_strength: float | None = None,
        star_toning: bool | None = None,
        starnet: bool = False,
        star_strength: float | None = None,
        star_stretch: float | None = None,
        keep_previous: bool = True,
        register_job: bool = True,
    ) -> PreviewRemakeResult:
        """Make a target's preview picture again from its existing stack.

        Runs only the preview step (GraXpert, Siril stretch, Cosmic Clarity,
        star toning), so a changed post-processing setting shows up without a
        restack. The stack file is not changed. The overrides apply to this
        run only and are never written to the configuration. By default the
        old pictures are copied into a ``_previous_preview`` folder beside
        the stack first, so the two can be compared; a failed run puts them
        back. The target is saved when the viewer shows the new picture. A
        missing target or stack raises `NotFoundError`, and a run that makes
        no preview raises `ProcessingError`.

        Parameters
        ----------
        target : `str` or `Target`
            The target whose stack to make a picture of.
        kind : `str`, optional
            ``"imaging"`` (default) or ``"spectral"`` stack.
        denoise : `bool`, optional
            `False` skips Cosmic Clarity for this run. `True` runs it even if
            the settings have it switched off. Left out, the settings apply.
        denoise_strength : `float`, optional
            Cosmic Clarity's denoise strength for this run, from 0 to 1.
        star_toning : `bool`, optional
            Turn the star toning on or off for this run.
        starnet : `bool`, optional
            Stretch the stars and the rest of the picture on their own,
            using StarNet through Siril. It adds about 35 to 55 seconds to
            the remake and is off by default. The star toning is not used
            with it, and a failed StarNet run gives the normal picture.
        star_strength : `float`, optional
            With ``starnet``, how much of the stars layer is blended, from 0
            to 1. Left out, 0.8.
        star_stretch : `float`, optional
            With ``starnet``, the arcsinh strength of the stars' own stretch,
            from 1 to 100000. Larger values lift fainter stars. Left out,
            1000.
        keep_previous : `bool`, optional
            Copy the current pictures aside first. On by default.
        register_job : `bool`, optional
            Record the run in the job list. Defaults to `True`.

        Returns
        -------
        result : `PreviewRemakeResult`
            Where the picture went, the steps that ran, and where the old
            pictures were kept.

        Raises
        ------
        InvalidArgumentError
            If ``kind``, ``denoise_strength``, ``star_strength`` or
            ``star_stretch`` is out of range.
        """
        from astrometricslib.pipelines.stacking.post_processing.preview_remake import remake_stack_preview
        from astrometricslib.pipelines.stacking.post_processing.stack_preview import PreviewSettings

        check_choice("kind", kind, STACK_KINDS)
        if denoise_strength is not None and not 0.0 <= denoise_strength <= 1.0:
            raise InvalidArgumentError("denoise_strength must be between 0 and 1.")
        if star_strength is not None and not 0.0 <= star_strength <= 1.0:
            raise InvalidArgumentError("star_strength must be between 0 and 1.")
        if star_stretch is not None and not 1.0 <= star_stretch <= 100000.0:
            raise InvalidArgumentError("star_stretch must be between 1 and 100000.")
        resolved = self._resolve(target)
        settings = PreviewSettings(
            denoise=denoise,
            denoise_strength=denoise_strength,
            star_toning=star_toning,
            starnet=starnet,
            star_strength=star_strength,
            star_stretch=star_stretch,
        )
        with (
            self.acquire_stacking_slot(),
            registered_job(enabled=register_job, job_type="preview", target_id=resolved.id),
        ):
            result = remake_stack_preview(resolved, kind == "spectral", settings, keep_previous)
        if result.shown_in_viewer:
            self._targets.save()
        return result

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
        register_job: bool = True,
    ) -> ProcessTargetResult | BatchRunSummary:
        """Run the analysis stages for one target, or the pipeline for many.

        For one target, the chosen ``stages`` run in this process, always
        in the order astrometry, photometry, spectroscopy, asteroids.
        Photometry measures only the frames of one camera and one optic,
        chosen the way the batch run chooses them: the camera the batch
        would give the target to (the first camera in the config's setups
        that has frames of it, the primary camera first) and the primary
        optic (``[Observatory.Telescope] focal_length_mm``). Pass
        ``camera_id`` or ``focal_length_mm`` to choose differently.
        Spectroscopy is skipped (with a ``{"status": "skipped", ...}``
        entry, not an error) when the target has no spectral data, and
        otherwise receives photometry's result. Each stage's own options
        are a separate dictionary, so an option meant for one stage never
        reaches another. Nothing is saved; save the target afterwards.

        For a list of targets, or `None` for every target, each target runs
        the full pipeline in its own worker process: stack the frames of
        ``camera_id``, plate solve, measure star brightness, and extract
        spectra when there are spectroscopy frames. Each target is saved
        after each stage.

        Arguments used by each form (any other argument is refused):

        - one target: ``stages``, ``astrometry``, ``photometry``,
          ``spectroscopy``, ``asteroids``, and optionally ``camera_id`` and
          ``focal_length_mm``.
        - several targets: ``camera_id`` (needed), ``focal_length_mm``,
          ``on_item_complete``.

        Parameters
        ----------
        target : `str`, `Target`, `list` or `None`
            One target (its id or the `Target`), a list of them, or `None`
            for every target in the library.
        stages : `list` [`str`], optional
            For one target, which of ``"astrometry"``, ``"photometry"``,
            ``"spectroscopy"`` and ``"asteroids"`` to run. Defaults to the
            first three. The asteroid search needs a stack.
        astrometry : `dict`, optional
            Options for the astrometry stage: ``path`` (the image to plate
            solve; the stack by default).
        photometry : `dict`, optional
            Options for the photometry stage: ``frames``, ``filter_type``,
            ``use_astrometry_seed`` and ``max_workers``.
        spectroscopy : `dict`, optional
            Options for the spectroscopy stage: ``path`` and ``limit`` (a
            cap on how many stars to process).
        asteroids : `dict`, optional
            Options for the asteroid search: ``moving_object_config`` (a
            `MovingObjectConfig`).
        camera_id : `str`, optional
            Only frames from this camera are processed (the full name, or
            part of it). Needed for several targets. For one target, it
            defaults to the camera the batch would use.
        focal_length_mm : `float`, optional
            Only frames at this focal length are processed. For one target,
            it defaults to the config's primary focal length.
        on_item_complete : `Callable`, optional
            For several targets, called as ``(target_id, result,
            completed_count, total_count)`` after each target finishes.
        register_job : `bool`, optional
            For one target, whether each stage shows up in the job list.
            Defaults to `True`.

        Returns
        -------
        result : `ProcessTargetResult` or `BatchRunSummary`
            For one target, each stage's result keyed by stage name, and
            the target's quality summaries afterwards (``quality``). For
            several, which targets succeeded, failed or were skipped.

        Raises
        ------
        InvalidArgumentError
            If a stage or stage option is unknown, or an argument the form
            does not use is given. Also if several targets are asked for
            while `Astrometrics` was given drivers, because the worker
            processes cannot receive them.
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
                target_ids = [item.id for item in self._targets.list()]
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
        chosen_stages = tuple(stages) if stages is not None else tasks.DEFAULT_ANALYSIS_STAGES
        for stage in chosen_stages:
            check_choice("stages", stage, tasks.ANALYSIS_STAGES)
        stage_options = {
            "astrometry": astrometry,
            "photometry": photometry,
            "spectroscopy": spectroscopy,
            "asteroids": asteroids,
        }
        for stage, options in stage_options.items():
            unknown = sorted(set(options or {}) - tasks.STAGE_OPTIONS[stage])
            if unknown:
                raise InvalidArgumentError(
                    f"Unknown {stage} option(s): {', '.join(unknown)}. "
                    f"Choose from: {', '.join(sorted(tasks.STAGE_OPTIONS[stage]))}."
                )
        resolved = self._resolve(target)
        frame_selection = None
        if "photometry" in chosen_stages or "spectroscopy" in chosen_stages:
            frame_selection = resolve_processing_frames(resolved, self._config, camera_id, focal_length_mm)
        return tasks.run_target_stages(
            resolved,
            chosen_stages,
            {stage: options or {} for stage, options in stage_options.items()},
            self._storage,
            register_job,
            drivers=self._drivers,
            frame_selection=frame_selection,
            measured_at=datetime.now(UTC),
        )

    def run_spectroscopy_by_session(
        self,
        astrometrics: Any,
        target: str | Target,
        frame_records: list[Any],
        max_workers: int | None = None,
        on_item_complete: Any | None = None,
    ) -> Any:
        """Analyze a target's spectroscopy frames, grouped by session.

        Parameters
        ----------
        astrometrics : `astrometricslib.Astrometrics`
            The parent astrometrics, needed to resolve session boundaries.
        target : `str` or `Target`
            The target whose spectroscopy frames should be processed.
        frame_records : `list`
            The frame records to process, grouped internally by session.
        max_workers : `int`, optional
            Maximum concurrent worker count; defaults to the
            configured spectroscopy concurrency.
        on_item_complete : `Callable`, optional
            A function to run after each session finishes.

        Returns
        -------
        summary : `BatchRunSummary`
            Aggregated success/failure/result state across every
            session's frames.
        session_results : `list` [`tuple`]
            One `(session, identify_result)` pair per session.
        """
        from astrometricslib.pipelines.spectroscopy import batch as spectroscopy_batch_operations

        resolved = self._resolve(target)
        with registered_job(
            enabled=True,
            job_type="spectroscopy_session",
            target_id=resolved.id,
            completed_message=f"[{resolved.id}] Session-based spectroscopy completed successfully.",
            failed_message=f"[{resolved.id}] Session-based spectroscopy failed.",
        ) as job:
            return spectroscopy_batch_operations.process_spectroscopy_frames_by_session(
                astrometrics,
                resolved,
                frame_records,
                max_workers=max_workers,
                on_item_complete=on_item_complete,
                job_id=job.job_id,
                drivers=self._drivers,
                measured_at=datetime.now(UTC),
            )

    # -- Reading and changing a target's stacks and set-aside frames --------

    def stack_summary(self, target: str | Target, kind: StackKind = "imaging") -> StackSummary:
        """Summarize a target's current stack in one short answer.

        Reads the numbers the stacking stage saved with the stack, so nothing
        is measured again. The answer covers how many frames went in and how
        many were set aside, the share of pixels rejected, the star width
        against what the input frames predict, the flags, and what happened
        to each exposure group (its frames, whether it clipped, how far it
        was moved to line up, and why it was left out if it was).

        Parameters
        ----------
        target : `str` or `Target`
            The target to summarize.
        kind : `str`, optional
            ``"imaging"`` (default) or ``"spectral"`` stack.

        Returns
        -------
        summary : `StackSummary`
            The summary.
        """
        from astrometricslib.pipelines.stacking.post_processing.stack_quality_report import summarize_stack

        check_choice("kind", kind, STACK_KINDS)
        return summarize_stack(self._resolve(target), kind)

    def discard_previous_stack(self, target: str | Target, kind: StackKind = "imaging") -> list[str]:
        """Delete the kept previous stack of a target.

        Use this once the new stack looks good. It deletes the old stack and
        its pictures, and it cannot be undone. The pipeline never does this by
        itself; it only replaces the previous stack with a newer one at the
        next restack.

        Parameters
        ----------
        target : `str` or `Target`
            The target whose previous stack to delete.
        kind : `str`, optional
            ``"imaging"`` (default) or ``"spectral"`` stack.

        Returns
        -------
        removed : `list` [`str`]
            The files deleted. Empty if no previous stack was kept.
        """
        from astrometricslib.pipelines.stacking.post_processing.previous_stack import discard_previous_stack
        from astrometricslib.pipelines.stacking.post_processing.stack_quality_report import stack_path_of

        check_choice("kind", kind, STACK_KINDS)
        return discard_previous_stack(stack_path_of(self._resolve(target), kind))

    def swap_with_previous_stack(self, target: str | Target, kind: StackKind = "imaging") -> list[str]:
        """Put the previous stack back as the current one.

        Use this when the new stack turned out worse. The current stack
        moves into ``_previous`` in the same step, so calling this again undoes
        it. Only the files change places. The stored quality summary describes
        the stack that was current when it was written, so restack to refresh
        it.

        Parameters
        ----------
        target : `str` or `Target`
            The target whose stacks to swap.
        kind : `str`, optional
            ``"imaging"`` (default) or ``"spectral"`` stacks.

        Returns
        -------
        restored : `list` [`str`]
            The files now current that came from the previous stack. Empty if
            no previous stack was kept, in which case nothing moves.
        """
        from astrometricslib.pipelines.stacking.post_processing.previous_stack import (
            swap_with_previous_stack,
        )
        from astrometricslib.pipelines.stacking.post_processing.stack_quality_report import stack_path_of

        check_choice("kind", kind, STACK_KINDS)
        return swap_with_previous_stack(stack_path_of(self._resolve(target), kind))

    @background_job("quality", grace_period_seconds=20.0)
    def record_frame_measurements(
        self,
        target: str | Target,
        frames: list[FrameRecord] | None = None,
        limit: int | None = None,
        register_job: bool = True,
    ) -> FrameMeasurementSummary:
        """Measure a target's light frames once and store the numbers.

        Each light frame is read once and measured: the width, elongation
        and direction of its stars, the sky brightness and noise in
        electrons, the camera's read noise, the temperatures, and the
        measurements the library already makes (background level, saturated
        fraction, Gaussian star width, count of bright regions). The numbers
        go into the quality database and can be read with
        ``Astrometrics.quality.query``. A value that cannot be measured is
        stored as ``not_measured`` with the reason. The sky brightness needs
        the bias level of the frame's exact camera setting, taken from the
        calibration library's bias frames; with none at that setting those
        two metrics are ``not_measured``.

        The frame files are never moved or changed. Each measured frame gets
        a stamp (the file size and change time at the measurement), saved to
        the stored target every 20 frames and at the end. Only the stamps are
        written, so the save never overwrites other changes to the target. A
        frame whose file still matches its stamp is skipped, so a second call
        measures nothing again. A frame that fails, for any reason, is
        counted and tried again next time; one failure does not stop the
        others. Each frame takes about 12 s of one core, and four frames are
        measured at once in four worker processes: real data (61 frames of
        3008 by 3008 pixels) took about 345 s and about 2.7 GB of memory.

        The worker processes are started with "spawn", which imports the
        calling script again in each worker. A script that calls this method
        must therefore keep its top-level code under
        ``if __name__ == "__main__":``, or the workers stop and every frame
        fails. The backend, the bundled scripts and notebooks run as modules
        or cells and are not affected.

        Only one run measures a target at a time, and only one run in the
        process reads frames at a time. A second call for a target that is
        already being measured returns at once with ``already_running`` set
        and nothing measured. A call for another target waits for its turn.

        Parameters
        ----------
        target : `str` or `Target`
            The target whose light frames to measure.
        frames : `list` [`FrameRecord`], optional
            Only these frames. All of the target's light frames when omitted.
        limit : `int`, optional
            Measure at most this many frames, newest first. The rest wait for
            the next call.
        register_job : `bool`, optional
            Record the run in the job list. Defaults to `True`.

        Returns
        -------
        summary : `FrameMeasurementSummary`
            How many frames were measured, skipped because they had not
            changed, or failed, and how many rows were written.

        Raises
        ------
        InvalidArgumentError
            If ``limit`` is below 1.
        """
        from astrometricslib.drivers.quality_store import QualityStore
        from astrometricslib.pipelines.shared.target_records import save_quality_stamps
        from astrometricslib.pipelines.stacking.frame_measurement_run import (
            FrameMeasurementSession,
            record_frame_measurements_for_target,
        )

        if limit is not None and limit < 1:
            raise InvalidArgumentError("limit must be at least 1.")
        resolved = self._resolve(target)
        with registered_job(enabled=register_job, job_type="quality", target_id=resolved.id) as job:
            # The run gets its own run id and reports progress only when it
            # registered (or joined) a job of its own. Called from inside
            # another job (stacking, ingestion), it leaves that job's
            # progress and id alone.
            session = FrameMeasurementSession(
                QualityStore.from_configuration(self._config),
                self.calibration.library,
                progress_job=job if job.job_id is not None and job.job_type == "quality" else None,
                measured_at=datetime.now(UTC),
            )
            return record_frame_measurements_for_target(
                resolved,
                session,
                frames=frames,
                limit=limit,
                save_target=lambda: save_quality_stamps(self._targets, resolved),
            )

    def restore_excluded_frames(self, target: str | Target, apply: bool = False) -> RestoreReport:
        """List, or move back, the frames the stacker set aside for a target.

        By default nothing moves: the call only lists the frames. With
        ``apply=True`` it moves them back and reindexes the target, which
        saves it. The next stack may set the same frames aside again. To
        keep them in the stack, turn off ``quarantine_bad_frames_enabled`` in
        the configuration first. To only read the list, use
        ``Astrometrics.quality.measure_frames(include=["excluded"])``.

        Parameters
        ----------
        target : `str` or `Target`
            The target whose frames to restore.
        apply : `bool`, optional
            Move the frames back. Defaults to `False`, which only lists them.

        Returns
        -------
        report : `RestoreReport`
            The frames that were set aside, and how many were moved back.
        """
        from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
            find_quarantine_folders,
            list_set_aside_frames,
            restore_quarantined_frames,
        )

        resolved = self._resolve(target)
        frames_path = str(self._config.get_frames_path())
        frames = list_set_aside_frames(frames_path, resolved.id)
        restored_count = 0
        if apply:
            for folder in find_quarantine_folders(frames_path, resolved.id):
                restored_count += len(restore_quarantined_frames(folder))
            if restored_count:
                self._targets.reindex_frames(resolved)
        return RestoreReport(
            target_id=resolved.id, applied=apply, frames=frames, restored_count=restored_count
        )

    # -- Limits on how much heavy work runs at once -------------------------

    def acquire_analysis_slot(self) -> AbstractContextManager:
        """Limit how many heavy jobs can run at the same time.

        Processing images takes a lot of CPU power. This makes sure the
        computer is not overwhelmed, by limiting how many jobs run at once.
        Shares its slot pool with stacking (see `acquire_stacking_slot`);
        both draw on ``max_concurrent_jobs``.

        Returns
        -------
        slot : `AbstractContextManager`
            Enter to block until a slot is free, then hold it for the
            analysis run's duration.
        """
        from astrometricslib.foundation.storage.process_locks import acquire_resource_slot

        return acquire_resource_slot(self._config, "job", self._config.get_max_concurrent_jobs())

    def acquire_stacking_slot(self) -> AbstractContextManager:
        """Limit how many stacking programs can run at the same time.

        Stacking images uses a lot of memory and CPU. This makes sure the
        computer does not run out, by limiting how many stacks run at once.
        Shares its slot pool with analysis (see `acquire_analysis_slot`);
        both draw on ``max_concurrent_jobs``.

        Returns
        -------
        slot : `AbstractContextManager`
            Enter to block until a slot is free, then hold it for the
            stacking run's duration.
        """
        from astrometricslib.foundation.storage.process_locks import acquire_resource_slot

        return acquire_resource_slot(self._config, "job", self._config.get_max_concurrent_jobs())
