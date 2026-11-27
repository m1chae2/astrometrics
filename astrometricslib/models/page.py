"""Purpose: `Page`, the one shape every query of both libraries returns.

Description: A query gives back the matches it was asked for as a `Page`:
the items, how many matches there are in all (``total``) and, when the
caller asked for fewer than all with ``limit``, the ``offset`` to ask for
next (``next_offset``). A page acts like a read-only list: ``for``,
``len`` and indexing work on its items.
"""

from collections.abc import Iterator
from typing import overload

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["Page"]


class Page[T](BaseModel):
    """The matches of a query: a frozen, list-like model.

    Parameters
    ----------
    items : `tuple` [`T`, ...]
        The matches on this page, in the query's order. A list is accepted
        and kept as a tuple.
    total : `int`
        How many matches there are in all, on every page.
    next_offset : `int` or `None`, optional
        The ``offset`` that gives the next page, or `None` when this page
        holds the last match. A page with a ``next_offset`` is cut short:
        it does not hold every match.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    items: tuple[T, ...]
    total: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)

    @classmethod
    def of(cls, items: list[T] | tuple[T, ...], *, offset: int = 0, limit: int | None = None) -> Page[T]:
        """Cut one page out of every match.

        Parameters
        ----------
        items : `list` [`T`] or `tuple` [`T`, ...]
            Every match, in order.
        offset : `int`, optional
            How many matches to skip. Defaults to 0.
        limit : `int` or `None`, optional
            The most matches to keep. `None` (the default) keeps every
            match after ``offset``.

        Returns
        -------
        page : `Page` [`T`]
            The page, with ``next_offset`` set when matches are left over.
        """
        end = len(items) if limit is None else min(len(items), offset + limit)
        return cls(
            items=tuple(items[offset:end]),
            total=len(items),
            next_offset=end if end < len(items) else None,
        )

    @property
    def is_complete(self) -> bool:
        """Whether this page holds every match from its offset to the end.

        Returns
        -------
        complete : `bool`
            `True` when ``next_offset`` is `None`.
        """
        return self.next_offset is None

    def __iter__(self) -> Iterator[T]:  # type: ignore[override]
        """Go through the items, as with a list.

        Returns
        -------
        items : `Iterator` [`T`]
            The items in order.
        """
        return iter(self.items)

    def __len__(self) -> int:
        """Count the items on this page.

        Returns
        -------
        count : `int`
            The number of items on this page (not ``total``).
        """
        return len(self.items)

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[T, ...]: ...

    def __getitem__(self, index: int | slice) -> T | tuple[T, ...]:
        """Read one item, or a slice of items, as with a list.

        Parameters
        ----------
        index : `int` or `slice`
            The position, or a slice.

        Returns
        -------
        item : `T` or `tuple` [`T`, ...]
            The item, or the items of the slice.
        """
        return self.items[index]

    def __repr__(self) -> str:
        """Give a short description that fits on one line.

        Returns
        -------
        text : `str`
            For example ``Page(3 of 120 items, next_offset=3)``.
        """
        more = "" if self.next_offset is None else f", next_offset={self.next_offset}"
        return f"Page({len(self.items)} of {self.total} items{more})"

    __str__ = __repr__
