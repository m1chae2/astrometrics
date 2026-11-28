"""Purpose: `QualityFrames` and `QualityStacks`, the live quality checks.

Description: `QualityFrames` is ``astrometrics.quality.frames``. It measures
raw frames (in the library or not), records a target's frame measurements,
picks a typical frame to blink a frame against, and follows positions from
one image to another. `QualityStacks` is ``astrometrics.quality.stacks``. It
measures a stack, reads the aperture record a stack's job wrote, and records
the noise of stacks made from parts of the library stack. Each method is a
thin wrapper of one owner function, which its docstring names.
"""

import os
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib._internal.renames import MovedNames
from astrometricslib.foundation.arguments import check_include
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.foundation.jobs.runner import background_job
from astrometricslib.models.quality.aperture_record import OddEvenApertureRecord
from astrometricslib.models.quality.blink_pair import BlinkPair
from astrometricslib.models.quality.frame_measurement_summary import FrameMeasurementSummary
from astrometricslib.models.quality.position_mapping import MappedPositions
from astrometricslib.models.quality.quality_reports import FrameQualityReport, StackQualityReport
from astrometricslib.models.quality.subset_stacks import (
    NightContributionResult,
    NoiseGrowthResult,
    OddEvenDifferenceNoiseResult,
)
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.api_arguments import TargetLookup
from astrometricslib.pipelines.shared.frame_choice import FrameChoice
from astrometricslib.pipelines.shared.position_mapping import RecordCache

__all__ = ["QualityFrames", "QualityStacks"]


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


class QualityFrames(MovedNames):
    """Measure and record raw frames, and follow positions between images.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    targets : `TargetLookup`, optional
        The target catalog, which saves the measurement stamps `record`
        sets on a target's frames.
    calibration_library : `Callable`, optional
        Gives the calibration library, for the bias frames.
    drivers : `Drivers`, optional
        The outside services, for the spectrum check.
    """

    def __init__(
        self,
        config: AppConfiguration,
        targets: TargetLookup | None = None,
        calibration_library: Callable[[], Any] | None = None,
        drivers: Drivers | None = None,
    ) -> None:
        self._config = config
        self._targets = targets
        self._calibration_library_provider = calibration_library
        self._drivers = drivers
        self._record_cache = RecordCache()

    def _calibration_library(self) -> Any:
        """Give the calibration library, which holds the bias frames.

        Returns
        -------
        library : `CalibrationLibrary`
            The library.

        Raises
        ------
        InvalidArgumentError
            If this object was built without one.
        """
        if self._calibration_library_provider is None:
            raise InvalidArgumentError("This quality object was built without a calibration library.")
        return self._calibration_library_provider()

    @background_job("diagnostics", grace_period_seconds=20.0)
    def measure(
        self,
        frames: FrameChoice,
        *,
        include: Sequence[str] | None = None,
        trend_frames: int | None = None,
        trend_threshold_pct: float | None = None,
        bias_level_adu: float | None = None,
        predict_exposure_s: float | None = None,
    ) -> FrameQualityReport:
        """Measure raw frames, in the library or not, storing nothing.

        Owner:
            astrometricslib.pipelines.stacking.frame_quality_report.measure_frame_quality

        Whether the frames are imaging or spectrum frames is worked out from
        their filters; a mix is refused. Imaging frames get the batch check:
        star count, width and roundness, trailing, sky level, saturation and
        the jump from the frame before, with the frames that stand out
        flagged and the slow drifts across the newest frames. With
        ``include=["metrics"]`` they also get the frame metric set that
        `record` stores (star width and shape, sky brightness and noise in
        electrons, background level, saturation, temperatures), so the
        numbers agree with the stored ones; this takes about 12 s a frame.
        Spectrum frames get the spectrum check: for each, the zero-order
        star, the streak's tilt and width, the sky level, the peaks and
        every saturated patch with its distance along the spectrum, grouped
        by exposure length and by pier side.

        The frames need not be in the library, so this is how to check
        tonight's staging folder, or frames drawn by a camera simulator,
        before they enter the library. Which frames a stack would set aside
        is said by ``processing.stacking.predict``.

        Parameters
        ----------
        frames : `FrameChoice`
            The page `targets.frames.query` returns, frame records, or file
            paths (read from their headers). A page cut short is refused.
        include : `Sequence` [`str`], optional
            ``"metrics"``: also the stored frame metric set (imaging only).
        trend_frames : `int`, optional
            How many of the newest frames to check for a slow drift. The
            tuned `TREND_WINDOW_FRAMES` (10) when left out.
        trend_threshold_pct : `float`, optional
            How far, as a percentage of its starting level, a number must
            move across those frames to count as a drift. The tuned
            `TREND_THRESHOLD_PERCENT` (15 until tuned) when left out.
        bias_level_adu : `float`, optional
            The bias level in ADU for the sky metrics of every frame. By
            default it comes from the calibration library's bias frames at
            each frame's camera setting; with none there, the sky metrics
            come back as not measured with the reason.
        predict_exposure_s : `float`, optional
            Spectrum frames only: an exposure to predict the peaks at. The
            report says whether the spectrum would clip there.

        Returns
        -------
        report : `FrameQualityReport`
            ``kind``, ``frames_measured`` and the sections: ``checks`` (the
            batch check of imaging frames; its widths are its own
            second-moment estimate, which has no ratio to the library's one
            star-width scale), ``metrics`` (with ``include=["metrics"]``;
            each star width names its estimator and is also given on the
            scale where it has a ratio) and ``spectral``.

        Notes
        -----
        Raises `InvalidArgumentError` if a section is unknown, the frames mix
        imaging and spectrum frames, an option does not apply to their kind,
        or ``frames`` is empty or a page cut short; `NotFoundError` if a path
        names no file.
        """
        from astrometricslib.foundation.jobs.runner import registered_job
        from astrometricslib.pipelines.stacking.frame_quality_report import (
            FRAME_QUALITY_SECTIONS,
            frame_records_of,
            measure_frame_quality,
        )

        sections = check_include(include, FRAME_QUALITY_SECTIONS)
        records = frame_records_of(frames, self._config)
        folders = sorted({os.path.dirname(frame.path) for frame in records})
        with registered_job(job_type="diagnostics", target_id=folders[0] if len(folders) == 1 else "frames"):
            return measure_frame_quality(
                records,
                self._config,
                datetime.now(UTC),
                include=sections,
                trend_frames=trend_frames,
                trend_threshold_pct=trend_threshold_pct,
                bias_level_adu=bias_level_adu,
                predict_exposure_s=predict_exposure_s,
                calibration_library=self._calibration_library_provider,
                drivers=self._drivers,
            )

    @background_job("quality", grace_period_seconds=20.0)
    def record(self, target: Target, frames: FrameChoice | None = None) -> FrameMeasurementSummary:
        """Measure a target's light frames once and store the numbers.

        Owner:
            astrometricslib.pipelines.stacking.frame_measurement_run.record_target_frame_measurements

        Each light frame is read once and measured: the width, elongation
        and direction of its stars, the sky brightness and noise in
        electrons, the camera's read noise, the temperatures, and the
        measurements the library already makes (background level, saturated
        fraction, Gaussian star width, count of bright regions). The numbers
        go into the quality database and can be read with
        ``quality.records.query``. A value that cannot be measured is stored
        as ``not_measured`` with the reason. The sky brightness needs the
        bias level of the frame's exact camera setting, taken from the
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
        target : `Target`
            The target whose light frames to measure.
        frames : `FrameChoice`, optional
            Only these frames (a frame query's page, frame records or the
            target's frame paths). All of the target's light frames when
            omitted.

        Returns
        -------
        summary : `FrameMeasurementSummary`
            How many frames were measured, skipped because they had not
            changed, or failed, and how many rows were written.
        """
        from astrometricslib.pipelines.shared.frame_choice import frames_from_choice
        from astrometricslib.pipelines.shared.target_records import save_quality_stamps
        from astrometricslib.pipelines.stacking.frame_measurement_run import record_target_frame_measurements

        target = _check_target(target)
        chosen = None if frames is None else frames_from_choice(frames, target)
        targets = self._targets
        return record_target_frame_measurements(
            target,
            self._config,
            self._calibration_library(),
            datetime.now(UTC),
            frames=chosen,
            save_target=None if targets is None else lambda: save_quality_stamps(targets, target),
        )

    def get_blink_partner(self, target: Target, path: str) -> BlinkPair:
        """Pick a typical frame and a reference star to blink a frame against.

        Owner:
            astrometricslib.pipelines.stacking.pre_processing.blink_pair.find_blink_partner

        Nothing is stored. The typical frame is the frame of the same session
        (the batch the set-aside step judges the frame in) whose stored
        ``frame.star_elongation`` and ``frame.sky_unevenness`` lie closest to
        the session medians. The reference star is a bright, isolated,
        unsaturated star near the frame center, chosen with the rules of the
        star-shape measurement. Its position in the typical frame is found by
        matching the two frames' stars. The frame may be one in the stack or
        one the stacker set aside.

        Parameters
        ----------
        target : `Target`
            The target.
        path : `str`
            The frame to examine.

        Returns
        -------
        pair : `BlinkPair`
            Fields: ``found``, ``reason``, ``path``, ``typical_path``,
            ``star_x`` and ``star_y`` (the star in ``path``),
            ``typical_star_x`` and ``typical_star_y`` (the same star in the
            typical frame), all in 1-based pixels, then ``turned``,
            ``votes``, ``session_frame_count`` and the frame size ``width``
            and ``height``. When nothing could be found, ``found`` is
            `False` and ``reason`` says why.

        Notes
        -----
        Raises `NotFoundError` if the frame file does not exist.
        """
        from astrometricslib.pipelines.stacking.pre_processing.blink_pair import find_blink_partner

        return find_blink_partner(_check_target(target), path, self._config)

    def map_positions(
        self, from_path: str, to_path: str, *, points: Sequence[tuple[float, float]] | None = None
    ) -> MappedPositions:
        """Find where positions of one image lie on another image.

        Owner:
            astrometricslib.pipelines.stacking.position_following.map_positions

        Nothing is stored. The viewer uses this to follow a measured star
        when the image on screen changes. It tries, in order: the sky
        position (both images plate solved), a stack's registration record
        (frame to stack, stack to frame, frame to frame, or those chained
        through the sky for frames of two stacks), and, between two frames
        of one size that nothing else links, matching the frames' bright
        stars the way the set-aside check's pointing check does (trying the
        second frame as it is and turned half a revolution). The first two
        read only headers and small files, so they are fast; star matching
        reads both frames' pixels. When nothing applies, the same pixel is
        given as a guess. The answer says which method was used.

        Parameters
        ----------
        from_path, to_path : `str`
            The two FITS images.
        points : `Sequence` [`tuple` [`float`, `float`]], optional
            The ``(x, y)`` positions on the first image, in 1-based pixels
            (as in FITS and DS9). The center of the first image when left
            out.

        Returns
        -------
        mapping : `MappedPositions`
            ``method`` (``"sky"``, ``"stack alignment"``, ``"stack alignment
            + sky"``, ``"star matching"``, ``"same pixel"`` or ``"none"``),
            ``note``, and ``points``, each with ``x``, ``y``, ``mapped_x``
            and ``mapped_y`` (1-based pixels on the second image, `None`
            when the position cannot be followed, such as a frame Siril left
            out of its stack) and ``inside``.

        Notes
        -----
        Raises `NotFoundError` if an image file does not exist.
        """
        from astrometricslib.pipelines.stacking.position_following import map_positions

        roots = [
            os.path.join(str(self._config.get_library_path()), "stacks"),
            str(self._config.get_stacks_path()),
        ]
        roots = list(dict.fromkeys(os.path.realpath(root) for root in roots))
        return map_positions(from_path, to_path, points, roots, self._record_cache)


class QualityStacks(MovedNames):
    """Measure stacks and record the noise of stacks of parts of a stack.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    drivers : `Drivers`, optional
        The outside services, for plate solves and the stacker.
    """

    def __init__(self, config: AppConfiguration, drivers: Drivers | None = None) -> None:
        self._config = config
        self._drivers = drivers

    def get_data_version(self) -> str:
        """Give a short text that changes whenever the library's stack data do.

        Owner:
            astrometricslib.pipelines.shared.data_version_texts.get_stack_data_version

        The stack data are the stored stack-level measurements of every
        target, the calibration records of the stacks and the capture
        settings. A result pooled over every library stack (a noise model,
        the stack roundness baseline) can keep its answer while this text
        stays the same. A new frame leaves it as it is; a stack run changes
        it. The text is opaque: compare it with an earlier one.

        Returns
        -------
        version : `str`
            The text.
        """
        from astrometricslib.pipelines.shared.data_version_texts import get_stack_data_version

        return get_stack_data_version(self._config)

    @background_job("diagnostics", grace_period_seconds=20.0)
    def measure(
        self,
        target_or_path: Target | str,
        *,
        stack: str | None = None,
        include: Sequence[str] | None = None,
        compare_to: str | None = None,
    ) -> StackQualityReport:
        """Measure a stack, and optionally compare it with another stack.

        Owner:
            astrometricslib.pipelines.stacking.post_processing.stack_measurement.measure_stack

        The comparison measures both stacks the same way: the sky level (the
        median of the usable pixels), the noise in empty sky, how flat the
        sky is across the frame, and star width. The noise is the number the
        target report stores (``stacking.empty_aperture_noise_normalized``),
        measured in the apertures the library stack chose. Each stack's
        reference frame and trimmed edges come from its registration record;
        the report says when they could not be recovered. A number that
        cannot be measured is reported as not measured, with the reason. A
        failed plate solve can take about ten minutes. A fine-grain pixel
        noise is kept and labelled. The result gives the numbers, the change
        in each, and a plain sentence for each. It does not pick a winner,
        because that depends on what the change was for. New flats should
        lower the flatness number and leave the noise alone; more frames
        should lower the noise and leave the flatness alone.

        Parameters
        ----------
        target_or_path : `Target` or `str`
            A target, whose current stack to measure, or a stack's FITS file
            (a path ending in ``.fit``, ``.fits`` or ``.fts``).
        stack : `str`, optional
            For a target: the path of another of its stacks, such as its
            spectral stack (whose empty-sky noise is then reported as not
            measured). The target's main imaging stack when left out.
            Refused with a path.
        include : `Sequence` [`str`], optional
            Measurements to make: ``"fwhm"`` (median star width of the
            brightest stars), ``"rejected_fraction"`` (from the rejection
            map beside the stack) and ``"registration"`` (one row per frame
            from the ``_Registration.seq`` file beside the stack).
        compare_to : `str`, optional
            ``"previous"`` compares with the stack the last restack kept in
            the ``_previous`` folder. Any other text is the path of a stack
            to compare with. The other stack is ``before``; this one is
            ``after``.

        Returns
        -------
        report : `StackQualityReport`
            The measurements asked for and the comparison. The star width
            names its estimator (``fwhm_estimator``, ``"stack Gaussian"``)
            and gives ``fwhm_on_scale_px``, the width on the library's one
            star-width scale; each compared stack does the same. The
            registration rows carry Siril's own width
            (``registration_fwhm_estimator``), which has no ratio to that
            scale.

        Notes
        -----
        Raises `InvalidArgumentError` if a section is unknown or ``stack`` is
        given with a path, and `NotFoundError` if a stack file does not
        exist or the target has no stack.
        """
        from astrometricslib.pipelines.stacking.post_processing.stack_measurement import measure_stack

        return measure_stack(
            target_or_path, stack=stack, include=include, compare_to=compare_to, drivers=self._drivers
        )

    def get_aperture_record(self, path: str) -> OddEvenApertureRecord | None:
        """Read the per-aperture noise record that a stack's job wrote.

        Owner:
            astrometricslib.pipelines.stacking.post_processing.stack_measurement.read_aperture_record

        The odd–even difference job saves, beside a stack, the sum and the
        difference of each empty sky aperture it measured. A stacking run's
        quality row names the file. Nothing is measured or written.

        Parameters
        ----------
        path : `str`
            The record file.

        Returns
        -------
        record : `OddEvenApertureRecord` or `None`
            The file's content: ``version``, ``stack``, ``aperture_identity``,
            ``note`` and ``apertures`` (rows with ``index``, ``x``, ``y``,
            ``distance_from_center_px``, ``distance_from_target_px``, ``sum``
            and ``difference``). `None` when the file is missing, cannot be
            read, is of another version or does not have the expected fields.
        """
        from astrometricslib.pipelines.stacking.post_processing.stack_measurement import read_aperture_record

        return read_aperture_record(path)

    @background_job("stacking", grace_period_seconds=8.0)
    def record_noise_growth(
        self, target: Target, *, maximum_steps: int | None = None, include: Sequence[str] | None = None
    ) -> NoiseGrowthResult:
        """Stack growing parts of the library stack and store their noise.

        Owner:
            astrometricslib.pipelines.stacking.noise_records.record_noise_growth

        The library stack's frames (read from the stack's ``_inputs.json``)
        are split by observing night. The subsets are the first night, the
        first two nights, and so on, up to one night fewer than the stack. A
        stack of one night uses the first quarter, half and three quarters
        of its frames instead. At most ``maximum_steps`` subsets are made,
        spaced evenly through the possible ones, and a subset with fewer
        than four frames is left out. Each is stacked into the target's
        ``_subsets`` folder with the normal settings (without the frame
        set-aside step) and its noise is measured in the library stack's
        sky apertures, so the numbers compare. The library stack is the
        last point of the curve and is not stacked again. The noise, the
        reference flux, the effective frame count and the effective time are
        stored at level ``stack`` with the subset's label in the subject.
        The library stack, its previews and its record are never changed.
        The cost is one stack for each subset, four by default, each with
        fewer frames than the library stack (a design estimate: about one to
        two library stacks' time). The target must already have the noise
        apertures its library stack stored, so stack it once first if it was
        stacked before they were recorded.

        Parameters
        ----------
        target : `Target`
            The target.
        maximum_steps : `int`, optional
            The most subsets to stack. Defaults to the registered
            ``stacking.default_growth_steps`` (4).
        include : `Sequence` [`str`], optional
            Heavy parts to run in the same job, under the same lock, after
            the series: ``"night_contributions"`` (what
            `record_night_contributions` stores, one more stack for every
            night) and ``"odd_even_difference"`` (what
            `record_odd_even_noise` stores, about one library stack's time).

        Returns
        -------
        result : `NoiseGrowthResult`
            The subsets, smallest first, with notes about steps left out,
            and the parts asked for.

        Notes
        -----
        Raises `NotFoundError` for a target with no stack or a stack with no
        ``_inputs.json``, and `InvalidArgumentError` for an unknown part or
        ``maximum_steps`` below 1.
        """
        from astrometricslib.pipelines.stacking.noise_records import record_noise_growth

        return record_noise_growth(
            _check_target(target),
            self._config,
            datetime.now(UTC),
            self._drivers,
            maximum_steps=maximum_steps,
            include=include,
        )

    @background_job("stacking", grace_period_seconds=8.0)
    def record_night_contributions(self, target: Target) -> NightContributionResult:
        """Store what each observing night gave to the library stack.

        Owner:
            astrometricslib.pipelines.stacking.noise_records.record_night_contributions

        For every night, the library stack's frames without that night are
        stacked into the target's ``_subsets`` folder, and four numbers are
        stored on that leave-out stack, with the night in the subject's
        observing night: the night's share of the library stack's total frame
        weight (``stacking.night_weight_fraction``), the noise of the stack
        without the night (``stacking.noise_without_night``), the paired
        difference from the library stack's noise
        (``stacking.noise_difference_without_night``) and the ratio the two
        stacks' effective frame counts predict
        (``stacking.subset_expected_noise_ratio``). The cost is one stack for
        every night, each close to the library stack's own cost. A stack of
        one night has nothing to leave out. A share is stored as not measured
        when the stack's weights are unknown (Siril weighted the frames
        itself).

        Parameters
        ----------
        target : `Target`
            The target.

        Returns
        -------
        result : `NightContributionResult`
            One entry for each night.

        Notes
        -----
        Raises `NotFoundError` for a target with no stack or a stack with no
        ``_inputs.json``.
        """
        from astrometricslib.pipelines.stacking.noise_records import record_night_contributions

        return record_night_contributions(
            _check_target(target), self._config, datetime.now(UTC), self._drivers
        )

    @background_job("stacking", grace_period_seconds=8.0)
    def record_odd_even_noise(
        self, target: Target, *, keep_halves: bool = False
    ) -> OddEvenDifferenceNoiseResult:
        """Store the library stack's random noise, from two halves.

        Owner:
            astrometricslib.pipelines.stacking.noise_records.record_odd_even_noise

        The frames are put in order of capture time and split into the 1st,
        3rd, ... and the 2nd, 4th, ... frame. The library stack's
        registration reference frame goes into both, and it is handed to the
        stacker as the frame to line up with, so the two half stacks have
        the same pixel grid. Each half is stacked (without the edge trim),
        the difference is taken, and its noise is measured in the library
        stack's stored empty apertures. The difference cancels what the
        frames share (sky structure, faint stars, nebula) and keeps their
        random noise, so it is the more honest noise of a deep or crowded
        field. Nine rows are stored on the library stack, beside its
        ``stacking.empty_aperture_noise_*`` rows and never replacing them
        (all ``stacking.odd_even_difference_*``), and five more say what the
        two halves share at the scale of a few pixels (the
        ``stacking.half_shared_fine_scale_*`` and ``stacking.walking_noise_*``
        metrics). The halves are deleted afterwards unless ``keep_halves``.
        The cost is two stacks of half the frames each, about one library
        stack's time in all, plus the temporary disk space Siril needs for
        each. While it runs, no library stack and no other subset job of the
        target can run. When the measurement cannot be made (a half with
        under 8 frames, no recorded registration reference, other apertures
        than the library stack's, a Siril failure), the rows are stored as
        not measured with the reason, and the result says why.

        Parameters
        ----------
        target : `Target`
            The target.
        keep_halves : `bool`, optional
            Keep the two half stacks in the ``_subsets`` folder.

        Returns
        -------
        result : `OddEvenDifferenceNoiseResult`
            The difference noise, the halves' effective frame counts and the
            library stack's own noise that the difference implies, or the
            reason there are none.

        Notes
        -----
        Raises `NotFoundError` for a target with no stack or a stack with no
        ``_inputs.json``.
        """
        from astrometricslib.pipelines.stacking.noise_records import record_odd_even_noise

        return record_odd_even_noise(
            _check_target(target), self._config, datetime.now(UTC), self._drivers, keep_halves=keep_halves
        )
