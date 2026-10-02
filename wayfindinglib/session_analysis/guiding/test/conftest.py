"""Purpose: Shared builders for the guiding-analysis tests.

Description: Builds a realistic equipment envelope, synthetic guide samples
and guiding runs, so each test states only what it is about. The defaults
match this observatory: a 121.05 mm guide scope with a 3.75 um camera
(6.39 arcsec/px), a measured star width of 5.59 arcsec, and a guide cycle of
about 3.2 seconds.
"""

import numpy as np
import pytest

from wayfindinglib.analytics.performance_envelope import MeasuredImageQuality, derive_performance_envelope
from wayfindinglib.models.equipment_and_site.equipment import (
    Camera,
    EquipmentConfiguration,
    GuideScope,
    Telescope,
)
from wayfindinglib.models.session.guiding_run import GuidingRunSummary

NIGHT_START = 1790217108.0
"""2026-09-23 20:31:48 MDT, as seconds since the epoch."""

GOOD_BASELINE = {
    "guide_snr": [250.0, 300.0, 280.0, 320.0, 290.0, 310.0],
    "guiding_rms": [1.0, 1.2, 0.9, 1.1, 1.3, 1.0],
    "guide_star_mass": [250000.0, 300000.0, 280000.0, 320000.0, 290000.0, 310000.0],
    "guide_lost_fraction": [0.0, 0.0, 0.0, 0.001, 0.0, 0.002],
    "guide_excursion_fraction": [0.0, 0.0, 0.0, 0.001, 0.0, 0.002],
}
"""Six earlier nights of this equipment, all good."""


@pytest.fixture
def good_baseline() -> dict:
    """Return a copy of the standard good baseline.

    Returns
    -------
    baseline : `dict`
        Six good earlier nights of this equipment.
    """
    return {key: list(values) for key, values in GOOD_BASELINE.items()}


@pytest.fixture
def make_envelope():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Return a function that builds this observatory's performance envelope.

    Returns
    -------
    make : `Callable`
        ``make(star_width=5.59, baseline=GOOD_BASELINE)`` returns the
        envelope. Pass ``star_width=None`` for equipment with no frames yet.
    """

    def make(star_width: float | None = 5.59, baseline: dict | None = GOOD_BASELINE):  # ruff: ignore[missing-return-type-private-function]
        """Build the envelope.

        Returns
        -------
        envelope : `PerformanceEnvelope`
            The derived limits.
        """
        telescope = Telescope(id="t", name="Apertura 75Q", focal_length_mm=405.0)
        camera = Camera(
            id="c", name="ZWO ASI 533MM Pro", pixel_size_um=3.76, sensor_width_px=3008, sensor_height_px=3008
        )
        guide_camera = Camera(
            id="g", name="ZWO ASI120MC-S", pixel_size_um=3.75, sensor_width_px=1280, sensor_height_px=960
        )
        return derive_performance_envelope(
            EquipmentConfiguration(telescope=telescope, camera=camera),
            GuideScope(id="gs", name="Apertura 32mm", focal_length_mm=121.05, aperture_mm=32.0),
            guide_camera,
            "fingerprint-a",
            measured_image_quality=None if star_width is None else MeasuredImageQuality(star_width, 361),
            baseline_values=baseline,
            guide_cadence_seconds=3.2,
        )

    return make


@pytest.fixture
def make_samples():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Return a function that builds synthetic guide samples.

    Returns
    -------
    make : `Callable`
        Builds evenly spaced samples with Gaussian errors.
    """

    def make(  # ruff: ignore[missing-return-type-private-function]
        count: int = 400,
        start: float = NIGHT_START + 60.0,
        cadence: float = 3.2,
        sigma: float = 1.0,
        snr: float = 300.0,
        pulse_dec_ms: float = 0.0,
        seed: int = 1,
        star_mass: float = 300000.0,
    ):
        """Build the samples.

        Returns
        -------
        samples : `list` [`dict`]
            Samples with ``timestamp``, ``dra``, ``ddec``, ``pulse_dec`` and
            ``snr``; errors in arcseconds.
        """
        generator = np.random.default_rng(seed)
        return [
            {
                "timestamp": start + cadence * index,
                "dra": float(generator.normal(0.0, sigma)),
                "ddec": float(generator.normal(0.0, sigma)),
                "pulse_ra": 0.0,
                "pulse_dec": pulse_dec_ms,
                "snr": snr,
                "star_mass": star_mass,
            }
            for index in range(count)
        ]

    return make


@pytest.fixture
def make_run():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Return a function that builds a guiding run.

    Returns
    -------
    make : `Callable`
        Builds a run with this observatory's guide optics.
    """

    def make(
        index: int = 0,
        start: float = NIGHT_START + 60.0,
        duration: float = 1300.0,
        frames_total: int = 400,
        frames_lost: int = 0,
        samples_stored: int = 400,
        ra_rate: float | None = 5.0,
        dec_rate: float | None = 7.5,
    ) -> GuidingRunSummary:
        """Build the run.

        Returns
        -------
        run : `GuidingRunSummary`
            The run.
        """
        return GuidingRunSummary(
            id=f"guide_log-test.txt#{index}",
            session_id="2026-09-23",
            source_file_name="guide_log-test.txt",
            written_by_ekos=True,
            started_at=start,
            ended_at=start + duration,
            pixel_scale_arcsec_per_px=6.39,
            focal_length_mm=121.05,
            ra_rate_arcsec_per_second=ra_rate,
            dec_rate_arcsec_per_second=dec_rate,
            frames_total=frames_total,
            frames_lost=frames_lost,
            samples_stored=samples_stored,
            altitude_degrees=66.0,
            azimuth_degrees=170.0,
            declination_degrees=22.8,
            pier_side="West",
        )

    return make
