"""Main interfaces for running processing pipelines and calibration data.

This module provides the primary ways to trigger large processing jobs like
stacking images, measuring stars (photometry), and analyzing light
(spectroscopy). It also provides tools to check the quality of processed
images and manage calibration frames (darks, biases, and flats) which are
used to remove noise from raw telescope images.
"""

from contextlib import AbstractContextManager
from typing import Any, Literal

from astrometricslib.drivers.job_logging import JobHandle, capture_job_logs, registered_job
from astrometricslib.drivers.logger_interface import DbLogHandler, LoggerInterface
from astrometricslib.drivers.siril_interface import ImageProcessing
from astrometricslib.models.target import Target
from astrometricslib.utilities.config_loader import AppConfiguration

__all__ = [
    "CalibrationCatalog",
    "DbLogHandler",
    "ImageProcessing",
    "JobHandle",
    "LoggerInterface",
    "ProcessingPipelines",
    "QualityDiagnostics",
    "capture_job_logs",
    "registered_job",
]

_CalibrationKind = Literal["dark", "bias", "flat"]
_CALIBRATION_KINDS: frozenset[str] = frozenset({"dark", "bias", "flat"})


class QualityDiagnostics:
    """Tools for checking the quality of processed images.

    This class lets you measure things like star sharpness (FWHM) and
    how much noisy data had to be thrown away during image stacking.
    This helps you figure out if an observation session had good tracking
    and clear skies, or if the resulting data is poor quality.
    """

    def __init__(self, config: AppConfiguration):  # ruff: ignore[missing-return-type-special-method]
        """Initialize with application configuration.

        Parameters
        ----------
        config : `AppConfiguration`
            Application configuration.
        """
        self._config = config

    def measure_stack_fwhm(self, path: str) -> float | None:
        """Get the median FWHM (pixels) of the brightest stars in a FITS image.

        Parameters
        ----------
        path : `str`
            Path to the stacked FITS image to measure.

        Returns
        -------
        fwhm : `float` or `None`
            Median FWHM in pixels across the measured stars, or `None`
            if it could not be measured.
        """
        from astrometricslib.image_processing.quality_metrics import measure_image_fwhm

        return measure_image_fwhm(path)

    def measure_stack_rejected_fraction(self, stacked_path: str) -> float | None:
        """Get the mean per-pixel rejected-frame fraction from the rejmap.

        Parameters
        ----------
        stacked_path : `str`
            Path to the stacked FITS image; its sibling rejmap file is
            resolved from this path.

        Returns
        -------
        fraction : `float` or `None`
            Mean rejected-pixel fraction over the rejmap, or `None` if
            the sibling rejmap file does not exist.
        """
        from astrometricslib.image_processing.quality_metrics import measure_rejected_fraction

        return measure_rejected_fraction(stacked_path)

    def parse_stack_registration_seq(self, seq_path: str) -> list[dict[str, float]]:
        """Parse registration-summary lines from a Siril .seq file.

        Parameters
        ----------
        seq_path : `str`
            Path to the Siril `.seq` file to parse.

        Returns
        -------
        frames : `list` of `dict`
            One dict per registered frame, in original submission order.
        """
        from astrometricslib.image_processing.quality_metrics import parse_seq_file

        return parse_seq_file(seq_path)

    def parse_stack_zero_order_star(self, lst_path: str) -> dict[str, float] | None:
        """Get the brightest star's stats from a Siril per-frame .lst list.

        Parameters
        ----------
        lst_path : `str`
            Path to the Siril `.lst` file to parse.

        Returns
        -------
        result : `dict` or `None`
            Stats for the brightest star, or `None` if the file does
            not exist or has no data rows.
        """
        from astrometricslib.image_processing.quality_metrics import parse_zero_order_star

        return parse_zero_order_star(lst_path)

    def flag_value_outliers(
        self, values: list[float | None], sigma_threshold: float, low_is_bad: bool = False
    ) -> list[bool]:
        """Flag entries more than sigma_threshold standard deviations away.

        Parameters
        ----------
        values : `list` [`float` or `None`]
            Values to check; `None` entries are never flagged.
        sigma_threshold : `float`
            Number of standard deviations from the mean beyond which a
            value is flagged.
        low_is_bad : `bool`, optional
            If `True`, only flag values below the mean; otherwise flag
            deviations on either side. Defaults to `False`.

        Returns
        -------
        flags : `list[bool]`
            A list the same length as values, `True` where the
            corresponding entry is an outlier.
        """
        from astrometricslib.pipelines.spectroscopy.registration_quality import flag_outliers

        return flag_outliers(values, sigma_threshold, low_is_bad)

    @property
    def spectral_registration_thresholds(self) -> dict[str, float]:
        """The default outlier thresholds for spectral registration checks.

        Returns
        -------
        thresholds : `dict` [`str`, `float`]
            Threshold values keyed by threshold name.
        """
        from astrometricslib.pipelines.spectroscopy import registration_quality as srq

        return {
            "min_matched_star_pairs": srq.MIN_MATCHED_STAR_PAIRS,
            "rmse_outlier_sigma": srq.RMSE_OUTLIER_SIGMA,
            "zero_order_amplitude_outlier_sigma": srq.ZERO_ORDER_AMPLITUDE_OUTLIER_SIGMA,
            "zero_order_position_jump_outlier_sigma": srq.ZERO_ORDER_POSITION_JUMP_OUTLIER_SIGMA,
        }


class CalibrationCatalog:
    """A catalog for managing calibration frames (darks, biases, and flats).

    Telescope cameras produce noise. Calibration frames are special pictures
    taken with the lens cap on (darks/biases) or pointed at a flat white
    surface (flats) to map out and remove this noise. This class tracks
    these files so they can be applied to real images later.
    """

    def __init__(self, config: AppConfiguration):  # ruff: ignore[missing-return-type-special-method]
        """Initialize with application configuration.

        Parameters
        ----------
        config : `AppConfiguration`
            Application configuration.
        """
        self._config = config
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

    @staticmethod
    def _validate_kind(kind: str) -> None:
        """Raise `ValueError` if `kind` is not a supported calibration kind.

        Parameters
        ----------
        kind : `str`
            The calibration kind to validate.

        Raises
        ------
        ValueError
            If `kind` is not one of ``"dark"``, ``"bias"``, ``"flat"``.
        """
        if kind not in _CALIBRATION_KINDS:
            raise ValueError(
                f"Unknown calibration kind {kind!r}; expected one of {sorted(_CALIBRATION_KINDS)}"
            )

    def load(self) -> None:
        """Load the calibration library from its on-disk JSON file."""
        self.library.load_library()

    def save(self) -> None:
        """Persist the calibration library to its on-disk JSON file."""
        self.library.save_library()

    def stats(self) -> dict[str, Any]:
        """Return aggregated per-camera/exposure/filter counts for all kinds.

        Returns
        -------
        stats : `dict`
            Dict with ``"darks"``, ``"biases"``, and ``"flats"`` keys,
            each a list of per-group count summaries.
        """
        return self.library.get_stats()

    def add(self, image_file: str, kind: _CalibrationKind, **kwargs: Any) -> None:
        """Add a calibration frame of the given kind (dark/bias/flat).

        Parameters
        ----------
        image_file : `str`
            Path to the calibration frame FITS file.
        kind : {"dark", "bias", "flat"}
            The calibration frame kind.
        **kwargs
            Forwarded to the kind-specific adder -- ``flat`` accepts
            `telescope`.

        Raises
        ------
        ValueError
            If `kind` is not one of ``"dark"``, ``"bias"``, ``"flat"``.
        """  # ruff: ignore[docstring-extraneous-exception] -- genuinely raised by self._validate_kind
        self._validate_kind(kind)
        method = getattr(self.library, f"add_{kind}_frame")
        method(image_file, **kwargs)

    def get(self, kind: _CalibrationKind, **kwargs: Any) -> list[str]:
        """Retrieve calibration frame paths of the given kind.

        Parameters
        ----------
        kind : {"dark", "bias", "flat"}
            The calibration frame kind.
        **kwargs
            Forwarded to the kind-specific getter -- common keys are
            `camera`, `exposure` (dark only), `telescope`/
            `filter_type` (flat only), and `validate_paths`.

        Returns
        -------
        frames : `list` [`str`]
            Matching frame file paths.

        Raises
        ------
        ValueError
            If `kind` is not one of ``"dark"``, ``"bias"``, ``"flat"``.
        """  # ruff: ignore[docstring-extraneous-exception] -- genuinely raised by self._validate_kind
        self._validate_kind(kind)
        method = getattr(self.library, f"get_{kind}_frames")
        return method(**kwargs)

    def refresh(self, kind: _CalibrationKind, prune_missing: bool = False) -> None:
        """Rescan the given kind's directory and re-index any FITS files.

        Parameters
        ----------
        kind : {"dark", "bias", "flat"}
            The calibration frame kind.
        prune_missing : `bool`, optional
            If `True`, remove index entries whose files no longer
            exist on disk. Defaults to `False`.

        Raises
        ------
        ValueError
            If `kind` is not one of ``"dark"``, ``"bias"``, ``"flat"``.
        """  # ruff: ignore[docstring-extraneous-exception] -- genuinely raised by self._validate_kind
        self._validate_kind(kind)
        method = getattr(self.library, f"refresh_{kind}_frames")
        method(prune_missing=prune_missing)


class ProcessingPipelines:
    """Main interface for triggering image stacking and analysis pipelines."""

    def __init__(self, config: AppConfiguration):  # ruff: ignore[missing-return-type-special-method]
        """Initialize with application configuration.

        Parameters
        ----------
        config : `AppConfiguration`
            Application configuration.
        """
        self._config = config
        self.diagnostics = QualityDiagnostics(config)
        self.calibration = CalibrationCatalog(config)

    # -- Pipeline execution, one method per pipeline type -----------------

    def run_stacking(
        self,
        target: Target,
        frames_to_stack: list[Any] | None = None,
        filter_type: Any | None = None,
        rejection_sigma: tuple[float, float] | None = None,
        filter_wfwhm: str | None = None,
        filter_round: str | None = None,
        stack_weight: str | None = None,
        output_file: str | None = None,
        solve: bool = False,
        log_file: str | None = None,
        generate_rejmap: bool | None = None,
    ) -> str | None:
        """Stack multiple images into one clean image, optionally solving it.

        Stacking combines many faint, noisy images into one clear image.
        If `solve` is True, it will also 'plate-solve' the final image,
        meaning it will calculate exactly where in the sky the camera was
        pointing by matching the stars in the image to a known database.

        Parameters
        ----------
        target : `Target`
            The target whose frames should be stacked.
        frames_to_stack : `list`, optional
            Explicit frame records to stack; defaults to the target's
            eligible light frames.
        filter_type : `astrometricslib.utilities.enums.FilterType`, optional
            Restrict stacking to frames captured with this filter.
        rejection_sigma : `tuple` [`float`, `float`], optional
            Low/high sigma-clipping rejection bounds.
        filter_wfwhm : `str`, optional
            Weighted-FWHM frame filter expression.
        filter_round : `str`, optional
            Roundness frame filter expression.
        stack_weight : `str`, optional
            Per-frame stacking weight expression.
        output_file : `str`, optional
            Output path override; ignored when `solve` is `True`.
        solve : `bool`, optional
            If `True`, also plate-solve the resulting stack. Defaults
            to `False`.
        log_file : `str`, optional
            Path to write the Siril process log to.
        generate_rejmap : `bool`, optional
            Whether to also generate a rejection map alongside the
            stack.

        Returns
        -------
        stacked_path : `str` or `None`
            The path to the stacked output file, or `None` if
            stacking did not produce an output.
        """
        if solve:
            from astrometricslib.pipelines.dispatch import stack_and_solve

            return stack_and_solve(
                target,
                log_file=log_file,
                frames_to_stack=frames_to_stack,
                filter_type=filter_type,
                rejection_sigma=rejection_sigma,
                filter_wfwhm=filter_wfwhm,
                filter_round=filter_round,
                stack_weight=stack_weight,
                generate_rejmap=generate_rejmap,
            )

        from astrometricslib.pipelines.stacking import stage as stacking_tasks

        return stacking_tasks.stack_frames(
            target,
            log_file=log_file,
            frames_to_stack=frames_to_stack,
            filter_type=filter_type,
            rejection_sigma=rejection_sigma,
            filter_wfwhm=filter_wfwhm,
            filter_round=filter_round,
            stack_weight=stack_weight,
            generate_rejmap=generate_rejmap,
            output_file=output_file,
        )

    def run_astrometry(self, target: Target, **kwargs: Any) -> dict[str, Any]:
        """Run astrometric plate-solving and catalog cross-matching.

        See `tasks.target_tasks.pipeline_tasks.analyze_target` for the
        full parameter/return documentation.

        Parameters
        ----------
        target : `Target`
            The target to run astrometry against.
        **kwargs
            Forwarded to `pipeline_tasks.analyze_target`.

        Returns
        -------
        result : `dict[str, Any]`
            Astrometry results and status fields.
        """
        from astrometricslib.pipelines.dispatch import analyze_target

        return analyze_target(target, pipeline_type="astrometry", **kwargs)

    def run_photometry(self, target: Target, **kwargs: Any) -> dict[str, Any]:
        """Run ensemble differential photometry.

        See `tasks.target_tasks.pipeline_tasks.analyze_target` for the
        full parameter/return documentation.

        Parameters
        ----------
        target : `Target`
            The target to run photometry against.
        **kwargs
            Forwarded to `pipeline_tasks.analyze_target`.

        Returns
        -------
        result : `dict[str, Any]`
            Photometry results and status fields.
        """
        from astrometricslib.pipelines.dispatch import analyze_target

        return analyze_target(target, pipeline_type="photometry", **kwargs)

    def run_spectroscopy(self, target: Target, **kwargs: Any) -> dict[str, Any]:
        """Run spectroscopy extraction and calibration.

        See `tasks.target_tasks.pipeline_tasks.analyze_target` for the
        full parameter/return documentation.

        Parameters
        ----------
        target : `Target`
            The target to run spectroscopy against.
        **kwargs
            Forwarded to `pipeline_tasks.analyze_target`.

        Returns
        -------
        result : `dict[str, Any]`
            Spectroscopy results and status fields.
        """
        from astrometricslib.pipelines.dispatch import analyze_target

        return analyze_target(target, pipeline_type="spectroscopy", **kwargs)

    def run_spectroscopy_by_session(
        self,
        astrometrics: Any,
        target: Target,
        frame_records: list[Any],
        max_workers: int | None = None,
        on_item_complete: Any | None = None,
    ) -> Any:
        """Analyze a target's spectroscopy frames, grouped by session.

        Parameters
        ----------
        astrometrics : `astrometricslib.Astrometrics`
            The parent astrometrics, needed to resolve session boundaries.
        target : `Target`
            The target whose spectroscopy frames should be processed.
        frame_records : `list`
            The frame records to process, grouped internally by session.
        max_workers : `int`, optional
            Maximum concurrent worker count; defaults to the
            configured spectroscopy concurrency.
        on_item_complete : `Callable`, optional
            Callback invoked after each session finishes.

        Returns
        -------
        summary : `BatchRunSummary`
            Aggregated success/failure/result state across every
            session's frames.
        session_results : `list` [`tuple`]
            One `(session, identify_result)` pair per session.
        """
        from astrometricslib.pipelines.spectroscopy import (
            batch as spectroscopy_batch_operations,
        )

        return spectroscopy_batch_operations.process_spectroscopy_frames_by_session(
            astrometrics, target, frame_records, max_workers=max_workers, on_item_complete=on_item_complete
        )

    def scan_target_directory(self, target: Target, frames_root_path: str) -> None:
        """Scan a folder to find and catalog any new image frames for a target.

        Parameters
        ----------
        target : `Target`
            The target to index frames into.
        frames_root_path : `str`
            Root directory to scan for FITS files.
        """
        from astrometricslib.data_access.frame_scanning import scan_target_directory

        scan_target_directory(target, frames_root_path)

    def create_frame_record(self, path: str, camera: str | None = None) -> Any:
        """Create a frame record by reading a FITS image's header data.

        Parameters
        ----------
        path : `str`
            Path to the FITS file to parse.
        camera : `str`, optional
            Camera name override; parsed from the header when omitted.

        Returns
        -------
        frame_record : `astrometricslib.models.target.FrameRecord`
            The frame record derived from the FITS header at `path`.
        """
        from astrometricslib.data_access.frame_scanning import create_frame_record_from_fits

        return create_frame_record_from_fits(path, camera)

    def acquire_analysis_slot(self) -> AbstractContextManager:
        """Limit how many analysis jobs can run at the same time.

        Processing images takes a lot of CPU power. This function ensures
        we don't overwhelm the computer by limiting how many jobs can run
        simultaneously.

        Returns
        -------
        slot : `AbstractContextManager`
            Enter to block until a slot is free, then hold it for the
            analysis run's duration.
        """
        from astrometricslib.drivers import disk_interface

        return disk_interface.acquire_resource_slot(
            self._config, "analysis", self._config.get_analysis_concurrency()
        )

    def acquire_stacking_slot(self) -> AbstractContextManager:
        """Limit how many image stacking jobs can run at the same time.

        Stacking images uses massive amounts of RAM and CPU. This function
        ensures we don't crash the computer by limiting how many stacking
        programs can run simultaneously.

        Returns
        -------
        slot : `AbstractContextManager`
            Enter to block until a slot is free, then hold it for the
            stacking run's duration.
        """
        from astrometricslib.drivers import disk_interface

        return disk_interface.acquire_resource_slot(
            self._config, "siril", self._config.get_siril_concurrency()
        )
