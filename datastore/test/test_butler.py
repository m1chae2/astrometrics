"""Tests for the shared, generic `datastore.Butler`."""

import pytest
from pydantic import BaseModel

from datastore.butler import Butler, DatasetSpec


class _Widget(BaseModel):
    """Minimal pydantic model standing in for a real domain model."""

    id: str
    label: str = ""
    score: float | None = None

    def serialize(self) -> dict:
        """Return this widget as a plain dict for JSON storage.

        Returns
        -------
        payload : `dict`
            This widget's fields as a plain dict.
        """
        return self.model_dump()


class _FakeConfig:
    """Minimal config stand-in exposing get_library_path()."""

    def __init__(self, library_path: str):  # ruff: ignore[missing-return-type-special-method]
        self._library_path = library_path

    def get_library_path(self) -> str:
        """Return the library root path.

        Returns
        -------
        path : `str`
            The configured library root path.
        """
        return self._library_path


def _make_butler(tmp_path, extra_columns=False) -> Butler:  # ruff: ignore[missing-type-function-argument]
    config = _FakeConfig(str(tmp_path))
    spec = DatasetSpec(
        table_name="widgets",
        model_class=_Widget,
        extra_column_types={"label": "TEXT"} if extra_columns else {},
        extra_columns=(lambda widget: {"label": widget.label}) if extra_columns else None,
    )
    return Butler(config, db_name="test.db", specs={"widget": spec})


def test_round_trip_without_extra_columns(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """get_all/put_all round-trip a widget with no extra_columns configured."""
    butler = _make_butler(tmp_path, extra_columns=False)
    widget = _Widget(id="a", label="Alpha", score=1.5)

    butler.put_all("widget", [widget])

    loaded = butler.get_all("widget")
    assert len(loaded) == 1
    assert loaded[0].id == "a"
    assert loaded[0].label == "Alpha"
    assert loaded[0].score == pytest.approx(1.5)


def test_round_trip_with_extra_columns(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """get_all/put round-trip a widget with an extra_columns callback."""
    butler = _make_butler(tmp_path, extra_columns=True)
    widget = _Widget(id="b", label="Beta")

    butler.put(widget, "widget")

    loaded = butler.get("widget", {"id": "b"})
    assert loaded is not None
    assert loaded.label == "Beta"


def test_merge_and_persist_disjoint_ids_do_not_clobber(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Two merge_and_persist calls on disjoint ids both survive."""
    butler = _make_butler(tmp_path)
    butler.put_all("widget", [_Widget(id="a", label="Alpha"), _Widget(id="b", label="Beta")])

    def keep_updated(existing, updated):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return updated

    butler.merge_and_persist("widget", [_Widget(id="a", label="Alpha-updated")], keep_updated)
    butler.merge_and_persist("widget", [_Widget(id="b", label="Beta-updated")], keep_updated)

    loaded = {widget.id: widget for widget in butler.get_all("widget")}
    assert loaded["a"].label == "Alpha-updated"
    assert loaded["b"].label == "Beta-updated"


def test_merge_and_persist_preserves_untouched_rows(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """merge_and_persist never deletes rows outside the given objects."""
    butler = _make_butler(tmp_path)
    butler.put_all("widget", [_Widget(id="a"), _Widget(id="b")])

    def keep_updated(existing, updated):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return updated

    butler.merge_and_persist("widget", [_Widget(id="a", label="only-a-touched")], keep_updated)

    ids = {widget.id for widget in butler.get_all("widget")}
    assert ids == {"a", "b"}


def test_delete_by_ids_scopes_to_targeted_rows(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """delete_by_ids removes only the requested ids."""
    butler = _make_butler(tmp_path)
    butler.put_all("widget", [_Widget(id="a"), _Widget(id="b"), _Widget(id="c")])

    butler.delete_by_ids("widget", ["b"])

    ids = {widget.id for widget in butler.get_all("widget")}
    assert ids == {"a", "c"}


def test_put_all_replaces_whole_table(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """put_all deletes rows not present in the given list."""
    butler = _make_butler(tmp_path)
    butler.put_all("widget", [_Widget(id="a"), _Widget(id="b")])

    butler.put_all("widget", [_Widget(id="a")])

    ids = {widget.id for widget in butler.get_all("widget")}
    assert ids == {"a"}


def test_get_by_ids_empty_input_returns_empty_list(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """get_by_ids short-circuits on an empty id list."""
    butler = _make_butler(tmp_path)
    assert butler.get_by_ids("widget", []) == []


def test_exists(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """exists() reflects whether a row is currently present."""
    butler = _make_butler(tmp_path)
    assert butler.exists("widget", {"id": "a"}) is False

    butler.put(_Widget(id="a"), "widget")
    assert butler.exists("widget", {"id": "a"}) is True
