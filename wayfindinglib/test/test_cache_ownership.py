"""Purpose: Check that each library caches only the data it owns.

Description: `astrometricslib` owns the stored quality measurements, and it
keeps its own reads of them up to date by reading only the rows written
since (issue #182). `wayfindinglib` keeps only what it works out from them,
with the science library's opaque data version
(``quality.records.get_data_version``) as the cache version. So no
`wayfindinglib` module may use the storage details a cache would need to
bring stored rows up to date itself, and every version of the science
library's data (all of one kind, one target, a camera, the stacks, a group
of frames, the target records) is an opaque text from a public object, such
as ``targets.get_data_version``. So no `wayfindinglib` module may import a
storage-version helper of `astrometricslib.drivers`, or read the named parts
of a stored-rows version (the row count, the newest row number, the sum of
the row numbers and the newest time). The only version helpers it may
import are `bump_data_version` and `read_data_versions`, the write counters
of its own database. These tests read the source of
every `wayfindinglib` module that is not a test.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import astrometricslib.drivers
import wayfindinglib

FORBIDDEN_NAMES = frozenset({
    "FrameMetricDataVersion",
    "TargetDataVersion",
    "get_camera_data_versions",
    "get_library_data_version",
    "get_frame_metric_data_versions",
    "get_frame_metric_signs",
    "get_stack_quality_data_version",
    "get_target_camera_names",
    "get_target_data_version",
    "get_target_document_signs",
})
"""Storage-version helpers that give out storage details."""

ALLOWED_COUNTERS = frozenset({"bump_data_version", "read_data_versions"})
"""The counter helpers of its own database that `wayfindinglib` may import."""

FORBIDDEN_ATTRIBUTES = frozenset({
    "measurement_count",
    "newest_row_id",
    "measurement_row_id_sum",
    "newest_measured_at",
})
"""The parts of a stored-rows version that an incremental update reads."""


def _library_modules() -> Iterator[tuple[Path, ast.Module]]:
    """Parse every `wayfindinglib` module that is not a test.

    Yields
    ------
    path : `pathlib.Path`
        The module's file.
    tree : `ast.Module`
        Its parsed source.
    """
    root = Path(wayfindinglib.__file__).parent
    for path in sorted(root.rglob("*.py")):
        if "test" in path.relative_to(root).parts[:-1] or path.name.startswith("test_"):
            continue
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_version_helper(name: str) -> bool:
    """Say whether a name looks like a storage-version helper.

    Returns
    -------
    helper : `bool`
        True for a name about data versions or storage signs.
    """
    return "data_version" in name or name.endswith("_signs") or name.endswith("DataVersion")


def test_the_storage_version_helpers_are_not_public() -> None:
    """The helpers that handed storage details across the boundary are gone."""
    public = set(astrometricslib.drivers.__all__)
    assert public & FORBIDDEN_NAMES == set()
    assert {name for name in public if _is_version_helper(name)} <= ALLOWED_COUNTERS


def test_wayfindinglib_imports_no_storage_version_helper() -> None:
    """No module imports a storage-version helper, apart from the counters."""
    found = [
        f"{path}: {alias.name}"
        for path, tree in _library_modules()
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("astrometricslib")
        for alias in node.names
        if alias.name in FORBIDDEN_NAMES
        or (_is_version_helper(alias.name) and alias.name not in ALLOWED_COUNTERS)
    ]
    assert found == []


def test_wayfindinglib_reads_no_stored_row_numbers_of_a_version() -> None:
    """No module reads the parts an incremental update of stored rows needs."""
    found = [
        f"{path}:{node.lineno}: {node.attr}"
        for path, tree in _library_modules()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES
    ]
    assert found == []
