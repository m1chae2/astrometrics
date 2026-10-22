"""The declaration of one quality metric, as kept in the metric registry.

Each pipeline declares its metrics as `MetricSpec` records. A record says
what the metric means, how it is estimated, which way is better, what kind of
uncertainty it has, and why it exists (its `MetricPurpose`). Readers such as
`wayfindinglib`, the backend and the MCP servers use the registry to
interpret stored measurements.

A `MetricSpec` is immutable. Changing a metric's estimator, sample or
constants means raising its `definition_version`.
"""

import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from astrometricslib.models.quality.measurement import SubjectLevel


class Checkpoint(StrEnum):
    """The point in processing where a metric is measured."""

    FRAME = "frame"
    CALIBRATION = "calibration"
    STACKING = "stacking"
    ASTROMETRY = "astrometry"
    PHOTOMETRY = "photometry"
    SPECTROSCOPY = "spectroscopy"
    ASTEROID_DETECTION = "asteroid_detection"


class Direction(StrEnum):
    """Which direction of a metric is better."""

    LOWER = "lower"
    HIGHER = "higher"
    NONE = "none"


class UncertaintyKind(StrEnum):
    """What kind of uncertainty a metric reports."""

    STATISTICAL_1_SIGMA = "statistical_1_sigma"
    SYSTEMATIC_BOUND = "systematic_bound"
    NONE = "none"


class MetricPurpose(StrEnum):
    """Why a metric exists."""

    DECISION = "decision"
    CONTEXT = "context"
    EXPLORATORY = "exploratory"


class MetricSpec(BaseModel):
    """The declaration of one registered metric.

    Parameters
    ----------
    name : `str`
        The metric name, such as ``"frame.star_width_moffat_arcsec"``.
    definition_version : `int`
        Raised whenever the estimator, sample or constants change.
    unit : `str`
        The unit every measurement of this metric must use.
    checkpoint : `Checkpoint`
        Where in processing the metric is measured.
    levels : `frozenset` of `SubjectLevel`
        The subject levels the metric applies to.
    better : `Direction`
        Which direction is better.
    meaning : `str`
        One line in plain words.
    decisions : `tuple` of `str`
        The decisions the metric informs. Documentation only.
    estimator : `str`
        How the value is estimated.
    sample_definition : `str`
        What is sampled, such as which stars.
    band : `str`
        The filter or color channel the metric applies to.
    computed_by : `str`
        Dotted path of the function that computes it, relative to
        ``astrometricslib``.
    uncertainty_kind : `UncertaintyKind`
        The kind of uncertainty reported.
    uncertainty_method : `str`
        How the uncertainty is found.
    register_entry : `str` or `None`
        The validation register entry an auditor checks.
    purpose : `MetricPurpose`
        Why the metric exists.
    purpose_reason : `str`
        Required for a decision or context metric.
    exploratory_question : `str`
        The question an exploratory metric answers. Required for exploratory.
    review_date : `datetime.date` or `None`
        When an exploratory metric must be promoted or removed. Required for
        exploratory.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    name: str
    definition_version: int = Field(alias="definitionVersion")
    unit: str
    checkpoint: Checkpoint
    levels: frozenset[SubjectLevel]
    better: Direction
    meaning: str
    decisions: tuple[str, ...] = ()
    estimator: str
    sample_definition: str = Field(alias="sampleDefinition")
    band: str
    computed_by: str = Field(alias="computedBy")
    uncertainty_kind: UncertaintyKind = Field(alias="uncertaintyKind")
    uncertainty_method: str = Field(alias="uncertaintyMethod")
    register_entry: str | None = Field(default=None, alias="registerEntry")
    purpose: MetricPurpose
    purpose_reason: str = Field(default="", alias="purposeReason")
    exploratory_question: str = Field(default="", alias="exploratoryQuestion")
    review_date: datetime.date | None = Field(default=None, alias="reviewDate")

    @model_validator(mode="after")
    def _check_purpose_fields(self) -> Self:
        """Require the fields that go with the metric's purpose.

        Returns
        -------
        spec : `MetricSpec`
            This spec, unchanged.

        Raises
        ------
        ValueError
            If a decision or context metric has no reason, or an exploratory
            metric has no question or review date.
        """
        if self.purpose == MetricPurpose.EXPLORATORY:
            if not self.exploratory_question.strip():
                raise ValueError("An exploratory metric must state its question.")
            if self.review_date is None:
                raise ValueError("An exploratory metric must have a review date.")
        elif not self.purpose_reason.strip():
            raise ValueError(f"A {self.purpose.value} metric must give a purpose_reason.")
        return self
