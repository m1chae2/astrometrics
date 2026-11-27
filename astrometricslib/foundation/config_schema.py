"""Pydantic models for validated application configuration.

Defines the structure and defaults of the processing and parallelism
sections of astrometrics.config. The equipment the file lists is read into
the core equipment models instead (`Telescope`, `Camera`, `GuideScope`; see
`astrometricslib.foundation.equipment_settings`).
"""

from pydantic import BaseModel, Field


class ProcessingConfig(BaseModel):
    """Stacking and frame-rejection settings for the processing pipeline.

    Attributes
    ----------
    siril_executable : `str` or `None`
        Path or command to invoke the Siril executable, by default
        `None`.
    path : `str`
        Library storage path, by default ``"library"``.
    frames_path : `str` or `None`
        Filesystem path where captured frames are stored -- always a
        `"frames"` subfolder of `path`, not independently
        configurable. Reported here for convenience, by default `None`.
    rejection_sigma_mode : `str`
        Rejection sigma selection mode, by default ``"adaptive"``.
    rejection_sigma_low : `float`
        Lower sigma-clipping rejection threshold, by default 3.0.
    rejection_sigma_high : `float`
        Upper sigma-clipping rejection threshold, by default 3.0.
    filter_wfwhm_percentile : `str` or `None`
        Siril ``-filter-wfwhm`` percentile setting, by default `None`.
    filter_round_percentile : `str` or `None`
        Siril ``-filter-round`` percentile setting, by default `None`.
    stack_weight : `str` or `None`
        Stack weighting strategy, by default `None` (unweighted). Siril's
        ``-weight=`` argument is only available in releases newer than the
        one Ubuntu's apt package ships.
    generate_rejmap : `bool`
        Whether to generate a rejection map alongside the stack, by
        default `True`.
    background_homogeneity_check_enabled : `bool`
        Whether to run the background homogeneity check, by default
        `True`.
    quarantine_bad_frames_enabled : `bool`
        Whether stacking moves frames with clouds or trailed stars into an
        ``_excluded`` folder before it runs, by default `True`.
    trim_noisy_stack_edges_enabled : `bool`
        Whether a finished imaging stack is trimmed back to where its edges
        are no noisier than its middle, by default `True`.
    keep_previous_stack_enabled : `bool`
        Whether a restack first moves the old stack and its pictures into a
        ``_previous`` folder, so the two can be compared, by default `True`.
    preview_star_tone_enabled : `bool`
        Whether the stack preview dims stars in proportion to their
        brightness and softens the brightest values, by default `True`.
    preview_star_width_match_enabled : `bool`
        Whether the stack preview of a colour stack sharpens a colour channel
        whose stars are wider than the green channel's, by default `True`.
    preview_colour_calibration_method : `str`
        How the preview calibrates the colours of a colour stack: ``"pcc"``
        for Siril's photometric colour calibration, with the star-based
        method as the fallback, or ``"stars"`` for the star-based method
        only. By default ``"pcc"``.
    preview_colour_calibration_enabled : `bool`
        Whether the stack preview of a colour stack first balances its
        colours from its own stars, by default `True`.
    preview_remove_green_noise_enabled : `bool`
        Whether the stack preview of a colour stack (such as one from the
        Nikon DSLR) first removes the excess green, by default `True`.
    preview_color_from_other_stack_enabled : `bool`
        Whether a monochrome stack also gets a color picture, with its color
        taken from a color stack of the same target, by default `True`.
    skip_unchanged_stacks_enabled : `bool`
        Whether stacking skips a stack whose frames, calibration frames and
        settings are the same as when it was last made, by default `True`.
    prefetch_frames : `bool`
        Whether stacking first reads every frame it will use into the
        network mount's cache, by default `False`.
    prefetch_workers : `int`
        Number of files the pre-fetch step reads at once, by default 4
        (matches the mount's ``--transfers 4``; not tuned).
    """

    siril_executable: str | None = None
    path: str = "library"
    frames_path: str | None = None
    rejection_sigma_mode: str = "adaptive"
    rejection_sigma_low: float = 3.0
    rejection_sigma_high: float = 3.0
    filter_wfwhm_percentile: str | None = None
    filter_round_percentile: str | None = None
    stack_weight: str | None = None
    generate_rejmap: bool = True
    background_homogeneity_check_enabled: bool = True
    quarantine_bad_frames_enabled: bool = True
    preview_star_tone_enabled: bool = True
    preview_remove_green_noise_enabled: bool = True
    preview_color_from_other_stack_enabled: bool = True
    preview_star_width_match_enabled: bool = True
    preview_colour_calibration_enabled: bool = True
    preview_colour_calibration_method: str = "pcc"
    keep_previous_stack_enabled: bool = True
    trim_noisy_stack_edges_enabled: bool = True
    skip_unchanged_stacks_enabled: bool = True
    prefetch_frames: bool = False
    prefetch_workers: int = 4


class ParallelismConfig(BaseModel):
    """Worker-count and priority settings for concurrent batch processing.

    target_workers and photometry_workers accept either an integer or the
    string "auto", in which case resolve_worker_counts() derives a value
    from os.cpu_count().

    Attributes
    ----------
    target_workers : `str`
        Configured outer worker count for target processing, or
        ``"auto"``, by default ``"auto"``.
    max_concurrent_jobs : `int`
        Maximum concurrent heavy jobs (Siril stacking and
        photometry/spectroscopy analysis share this one pool), by
        default 2.
    photometry_workers : `str`
        Configured worker count for photometry processing, or
        ``"auto"``, by default ``"auto"``.
    worker_niceness : `int`
        POSIX ``nice`` value applied to spawned worker processes, by
        default 10.
    """

    target_workers: str = "auto"
    max_concurrent_jobs: int = 2
    photometry_workers: str = "auto"
    worker_niceness: int = 10


class AppConfigSchema(BaseModel):
    """Top-level validated application configuration schema.

    Attributes
    ----------
    processing : `ProcessingConfig`
        Stacking and frame-rejection processing settings.
    parallelism : `ParallelismConfig`
        Worker-count and priority settings for concurrent processing.
    """

    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    parallelism: ParallelismConfig = Field(default_factory=ParallelismConfig)
