"""Tests for the calibration ingest report's pure helpers.

These check how the library's nested frame lists are flattened, how two
snapshots are compared, how a flat set is measured, and how the report carries
the health records it is given.
"""

from pathlib import Path

import numpy as np
from astropy.io import fits

from astrometricslib.drivers.calibration_library import FlatGroup
from astrometricslib.models.calibration_ingest import CalibrationSetHealth
from astrometricslib.pipelines.stacking import calibration_ingest as ci


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


def test_report_carries_the_assessments_it_is_given() -> None:
    """The report counts the frames and passes the health records through."""
    health = CalibrationSetHealth(
        kind="dark",
        rowKey="dark|cam",
        camera="cam",
        gain="0.0",
        offset=10.0,
        frameCount=2,
        uniqueFrameCount=2,
        status="not_checked",
        summary="2 of 2 frame(s) are not measured yet.",
    )

    report = ci.build_ingest_report("dark", {}, {"cam / 0.0 / 30.0": {"a", "b"}}, [health])

    assert (report.kind, report.added_count, report.total_count) == ("dark", 2, 2)
    assert report.assessments == [health]


def test_report_without_assessments_has_an_empty_list() -> None:
    """With no assessments given, only the counts are reported."""
    report = ci.build_ingest_report("dark", {}, {"cam / 0.0 / 30.0": {"a", "b"}})

    assert report.added_count == 2
    assert report.assessments == []
