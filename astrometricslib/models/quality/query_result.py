"""What `QualityRecords.query` returns: stored measurements, with paging.

`QualityQueryResult` holds the matching measurements and says how many
matched in all, so a caller that asked for a page knows whether more remain.
With no ``limit`` it holds every match.
"""

from pydantic import BaseModel, Field

from astrometricslib.models.quality.measurement import QualityMeasurement

__all__ = ["QualityQueryResult"]


class QualityQueryResult(BaseModel):
    """The answer of `QualityRecords.query`.

    Parameters
    ----------
    rows : `list` [`QualityMeasurement`]
        The matching measurements on this page, oldest first.
    total_matching : `int`
        How many measurements matched in all.
    offset : `int`
        How many matching measurements were skipped before this page.
    truncated : `bool`
        `True` when the limit cut the list, so more matches remain.
    """

    rows: list[QualityMeasurement] = Field(default_factory=list)
    total_matching: int = 0
    offset: int = 0
    truncated: bool = False
