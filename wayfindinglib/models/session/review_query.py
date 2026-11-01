"""Purpose: The result of `review.query`, one model for every kind.

Description: `review.query(kind=...)` answers many kinds of question about
recorded observing nights. Each kind fills in some of the fields below and
leaves the rest empty. A caller reads a named field instead of looking
into a dictionary. When the model is turned into plain data (`model_dump`),
fields that were never filled are dropped (see `SparseResult`), so the
reply keeps the same shape the dictionary replies had before.

Three kinds answer with typed models, so they have their own fields:
``nights`` fills `night_list`, ``target_changes`` fills `target_changes`,
and ``night_quality`` fills `night_quality`. The other kinds fill the plain
data fields. That data is JSON-safe, and a reply that was too large to send
whole has had its lists and text cut (see `truncated`).
"""

from pydantic import JsonValue

from wayfindinglib.models.session.night_list import NightListEntry, TargetChange
from wayfindinglib.models.session.night_quality import NightQuality
from wayfindinglib.models.session.sparse_result import SparseResult
from wayfindinglib.models.session.telemetry import MountPointingModel

__all__ = ["ReviewQueryResult"]


class ReviewQueryResult(SparseResult):
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
    model : `MountPointingModel`, optional
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
    model: MountPointingModel | None = None
    attempts: JsonValue = None
    targets: JsonValue = None
    polar_alignment: JsonValue = None
    night_list: list[NightListEntry] | None = None
    target_changes: list[TargetChange] | None = None
    night_quality: NightQuality | None = None
