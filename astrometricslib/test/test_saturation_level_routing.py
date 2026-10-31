"""Guards the rule that saturation levels come from one place.

A camera profile holds a raw saturation threshold, but the number a
pipeline should use is never the raw threshold. A pixel is counted as
saturated at `CameraProfile.pixel_saturation_level_adu` (the threshold
capped at the clip ceiling). A star is called photometric below
`CameraProfile.usable_peak_level_adu` (also capped at the photometric
linearity limit). Reading the raw threshold directly lets a profile whose
threshold lies above the clip ceiling, such as the Nikon D5300's, hide
real saturation.

This test reads every module of the libraries and the backend (not the
tests) and fails when one reads `saturation_threshold_adu.value` outside
the few modules that own the profile data.
"""

import ast
import pathlib

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCANNED_FOLDERS = ("astrometricslib", "wayfindinglib", "backend", "mcp_servers")

# Modules that may read the raw threshold: they define, load, migrate or
# echo the profile's configured numbers and make no saturation decision.
ALLOWED_MODULES = frozenset({
    "astrometricslib/models/camera_profile.py",
    "astrometricslib/drivers/camera_profile_store.py",
    "astrometricslib/drivers/camera_profile_migration.py",
    "astrometricslib/pipelines/shared/applied_camera_profile.py",
})


def _is_test_module(path: pathlib.Path) -> bool:
    """Say whether a path is a test module.

    Parameters
    ----------
    path : `pathlib.Path`
        A Python file.

    Returns
    -------
    is_test : `bool`
        `True` for files in a ``test`` folder or named ``test_*.py``.
    """
    return "test" in path.parts or path.name.startswith("test_") or path.name == "conftest.py"


def _reads_raw_threshold_value(node: ast.AST) -> bool:
    """Say whether a node reads `<profile>.saturation_threshold_adu.value`.

    Parameters
    ----------
    node : `ast.AST`
        Any node of a parsed module.

    Returns
    -------
    reads : `bool`
        `True` when the node reads that attribute chain.
    """
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "value"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "saturation_threshold_adu"
    )


def test_no_module_reads_the_raw_saturation_threshold_value() -> None:
    """Only the profile's own modules may read the raw threshold value."""
    offenders: list[str] = []
    for folder in SCANNED_FOLDERS:
        for path in sorted((REPOSITORY_ROOT / folder).rglob("*.py")):
            relative = path.relative_to(REPOSITORY_ROOT).as_posix()
            if _is_test_module(path.relative_to(REPOSITORY_ROOT)) or relative in ALLOWED_MODULES:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            offenders.extend(
                f"{relative}:{node.lineno}" for node in ast.walk(tree) if _reads_raw_threshold_value(node)
            )
    assert not offenders, (
        "Use CameraProfile.pixel_saturation_level_adu or usable_peak_level_adu instead of "
        f"saturation_threshold_adu.value in: {offenders}"
    )


def test_the_scan_detects_a_direct_read() -> None:
    """The checker flags the forbidden read and ignores the allowed ones."""
    forbidden = ast.parse("profile.saturation_threshold_adu.value").body[0].value
    allowed = ast.parse("profile.pixel_saturation_level_adu.value").body[0].value
    assert _reads_raw_threshold_value(forbidden)
    assert not _reads_raw_threshold_value(allowed)
