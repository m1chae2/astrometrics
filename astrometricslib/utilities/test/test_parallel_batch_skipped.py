"""Tests for the "skipped" outcome in the parallel batch engine.

Previously, targets that were skipped because they had no valid frames
were counted as successes. This made it look like more work was done
than actually was. Skips are now tracked separately.
"""

from astrometricslib.utilities import parallel_batch
from astrometricslib.utilities.parallel_batch import BatchRunSummary, run_parallel_batch


def _worker_by_status(item_id: str) -> dict:
    """Report success, skip, or failure based on the item's name.

    Returns
    -------
    result : `dict`
        A batch-engine result dict for this item.
    """
    if item_id.startswith("skip"):
        return {"status": "skipped", "error": "No frames matching camera 'X'"}
    if item_id.startswith("fail"):
        return {"status": "failed", "error": "boom"}
    return {"status": "success", "error": None}


def test_skipped_items_are_not_counted_as_successes():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Skips land in their own list, keeping the success count honest."""
    summary = run_parallel_batch(["ok_a", "skip_a", "skip_b", "fail_a"], _worker_by_status, max_workers=2)

    assert summary.succeeded == ["ok_a"]
    assert sorted(item_id for item_id, _reason in summary.skipped) == ["skip_a", "skip_b"]
    assert [item_id for item_id, _reason in summary.failed] == ["fail_a"]


def test_skip_reason_is_preserved():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The worker's explanation reaches the summary for reporting."""
    summary = run_parallel_batch(["skip_a"], _worker_by_status, max_workers=1)

    assert summary.skipped[0][1] == "No frames matching camera 'X'"


def test_skipped_items_still_appear_in_results():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A skip is a real outcome, so callers can still inspect its result."""
    summary = run_parallel_batch(["skip_a"], _worker_by_status, max_workers=1)

    assert summary.results["skip_a"]["status"] == "skipped"


def test_summary_defaults_to_no_skips():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Workers that never skip leave the list empty rather than absent."""
    assert BatchRunSummary().skipped == []


def test_merge_preserves_skipped_entries():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Per-session summaries are concatenated without dropping skips."""
    from astrometricslib.pipelines.spectroscopy.batch import (
        _merge_batch_summaries,
    )

    first = parallel_batch.BatchRunSummary(succeeded=["a"], skipped=[("s1", "no frames")])
    second = parallel_batch.BatchRunSummary(failed=[("b", "boom")], skipped=[("s2", "no frames")])

    merged = _merge_batch_summaries([first, second])

    assert merged.succeeded == ["a"]
    assert merged.failed == [("b", "boom")]
    assert merged.skipped == [("s1", "no frames"), ("s2", "no frames")]
