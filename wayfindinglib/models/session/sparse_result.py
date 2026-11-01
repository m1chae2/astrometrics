"""Purpose: A result model that leaves out the fields a reply did not fill.

Description: Several calls (`review.query`, `control.remote.sync_frames` and
others) answer with different fields in different cases. A result model
for them names every field that can appear. A field the case did not
fill is left out when the model is turned into plain data (`model_dump`),
so the data has the same keys the dictionary replies had before. A field
that was filled with `None` on purpose is kept.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, model_serializer

__all__ = ["SparseResult"]


class SparseResult(BaseModel):
    """Base of the result models whose fields depend on the case asked.

    A name that the model does not list is kept as an extra field, so a
    case that grows a new key loses nothing.
    """

    model_config = ConfigDict(extra="allow")

    @model_serializer(mode="wrap")
    def _keep_only_filled_fields(self, handler: Any) -> dict[str, Any]:
        """Leave out the fields that were never given a value.

        Parameters
        ----------
        handler : `Any`
            Pydantic's own serializer for the model.

        Returns
        -------
        data : `dict` [`str`, `Any`]
            The fields that were set, `None` or not, and the extras.
        """
        data = handler(self)
        filled = self.model_fields_set
        return {
            key: value for key, value in data.items() if key in filled or key not in type(self).model_fields
        }
