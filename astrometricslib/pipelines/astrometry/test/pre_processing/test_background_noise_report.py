"""Tests that the source detector reports the background noise it used."""

import numpy as np
import pytest

from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector


def test_the_detector_keeps_the_background_noise_of_its_last_picture() -> None:
    """A flat picture with sigma 3 reports a noise of about 3."""
    detector = SourceDetector()
    assert detector.last_background_noise is None
    data = np.random.default_rng(5).normal(100.0, 3.0, (300, 300))
    detector.detect(data, with_radius=False)
    assert detector.last_background_noise == pytest.approx(3.0, rel=0.05)
