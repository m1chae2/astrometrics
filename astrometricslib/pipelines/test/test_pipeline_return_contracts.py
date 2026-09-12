"""Locks the exact shape of the dictionaries each analysis returns.

These dictionaries are not internal. Their keys travel out through the
backend and land in TypeScript, in `ui/common/types/backendTypes.ts`, so
dropping or renaming one is a silent break in the user interface rather
than a loud failure here.

That matters most right now because the big `match` statement that builds
these dictionaries is about to be split into one module per pipeline.
Splitting is supposed to move code without changing it, and the only way
to prove that is to write down what the code produces *before* the move
and check the same thing after.

`EXPECTED_RESULT_KEYS` below is the single place those key sets are
written down. Other test files import it rather than repeating the lists,
so there is only ever one copy to update on a deliberate change.
"""

import pytest

from astrometricslib.models.target import Target
from astrometricslib.pipelines import dispatch

# The exact keys each analysis mode returns. A deliberate change here
# should be a deliberate edit to this table, reviewed on its own.
EXPECTED_RESULT_KEYS: dict[str, set[str]] = {
    "astrometry": {"context", "stellar_objects", "wcs", "image_stats"},
    "spectroscopy": {"context", "stellar_objects"},
    "photometry": {
        "status",
        "targetId",
        "totalImages",
        "analysisMode",
        "starsProcessed",
        "spectraExtracted",
        "starsFound",
        "framesProcessed",
        "rejectedCount",
        "rejectedFiles",
        "variableCandidates",
        "longTermVariableCandidates",
        "crossSessionMatchCount",
    },
    "asteroid_recovery": {
        "status",
        "targetId",
        "analysisMode",
        "candidatesDetected",
        "candidatesRateLinearityConfirmed",
        "candidatesEphemerisMatched",
        "candidates",
    },
}


def assert_result_keys(result: dict, mode: str) -> None:
    """Check a result dictionary has exactly the keys we promised.

    Parameters
    ----------
    result : `dict`
        The dictionary an analysis returned.
    mode : `str`
        Which analysis produced it, used to look up the expected keys.
    """
    assert set(result) == EXPECTED_RESULT_KEYS[mode], (
        f"{mode} result keys drifted. This dictionary is consumed by the user "
        f"interface, so a change here needs a matching change in "
        f"ui/common/types/backendTypes.ts."
    )


def test_photometry_with_no_frames_for_the_filter_returns_completed_with_zero_counts():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify "nothing matched this filter" is a normal empty run.

    `process_input` produces an empty, `has_work=False` `Result` before
    any real work starts, but that `Result` still flows through
    `validate_output`/`to_result_dict` exactly like a real run's would --
    so the target still gets a real (empty) quality summary, flagged
    with the reason, and the caller gets the same result shape as any
    other completed photometry run, just with every count at zero.
    """
    target = Target(id="NoMatchingFramesTarget")

    result = dispatch.analyze_target(
        target, pipeline_type="photometry", filter_type="LUMINANCE", register_job=False
    )

    assert_result_keys(result, "photometry")
    assert result["status"] == "completed"
    assert result["targetId"] == "NoMatchingFramesTarget"
    assert result["analysisMode"] == "photometry"
    assert result["starsFound"] == 0
    assert result["totalImages"] == 0

    summary = target.photometry_quality_summary
    assert summary is not None
    assert summary.flagged
    assert any("No frames found for filter" in reason for reason in summary.flag_reasons)


def test_an_unknown_analysis_mode_is_rejected_by_name():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the error message for an unknown mode is unchanged.

    The split replaces the `match` statement with a lookup table, and the
    lookup has to fail the same way the `match` did.
    """
    with pytest.raises(ValueError, match="Unknown analysis type: not_a_real_mode"):
        dispatch.analyze_target(
            Target(id="UnknownModeTarget"),
            pipeline_type="not_a_real_mode",
            path="unused.fits",
            register_job=False,
        )


def test_every_analysis_mode_has_a_recorded_key_set():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify this file covers every mode the dispatcher accepts.

    If someone adds a fifth analysis mode, this fails and points them at
    the table above, so a new mode cannot ship without its shape written
    down.
    """
    from astrometricslib.pipelines import PIPELINE_RUNNERS

    assert set(PIPELINE_RUNNERS) == set(EXPECTED_RESULT_KEYS), (
        "PIPELINE_RUNNERS and this table disagree about which analysis modes exist."
    )
