"""Purpose: Unit tests for choosing which detected stars get a spectrum.

Description: Before any extraction, a star with no trail on the image and a
star with no catalog identity are left out, and the star limit counts only
what remains. An unidentified star that comes before the last analyzed star
is still read, as a neighbor only. These tests pin the filters, the limit,
the neighbor-only handling, the extended target, and a run with no identities
at all.
"""

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject
from astrometricslib.pipelines.spectroscopy.pipeline import (
    SpectroscopyPipeline,
    count_trail_samples_on_frame,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.neighbor_wing_correction import (
    STATUS_NOT_NEEDED,
    correct_neighbor_wings,
)
from astrometricslib.pipelines.spectroscopy.processing.spectrum_analysis import EXTENDED_TARGET_SPECTRAL_TYPE
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig

IMAGE_SIZE = 900
IMAGE = SimpleNamespace(data=np.zeros((IMAGE_SIZE, IMAGE_SIZE)))


def _build_pipeline() -> SpectroscopyPipeline:
    """Build a pipeline whose trails run down the image from each star.

    Returns
    -------
    pipeline : `SpectroscopyPipeline`
        A vertical, positive-direction setup with the trail starting 200
        pixels from the star.
    """
    camera = CameraConfig(
        name="TestCam",
        pixel_size_um=3.76,
        sensor_width_px=IMAGE_SIZE,
        sensor_height_px=IMAGE_SIZE,
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


def _star(name: str, x: float, y: float) -> StellarObject:
    """Build a detected star with a given id.

    Returns
    -------
    star : `StellarObject`
        A star at the pixel position.
    """
    return StellarObject(id=name, star_data={"xcentroid": x, "ycentroid": y})


def _ids(stars: list) -> list[str]:
    """List the ids of some stars.

    Returns
    -------
    ids : `list` [`str`]
        The ids, in order.
    """
    return [star.id for star in stars]


def test_a_trail_that_starts_off_the_image_has_no_samples() -> None:
    """The count is zero when the whole trail is past the edge."""
    vector = np.array([0.0, 1.0])

    on_frame = count_trail_samples_on_frame((100.0, 100.0), vector, 200.0, 300.0, (0.0, 0.0), (900, 900))
    off_frame = count_trail_samples_on_frame((100.0, 800.0), vector, 200.0, 300.0, (0.0, 0.0), (900, 900))
    partly = count_trail_samples_on_frame((100.0, 650.0), vector, 200.0, 300.0, (0.0, 0.0), (900, 900))

    assert on_frame == 300
    assert off_frame == 0
    assert partly == 50


def test_stars_with_no_trail_are_counted_and_left_out() -> None:
    """A star whose trail is off the frame is skipped and counted."""
    pipeline = _build_pipeline()
    stars = [_star("GAIA_1", 100, 100), _star("GAIA_2", 200, 850), _star("GAIA_3", 300, 120)]

    selection = pipeline.select_stars_for_spectra(IMAGE, stars, None)

    assert _ids(selection.stars) == ["GAIA_1", "GAIA_3"]
    assert selection.skipped_no_trail_count == 1
    assert selection.skipped_unidentified_count == 0


def test_the_limit_counts_only_the_stars_that_remain() -> None:
    """Stars left out do not take a place among the stars kept."""
    pipeline = _build_pipeline()
    stars = [
        _star("Star_1", 100, 100),
        _star("GAIA_A", 150, 100),
        _star("GAIA_B", 200, 850),
        _star("GAIA_C", 250, 100),
        _star("GAIA_D", 300, 100),
    ]

    selection = pipeline.select_stars_for_spectra(IMAGE, stars, 2)

    analyzed = [star for star in selection.stars if star not in selection.neighbor_only_stars]
    assert _ids(analyzed) == ["GAIA_A", "GAIA_C"]
    assert selection.skipped_no_trail_count == 1


def test_an_unidentified_star_before_the_last_analyzed_star_is_kept_as_a_neighbor() -> None:
    """It is read, not analyzed; one past the last analyzed star is not."""
    pipeline = _build_pipeline()
    stars = [
        _star("GAIA_A", 100, 100),
        _star("Star_1", 105, 100),
        _star("GAIA_B", 200, 100),
        _star("Star_2", 300, 100),
        _star("GAIA_C", 400, 100),
    ]

    selection = pipeline.select_stars_for_spectra(IMAGE, stars, 2)

    assert _ids(selection.stars) == ["GAIA_A", "Star_1", "GAIA_B"]
    assert _ids(selection.neighbor_only_stars) == ["Star_1"]
    assert selection.skipped_unidentified_count == 2


def test_with_no_limit_every_unidentified_star_is_kept_as_a_neighbor() -> None:
    """Without a limit the neighbors are the same as before the filter."""
    pipeline = _build_pipeline()
    stars = [_star("GAIA_A", 100, 100), _star("Star_1", 105, 100), _star("Star_2", 300, 100)]

    selection = pipeline.select_stars_for_spectra(IMAGE, stars, None)

    assert _ids(selection.stars) == ["GAIA_A", "Star_1", "Star_2"]
    assert _ids(selection.neighbor_only_stars) == ["Star_1", "Star_2"]


def test_a_position_only_star_is_analyzed() -> None:
    """A name made from the sky position is an identity that can be saved."""
    pipeline = _build_pipeline()
    stars = [_star("FIELD_J123456+654321", 100, 100)]

    selection = pipeline.select_stars_for_spectra(IMAGE, stars, None)

    assert _ids(selection.stars) == ["FIELD_J123456+654321"]
    assert selection.neighbor_only_stars == []
    assert selection.skipped_unidentified_count == 0


def test_the_extended_target_is_never_left_out_for_its_name() -> None:
    """A nebula or cluster is kept even with a placeholder-looking id."""
    pipeline = _build_pipeline()
    nebula = StellarObject(
        id="Star_9",
        stellar_spectral_type=EXTENDED_TARGET_SPECTRAL_TYPE,
        star_data={"xcentroid": 400.0, "ycentroid": 100.0},
        spectroscopy=SpectroscopyResult(extraction_radius=60),
    )

    selection = pipeline.select_stars_for_spectra(IMAGE, [nebula, _star("GAIA_A", 100, 100)], 1)

    assert selection.stars[0] is nebula
    assert selection.skipped_unidentified_count == 0


def test_a_run_with_no_identities_selects_nothing_and_does_not_raise() -> None:
    """Every star would be dropped at save, so nothing is extracted."""
    pipeline = _build_pipeline()
    stars = [_star("Star_1", 100, 100), _star("Star_2", 200, 100)]

    selection = pipeline.select_stars_for_spectra(IMAGE, stars, 10)

    assert selection.stars == []
    assert selection.skipped_unidentified_count == 2
    assert pipeline.process_image(IMAGE, target_stars=selection.stars) == []


def test_process_records_the_counts_and_hands_the_neighbors_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """`process` hands the selection on and keeps the counts."""
    pipeline = _build_pipeline()
    seen: dict = {}

    def fake_process_image(self: SpectroscopyPipeline, image: Any, **keywords: Any) -> list:
        """Record what `process` asked for.

        Returns
        -------
        results : `list`
            No results.
        """
        seen.update(keywords)
        return []

    monkeypatch.setattr(SpectroscopyPipeline, "process_image", fake_process_image)
    context = SimpleNamespace(
        image=IMAGE,
        extended_target=None,
        extended_source_hint=None,
        stellar_objects=[
            _star("GAIA_A", 100, 100),
            _star("Star_1", 105, 100),
            _star("GAIA_B", 200, 850),
            _star("GAIA_C", 300, 100),
        ],
    )

    assert pipeline.process(context, limit=2) == []

    assert pipeline.last_run_skipped_no_trail_count == 1
    assert pipeline.last_run_skipped_unidentified_count == 1
    assert _ids(seen["target_stars"]) == ["GAIA_A", "Star_1", "GAIA_C"]
    assert _ids(seen["neighbor_only_stars"]) == ["Star_1"]


def test_a_neighbor_only_star_is_read_but_not_corrected_or_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    """It is read and offered to the wing step, then dropped."""
    pipeline = _build_pipeline()
    pipeline.config.subtract_neighbor_wings = True
    wing_inputs: list[list[dict]] = []
    applied: list[str] = []

    def fake_single_star(self: SpectroscopyPipeline, image: Any, pos: Any, **keywords: Any) -> dict:
        """Give every star an empty extraction result.

        Returns
        -------
        result : `dict`
            A minimal result.
        """
        return {"zero_order_saturated_pixel_fraction": 0.0}

    def fake_wings(self: SpectroscopyPipeline, image: Any, results: list[dict]) -> None:
        """Record the results the wing step receives."""
        wing_inputs.append(list(results))

    def fake_apply(self: SpectroscopyPipeline, star: Any, result: Any, image: Any) -> None:
        """Record which stars were analyzed."""
        applied.append(star.id)

    monkeypatch.setattr(SpectroscopyPipeline, "_process_single_star", fake_single_star)
    monkeypatch.setattr(SpectroscopyPipeline, "_subtract_neighbor_wings", fake_wings)
    monkeypatch.setattr(SpectroscopyPipeline, "_apply_result_to_stellar_object", fake_apply)
    neighbor = _star("Star_1", 105, 100)
    analyzed = _star("GAIA_A", 100, 100)

    results = pipeline.process_image(
        IMAGE, target_stars=[analyzed, neighbor], auto_detect_angle=False, neighbor_only_stars=[neighbor]
    )

    assert [result["star_source"].id for result in results] == ["GAIA_A"]
    assert applied == ["GAIA_A"]
    assert [result.get("is_neighbor_only") for result in wing_inputs[0]] == [None, True]


def test_the_wing_step_corrects_no_neighbor_only_star() -> None:
    """A flagged star gets "not needed"; the others are as before."""
    outcomes = correct_neighbor_wings(
        np.zeros((50, 50)), [None, None], None, (0.0, 1.0), neighbor_only=[True, False]
    )

    assert outcomes[0].status == STATUS_NOT_NEEDED
    assert outcomes[1].status.startswith("skipped")
