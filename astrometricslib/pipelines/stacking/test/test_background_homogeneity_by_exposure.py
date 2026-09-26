"""Purpose: Unit tests for the background check within each exposure length.

Description: The sky background of a frame grows with its exposure length. On
the NGC 2403 session (14 frames of 60 s at about 480 counts, 20 of 300 s at
about 2256) checking all frames together read the two lengths as a change of
sky and excluded every 60 s frame. These tests check that exposure lengths are
compared only with themselves, and that a real change of sky inside one length
is still found.
"""

from types import SimpleNamespace

from astrometricslib.pipelines.stacking.background_homogeneity import (
    find_dominant_background_subset,
    find_dominant_background_subset_by_exposure,
)


def frames(exposure: str, levels: list[float]) -> list[SimpleNamespace]:
    """Build frames of one exposure length with the given background levels.

    Returns
    -------
    frames : `list` [`types.SimpleNamespace`]
        Frames with an exposure and a measured background level.
    """
    return [
        SimpleNamespace(exposure=exposure, background_level=level, name=f"{exposure}_{index}")
        for index, level in enumerate(levels)
    ]


def test_all_frames_together_wrongly_split_the_ngc_2403_exposures() -> None:
    """The old whole-session check drops the short group (the defect)."""
    short = frames("60.0", [480.0 + i for i in range(14)])
    long = frames("300.0", [2256.0 + i for i in range(20)])

    _, excluded, split = find_dominant_background_subset(short + long)

    assert split is not None
    assert len(excluded) == 14


def test_each_exposure_length_is_checked_only_against_itself() -> None:
    """Groups differing only because of exposure are all kept."""
    short = frames("60.0", [480.0 + i for i in range(14)])
    long = frames("300.0", [2256.0 + i for i in range(20)])

    kept, excluded, splits = find_dominant_background_subset_by_exposure(short + long)

    assert len(kept) == 34
    assert excluded == []
    assert splits == []


def test_a_cloud_inside_one_exposure_length_is_still_found() -> None:
    """Frames far brighter than the rest of their own length are excluded."""
    short = frames("60.0", [480.0 + i for i in range(14)])
    long = frames("300.0", [2256.0 + i for i in range(17)] + [9000.0, 9100.0, 9200.0])

    kept, excluded, splits = find_dominant_background_subset_by_exposure(short + long)

    assert {frame.name for frame in excluded} == {"300.0_17", "300.0_18", "300.0_19"}
    assert len(kept) == 31
    assert [split["exposure_seconds"] for split in splits] == [300.0]


def test_the_kept_frames_keep_their_original_order() -> None:
    """The kept list follows the order the frames came in."""
    mixed = frames("300.0", [2256.0] * 6) + frames("60.0", [480.0] * 6)

    kept, _, _ = find_dominant_background_subset_by_exposure(mixed)

    assert [frame.name for frame in kept] == [frame.name for frame in mixed]


def test_no_frames_gives_empty_results() -> None:
    """An empty list has nothing to keep, exclude or split."""
    assert find_dominant_background_subset_by_exposure([]) == ([], [], [])
