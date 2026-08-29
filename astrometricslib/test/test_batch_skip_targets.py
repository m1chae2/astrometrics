"""Tests for excluding targets that star-based registration cannot process.

A solar or planetary disk carries no star field, so Siril reports "not
enough stars in reference image" and fails -- Mirfak found 0 stars,
Jupiter 1, Venus 2, against the 3 an alignment needs. It fails only
after staging and converting every frame, which cost Sun about 25
minutes of a run to reach a conclusion that was never in doubt.
"""

from astrometricslib.scripts.run_all_target_processing import _build_argument_parser


def test_skip_target_is_repeatable():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Several targets must be excludable in one invocation."""
    arguments = _build_argument_parser().parse_args(["--skip-target", "Sun", "--skip-target", "Venus"])

    assert arguments.skip_target_ids == ["Sun", "Venus"]


def test_skipping_defaults_to_nothing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A plain run must still process everything."""
    arguments = _build_argument_parser().parse_args([])

    assert arguments.skip_target_ids is None
    assert arguments.skip_targets_from is None


def test_skips_can_come_from_a_file():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A permanent exclusion list belongs in a file, not a command line."""
    arguments = _build_argument_parser().parse_args(["--skip-targets-from", "/tmp/skip.txt"])

    assert arguments.skip_targets_from == "/tmp/skip.txt"


def test_skip_and_select_can_combine():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Selecting a subset and excluding from it are independent."""
    arguments = _build_argument_parser().parse_args([
        "--target",
        "M 31",
        "--target",
        "Sun",
        "--skip-target",
        "Sun",
    ])

    assert arguments.target_ids == ["M 31", "Sun"]
    assert arguments.skip_target_ids == ["Sun"]
