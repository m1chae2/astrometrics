"""Tests for running the batch script over a chosen subset of targets.

Selecting a subset of targets allows us to skip ones that are already
finished, reducing a multi-hour job into a much shorter one.
"""

import pytest

from astrometricslib.scripts.run_all_target_processing import (
    _build_argument_parser,
    _read_target_ids_from_file,
    _resolve_requested_targets,
)


class _Target:
    """A target stand-in exposing only its id."""

    def __init__(self, target_id):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.id = target_id


@pytest.fixture
def catalog():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Return a small stand-in catalog.

    Returns
    -------
    targets : `list` [`_Target`]
        Targets whose ids include spaces, as real ones do.
    """
    return [_Target("M 31"), _Target("NGC 7023"), _Target("Sun"), _Target("M 27")]


def test_requested_targets_are_selected(catalog):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The subset actually narrows the run."""
    selected, unmatched = _resolve_requested_targets(catalog, ["M 31", "Sun"])

    assert [target.id for target in selected] == ["M 31", "Sun"]
    assert unmatched == []


def test_selection_is_case_and_whitespace_insensitive(catalog):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Ids are typed by hand, so matching must tolerate that."""
    selected, unmatched = _resolve_requested_targets(catalog, ["  m 31 ", "ngc 7023"])

    assert {target.id for target in selected} == {"M 31", "NGC 7023"}
    assert unmatched == []


def test_a_mistyped_id_is_reported(catalog):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A typo must not quietly shrink the run into a clean-looking pass."""
    selected, unmatched = _resolve_requested_targets(catalog, ["M 31", "NGC 9999"])

    assert [target.id for target in selected] == ["M 31"]
    assert unmatched == ["NGC 9999"]


def test_selection_preserves_catalog_order(catalog):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Processing order should not depend on argument order."""
    selected, _ = _resolve_requested_targets(catalog, ["M 27", "M 31"])

    assert [target.id for target in selected] == ["M 31", "M 27"]


def test_requesting_nothing_matches_nothing(catalog):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """An empty request is not silently treated as "everything"."""
    selected, unmatched = _resolve_requested_targets(catalog, [])

    assert selected == []
    assert unmatched == []


def test_blank_entries_are_ignored(catalog):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A stray blank line must not count as an unmatched id."""
    selected, unmatched = _resolve_requested_targets(catalog, ["M 31", "   "])

    assert [target.id for target in selected] == ["M 31"]
    assert unmatched == []


def test_ids_are_read_from_a_file(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A reprocess list is easier to keep in a file than on a command line."""
    listing = tmp_path / "reprocess.txt"
    listing.write_text("# targets the fixes unblock\nSun\n\nM 27\n  M 31  \n")

    assert _read_target_ids_from_file(str(listing)) == ["Sun", "M 27", "M 31"]


def test_target_flag_is_repeatable():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Several targets must be selectable in one invocation."""
    arguments = _build_argument_parser().parse_args(["--target", "M 31", "--target", "Sun"])

    assert arguments.target_ids == ["M 31", "Sun"]


def test_no_arguments_means_the_whole_catalog():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The default must stay a full run, so existing usage is unchanged."""
    arguments = _build_argument_parser().parse_args([])

    assert arguments.target_ids is None
    assert arguments.dry_run is False
    assert arguments.skip_reindex is False


def test_dry_run_and_skip_reindex_are_available():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Both exist so a subset can be previewed and re-run cheaply."""
    arguments = _build_argument_parser().parse_args(["--dry-run", "--skip-reindex"])

    assert arguments.dry_run is True
    assert arguments.skip_reindex is True
