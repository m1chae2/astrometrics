"""Tests for wayfindinglib's rename table and the "moved to ..." error.

The same mechanism as astrometricslib's: reading a renamed method on a
sub-API object raises `MovedError` with the new call.
"""

import pytest

import wayfindinglib
from astrometricslib.exceptions import MovedError
from wayfindinglib._internal.renames import RENAMES, MovedNames
from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.api.control.control import ObservatoryControl
from wayfindinglib.api.planning import ObservationPlanning
from wayfindinglib.api.review import ObservationReview

SUB_API_CLASSES = (
    wayfindinglib.Wayfinder,
    ObservationPlanning,
    ObservationReview,
    ObservatoryControl,
    wayfindinglib.ObservationExecution,
    ControlChild,
)


@pytest.mark.parametrize("sub_api", SUB_API_CLASSES)
def test_every_sub_api_class_reads_the_rename_table(sub_api: type) -> None:
    """Each sub-API class, and the root, inherits the hook."""
    assert issubclass(sub_api, MovedNames)


def test_a_name_moved_on_a_base_class_is_found_from_a_subclass(monkeypatch: pytest.MonkeyPatch) -> None:
    """An entry for `ControlChild` covers each control child and the review."""
    monkeypatch.setitem(RENAMES, "wayfindinglib.ControlChild.old_status", "wayfinder.control.mount.status()")
    review = ObservationReview.__new__(ObservationReview)
    with pytest.raises(MovedError, match=r"moved to wayfinder\.control\.mount\.status\(\)"):
        _ = review.old_status


def test_another_missing_name_is_a_plain_attribute_error() -> None:
    """A name that was never public gets the usual error."""
    planning = ObservationPlanning.__new__(ObservationPlanning)
    with pytest.raises(AttributeError) as caught:
        _ = planning.no_such_method
    assert not isinstance(caught.value, MovedError)


def test_the_table_maps_text_to_text() -> None:
    """Every entry has a library-qualified key and a non-empty new call."""
    for old, new in RENAMES.items():
        assert old.startswith("wayfindinglib.") and old.count(".") == 2, old
        assert new.strip(), old
