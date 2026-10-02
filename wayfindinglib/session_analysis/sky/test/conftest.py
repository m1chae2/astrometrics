"""Purpose: Shared builders for the sky-analysis tests.

Description: Builds synthetic nights of measurements in which a chosen part
of the sky is made worse by a known amount, so each test can check that the
analysis finds that effect and nothing else.
"""

from typing import Any

import numpy as np
import pytest

from wayfindinglib.models.session.sky_quality import SkySample


@pytest.fixture
def make_night() -> Any:
    """Return a function that builds one night of star-width samples.

    Returns
    -------
    make : `Callable`
        ``make(night, bands={...})`` returns the night's samples.
    """

    def make(
        night: str,
        bands: dict[float, float] | None = None,
        per_band: int = 10,
        metric: str = "star_width",
        base: float = 5.0,
        night_factor: float = 1.0,
        noise: float = 0.02,
        seed: int = 0,
        pier_side: str | None = "West",
        azimuth: float | None = 180.0,
    ) -> list[SkySample]:
        """Build the samples.

        Returns
        -------
        samples : `list` [`SkySample`]
            `per_band` samples at each altitude in `bands`. Each altitude maps
            to a factor applied to `base`, so ``{35.0: 1.25, 65.0: 1.0}`` makes
            the 35 degree samples 25 percent worse. `night_factor` scales the
            whole night, as poor seeing would.
        """
        generator = np.random.default_rng(seed + sum(map(ord, night)))
        samples = []
        for altitude, factor in (bands or {35.0: 1.0, 65.0: 1.0}).items():
            for _ in range(per_band):
                value = base * night_factor * factor * (1.0 + generator.normal(0.0, noise))
                samples.append(
                    SkySample(
                        night=night,
                        metric=metric,
                        value=float(value),
                        altitude_degrees=altitude,
                        azimuth_degrees=azimuth,
                        pier_side=pier_side,
                    )
                )
        return samples

    return make


@pytest.fixture
def make_nights(make_night: Any) -> Any:
    """Return a function that builds several nights with one planted effect.

    Returns
    -------
    make : `Callable`
        ``make(nights=8, bands={...})`` returns every night's samples.
    """

    def make(nights: int = 8, **kwargs: Any) -> list[SkySample]:
        """Build the nights.

        Returns
        -------
        samples : `list` [`SkySample`]
            The samples of nights ``2026-01-01`` onward, with a different
            overall quality each night.
        """
        samples: list[SkySample] = []
        for index in range(nights):
            samples += make_night(
                f"2026-01-{index + 1:02d}", night_factor=1.0 + 0.1 * (index % 3), seed=index, **kwargs
            )
        return samples

    return make
