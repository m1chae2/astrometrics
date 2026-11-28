"""Tests for `Page`, the shape every query returns."""

import pytest
from pydantic import ValidationError

from astrometricslib.models.page import Page


def test_a_page_acts_like_a_read_only_list() -> None:
    """``for``, ``len`` and indexing work on the items."""
    page = Page[int](items=[3, 1, 2], total=3)
    assert list(page) == [3, 1, 2]
    assert len(page) == 3
    assert page[0] == 3
    assert page[-1] == 2
    assert page[1:] == (1, 2)
    assert page.is_complete


def test_a_page_is_frozen() -> None:
    """A returned page cannot be changed."""
    page = Page[int](items=[1], total=1)
    with pytest.raises(ValidationError):
        page.total = 2  # type: ignore[misc]


def test_of_cuts_a_page_and_says_where_the_next_one_starts() -> None:
    """A limit leaves a ``next_offset``; the last page has none."""
    first = Page[int].of([1, 2, 3, 4, 5], limit=2)
    assert (first.items, first.total, first.next_offset) == ((1, 2), 5, 2)
    last = Page[int].of([1, 2, 3, 4, 5], offset=4, limit=2)
    assert (last.items, last.next_offset) == ((5,), None)
    every = Page[int].of([1, 2, 3])
    assert every.next_offset is None
    assert not first.is_complete


def test_a_page_checks_its_item_type() -> None:
    """An item of the wrong type is refused."""
    with pytest.raises(ValidationError):
        Page[int](items=["not a number"], total=1)


def test_the_repr_is_one_short_line() -> None:
    """The repr gives the counts, not every item."""
    assert repr(Page[int].of(list(range(100)), limit=3)) == "Page(3 of 100 items, next_offset=3)"
    assert str(Page[int](items=[1], total=1)) == "Page(1 of 1 items)"
