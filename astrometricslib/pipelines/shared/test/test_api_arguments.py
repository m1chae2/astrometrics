"""Tests for `resolve_target`, which turns a target id into its `Target`.

Every public method that takes ``target: str | Target`` calls it, so a
caller such as an MCP client can pass a plain id. A name is looked up after a
fresh read of the stored targets, so a target another program changed is
seen.
"""

import pytest

from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.api_arguments import resolve_target


class _Catalog:
    """A target catalog that records how it is asked."""

    def __init__(self, targets: list[Target]) -> None:
        """Hold the given targets and start with no recorded lookups.

        Parameters
        ----------
        targets : `list` [`Target`]
            The stored targets.
        """
        self.by_id = {target.id: target for target in targets}
        self.lookups: list[tuple[str, bool]] = []

    def get(self, target_id: str, refresh: bool = False) -> Target | None:
        """Record the lookup and answer it.

        Returns
        -------
        target : `Target` or `None`
            The target with that id, if any.
        """
        self.lookups.append((target_id, refresh))
        return self.by_id.get(target_id)


def test_a_name_is_looked_up_after_a_fresh_read() -> None:
    """A target id asks the catalog to re-read its stored targets."""
    catalog = _Catalog([Target(id="M 13")])

    assert resolve_target(catalog, "M 13").id == "M 13"
    assert catalog.lookups == [("M 13", True)]


def test_a_target_object_is_returned_without_a_lookup() -> None:
    """A `Target` passes through and the catalog is not read."""
    catalog = _Catalog([])
    target = Target(id="M 57")

    assert resolve_target(catalog, target) is target
    assert catalog.lookups == []


def test_unknown_and_unusable_names_are_refused() -> None:
    """An unknown id is not found; a blank or non-text value is invalid."""
    catalog = _Catalog([])

    with pytest.raises(NotFoundError, match="Nope"):
        resolve_target(catalog, "Nope")
    with pytest.raises(InvalidArgumentError):
        resolve_target(catalog, "  ")
    with pytest.raises(InvalidArgumentError):
        resolve_target(catalog, 42)
