"""What `TargetCatalog.delete_images` returns: files removed and not.

- `FailedDeletion` is one file that could not be removed, with the reason.
- `ImageDeletionResult` is the whole answer.
"""

from pydantic import BaseModel, Field

__all__ = ["FailedDeletion", "ImageDeletionResult"]


class FailedDeletion(BaseModel):
    """One file that could not be removed.

    Parameters
    ----------
    path : `str`
        The file's path.
    reason : `str`
        Why the file could not be removed.
    """

    path: str
    reason: str


class ImageDeletionResult(BaseModel):
    """The outcome of deleting some image files.

    Parameters
    ----------
    deleted : `list` [`str`]
        The paths removed. A path that was already gone counts as removed.
    failed : `list` [`FailedDeletion`]
        Each path that could not be removed, with the reason.
    """

    deleted: list[str] = Field(default_factory=list)
    failed: list[FailedDeletion] = Field(default_factory=list)
