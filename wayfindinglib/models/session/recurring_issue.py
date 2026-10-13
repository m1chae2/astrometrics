"""Purpose: Recurring Issue Model.

Description: A problem that the per-night analyses report on more than one
night. One bad night can be weather. The same finding on many nights points
at the equipment or the routine, which is where a change helps.
"""

from pydantic import BaseModel, ConfigDict, Field

from wayfindinglib.models.session.session_quality import RecommendationKind, RecommendationSeverity


class RecurringIssue(BaseModel):
    """One kind of finding that repeats across nights.

    Attributes
    ----------
    pipeline : `str`
        The analysis that reports it, ``"guiding"`` or ``"capture"``.
    kind : `RecommendationKind`
        What the finding is about.
    worst_severity : `RecommendationSeverity`
        The most serious severity it had on any night.
    nights : `list` [`str`]
        The nights it was reported on, oldest first.
    nights_analysed : `int`
        Nights the analysis covered, whether or not they had the finding.
    share : `float`
        `len(nights)` divided by `nights_analysed`.
    example : `str`
        The message from the most recent night, as an example of what it says.
    """

    model_config = ConfigDict(populate_by_name=True)

    pipeline: str
    kind: RecommendationKind
    worst_severity: RecommendationSeverity = Field(alias="worstSeverity")
    nights: list[str]
    nights_analysed: int = Field(alias="nightsAnalysed")
    share: float
    example: str
