"""Tests that SourceDetector.deduplicate merges only a star reported twice.

A merged source sits at the position of the brightest detection in its own
group, whatever its place in the brightness-sorted list (issue #74), never at
the average of separate stars (issue #175). With no distance given, only
detections within one kernel width merge, so stars the finder kept apart stay
apart.
"""

import pytest

from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector


def _source(x: float, y: float, flux: float) -> dict[str, float]:
    """Build one detection in the shape DAOStarFinder output is converted to.

    Returns
    -------
    source : `dict`
        A detection with ``x_centroid``, ``y_centroid`` and ``flux``.
    """
    return {"x_centroid": x, "y_centroid": y, "flux": flux}


def test_merged_pair_sits_at_its_brightest_member() -> None:
    """A pair below the brightest sources merges at its brighter member."""
    sources = [
        _source(100.0, 100.0, 9000.0),
        _source(400.0, 300.0, 8000.0),
        _source(700.0, 700.0, 500.0),
        _source(704.0, 702.0, 400.0),
    ]

    merged = SourceDetector().deduplicate(sources, separation_px=15.0)

    assert len(merged) == 3
    pair = merged[2]
    assert pair["x_centroid"] == pytest.approx(700.0)
    assert pair["y_centroid"] == pytest.approx(700.0)
    assert pair["flux"] == pytest.approx(900.0)


def test_merged_pair_with_legacy_centroid_keys() -> None:
    """Detections using ``xcentroid``/``ycentroid`` keys merge the same way."""
    sources = [
        {"xcentroid": 10.0, "ycentroid": 10.0, "flux": 5000.0},
        {"xcentroid": 300.0, "ycentroid": 40.0, "flux": 100.0},
        {"xcentroid": 306.0, "ycentroid": 44.0, "flux": 300.0},
    ]

    merged = SourceDetector().deduplicate(sources, separation_px=15.0)

    assert len(merged) == 2
    assert merged[1]["xcentroid"] == pytest.approx(306.0)
    assert merged[1]["ycentroid"] == pytest.approx(44.0)
    assert merged[1]["flux"] == pytest.approx(400.0)


def test_isolated_sources_are_unchanged() -> None:
    """Detections farther apart than the separation are returned unchanged."""
    sources = [_source(0.0, 0.0, 3.0), _source(50.0, 0.0, 2.0), _source(0.0, 50.0, 1.0)]

    assert SourceDetector().deduplicate(sources, separation_px=15.0) == sources


def test_by_default_stars_the_finder_kept_apart_stay_apart() -> None:
    """Two stars 2.5 kernel widths apart (the finder's spacing) are kept."""
    detector = SourceDetector(fwhm=4.0)
    sources = [_source(100.0, 100.0, 900.0), _source(110.0, 100.0, 800.0)]

    assert detector.deduplicate(sources) == sources


def test_by_default_a_star_reported_twice_is_merged() -> None:
    """Two detections within one kernel width are one star."""
    detector = SourceDetector(fwhm=4.0)
    sources = [_source(100.0, 100.0, 900.0), _source(102.0, 101.0, 100.0)]

    merged = detector.deduplicate(sources)

    assert len(merged) == 1
    assert merged[0]["x_centroid"] == pytest.approx(100.0)
    assert merged[0]["flux"] == pytest.approx(1000.0)
