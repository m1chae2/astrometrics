"""What the subset-stack jobs report.

Two jobs stack some of a target's frames, apart from the library stack, and
measure the noise of each result:

- `ProcessingPipelines.stack_subset` stacks exactly the frames it is given and
  returns a `SubsetStackResult`.
- `ProcessingPipelines.measure_noise_growth` stacks the first nights (or the
  first part of one night) and returns a `NoiseGrowthResult`.
- `ProcessingPipelines.measure_night_contributions` stacks the library stack's
  frames without one night at a time and returns a `NightContributionResult`.

The measurements themselves go to the quality database. These records report
what was done, so a caller can tell a step that was skipped from one that
failed.
"""

from pydantic import BaseModel, ConfigDict, Field


class SubsetStackResult(BaseModel):
    """One subset stack and what was measured on it.

    Attributes
    ----------
    target_id : `str`
        The target the frames belong to.
    label : `str`
        The subset's label. It names the subset's folder.
    stack_path : `str` or `None`
        The subset stack in the target's ``_subsets`` folder, or `None` when
        the stacker made none.
    frames_submitted : `int`
        How many frames were given to the stacker.
    frames_stacked : `int`
        How many frames went into the stack (the members; this can be fewer
        than were submitted when the stacker dropped some).
    observing_night_ids : `list` [`str`]
        The observing nights of the submitted frames, sorted.
    effective_frame_count : `float` or `None`
        The weighted frame count (`None` when the weights are unknown).
    effective_time_seconds : `float` or `None`
        The weighted exposure time (`None` when the weights are unknown).
    normalized_noise : `float` or `None`
        The empty-aperture noise as a fraction of the reference stars'
        brightness, or `None` when it could not be measured.
    normalized_noise_uncertainty : `float` or `None`
        Its 1-sigma uncertainty.
    noise_reason : `str`
        Why there is no noise value. Empty when there is one.
    expected_noise_ratio : `float` or `None`
        The ratio of this stack's noise to the library stack's that the two
        stacks' own effective frame counts predict.
    stack_error : `str`
        Why the stacker failed, when it did. Empty otherwise.
    rows_written : `int`
        New rows stored in the quality database.
    """

    model_config = ConfigDict(populate_by_name=True)

    target_id: str = Field(alias="targetId")
    label: str
    stack_path: str | None = Field(default=None, alias="stackPath")
    frames_submitted: int = Field(default=0, alias="framesSubmitted")
    frames_stacked: int = Field(default=0, alias="framesStacked")
    observing_night_ids: list[str] = Field(default_factory=list, alias="observingNightIds")
    effective_frame_count: float | None = Field(default=None, alias="effectiveFrameCount")
    effective_time_seconds: float | None = Field(default=None, alias="effectiveTimeSeconds")
    normalized_noise: float | None = Field(default=None, alias="normalizedNoise")
    normalized_noise_uncertainty: float | None = Field(default=None, alias="normalizedNoiseUncertainty")
    noise_reason: str = Field(default="", alias="noiseReason")
    expected_noise_ratio: float | None = Field(default=None, alias="expectedNoiseRatio")
    stack_error: str = Field(default="", alias="stackError")
    rows_written: int = Field(default=0, alias="rowsWritten")


class NightContribution(BaseModel):
    """One observing night's share of the stack and its leave-out stack.

    Attributes
    ----------
    night_id : `str`
        The observing night.
    frame_count : `int`
        How many of the library stack's frames came from this night.
    weight_share : `float` or `None`
        The night's share of the library stack's total frame weight, or
        `None` when the weights are unknown.
    weight_reason : `str`
        Why there is no share. Empty when there is one.
    subset : `SubsetStackResult` or `None`
        The stack without this night, or `None` when it was not made (a note
        on the result says why).
    """

    model_config = ConfigDict(populate_by_name=True)

    night_id: str = Field(alias="nightId")
    frame_count: int = Field(default=0, alias="frameCount")
    weight_share: float | None = Field(default=None, alias="weightShare")
    weight_reason: str = Field(default="", alias="weightReason")
    subset: SubsetStackResult | None = None


class NightContributionResult(BaseModel):
    """What the each-night's-contribution job did.

    Attributes
    ----------
    target_id : `str`
        The target.
    library_stack_path : `str`
        The library stack the nights were taken from.
    nights : `list` [`NightContribution`]
        One entry for each observing night of the library stack's frames.
    notes : `list` [`str`]
        Plain sentences about anything not done and why.
    rows_written : `int`
        New rows stored in the quality database.
    """

    model_config = ConfigDict(populate_by_name=True)

    target_id: str = Field(alias="targetId")
    library_stack_path: str = Field(alias="libraryStackPath")
    nights: list[NightContribution] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    rows_written: int = Field(default=0, alias="rowsWritten")


class NoiseGrowthResult(BaseModel):
    """What the noise-against-frame-count job did.

    Attributes
    ----------
    target_id : `str`
        The target.
    library_stack_path : `str`
        The library stack the subsets were taken from.
    subsets : `list` [`SubsetStackResult`]
        The subset stacks, smallest first.
    night_contributions : `NightContributionResult` or `None`
        The each-night's-contribution result when it was asked for.
    notes : `list` [`str`]
        Plain sentences about steps that were not done and why.
    rows_written : `int`
        New rows stored in the quality database by the subset series (the
        night contributions count their own).
    """

    model_config = ConfigDict(populate_by_name=True)

    target_id: str = Field(alias="targetId")
    library_stack_path: str = Field(alias="libraryStackPath")
    subsets: list[SubsetStackResult] = Field(default_factory=list)
    night_contributions: NightContributionResult | None = Field(default=None, alias="nightContributions")
    notes: list[str] = Field(default_factory=list)
    rows_written: int = Field(default=0, alias="rowsWritten")
