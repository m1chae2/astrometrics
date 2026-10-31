"""Tests for `GroupResult` and the choice of the groups that are combined."""

import pytest

from astrometricslib.pipelines.stacking.stack_runner import GroupResult, used_groups


def _make_result(exposure: float, summary_index: int, left_out_reason: str | None = None) -> GroupResult:
    """Build a `GroupResult` with only the fields the selection reads.

    Returns
    -------
    result : `GroupResult`
        A result for a group of three frames.
    """
    return GroupResult(
        group=None,
        path=f"exp{exposure:g}s.fits",
        diagnostics={},
        summary_index=summary_index,
        exposure=exposure,
        count=3,
        left_out_reason=left_out_reason,
    )


def test_used_groups_keeps_groups_without_a_reason_in_group_order() -> None:
    """Groups with a left-out reason are dropped; the rest keep their order."""
    results = [
        _make_result(10.0, 0),
        _make_result(30.0, 1, "could not be lined up"),
        _make_result(60.0, 2),
    ]
    assert [result.summary_index for result in used_groups(results)] == [0, 2]


def test_used_groups_is_empty_when_every_group_is_left_out() -> None:
    """No group is combined when every group has a reason."""
    results = [_make_result(10.0, 0, "nonlinear")]
    assert used_groups(results) == []


def test_group_result_defaults_describe_a_group_not_yet_measured() -> None:
    """A fresh result has stand-in noise, no alignment and no gain."""
    result = _make_result(10.0, 0)
    assert result.frame_noise == pytest.approx(1.0)
    assert result.alignment is None
    assert result.gain_applied is None
    assert result.kept_path is None
