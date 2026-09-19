"""Purpose: Unit tests for plot_asteroid_detection.

Description: Verifies the target's stacked image and its detected
candidates render together, and that a missing stacked_image is
rejected the same way the other per-pipeline plot functions are.
"""

from types import SimpleNamespace

import matplotlib.pyplot as plt
import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.moving_object import AsteroidDetectionCandidate, CascadeStage, FrameDetection
from astrometricslib.visualization.helpers import plot_asteroid_detection


def _write_stack_fits(path) -> None:  # ruff: ignore[missing-type-function-argument]
    """Write a synthetic stack FITS file with a real TAN WCS header."""
    header = fits.Header()
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
    fits.PrimaryHDU(np.zeros((64, 64), dtype=np.float32), header=header).writeto(path, overwrite=True)


def test_plot_asteroid_detection_raises_on_missing_stacked_image():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a target with no stacked_image is rejected."""
    target = SimpleNamespace(id="M 13", stacked_image=None, asteroid_candidates=[])

    with pytest.raises(ValueError, match="has no stacked_image"):
        plot_asteroid_detection(target)


def test_plot_asteroid_detection_draws_a_track_for_each_candidate(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a candidate's detections are drawn as a track."""
    stack_path = tmp_path / "stack.fits"
    _write_stack_fits(stack_path)

    candidate = AsteroidDetectionCandidate(
        id="candidate-1",
        target_id="M 13",
        frame_detections=[
            FrameDetection(
                frame_path="/fake/frame0.fits",
                timestamp=0.0,
                pixel_x=5.0,
                pixel_y=5.0,
                right_ascension_deg=150.0,
                declination_deg=30.0,
            ),
            FrameDetection(
                frame_path="/fake/frame1.fits",
                timestamp=1.0,
                pixel_x=10.0,
                pixel_y=10.0,
                right_ascension_deg=150.001,
                declination_deg=30.001,
            ),
        ],
        cascade_stage=CascadeStage.RATE_LINEARITY_CONFIRMED,
    )
    target = SimpleNamespace(id="M 13", stacked_image=str(stack_path), asteroid_candidates=[candidate])

    fig = plot_asteroid_detection(target)

    (ax,) = fig.axes
    assert len(ax.lines) == 1
    plt.close(fig)


def test_plot_asteroid_detection_with_no_candidates_still_renders_the_image(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a target with no candidates still returns a usable figure."""
    stack_path = tmp_path / "stack.fits"
    _write_stack_fits(stack_path)
    target = SimpleNamespace(id="M 13", stacked_image=str(stack_path), asteroid_candidates=[])

    fig = plot_asteroid_detection(target)

    (ax,) = fig.axes
    assert len(ax.lines) == 0
    plt.close(fig)
