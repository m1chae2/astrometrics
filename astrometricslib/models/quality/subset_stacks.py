"""What the subset-stack jobs report.

Four jobs stack some of a target's frames, apart from the library stack, and
measure the noise of each result:

- `ProcessingPipelines.stack_subset` stacks exactly the frames it is given and
  returns a `SubsetStackResult`.
- `ProcessingPipelines.measure_noise_growth` stacks the first nights (or the
  first part of one night) and returns a `NoiseGrowthResult`.
- `ProcessingPipelines.measure_night_contributions` stacks the library stack's
  frames without one night at a time and returns a `NightContributionResult`.
- `ProcessingPipelines.measure_half_difference_noise` stacks two disjoint
  halves of the library stack's frames, subtracts them, and returns a
  `HalfDifferenceNoiseResult`.

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


class HalfDifferenceNoiseResult(BaseModel):
    """What the half-stack difference job did and measured.

    The job stacks two disjoint halves of the library stack's frames, takes
    their difference, and measures its noise in the target's sky apertures.

    Attributes
    ----------
    target_id : `str`
        The target.
    library_stack_path : `str`
        The library stack whose frames were split.
    odd_half_frames : `int`
        Frames given to the stacker for the odd half (the 1st, 3rd, ... frame
        by capture time, plus the shared frame when it is an even one).
    even_half_frames : `int`
        Frames given for the even half (the 2nd, 4th, ... frame, plus the
        shared frame when it is an odd one).
    shared_frame : `str` or `None`
        The frame in both halves: the library stack's registration reference
        frame. `None` when no split was made.
    odd_half_effective_frame_count : `float` or `None`
        The odd half's weighted frame count (`None` when its weights are
        unknown).
    even_half_effective_frame_count : `float` or `None`
        The even half's weighted frame count.
    difference_noise_stack_unit : `float` or `None`
        The noise of the difference in the stack's own units, or `None`.
    difference_noise_normalized : `float` or `None`
        The same as a fraction of the reference stars' brightness.
    difference_noise_normalized_uncertainty : `float` or `None`
        Its 1-sigma uncertainty.
    reference_flux_stack_unit : `float` or `None`
        The reference stars' brightness the normalized noise divides by, in
        the stack's units.
    aperture_count : `int` or `None`
        The empty apertures behind the noise.
    variance_factor : `float` or `None`
        The difference's variance in units of one frame's variance if all
        frames had the same noise (see
        `subset_noise_run.difference_variance_factor`), or `None`.
    shared_frame_odd_weight_fraction : `float` or `None`
        The shared frame's share of the odd half's weight.
    shared_frame_even_weight_fraction : `float` or `None`
        The shared frame's share of the even half's weight.
    model_variance_ratio : `float` or `None`
        The difference's variance in units of the library stack's variance,
        with each frame's own noise from its sky and the camera's read noise
        (see `subset_noise_run.model_variance_ratio`), or `None`.
    full_stack_equivalent_normalized : `float` or `None`
        What the difference says about the library stack's own random noise,
        as a fraction of the reference stars' brightness, if every frame had
        the same noise (see `full_stack_noise_equivalent`). Not stored in the
        database; an estimate. `None` when a weight is unknown.
    full_stack_equivalent_model_normalized : `float` or `None`
        The same, with each frame's own modeled noise (the difference divided
        by the square root of `model_variance_ratio`). `None` when a frame's
        sky rate is not stored or a weight is unknown.
    shared_fine_scale_variance_ratio : `float` or `None`
        The variance the two halves share at a few pixels, as a multiple of the
        library stack's random variance (an upper limit of the sensor pattern;
        see `half_pattern.measure_half_pattern`), or `None`.
    walking_noise_correlation_excess : `float` or `None`
        The halves' correlation at a 6 px lag along the drift minus the same
        across it, or `None`.
    walking_noise_correlation_excess_error : `float` or `None`
        Its bootstrap standard deviation.
    reason : `str`
        Why there is no measurement. Empty when there is one.
    notes : `list` [`str`]
        Plain sentences about what was done.
    rows_written : `int`
        New rows stored in the quality database.
    """

    model_config = ConfigDict(populate_by_name=True)

    target_id: str = Field(alias="targetId")
    library_stack_path: str = Field(alias="libraryStackPath")
    odd_half_frames: int = Field(default=0, alias="oddHalfFrames")
    even_half_frames: int = Field(default=0, alias="evenHalfFrames")
    shared_frame: str | None = Field(default=None, alias="sharedFrame")
    odd_half_effective_frame_count: float | None = Field(default=None, alias="oddHalfEffectiveFrameCount")
    even_half_effective_frame_count: float | None = Field(default=None, alias="evenHalfEffectiveFrameCount")
    difference_noise_stack_unit: float | None = Field(default=None, alias="differenceNoiseStackUnit")
    difference_noise_normalized: float | None = Field(default=None, alias="differenceNoiseNormalized")
    difference_noise_normalized_uncertainty: float | None = Field(
        default=None, alias="differenceNoiseNormalizedUncertainty"
    )
    reference_flux_stack_unit: float | None = Field(default=None, alias="referenceFluxStackUnit")
    aperture_count: int | None = Field(default=None, alias="apertureCount")
    shared_frame_odd_weight_fraction: float | None = Field(default=None, alias="sharedFrameOddWeightFraction")
    shared_frame_even_weight_fraction: float | None = Field(
        default=None, alias="sharedFrameEvenWeightFraction"
    )
    model_variance_ratio: float | None = Field(default=None, alias="modelVarianceRatio")
    variance_factor: float | None = Field(default=None, alias="varianceFactor")
    full_stack_equivalent_normalized: float | None = Field(
        default=None, alias="fullStackEquivalentNormalized"
    )
    full_stack_equivalent_model_normalized: float | None = Field(
        default=None, alias="fullStackEquivalentModelNormalized"
    )
    shared_fine_scale_variance_ratio: float | None = Field(default=None, alias="sharedFineScaleVarianceRatio")
    walking_noise_correlation_excess: float | None = Field(
        default=None, alias="walkingNoiseCorrelationExcess"
    )
    walking_noise_correlation_excess_error: float | None = Field(
        default=None, alias="walkingNoiseCorrelationExcessError"
    )
    reason: str = ""
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
    half_difference : `HalfDifferenceNoiseResult` or `None`
        The half-difference noise result when it was asked for.
    notes : `list` [`str`]
        Plain sentences about steps that were not done and why.
    rows_written : `int`
        New rows stored in the quality database by the subset series (the
        night contributions and the half difference count their own).
    """

    model_config = ConfigDict(populate_by_name=True)

    target_id: str = Field(alias="targetId")
    library_stack_path: str = Field(alias="libraryStackPath")
    subsets: list[SubsetStackResult] = Field(default_factory=list)
    night_contributions: NightContributionResult | None = Field(default=None, alias="nightContributions")
    half_difference: HalfDifferenceNoiseResult | None = Field(default=None, alias="halfDifference")
    notes: list[str] = Field(default_factory=list)
    rows_written: int = Field(default=0, alias="rowsWritten")
