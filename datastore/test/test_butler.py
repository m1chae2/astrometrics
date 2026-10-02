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
    """Two merge_and_record calls on disjoint ids both survive."""
    butler = _make_butler(tmp_path)
    butler.put_all("widget", [_Widget(id="a", label="Alpha"), _Widget(id="b", label="Beta")])

    def keep_updated(existing, updated):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return updated

    butler.merge_and_record("widget", [_Widget(id="a", label="Alpha-updated")], keep_updated)
    butler.merge_and_record("widget", [_Widget(id="b", label="Beta-updated")], keep_updated)

    loaded = {widget.id: widget for widget in butler.get_all("widget")}
    assert loaded["a"].label == "Alpha-updated"
    assert loaded["b"].label == "Beta-updated"


def test_merge_and_persist_preserves_untouched_rows(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """merge_and_record never deletes rows outside the given objects."""
    butler = _make_butler(tmp_path)
    butler.put_all("widget", [_Widget(id="a"), _Widget(id="b")])

    def keep_updated(existing, updated):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return updated

    butler.merge_and_record("widget", [_Widget(id="a", label="only-a-touched")], keep_updated)

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

    columns/like can originate from caller-assembled lists, so this
    is a real injection guard, not just input validation.
    """
    butler = _make_indexed_butler(tmp_path)

    with pytest.raises(ValueError, match="unknown column"):
        butler.list_projected("widget", ["id", "; DROP TABLE widgets"])


def test_list_projected_requires_at_least_one_column(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an empty column list raises rather than selecting nothing."""
    butler = _make_indexed_butler(tmp_path)

    with pytest.raises(ValueError, match="at least one column"):
        butler.list_projected("widget", [])


def test_list_projected_on_a_missing_database_returns_empty(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify no database file yet is handled the same as an empty table."""
    butler = _make_indexed_butler(tmp_path)

    assert butler.list_projected("widget", ["id"]) == []


def test_list_projected_like_matches_a_substring(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify like= narrows to rows whose column contains the substring."""
    butler = _make_indexed_butler(tmp_path)
    butler.put(_Widget(id="w1", label="M 13 Field"), "widget")
    butler.put(_Widget(id="w2", label="M 81 Field"), "widget")
    butler.put(_Widget(id="w3", label="NGC 7023"), "widget")

    rows = butler.list_projected("widget", ["id"], like={"label": "M 13"})

    assert rows == [{"id": "w1"}]


def test_list_projected_like_escapes_sql_wildcard_characters(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a literal '%' or '_' typed by a caller matches literally.

    Without escaping, a caller-supplied '%' or '_' would act as a SQL
    wildcard instead of the literal character it actually is -- this
    matters because `like` values can originate from end-user input
    (e.g. a search box), not just trusted code.
    """
    butler = _make_indexed_butler(tmp_path)
    butler.put(_Widget(id="w1", label="100% Done"), "widget")
    butler.put(_Widget(id="w2", label="100X Done"), "widget")

    rows = butler.list_projected("widget", ["id"], like={"label": "100%"})

    assert rows == [{"id": "w1"}]


def test_list_projected_limit_caps_the_row_count(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify limit= bounds how many rows are returned."""
    butler = _make_indexed_butler(tmp_path)
    for index in range(5):
        butler.put(_Widget(id=f"w{index}", label="star"), "widget")

    rows = butler.list_projected("widget", ["id"], limit=2)

    assert len(rows) == 2


def test_list_projected_rejects_an_unregistered_like_column(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify like= keys are validated the same way columns are."""
    butler = _make_indexed_butler(tmp_path)

    with pytest.raises(ValueError, match="unknown column"):
        butler.list_projected("widget", ["id"], like={"nonexistent_column": "x"})


def test_list_projected_between_keeps_rows_inside_the_range_ends_included(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify between= keeps rows at or between the low and high values."""
    butler = _make_indexed_butler(tmp_path)
    for widget_id, score in (("w1", 1.0), ("w2", 2.0), ("w3", 3.0), ("w4", 4.0)):
        butler.put(_Widget(id=widget_id, label="x", score=score), "widget")

    rows = butler.list_projected("widget", ["id"], between={"score": (2.0, 3.0)})

    assert sorted(row["id"] for row in rows) == ["w2", "w3"]


def test_list_projected_between_never_matches_an_empty_column(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a row whose column is empty (NULL) is left out of a range."""
    butler = _make_indexed_butler(tmp_path)
    butler.put(_Widget(id="w1", label="x", score=None), "widget")
    butler.put(_Widget(id="w2", label="x", score=5.0), "widget")

    rows = butler.list_projected("widget", ["id"], between={"score": (-1000.0, 1000.0)})

    assert rows == [{"id": "w2"}]


def test_list_projected_between_combines_with_like(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify between= and like= must both hold for a row to be returned."""
    butler = _make_indexed_butler(tmp_path)
    butler.put(_Widget(id="w1", label="M 13", score=1.0), "widget")
    butler.put(_Widget(id="w2", label="M 13", score=9.0), "widget")
    butler.put(_Widget(id="w3", label="M 81", score=1.0), "widget")

    rows = butler.list_projected("widget", ["id"], like={"label": "M 13"}, between={"score": (0.0, 2.0)})

    assert rows == [{"id": "w1"}]


def test_list_projected_rejects_an_unregistered_between_column(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify between= names are validated like columns and like keys."""
    butler = _make_indexed_butler(tmp_path)

    with pytest.raises(ValueError, match="unknown column"):
        butler.list_projected("widget", ["id"], between={"1=1; --": (0.0, 1.0)})


def _write_widgets_with_only_the_label_column(tmp_path) -> None:  # ruff: ignore[missing-type-function-argument]
    """Create a widgets table as an older version of the app would have.

    The table has an ``id``, a ``data_json`` and a ``label`` column but
    no ``score`` column, and holds two rows whose ``data_json`` carries
    a score.
    """
    import json
    import sqlite3

    connection = sqlite3.connect(str(tmp_path / "test.db"))
    connection.execute("CREATE TABLE widgets (id TEXT PRIMARY KEY, data_json TEXT, label TEXT)")
    for widget_id, score in (("w1", 1.5), ("w2", 2.5)):
        payload = json.dumps({"id": widget_id, "label": "old", "score": score})
        connection.execute("INSERT INTO widgets VALUES (?, ?, ?)", (widget_id, payload, "old"))
    connection.commit()
    connection.close()


def _make_butler_with_score_backfill(tmp_path) -> Butler:  # ruff: ignore[missing-type-function-argument]
    """Build a Butler whose spec backfills the score column from data_json.

    Returns
    -------
    butler : `Butler`
        A Butler whose ``score`` column is filled from ``data_json`` when
        that column is first added to an existing table.
    """
    spec = DatasetSpec(
        table_name="widgets",
        model_class=_Widget,
        extra_column_types={"label": "TEXT", "score": "REAL"},
        extra_columns=lambda widget: {"label": widget.label, "score": widget.score},
        column_backfills={"score": "json_extract(data_json, '$.score')"},
    )
    return Butler(_FakeConfig(str(tmp_path)), db_name="test.db", specs={"widget": spec})


def test_new_column_is_backfilled_from_data_json_even_by_a_read_only_call(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify old rows get the new column's value and it is really saved.

    ``list_projected`` never commits, so this also checks the backfill
    is kept after that call closes its connection.
    """
    _write_widgets_with_only_the_label_column(tmp_path)
    butler = _make_butler_with_score_backfill(tmp_path)

    butler.list_projected("widget", ["id"])
    rows_from_a_second_connection = butler.list_projected("widget", ["id", "score"])

    assert sorted((row["id"], row["score"]) for row in rows_from_a_second_connection) == [
        ("w1", 1.5),
        ("w2", 2.5),
    ]


def test_backfill_runs_once_and_does_not_overwrite_later_writes(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a value written after the backfill survives later reads."""
    _write_widgets_with_only_the_label_column(tmp_path)
    butler = _make_butler_with_score_backfill(tmp_path)
    butler.list_projected("widget", ["id"])

    butler.put(_Widget(id="w1", label="new", score=99.0), "widget")
    rows = butler.list_projected("widget", ["id", "score"])

    assert {row["id"]: row["score"] for row in rows} == {"w1": 99.0, "w2": 2.5}


def test_ensure_table_creates_one_index_over_a_tuple_of_columns(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a tuple in indexed_columns makes a single multi-column index.

    A query that reads only those columns is then answered from the index
    alone, so the plan must say it uses a covering index.
    """
    import sqlite3

    spec = DatasetSpec(
        table_name="widgets",
        model_class=_Widget,
        extra_column_types={"label": "TEXT", "score": "REAL"},
        extra_columns=lambda widget: {"label": widget.label, "score": widget.score},
        indexed_columns=(("score", "label", "id"),),
    )
    butler = Butler(_FakeConfig(str(tmp_path)), db_name="test.db", specs={"widget": spec})
    butler.put(_Widget(id="w1", label="alpha", score=1.0), "widget")

    connection = sqlite3.connect(str(tmp_path / "test.db"))
    plan_text = str(
        connection.execute(
            "EXPLAIN QUERY PLAN SELECT id, label, score FROM widgets WHERE score BETWEEN 0 AND 2"
        ).fetchall()
    )
    connection.close()

    assert "COVERING INDEX idx_widgets_score_label_id" in plan_text
