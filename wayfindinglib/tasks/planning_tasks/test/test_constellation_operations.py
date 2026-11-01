"""Tests that the constellation line library finds its bundled data."""

from wayfindinglib.tasks.planning_tasks.constellation_operations import (
    ConstellationLineLibrary,
    default_constellation_lines_path,
)


def test_default_path_points_at_the_bundled_file() -> None:
    """The default path names a file that exists in the package."""
    assert default_constellation_lines_path().is_file()


def test_library_returns_line_segments_from_the_bundled_file() -> None:
    """The library returns real segments by default, not an empty list."""
    segments = ConstellationLineLibrary().get_all_line_segments()
    assert segments
    assert {"constellation", "ra1", "dec1", "ra2", "dec2"} <= set(segments[0])
