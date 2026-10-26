"""What `QualityRecords.resolve_rows` returns: the file behind each row.

A stored quality row names its subject (a frame, a stack or a run) by a stable
id, never by a path on disk. A viewer needs a path to open the picture. These
structures hold the answer the library gives for each row id, so the viewer
does not have to build a path from the id or work out which ids are missing:

- `QualityRowFile` is one row, with its file path or the reason it has none.
- `QualityRowResolution` is the whole answer: one entry for each row found, and
  the ids that were not found.
"""

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["QualityRowFile", "QualityRowResolution"]


class QualityRowFile(BaseModel):
    """The file behind one stored quality row.

    Parameters
    ----------
    row_id : `int`
        The store's row id, as the caller gave it.
    level : `str`
        ``"frame"``, ``"stack"`` or ``"run"``.
    target_id : `str` or `None`
        The target the row belongs to.
    subject_id : `str`
        The row's stable subject id.
    path : `str` or `None`
        The picture's file path, in the form the app's image reads accept.
        `None` when the row has no picture or it could not be found.
    reason : `str`
        Why `path` is `None`. Empty when a path is given.
    """

    model_config = ConfigDict(populate_by_name=True)

    row_id: int = Field(alias="rowId")
    level: str
    target_id: str | None = Field(default=None, alias="targetId")
    subject_id: str = Field(alias="subjectId")
    path: str | None = None
    reason: str = ""


class QualityRowResolution(BaseModel):
    """The answer to "which pictures do these rows describe?".

    Parameters
    ----------
    rows : `list` [`QualityRowFile`]
        One entry for each row id that was found, in the order asked.
    not_found_row_ids : `list` [`int`]
        The ids that no stored row has, in the order asked.
    """

    model_config = ConfigDict(populate_by_name=True)

    rows: list[QualityRowFile] = Field(default_factory=list)
    not_found_row_ids: list[int] = Field(default_factory=list, alias="notFoundRowIds")
