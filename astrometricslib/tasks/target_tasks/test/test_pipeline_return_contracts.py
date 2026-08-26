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
from astrometricslib.tasks.target_tasks import pipeline_tasks

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

# Both of photometry's give-up paths return this same short shape.
PHOTOMETRY_REJECTION_KEYS: set[str] = {"status", "targetId", "analysisMode", "message"}


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


def test_photometry_with_no_frames_for_the_filter_returns_the_rejection_shape():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the "no frames for this filter" give-up path keeps its shape.

    This runs before any real work starts, so it needs no image files.
    """
    target = Target(id="NoMatchingFramesTarget")

    result = pipeline_tasks.analyze_target(
        target, pipeline_type="photometry", filter_type="LUMINANCE", register_job=False
    )

    assert set(result) == PHOTOMETRY_REJECTION_KEYS
    assert result["status"] == "failed"
    assert result["targetId"] == "NoMatchingFramesTarget"
    assert result["analysisMode"] == "photometry"
    assert "No frames found for filter" in result["message"]


def test_an_unknown_analysis_mode_is_rejected_by_name():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the error message for an unknown mode is unchanged.

    The split replaces the `match` statement with a lookup table, and the
    lookup has to fail the same way the `match` did.
    """
    with pytest.raises(ValueError, match="Unknown analysis type: not_a_real_mode"):
        pipeline_tasks.analyze_target(
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
    import inspect

    source = inspect.getsource(pipeline_tasks._run_analysis_pipeline_match)
    dispatched_modes = {
        line.split('"')[1] for line in source.splitlines() if line.strip().startswith('case "')
    }

    assert dispatched_modes == set(EXPECTED_RESULT_KEYS), (
        "The dispatcher and this table disagree about which analysis modes exist."
    )
