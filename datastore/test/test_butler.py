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


def _make_indexed_butler(tmp_path) -> Butler:  # ruff: ignore[missing-type-function-argument]
    """Build a Butler whose spec declares label as an indexed column.

    Returns
    -------
    butler : `Butler`
        A Butler registered with a "widget" dataset type whose
        ``label`` column has a real SQL index.
    """
    config = _FakeConfig(str(tmp_path))
    spec = DatasetSpec(
        table_name="widgets",
        model_class=_Widget,
        extra_column_types={"label": "TEXT", "score": "REAL"},
        extra_columns=lambda widget: {"label": widget.label, "score": widget.score},
        indexed_columns=("label",),
    )
    return Butler(config, db_name="test.db", specs={"widget": spec})


def test_ensure_table_creates_the_declared_index(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify indexed_columns produces a real SQL index, not just a column."""
    import sqlite3

    butler = _make_indexed_butler(tmp_path)
    butler.put(_Widget(id="w1", label="alpha"), "widget")

    conn = sqlite3.connect(str(tmp_path / "test.db"))
    indexes = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    }
    conn.close()

    assert "idx_widgets_label" in indexes


def test_list_projected_returns_only_the_requested_columns(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the result dicts carry exactly the requested columns."""
    butler = _make_indexed_butler(tmp_path)
    butler.put(_Widget(id="w1", label="alpha", score=1.5), "widget")
    butler.put(_Widget(id="w2", label="beta", score=2.5), "widget")

    rows = butler.list_projected("widget", ["id", "label"])

    assert sorted(rows, key=lambda r: r["id"]) == [
        {"id": "w1", "label": "alpha"},
        {"id": "w2", "label": "beta"},
    ]


def test_list_projected_filters_with_where(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify where= restricts results to matching rows."""
    butler = _make_indexed_butler(tmp_path)
    butler.put(_Widget(id="w1", label="alpha"), "widget")
    butler.put(_Widget(id="w2", label="beta"), "widget")

    rows = butler.list_projected("widget", ["id"], where={"label": "beta"})

    assert rows == [{"id": "w2"}]


def test_list_projected_never_touches_data_json_unless_asked(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify data_json is absent from results that don't request it.

    The whole point of this method is avoiding the cost of parsing
    data_json for callers that only need indexed columns -- this
    checks the contract, not just the happy path.
    """
    butler = _make_indexed_butler(tmp_path)
    butler.put(_Widget(id="w1", label="alpha"), "widget")

    (row,) = butler.list_projected("widget", ["id", "label"])

    assert "data_json" not in row


def test_list_projected_rejects_an_unregistered_column(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an unknown column name raises rather than building raw SQL.

    columns/where can originate from caller-assembled lists, so this
    is a real injection guard, not just input validation.
    """
    butler = _make_indexed_butler(tmp_path)

    with pytest.raises(ValueError, match="unknown column"):
        butler.list_projected("widget", ["id", "; DROP TABLE widgets"])


def test_list_projected_rejects_an_unregistered_where_column(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify where= keys are validated the same way columns are."""
    butler = _make_indexed_butler(tmp_path)

    with pytest.raises(ValueError, match="unknown column"):
        butler.list_projected("widget", ["id"], where={"nonexistent_column": "x"})


def test_list_projected_requires_at_least_one_column(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an empty column list raises rather than selecting nothing."""
    butler = _make_indexed_butler(tmp_path)

    with pytest.raises(ValueError, match="at least one column"):
        butler.list_projected("widget", [])


def test_list_projected_on_a_missing_database_returns_empty(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify no database file yet is handled the same as an empty table."""
    butler = _make_indexed_butler(tmp_path)

    assert butler.list_projected("widget", ["id"]) == []
