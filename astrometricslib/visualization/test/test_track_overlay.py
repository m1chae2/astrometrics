"""Purpose: Unit tests for TrackOverlay.

Description: Verifies asteroid-detection candidates are projected from
sky coordinates into the displayed image's pixel frame and styled by
whether they matched a known body.
"""

import matplotlib.pyplot as plt
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib.models.moving_object import (
    AsteroidDetectionCandidate,
    CascadeStage,
    EphemerisMatch,
    FrameDetection,
)
from astrometricslib.visualization.layers.track_overlay import TrackOverlay
from astrometricslib.visualization.visualization_config import VisualizationConfig


def _build_wcs() -> WCS:
    """Build a real TAN WCS centered on a fixed sky position.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        A coordinate system usable to project RA/Dec into pixels.
    """
    header = fits.Header()
    header["WCSAXES"] = 2
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["CRVAL1"] = 150.0
    header["CRVAL2"] = 30.0
    header["CRPIX1"] = 32.0
    header["CRPIX2"] = 32.0
    header["CD1_1"] = -0.0005
    header["CD1_2"] = 0.0
    header["CD2_1"] = 0.0
    header["CD2_2"] = 0.0005
    header["CUNIT1"] = "deg"
    header["CUNIT2"] = "deg"
    return WCS(header)


def _make_candidate(**overrides) -> AsteroidDetectionCandidate:  # ruff: ignore[missing-type-kwargs]
    defaults = {
        "id": "candidate-1",
        "target_id": "M 13",
        "frame_detections": [
            FrameDetection(
                frame_path="/fake/frame1.fits",
                timestamp=1.0,
                pixel_x=10.0,
                pixel_y=10.0,
                right_ascension_deg=150.001,
                declination_deg=30.001,
            ),
            FrameDetection(
                frame_path="/fake/frame0.fits",
                timestamp=0.0,
                pixel_x=5.0,
                pixel_y=5.0,
                right_ascension_deg=150.0,
                declination_deg=30.0,
            ),
        ],
        "cascade_stage": CascadeStage.RATE_LINEARITY_CONFIRMED,
    }
    defaults.update(overrides)
    return AsteroidDetectionCandidate(**defaults)


def test_render_draws_one_line_per_candidate_with_detections():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A candidate with detections gets one Line2D artist."""
    fig, ax = plt.subplots()
    overlay = TrackOverlay(ax, VisualizationConfig())

    lines = overlay.render([_make_candidate()], _build_wcs())

    assert len(lines) == 1
    plt.close(fig)


def test_render_skips_candidates_with_no_detections():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A candidate that somehow has zero detections draws nothing."""
    fig, ax = plt.subplots()
    overlay = TrackOverlay(ax, VisualizationConfig())

    lines = overlay.render([_make_candidate(frame_detections=[])], _build_wcs())

    assert lines == []
    plt.close(fig)


def test_render_orders_the_path_by_timestamp_not_input_order():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The drawn path follows time, not the input order."""
    fig, ax = plt.subplots()
    overlay = TrackOverlay(ax, VisualizationConfig())
    wcs = _build_wcs()
    candidate = _make_candidate()
    expected_x, expected_y = wcs.world_to_pixel_values(
        [d.right_ascension_deg for d in sorted(candidate.frame_detections, key=lambda d: d.timestamp)],
        [d.declination_deg for d in sorted(candidate.frame_detections, key=lambda d: d.timestamp)],
    )

    (line,) = overlay.render([candidate], wcs)

    drawn_x, drawn_y = line.get_data()
    assert list(drawn_x) == list(expected_x)
    assert list(drawn_y) == list(expected_y)
    plt.close(fig)


def test_matched_candidate_uses_matched_color_and_designation_label():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A candidate matched to a known body is styled and labeled as such."""
    fig, ax = plt.subplots()
    config = VisualizationConfig()
    overlay = TrackOverlay(ax, config)
    candidate = _make_candidate(
        cascade_stage=CascadeStage.EPHEMERIS_MATCHED,
        ephemeris_match=EphemerisMatch(designation="(433) Eros", angular_separation_arcsec=2.0),
    )

    (line,) = overlay.render([candidate], _build_wcs())

    assert line.get_color() == config.matched_track_color
    assert line.get_linestyle() == "-"
    labels = [t.get_text() for t in ax.texts]
    assert "(433) Eros" in labels
    plt.close(fig)


def test_unconfirmed_candidate_uses_unconfirmed_color_and_generic_label():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A candidate not matched to any known body is styled accordingly."""
    fig, ax = plt.subplots()
    config = VisualizationConfig()
    overlay = TrackOverlay(ax, config)
    candidate = _make_candidate(cascade_stage=CascadeStage.RATE_LINEARITY_CONFIRMED, ephemeris_match=None)

    (line,) = overlay.render([candidate], _build_wcs())

    assert line.get_color() == config.unconfirmed_track_color
    assert line.get_linestyle() == "--"
    labels = [t.get_text() for t in ax.texts]
    assert "unconfirmed mover" in labels
    plt.close(fig)
