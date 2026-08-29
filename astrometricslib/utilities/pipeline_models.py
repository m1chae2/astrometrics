"""Domain models for tracking background processing tasks.

These models provide type safety and schema validation for job
tracking in memory and within the storage layer, within the
Astrometrics ecosystem.
"""

from pydantic import BaseModel, ConfigDict, Field


class ProcessingJob(BaseModel):
    """Represents a background scientific processing or ingestion task.

    Enforces validation on the job's lifecycle status, progress
    metrics, and associated metadata.

    Attributes
    ----------
    id : `str`
        Unique identifier for this processing job.
    target_id : `str`
        Identifier of the target this job operates on.
    job_type : `str`
        Type of job being tracked (e.g. stacking, analysis).
    status : `str`
        Current lifecycle status of the job.
    progress_current : `int`
        Current progress count toward `progress_total`, by default 0.
    progress_total : `int`
        Total expected progress count, by default 0.
    message : `str` or `None`
        Optional human-readable status message, by default `None`.
    log_file_path : `str` or `None`
        Filesystem path to the job's log file, by default `None`.
    created_at : `str` or `None`
        Timestamp the job was created, by default `None`.
    updated_at : `str` or `None`
        Timestamp the job was last updated, by default `None`.
    completed_at : `str` or `None`
        Timestamp the job completed, by default `None`.
    """

    model_config = ConfigDict(populate_by_name=True)
    id: str
    target_id: str = Field(..., alias="targetId")
    job_type: str = Field(..., alias="jobType")
    status: str
    progress_current: int = Field(0, alias="progressCurrent")
    progress_total: int = Field(0, alias="progressTotal")
    message: str | None = None
    log_file_path: str | None = Field(None, alias="logFilePath")
    created_at: str | None = Field(None, alias="createdAt")
    updated_at: str | None = Field(None, alias="updatedAt")
    completed_at: str | None = Field(None, alias="completedAt")


class ProcessStatus(BaseModel):
    """High-level execution status of a processing pipeline run.

    Includes the active job identifier and references to generated
    logs or outputs.

    Attributes
    ----------
    status : `str`
        Current execution status of the pipeline run.
    target_id : `str` or `None`
        Identifier of the target being processed, by default `None`.
    job_id : `str` or `None`
        Identifier of the active `ProcessingJob`, by default `None`.
    expected_output : `str` or `None`
        Path where the run's output is expected to be written, by
        default `None`.
    log_file : `str` or `None`
        Filesystem path to the run's log file, by default `None`.
    """

    model_config = ConfigDict(populate_by_name=True)
    status: str
    target_id: str | None = Field(None, alias="targetId")
    job_id: str | None = Field(None, alias="jobId")
    expected_output: str | None = Field(None, alias="expectedOutput")
    log_file: str | None = Field(None, alias="logFile")
