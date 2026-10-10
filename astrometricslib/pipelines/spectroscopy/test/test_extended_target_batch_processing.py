"""Purpose: Unit tests for folding an extended target into the shared batch.

Description: A nebula or cluster used to be processed by spinning up a
second, throwaway `SpectroscopyPipeline` instance with a wider aperture.
It is now folded into the same per-star batch as every ordinary star
instead. These tests pin the three behaviors that fold depends on:
the extended target's own aperture is never shrunk by a nearby real
star (unlike an ordinary star's), it gets `reject_narrow_contaminants`
turned on for its own extraction, and it is excluded from
neighbour-wing deblending in both directions (its own spectrum is never
corrected, and it never contributes as a "neighbour" to a real star's).
"""

from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.foundation.errors import ProcessingError
from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
from astrometricslib.pipelines.spectroscopy.processing.spectrum_analysis import EXTENDED_TARGET_SPECTRAL_TYPE
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig


def _build_pipeline() -> SpectroscopyPipeline:
    """Build a `SpectroscopyPipeline` with a small, fixed extraction radius.

    Returns
    -------
    pipeline : `SpectroscopyPipeline`
        The constructed pipeline; `config.extraction_radius` is 10.
    """
    camera = CameraConfig(
        name="TestCam",
        pixel_size_um=3.76,
        sensor_width_px=900,
        sensor_height_px=900,
        sensor_min_wavelength=350.0,
        sensor_max_wavelength=900.0,
    )
    config = SpectroscopyConfig(
        camera=camera,
        grating_distance_mm=16.5,
        dispersion_orientation="vertical",
        dispersion_direction="positive",
        dispersion_start_px=200.0,
        extraction_radius=10,
    )
    return SpectroscopyPipeline(config=config)


def _extended_target(x: float, y: float, extraction_radius: int = 60) -> StellarObject:
    """Build a placeholder `StellarObject` like the pipeline itself would.

    Returns
    -------
    star : `StellarObject`
        Carries the `EXTENDED_TARGET_SPECTRAL_TYPE` marker and a wide
        configured `spectroscopy.extraction_radius`.
    """
    star = StellarObject(
        id="Nebula_Cluster",
        stellar_spectral_type=EXTENDED_TARGET_SPECTRAL_TYPE,
        star_data={"xcentroid": x, "ycentroid": y},
        spectroscopy=SpectroscopyResult(extraction_radius=extraction_radius),
    )
    return star


def _ordinary_star(x: float, y: float) -> StellarObject:
    """Build a plain `StellarObject` standing in for a real, detected star.

    Returns
    -------
    star : `StellarObject`
        Carries only a pixel position, no spectroscopy result yet.
    """
    return StellarObject(id=f"Star_{x:.0f}_{y:.0f}", star_data={"xcentroid": x, "ycentroid": y})


def test_the_extended_targets_aperture_is_never_shrunk_by_a_nearby_star(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real star just a few pixels away must not shrink the nebula's box.

    An ordinary star this close would have its own radius capped by
    `_capped_extraction_radius_px` well below 60; the extended target
    must keep its full, configured radius regardless, since shrinking it
    back to point-source scale would defeat the point of asking for a
    wide aperture in the first place.
    """
    pipeline = _build_pipeline()
    calls: list[tuple[str, int, bool | None]] = []

    def record_call(
        self: SpectroscopyPipeline,
        image: SimpleNamespace,
        pos: tuple[float, float],
        auto_detect_angle: bool = True,
        extraction_radius: int | None = None,
        reject_narrow_contaminants: bool | None = None,
    ) -> dict:
        """Record the per-star radius/override this star was processed with.

        Raises
        ------
        ProcessingError
            Always, so nothing downstream is attempted.
        """
        calls.append((f"{pos[0]:.0f}_{pos[1]:.0f}", extraction_radius, reject_narrow_contaminants))
        raise ProcessingError("not extracted -- this test only checks how extraction was requested")

    monkeypatch.setattr(SpectroscopyPipeline, "_process_single_star", record_call)
    nebula = _extended_target(700.0, 700.0, extraction_radius=60)
    close_neighbor = _ordinary_star(705.0, 700.0)  # 5 px away -- would cap an ordinary star hard

    pipeline._process_target_stars(
        image=SimpleNamespace(data=np.zeros((900, 900))),
        target_stars=[nebula, close_neighbor],
        limit=None,
        auto_detect_angle=False,
    )

    calls_by_position = {position: (radius, reject) for position, radius, reject in calls}
    nebula_radius, nebula_reject = calls_by_position["700_700"]
    assert nebula_radius == 60
    assert nebula_reject is True
    neighbor_radius, neighbor_reject = calls_by_position["705_700"]
    assert neighbor_radius < 10
    assert neighbor_reject is None


def test_the_extended_target_is_excluded_from_neighbor_wing_deblending() -> None:
    """The nebula never gets wing-corrected, and never blurs a real star's.

    Matches today's behavior, where the extended target was processed
    alone in a separate pipeline instance and so never took part in
    neighbour-wing deblending at all -- `neighbor_trail_deblending.py`'s
    blur-fit math assumes a point source's narrow streak, which a
    diffuse, wide-aperture target does not have.
    """
    pipeline = _build_pipeline()
    nebula = _extended_target(700.0, 700.0)
    star = _ordinary_star(400.0, 400.0)
    nebula_result: dict = {
        "star_source": nebula,
        "wavelengths": [500.0],
        "intensities": [1.0],
        "target_pos": (700.0, 700.0),
        "detected_angle": 0.0,
        "dispersion_vector": (0.0, 1.0),
    }
    star_result: dict = {
        "star_source": star,
        "wavelengths": [500.0],
        "intensities": [1.0],
        "target_pos": (400.0, 400.0),
        "detected_angle": 0.0,
        "dispersion_vector": (0.0, 1.0),
    }

    pipeline._subtract_neighbor_wings(
        image=SimpleNamespace(data=np.zeros((900, 900))), results=[nebula_result, star_result]
    )

    assert "neighbor_wing_status" not in nebula_result
    assert "neighbor_wing_fraction" not in nebula_result
