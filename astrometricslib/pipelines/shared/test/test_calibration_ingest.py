"""Tests for the calibration ingest report's pure helpers.

These check how the library's nested frame lists are flattened, how two
snapshots are compared, and how the report names and measures flat sets.
"""

from pathlib import Path

import numpy as np
from astropy.io import fits

from astrometricslib.drivers.calibration_library import FlatGroup
from astrometricslib.pipelines.shared import calibration_ingest as ci


def write_flat(path: Path, seed: int, level: float = 0.45) -> str:
    """Write a quiet 16-bit flat frame.

    Parameters
    ----------
    path : `pathlib.Path`
        File to write.
    seed : `int`
        Seed of the noise.
    level : `float`, optional
        Mean brightness as a fraction of full scale.

    Returns
    -------
    path : `str`
        The file written.
    """
    generator = np.random.default_rng(seed)
    data = level * 65535.0 * (1.0 + 0.007 * generator.standard_normal((64, 64)))
    fits.writeto(path, np.clip(data, 0, 65535).astype(np.uint16), overwrite=True)
    return str(path)


def test_flatten_names_each_group_by_its_keys() -> None:
    """Lists at any nesting depth become one group named by the keys above."""
    index = {"Scope": {"Camera": {"L": {"0.0@offset=10": ["a", "b"]}}}, "Bias cam": {"0.0": ["c"]}}

    groups = ci.flatten_frame_index(index)

    assert groups == {
        "Scope / Camera / L / 0.0@offset=10": {"a", "b"},
        "Bias cam / 0.0": {"c"},
    }


def test_summarize_changes_lists_only_groups_that_changed() -> None:
    """Gained and lost frames are counted per group and in total."""
    before = {"g1": {"a", "b"}, "g2": {"x"}, "g3": {"q"}}
    after = {"g1": {"a", "b", "c", "d"}, "g2": {"x"}, "g4": {"n"}}

    changes, added, removed = ci.summarize_changes(before, after)

    assert [(c.group, c.added, c.removed, c.total) for c in changes] == [
        ("g1", 2, 0, 4),
        ("g3", 0, 1, 0),
        ("g4", 1, 0, 1),
    ]
    assert (added, removed) == (3, 1)


def test_flat_group_name_matches_the_flattened_name() -> None:
    """A `FlatGroup` is named the way `flatten_frame_index` names its slot."""
    group = FlatGroup("Scope", "Camera", "Luminance", "0.0", 10.0, ["a"])
    index = {"Scope": {"Camera": {"Luminance": {"0.0@offset=10": ["a"]}}}}

    assert ci.flat_group_name(group) in ci.flatten_frame_index(index)


def test_a_quiet_flat_set_passes(tmp_path: Path) -> None:
    """Thirty quiet, well-lit flats pass with no issues and no smoothing."""
    paths = [write_flat(tmp_path / f"flat_{index}.fits", seed=index) for index in range(30)]

    assessment = ci.assess_flat_group(FlatGroup("Scope", "Camera", "Luminance", "0.0", 10.0, paths))

    assert assessment.passes
    assert assessment.issues == []
    assert assessment.frame_count == 30
    assert assessment.smoothing_sigma_pixels is None
    assert 0.4 < assessment.level_fraction < 0.5


def test_a_faint_flat_set_fails_with_an_issue(tmp_path: Path) -> None:
    """A set at 1% of full scale is reported as faint."""
    paths = [write_flat(tmp_path / f"flat_{index}.fits", seed=index, level=0.01) for index in range(3)]

    assessment = ci.assess_flat_group(FlatGroup("Scope", "Camera", "Luminance", "0.0", 10.0, paths))

    assert not assessment.passes
    assert any("faint" in issue for issue in assessment.issues)


def test_report_measures_only_flat_sets_that_gained_frames(tmp_path: Path) -> None:
    """A set that gained nothing is left unmeasured; a new set is measured."""
    new_paths = [write_flat(tmp_path / f"new_{index}.fits", seed=index) for index in range(4)]
    old_paths = [write_flat(tmp_path / f"old_{index}.fits", seed=10 + index) for index in range(4)]
    new_group = FlatGroup("Scope", "Camera", "Luminance", "0.0", 10.0, new_paths)
    old_group = FlatGroup("Scope", "Camera", "Star Analyzer 200", "0.0", 10.0, old_paths)
    before = {ci.flat_group_name(old_group): set(old_paths)}
    after = {ci.flat_group_name(old_group): set(old_paths), ci.flat_group_name(new_group): set(new_paths)}

    report = ci.build_ingest_report("flat", before, after, [new_group, old_group])

    assert report.kind == "flat"
    assert (report.added_count, report.removed_count, report.total_count) == (4, 0, 8)
    assert [assessment.filter for assessment in report.flat_assessments] == ["Luminance"]


def test_report_for_darks_has_no_flat_assessments() -> None:
    """Without flat groups, only the counts are reported."""
    report = ci.build_ingest_report("dark", {}, {"cam / 0.0 / 30.0": {"a", "b"}})

    assert report.added_count == 2
    assert report.flat_assessments == []
