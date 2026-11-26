"""Tests for the short, ranked list of objects in a stack's field.

A galaxy such as M 31 holds dozens of planetary nebulae, remnants and
candidates in SIMBAD. These tests check that only the few objects that could
matter in the picture are listed: bright ones first, then big ones, with
sub-components, candidates and tiny objects dropped.
"""

from astrometricslib.models.processing_results import BrightObjectChoice
from astrometricslib.pipelines.stacking.post_processing import bright_object_choices
from astrometricslib.pipelines.stacking.post_processing.bright_object_choices import (
    _Candidate,
    clean_candidates,
)


def candidate(
    name: str, object_type: str, axis: float | None, magnitude: float | None = None, offset: float = 0.0
) -> _Candidate:
    """Build one candidate.

    Returns
    -------
    candidate : `_Candidate`
        The candidate, at ``offset`` degrees of declination from the origin.
    """
    choice = BrightObjectChoice(
        simbad_id=name,
        object_type=object_type,
        category="galaxy" if object_type in ("G", "LIN") else "nebula",
        ra_deg=10.0,
        dec_deg=41.0 + offset,
        major_axis_arcmin=axis,
    )
    return _Candidate(distance=offset, object_id=None, choice=choice, magnitude=magnitude)


def test_an_m31_like_list_is_short_and_keeps_the_major_objects() -> None:
    """Parts, candidates and tiny objects go; five stay, brightest first."""
    candidates = [
        candidate("M 31", "G", 190.0, 3.4),
        candidate("M 32", "G", 8.0, 8.1, 0.1),
        candidate("NGC 205", "G", 20.0, 8.9, 0.2),
        candidate("NGC 206", "HII", 4.0, None, 0.01),
        candidate("Ford M 31 654 PN", "PN", 0.1, 20.0, 0.02),
        candidate("[B90] 95 SNR", "SNR", 0.2, None, 0.03),
        candidate("[B2015] M31 B127-38 PN?", "PN?", 3.5, None, 0.9),
        candidate("PGC 2", "G", 2.0, 15.0, 0.4),
        candidate("PGC 3", "G", 1.5, 16.0, 0.5),
        candidate("PGC 4", "G", 1.2, 17.0, 0.6),
        candidate("PGC 5", "G", 1.1, 18.0, 0.7),
    ]
    names = [entry.simbad_id for entry in clean_candidates(candidates, {})]

    assert len(names) <= bright_object_choices.MAXIMUM_LISTED_OBJECTS == 5
    assert names[:3] == ["M 31", "M 32", "NGC 205"]
    assert all("PN" not in name and "SNR" not in name for name in names)


def test_objects_without_a_magnitude_rank_by_size_after_the_measured_ones() -> None:
    """A faint measured object beats a big unmeasured one."""
    candidates = [
        candidate("NGC 1", "HII", 30.0, None),
        candidate("NGC 2", "HII", 10.0, None, 1.0),
        candidate("NGC 3", "G", 2.0, 14.0, 2.0),
    ]
    assert [entry.simbad_id for entry in clean_candidates(candidates, {})] == ["NGC 3", "NGC 1", "NGC 2"]


def test_m52_like_field_lists_the_bubble_not_giant_gas_structures() -> None:
    """Shells, clouds, infrared entries and frame-sized objects go."""
    candidates = [
        candidate("GSH 113+01-86", "sh", 203.0),
        candidate("[MML2017] 2249", "MoC", 74.6, None, 0.1),
        candidate("[MML2017] 5854", "MoC", 39.2, None, 0.2),
        candidate("[ABB2014] WISE G112.071+01.063", "HII", 17.6, None, 0.3),
        candidate("Sh 2-162", "HII", 50.0, None, 0.4),
        candidate("NGC 7635", "HII", 15.0, None, 0.41),
        candidate("Big Thing", "HII", 120.0, None, 0.5),
    ]
    names = [entry.simbad_id for entry in clean_candidates(candidates, {}, frame_short_side_arcmin=96.0)]

    assert names == ["NGC 7635", "Sh 2-162"]
    assert "GSH 113+01-86" not in names


def test_common_names_rank_before_unnamed_objects_with_a_magnitude() -> None:
    """A named object beats an unnamed one that has a magnitude."""
    candidates = [
        candidate("Unnamed", "HII", 5.0, 9.0, 2.0),
        candidate("NGC 7635", "HII", 15.0, None, 0.1),
    ]
    names = [entry.simbad_id for entry in clean_candidates(candidates, {})]

    assert names == ["NGC 7635", "Unnamed"]


def test_dark_nebulae_xray_sources_and_galaxy_parts_are_dropped() -> None:
    """Only optical nebulae and single galaxies stay; galaxy parts go."""
    candidates = [
        candidate("M 31", "G", 190.0, 3.4),
        candidate("LDN 1231", "DNe", 7.0, None, 0.5),
        candidate("RX J004344.1+411219", "ClG", 5.0, None, 0.6),
        candidate("[CHM2007] HDC 29", "GrG", 60.0, None, 0.7),
        candidate("[PAV78] 184", "EmO", 5.5, None, 0.1),
        candidate("M 32", "G", 3.4, 8.1, 0.2),
    ]
    names = [e.simbad_id for e in clean_candidates(candidates, {}, 96.0)]

    assert names == ["M 32"]
