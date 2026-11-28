"""Data structures for what the processing pipelines take and report back.

`ProcessingPipelines` holds one object per pipeline: stacking, enhancement
(the preview picture), astrometry, photometry, spectroscopy and the asteroid
search. Each run returns one of these structures, so a caller reads named
fields instead of guessing at the keys of a dictionary.

- `EnhancementSettings` holds the picture choices for one enhancement run.
- `StackResult` says which frames were chosen, where the stack went and its
  quality summary.
- `PreviewRemakeResult` says where the new preview picture went and which
  steps made it.
- `ProcessTargetResult` holds one target's result from each analysis stage
  that ran, and the target's quality summaries afterwards.
- `StackWeightPrediction` says how the stacker would weight a set of frames:
  the mode, each frame's exposure group and each frame's weight.
- `StackPrediction` says what a stack would do with its frames: which it
  would set aside and why, and which darks, flats and bias each exposure
  group would use. It is made without stacking anything.
"""

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from astrometricslib.models.quality.gate_result import GateResult
from astrometricslib.models.quality.quality_summary import StackQualitySummary
from astrometricslib.models.target import PreviewBrightObject, TargetQualitySummaries

__all__ = [
    "BrightObjectChoice",
    "BrightObjectChoices",
    "EnhancementSettings",
    "PredictedCalibration",
    "PredictedFrame",
    "PredictedGroup",
    "PreviewRemakeResult",
    "PreviewSettingsRecord",
    "PreviewSettingsUsed",
    "ProcessTargetResult",
    "StackPrediction",
    "StackResult",
    "StackWeightPrediction",
]


class StackResult(BaseModel):
    """What one stacking run (`processing.stacking.run`) chose and made."""

    target_id: str
    # "imaging" or "spectral": the two are never stacked together.
    kind: Literal["imaging", "spectral"]
    # The camera every chosen frame came from, or None if it is not recorded.
    camera: str | None = None
    frames_selected: int
    first_file: str
    last_file: str
    # Where the stack is. None when stacking made no image.
    stacked_path: str | None = None
    # The new stack's quality summary (rejected fraction, star sharpness
    # against the inputs, gates). Its warnings are listed by
    # `Jobs.query(job_id=..., detail="warnings")`.
    quality_summary: StackQualitySummary | None = None
    note: str = ""

    @property
    def provenance_activity_id(self) -> str | None:
        """The id of the provenance record of the run that made this stack.

        It is read from the quality summary, where the run stamped it, so
        nothing extra is stored. Pass it to `Jobs.query` with
        ``detail="lineage"`` and the target to see the chain of runs.

        Returns
        -------
        activity_id : `str` or `None`
            The run's id, or `None` for a run with no provenance.
        """
        if self.quality_summary is None:
            return None
        return self.quality_summary.provenance_activity_id


class BrightObjectChoice(BaseModel):
    """One nebula or galaxy SIMBAD lists inside a stack's field.

    The user may pick it as the object the preview's masked stretch protects.
    """

    # SIMBAD's main name for the object.
    simbad_id: str
    # SIMBAD's main type code (such as "HII" or "G").
    object_type: str
    # The broad kind the type maps to: "nebula" or "galaxy".
    category: str
    ra_deg: float
    dec_deg: float
    # SIMBAD's major axis in arcminutes, or None when it lists none.
    major_axis_arcmin: float | None = None


class BrightObjectChoices(BaseModel):
    """The objects a target's stack could protect with the masked stretch.

    ``objects`` is empty when the list could not be made, and ``message``
    then says why in plain words. A list read from the saved copy beside the
    stack has ``from_cache`` set. ``current`` is the choice saved on the
    target.
    """

    target_id: str
    objects: list[BrightObjectChoice] = Field(default_factory=list)
    # Why there are no objects (no plate solution, no stack, SIMBAD not
    # reached), or None when the list was made.
    message: str | None = None
    from_cache: bool = False
    # The choice saved on the target, or None when the list was made
    # without a target record.
    current: PreviewBrightObject | None = None


class PreviewSettingsUsed(BaseModel):
    """The choices a preview run really used, as recorded beside the stack."""

    # Whether Cosmic Clarity's denoise ran (or was due to run).
    denoise: bool
    # The denoise strength, from 0 to 1. None when the denoise did not run.
    denoise_strength: float | None = None
    # Whether the star toning was on.
    star_toning: bool
    # Whether the stars were stretched on their own with StarNet.
    starnet: bool = False
    # True when the stack is a bright extended object, which takes its own
    # stretch and skips GraXpert, the denoise and the star toning.
    bright_object: bool = False


class PreviewSettingsRecord(BaseModel):
    """What made a stack's preview picture (its settings record file)."""

    # When the picture was made, as an ISO 8601 time in UTC.
    made_at: str
    settings_used: PreviewSettingsUsed
    # The sentence for each step and whether it ran.
    steps_run: list[str] = Field(default_factory=list)
    # True when the run overrode the saved settings.
    custom: bool = False


class EnhancementSettings(BaseModel):
    """Picture choices for one enhancement run that replace the settings.

    Each field left as `None` uses the configured value. The values apply to
    the run they are given to and are never written to the configuration.

    Attributes
    ----------
    denoise : `bool` or `None`
        `False` skips Cosmic Clarity. `True` runs it even if the settings
        have it switched off.
    denoise_strength : `float` or `None`
        Cosmic Clarity's denoise strength, from 0 to 1.
    star_toning : `bool` or `None`
        Turn the star toning on or off.
    starnet : `bool`
        Stretch the stars and the rest of the picture on their own, using
        StarNet through Siril. It adds about 35 to 55 seconds. The star
        toning is not used with it, and a failed StarNet run gives the
        normal picture. Off by default.
    star_strength : `float` or `None`
        With ``starnet``, how much of the stars layer is blended, from 0 to
        1. Left out, 0.8.
    star_stretch : `float` or `None`
        With ``starnet``, the arcsinh strength of the stars' own stretch,
        from 1 to 100000. Larger values lift fainter stars. Left out, 1000.
    """

    model_config = ConfigDict(frozen=True)

    denoise: bool | None = None
    denoise_strength: float | None = None
    star_toning: bool | None = None
    starnet: bool = False
    star_strength: float | None = None
    star_stretch: float | None = None


class PreviewRemakeResult(BaseModel):
    """What one enhancement run (`processing.enhancement.run`) made."""

    target_id: str
    stack_path: str
    preview_path: str
    # The stretched FITS file the preview was drawn from, if one was written.
    processed_fits_path: str | None = None
    # The preview steps that ran, in order (GraXpert, stretch, denoise, ...).
    steps_run: list[str] = Field(default_factory=list)
    # Whether the app's viewer now shows the new picture for this stack.
    shown_in_viewer: bool = False
    # Copies of the old pictures, kept so the two can be compared.
    previous_pictures: list[str] = Field(default_factory=list)
    # True when the stack file itself was not touched, as it never should be.
    stack_file_unchanged: bool = True
    # What made the new picture, read from the record saved beside the stack.
    # None when the record could not be read, which means unknown.
    preview_settings: PreviewSettingsRecord | None = None
    # The color picture of a monochrome stack, with its color taken from a
    # color stack of another setup, when one was made.
    color_preview_path: str | None = None
    # One sentence on the color picture: what it was made from, or why it
    # was not made. None for a spectral stack's remake.
    color_preview_note: str | None = None
    # True when the picture was already up to date for this stack and its
    # settings, so it was kept and nothing was made (``force`` remakes it).
    kept_unchanged: bool = False


class ProcessTargetResult(BaseModel):
    """One target's results from the analysis stages that ran.

    Each stage's entry is the result its pipeline returned. A stage that had
    nothing to work on, such as spectroscopy for a target with no spectra,
    has ``{"status": "skipped", "reason": ...}`` instead.
    """

    target_id: str
    # The stages that ran, in the order they ran.
    stages_run: list[str] = Field(default_factory=list)
    # The stages that ran only because a stage the caller asked for needs
    # them and their saved result was missing or out of date (they are also
    # in `stages_run`). Only spectroscopy is ever added, before spectral
    # variation: astrometry is never run for another stage.
    prerequisites_run: list[str] = Field(default_factory=list)
    # The stages that did not run because the astrometry result they read
    # was missing or out of date, or a prerequisite could not run, with the
    # reason (such as "needs astrometry: its result is missing; run
    # processing.astrometry.run(target) first"). Each also has a "skipped"
    # entry in `results`, and the job lists the astrometry skips as warnings.
    stages_skipped: dict[str, str] = Field(default_factory=dict)
    results: dict[str, Any] = Field(default_factory=dict)
    # The target's astrometry, photometry and spectroscopy quality
    # summaries after the stages ran.
    quality: TargetQualitySummaries | None = None

    @property
    def provenance_activity_ids(self) -> dict[str, str]:
        """The provenance record ids of the analysis runs, by stage.

        They are read from the quality summaries, where each run stamped its
        id, so nothing extra is stored. Pass an id to `Jobs.query` with
        ``detail="lineage"`` and the target to see the chain of runs.

        Returns
        -------
        activity_ids : `dict` [`str`, `str`]
            The id of each stage that has one, keyed by stage name
            (``"astrometry"``, ``"photometry"`` or ``"spectroscopy"``).
        """
        if self.quality is None:
            return {}
        summaries = {
            "astrometry": self.quality.astrometry,
            "photometry": self.quality.photometry,
            "spectroscopy": self.quality.spectroscopy,
        }
        return {
            stage: summary.provenance_activity_id
            for stage, summary in summaries.items()
            if summary is not None and summary.provenance_activity_id
        }


class StackWeightPrediction(BaseModel):
    """How the stacker would weight a set of frames."""

    # The weighting mode: "equal", "noise", "wfwhm" (star width), "group" (the
    # frames fall in more than one exposure group), or the configured mode's
    # own text for a mode the forecast does not know.
    mode: str
    # For each frame, the length of the exposure group it would land in, in
    # seconds. Every entry is None when the frames stay in one group because
    # a length cannot be read or no length has enough frames. A frame that is
    # left out (see `left_out_reasons`) shows its own length.
    folded_exposure_lengths: list[float | None] = Field(default_factory=list)
    # For each frame, None when the stacker would stack it, or a plain
    # sentence when it would leave the frame out because its exposure length
    # has too few frames and no group within the fold ratio limit. Empty when
    # the prediction was made without this check. A frame left out has weight
    # 0 in `weights`.
    left_out_reasons: list[str | None] = Field(default_factory=list)
    # For each frame, the weight it would carry (the star-width weights have
    # mean 1; the others are not scaled). None when they are unknown.
    weights: list[float] | None = None
    # Why the weights are unknown. Empty when they are known.
    reasons: list[str] = Field(default_factory=list)


class PredictedFrame(BaseModel):
    """One frame a stack would set aside, or keep with a warning."""

    path: str
    # The file name, for a list.
    file: str
    # Why, in one sentence with the frame's value and the limit.
    reason: str
    # "clouded", "trailed" or "off_field" (the quarantine step), "uneven_sky"
    # (kept with a warning), "minority_gain", "already_set_aside",
    # "background_split" or "exposure_not_foldable" (its exposure length has
    # too few frames and no group within the fold ratio limit).
    kind: str


class PredictedCalibration(BaseModel):
    """The calibration frames of one kind that an exposure group would use."""

    # "dark", "bias", "flat" or "flat_bias" (the bias made for the flats).
    kind: str
    # How many frames the library would use. Zero when none fits.
    frame_count: int
    # The distinct folders the frames lie in, so a person can see which set
    # of the library was chosen (for example
    # ``darks/ZWO ASI 533MM Pro/0.0/30.0``).
    folders: list[str] = Field(default_factory=list)
    # The gain and offset of the chosen frames, such as "gain 100, offset 10".
    # Empty when the library does not say.
    settings: str = ""
    # Mismatches the library worked around (the frames are still used), such
    # as a dark of another temperature or flats of another offset.
    flags: list[str] = Field(default_factory=list)
    # Mismatches that stop the frames from being used (a binning mismatch).
    blocking_flags: list[str] = Field(default_factory=list)


class PredictedGroup(BaseModel):
    """One exposure group of a stack and the calibration it would get."""

    exposure_s: float = Field(validation_alias=AliasChoices("exposure_s", "exposure_seconds"))
    # The observing night, for a spectral group split by night; else None.
    night: str | None = None
    frames_kept: int
    first_file: str
    last_file: str
    filter_name: str
    gain: str
    offset: str
    binning: str
    temperature_c: float | None = None
    dark: PredictedCalibration
    bias: PredictedCalibration
    flat: PredictedCalibration
    # Only when the flats need a bias of their own; else None.
    flat_bias: PredictedCalibration | None = None
    # Every flag and note of this group's calibration in one list.
    warnings: list[str] = Field(default_factory=list)


class StackPrediction(BaseModel):
    """What a stack would do with its frames, made without stacking.

    The frame checks and the calibration choice are the ones the stack runs
    (`frame_screening.screen_frames` and `batch_calibration`), so the answer
    and the stack cannot disagree. Nothing is stacked, moved, stored or saved.
    """

    target_id: str
    kind: Literal["imaging", "spectral"]
    camera: str | None = None
    frames_selected: int
    frames_kept: int
    # The file names of the first and last chosen frame ("" when none).
    first_file: str = ""
    last_file: str = ""
    # False when only the frame choice was made (``checks=False``): nothing
    # below was looked at, so no frame is set aside or flagged.
    checks_run: bool = True
    set_aside: list[PredictedFrame] = Field(default_factory=list)
    flagged: list[PredictedFrame] = Field(default_factory=list)
    groups: list[PredictedGroup] = Field(default_factory=list)
    # Checks that failed, were not run, or have a note: one sentence each.
    warnings: list[str] = Field(default_factory=list)
    # What each frame check found, in the order they ran.
    gates: list[GateResult] = Field(default_factory=list)
    # Frames the quarantine check could not read. They stay in the stack.
    unreadable: list[str] = Field(default_factory=list)
    # True when no frame is left, so a stack would fail.
    nothing_left: bool = False
    note: str = "Nothing was stacked, moved, stored or saved."
