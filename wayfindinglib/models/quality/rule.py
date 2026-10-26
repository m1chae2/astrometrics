"""Purpose: The row shape of the quality rule table.

Description: A quality rule says when a statement is made and what it says.
Each row of the rule table is a `QualityRule`. The row names the metrics it
reads, each pinned to one definition version, so a change in what a metric
means makes the rule say "remeasure" instead of reading numbers of another
meaning. A row also names the validation register entry that backs it, and
its confidence is the status label of that entry.

The same checks as for a statement apply: a warning has a severity, and a
warning or recommendation names one action class. A constraint or a note
about missing data names none, because the library never advises hardware
and never hides missing data.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from astrometricslib import QualityLimit
from wayfindinglib.models.quality.statements import ActionClass, Confidence, Severity, StatementKind


class MetricRequirement(BaseModel):
    """One metric a rule reads, pinned to one definition version.

    Parameters
    ----------
    name : `str`
        A registered metric name, such as ``"frame.star_width_moffat_arcsec"``.
    definition_version : `int`
        The definition version the rule was written against. Rows measured
        under another version are not used.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    definition_version: int = Field(ge=1, alias="definitionVersion")


class QualityRule(BaseModel):
    """One row of the rule table.

    Parameters
    ----------
    name : `str`
        A short stable name, such as ``"overexposed_setting"``.
    statement_kind : `StatementKind`
        The kind of statement the rule makes.
    action_class : `ActionClass` or `None`
        The kind of change it asks for. Required for a warning or a
        recommendation, and ``None`` otherwise.
    severity : `Severity` or `None`
        The severity of the warning it makes. Required for a warning.
    requires : `list` [`MetricRequirement`]
        The metrics it reads, each at one definition version.
    limits : `list` [`QualityLimit`]
        The limits it compares them with.
    register_entry : `str`
        The validation register entry that backs the rule, such as
        ``"WAY-01"``, or a design-estimate row such as ``"WAY-D01"``.
    confidence : `Confidence`
        The status label of that entry.
    wording : `str`
        A template for the message, with the numbers filled in when a
        statement is built.
    plain_wording : `str`
        A template for the plain message, in everyday words: one or two short
        sentences that say what the finding means for the observer and what
        to do. It uses the same fields as ``wording`` (and may use more, which
        the code that builds the statement passes). It avoids statistics
        terms, uses forecast words (``"we expect"``, ``"roughly"``) when the
        rule is a design estimate, and says when a value comes from a model.
    produces : `str`
        What the rule's proposed action becomes in the observer's plan:
        ``"package_row"`` (an exposure row of a suggested observation
        package), ``"checklist_item"`` (an item of the assigned session's
        checklist, such as taking flats or darks), or ``"none"`` (a setting
        change or a note, which only the statement shows).
    purpose : `str`
        Why the rule exists, in one plain sentence.
    computed_by : `str` or `None`
        The module that makes the statement when it is not a plain limit
        comparison, such as ``"noise_growth"``. ``None`` for a plain
        comparison.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    statement_kind: StatementKind = Field(alias="statementKind")
    action_class: ActionClass | None = Field(default=None, alias="actionClass")
    severity: Severity | None = None
    requires: list[MetricRequirement] = Field(default_factory=list)
    limits: list[QualityLimit] = Field(default_factory=list)
    register_entry: str = Field(min_length=1, alias="registerEntry")
    confidence: Confidence
    wording: str
    plain_wording: str = Field(default="", alias="plainWording")
    produces: Literal["package_row", "checklist_item", "none"] = "none"
    purpose: str
    computed_by: str | None = Field(default=None, alias="computedBy")

    @model_validator(mode="after")
    def _check_fields_fit_the_kind(self) -> Self:
        """Check that the severity and action class fit the statement kind.

        Returns
        -------
        rule : `QualityRule`
            This rule, unchanged.

        Raises
        ------
        ValueError
            If a warning has no severity, a warning or recommendation has no
            action class, or a constraint or insufficient-data rule has an
            action class.
        """
        if self.statement_kind == StatementKind.WARNING and self.severity is None:
            raise ValueError("A warning rule must have a severity.")
        if self.statement_kind in (StatementKind.WARNING, StatementKind.RECOMMENDATION):
            if self.action_class is None:
                raise ValueError(f"A {self.statement_kind.value} rule must name one action class.")
        elif self.action_class is not None:
            raise ValueError(f"A {self.statement_kind.value} rule has no action class.")
        return self
