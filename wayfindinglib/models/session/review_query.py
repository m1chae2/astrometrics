"""Purpose: The result of `review.query`, one model for every kind.

Description: `review.query(kind=...)` answers many kinds of question about
recorded observing nights. Each kind fills in some of the fields below and
leaves the rest empty. A caller reads a named field instead of looking
into a dictionary. When the model is turned into plain data (`model_dump`),
fields that were left empty are dropped, so the reply keeps the same shape
the dictionary replies had before.

Three kinds answer with typed models, so they have their own fields:
``nights`` fills `night_list`, ``target_changes`` fills `target_changes`,
and ``night_quality`` fills `night_quality`. The other kinds fill the plain
data fields. That data is JSON-safe, and a reply that was too large to send
whole has had its lists and text cut (see `truncated`).
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue, model_serializer

from wayfindinglib.models.session.night_list import NightListEntry, TargetChange
from wayfindinglib.models.session.night_quality import NightQuality

__all__ = ["ReviewQueryResult"]


class ReviewQueryResult(BaseModel):
    """The answer to one `review.query` question.

    Attributes
    ----------
    kind : `str`
        The kind that was asked.
    session_id : `str`, optional
        The night the answer covers, when one night was asked for.
    nights_covered : `str`, optional
        In words, how many recent nights a summary covers.
    total, shown : `int`, optional
        How many rows exist and how many the reply holds.
    truncated : `bool`
        Whether the reply had to be cut to stay under the size limit.
    truncation_note : `str`, optional
        What was cut, when `truncated` is true.
    fields : `list` [`str`], optional
        If nothing fitted, the names of the fields the full answer had.
    analysis : `JsonValue`, optional
        One night's analysis, or the sky-coverage analysis.
    issues : `JsonValue`, optional
        The findings that recur (kind ``recurring_issues``).
    nights : `JsonValue`, optional
        One summary row per recent night (kinds ``capture``, ``guiding``).
    runs : `JsonValue`, optional
        Guiding runs (kind ``guiding_runs``).
    sessions : `JsonValue`, optional
        Ekos sessions, or the nights with alignment data (kinds
        ``ekos_sessions`` and ``alignment``).
    session : `JsonValue`, optional
        One Ekos session (kind ``ekos_sessions`` with ``ekos_file_id``).
    model : `JsonValue`, optional
        The fitted pointing model (kind ``pointing_model``).
    attempts, targets, polar_alignment : `JsonValue`, optional
        One night's alignment attempts, the same grouped by target, and
        its latest polar alignment (kind ``alignment`` with ``session_id``).
    night_list : `list` [`NightListEntry`], optional
        The recorded nights (kind ``nights``).
    target_changes : `list` [`TargetChange`], optional
        The slews to new targets (kind ``target_changes``).
    night_quality : `NightQuality`, optional
        The review of one night (kind ``night_quality``).
    """

    model_config = ConfigDict(extra="allow")

    kind: str
    session_id: str | None = None
    nights_covered: str | None = None
    total: int | None = None
    shown: int | None = None
    truncated: bool = False
    truncation_note: str | None = None
    fields: list[str] | None = None
    analysis: JsonValue = None
    issues: JsonValue = None
    nights: JsonValue = None
    runs: JsonValue = None
    sessions: JsonValue = None
    session: JsonValue = None
    model: JsonValue = None
    attempts: JsonValue = None
    targets: JsonValue = None
    polar_alignment: JsonValue = None
    night_list: list[NightListEntry] | None = None
    target_changes: list[TargetChange] | None = None
    night_quality: NightQuality | None = None

    @model_serializer(mode="wrap")
    def _drop_empty_fields(self, handler: Any) -> dict[str, Any]:
        """Leave out the fields a kind did not fill in.

        Parameters
        ----------
        handler : `Any`
            Pydantic's own serializer for the model.

        Returns
        -------
        data : `dict` [`str`, `Any`]
            The fields that hold a value. A false `truncated` is dropped
            too, as the dictionary replies never carried it.
        """
        data = handler(self)
        return {
            key: value
            for key, value in data.items()
            if value is not None and not (key == "truncated" and value is False)
        }
