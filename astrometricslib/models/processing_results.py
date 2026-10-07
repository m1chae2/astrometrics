"""Data structures for what the processing methods report back.

`ProcessingPipelines` stacks a target, makes its preview picture again, and
runs the analysis stages. Each of those returns one of these structures, so a
caller (a script, the backend, an AI client) reads named fields instead of
guessing at the keys of a dictionary.

- `StackResult` says which frames were chosen and, unless only a plan was
  asked for, where the stack went, its quality summary, and whether it was
  flagged.
- `PreviewRemakeResult` says where the new preview picture went and which
  steps made it.
- `ProcessTargetResult` holds one target's result from each analysis stage
  that ran, and the target's quality summaries afterwards.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from astrometricslib.models.quality_summary import StackQualitySummary
from astrometricslib.models.target import TargetQualitySummaries

__all__ = ["PreviewRemakeResult", "ProcessTargetResult", "StackResult"]


class StackResult(BaseModel):
    """What one call of `ProcessingPipelines.stack` chose and made."""

    target_id: str
    # "imaging" or "spectral": the two are never stacked together.
    kind: Literal["imaging", "spectral"]
    # The camera every chosen frame came from, or None if it is not recorded.
    camera: str | None = None
    frames_selected: int
    first_file: str
    last_file: str
    # True when only the frame choice was reported and nothing was stacked.
    plan_only: bool = False
    # Where the stack is. None for a plan, or when stacking made no image.
    stacked_path: str | None = None
    # Whether the stack's quality summary flags a problem. None for a plan.
    flagged: bool | None = None
    flag_reasons: list[str] = Field(default_factory=list)
    # The new stack's quality summary (rejected fraction, star sharpness
    # against the inputs, flags). None for a plan.
    quality_summary: StackQualitySummary | None = None
    note: str = ""


class PreviewRemakeResult(BaseModel):
    """What one call of `ProcessingPipelines.remake_preview` made."""

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


class ProcessTargetResult(BaseModel):
    """One target's results from the analysis stages that ran.

    Each stage's entry is the result its pipeline returned. A stage that had
    nothing to work on, such as spectroscopy for a target with no spectra,
    has ``{"status": "skipped", "reason": ...}`` instead.
    """

    target_id: str
    # The stages that ran, in the order they ran.
    stages_run: list[str] = Field(default_factory=list)
    results: dict[str, Any] = Field(default_factory=dict)
    # The target's astrometry, photometry and spectroscopy quality
    # summaries after the stages ran.
    quality: TargetQualitySummaries | None = None
