"""Purpose: Check that each library caches only the data it owns.

Description: `astrometricslib` owns the stored quality measurements, and it
keeps its own reads of them up to date by reading only the rows written
since (issue #182). `wayfindinglib` keeps only what it works out from them,
with the science library's opaque data version
(``quality.records.get_data_version``) as the cache version. So no
`wayfindinglib` module may use the storage details a cache would need to
bring stored rows up to date itself: the removed frame-metric data versions
and the named parts of a stored-rows version (the row count, the newest row
number, the sum of the row numbers and the newest time). These tests read
the source of every `wayfindinglib` module that is not a test.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import astrometricslib.drivers
import wayfindinglib

FORBIDDEN_NAMES = frozenset({"get_frame_metric_data_versions", "FrameMetricDataVersion", "TargetDataVersion"})
"""Storage helpers of `astrometricslib` that hand row details to a caller."""

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


def test_the_frame_metric_data_versions_are_not_public() -> None:
    """The helper that handed row numbers across the boundary is gone."""
    assert not hasattr(astrometricslib.drivers, "get_frame_metric_data_versions")
    assert "get_frame_metric_data_versions" not in astrometricslib.drivers.__all__


def test_wayfindinglib_imports_no_storage_row_helper() -> None:
    """No module imports a helper that gives stored row numbers."""
    found = [
        f"{path}: {alias.name}"
        for path, tree in _library_modules()
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name in FORBIDDEN_NAMES
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
