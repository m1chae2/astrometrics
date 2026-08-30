"""Pydantic models for validated application configuration.

Defines the structure and defaults for astrometrics.config.
"""

from pydantic import BaseModel, Field


class CameraConfig(BaseModel):
    """Configuration entry for a named camera and its known models.

    Attributes
    ----------
    name : `str`
        Identifying name for this camera configuration entry.
    models : `list` of `str`
        Known model identifiers associated with this camera, by
        default an empty list.
    default_primary_camera : `str` or `None`
        Name of the model to use as the default primary camera, by
        default `None`.
    """

    name: str
    models: list[str] = Field(default_factory=list)
    default_primary_camera: str | None = None

    # Nested parameters for specific cameras are handled dynamically
    # but we can define common fields if needed.


class TelescopeConfig(BaseModel):
    """Connection and geometry settings for the telescope mount.

    Attributes
    ----------
    hostname : `str`
        Hostname or address of the telescope control host, by default
        ``"localhost"``.
    focal_length_mm : `float`
        Telescope focal length in millimeters, by default 0.0.
    focal_ratio : `float`
        Telescope focal ratio (f-number), by default 0.0.
    remote_pictures_path : `str`
        Remote filesystem path where captured pictures are stored, by
        default ``"/home/stellarmate/Pictures"``.
    allow_commands : `bool`
        Whether remote command execution against the telescope host
        is permitted, by default `False`.
    """

    hostname: str = "localhost"
    focal_length_mm: float = 0.0
    focal_ratio: float = 0.0
    remote_pictures_path: str = "/home/stellarmate/Pictures"
    allow_commands: bool = False


class ProcessingConfig(BaseModel):
    """Stacking and frame-rejection settings for the processing pipeline.

    Attributes
    ----------
    siril_executable : `str` or `None`
        Path or command to invoke the Siril executable, by default
        `None`.
    path : `str`
        Library index storage path, by default ``"libraryIndex"``.
    frames_path : `str` or `None`
        Filesystem path where captured frames are stored, by default
        `None`.
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
    """

    siril_executable: str | None = None
    path: str = "libraryIndex"
    frames_path: str | None = None
    rejection_sigma_mode: str = "adaptive"
    rejection_sigma_low: float = 3.0
    rejection_sigma_high: float = 3.0
    filter_wfwhm_percentile: str | None = None
    filter_round_percentile: str | None = None
    stack_weight: str | None = None
    generate_rejmap: bool = True
    background_homogeneity_check_enabled: bool = True


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
    siril_concurrency : `int`
        Maximum concurrent Siril processes, by default 2.
    photometry_workers : `str`
        Configured worker count for photometry processing, or
        ``"auto"``, by default ``"auto"``.
    worker_niceness : `int`
        POSIX ``nice`` value applied to spawned worker processes, by
        default 10.
    analysis_concurrency : `int`
        Maximum concurrent analysis processes, by default 2.
    """

    target_workers: str = "auto"
    siril_concurrency: int = 2
    photometry_workers: str = "auto"
    worker_niceness: int = 10
    analysis_concurrency: int = 2


class AppConfigSchema(BaseModel):
    """Top-level validated application configuration schema.

    Attributes
    ----------
    telescope : `TelescopeConfig`
        Telescope connection and geometry settings.
    processing : `ProcessingConfig`
        Stacking and frame-rejection processing settings.
    cameras : `list` of `CameraConfig`
        Configured camera entries, by default an empty list.
    parallelism : `ParallelismConfig`
        Worker-count and priority settings for concurrent processing.
    """

    telescope: TelescopeConfig = Field(default_factory=TelescopeConfig)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    cameras: list[CameraConfig] = Field(default_factory=list)
    parallelism: ParallelismConfig = Field(default_factory=ParallelismConfig)
