"""Purpose: Test the dereddening and line-index cross-checks end to end.

Description: These tests cover the wiring around the two cross-checks. A
`SpectroscopyPipeline` is built with a fake reddening driver, so no Gaia query
leaves the machine, and it processes the counts a reddened G0V star of known
reddening would give this instrument. The tests check which star gets a
reddening lookup (only one with a Gaia DR3 id), what the result records, and
that quality checkpoint 2 carries ``ebv_used``,
``dereddening_type_shift_steps`` and ``index_vs_template_type_steps`` with the
``slope_and_lines_disagree`` flag.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.drivers.interfaces.reddening_driver import ReddeningDriver, ReddeningEstimate
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.spectral_cross_checks import LineIndexClassification, ReddeningRecord
from astrometricslib.models.spectroscopy_quality import StageQualityCheckpoint, StageQualityMetric
from astrometricslib.models.stellar_source import DIFFERS_FROM_CATALOG_SUBTYPES, StellarObject
from astrometricslib.pipelines.shared.catalog_star_identity import gaia_dr3_source_id_of
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
from astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response import load_instrument_response
from astrometricslib.pipelines.spectroscopy.pre_processing.quantum_efficiency_correction import (
    curve_from_profile_record,
    interpolate_quantum_efficiency,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    blur_to_resolution_profile,
    load_line_spread_profile,
)
from astrometricslib.pipelines.spectroscopy.processing.assess_processing_quality import (
    assess_processing_quality,
)
from astrometricslib.pipelines.spectroscopy.processing.interstellar_extinction import redden_spectrum
from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import _get_reference_templates
from astrometricslib.pipelines.spectroscopy.processing.star_reddening import (
    look_up_star_reddening,
)
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig

CAMERA_NAME = "ZWO ASI 533MM Pro"
GAIA_ID = 1328045433153485824
EBV = 0.3
_FAKE_IMAGE = SimpleNamespace()


class FakeReddening(ReddeningDriver):
    """A reddening source with one fixed answer.

    Attributes
    ----------
    calls : `list` [`int`]
        The Gaia source ids asked for, in order.
    """

    def __init__(self, ebv: float | None, fail: bool = False) -> None:
        """Store the answer.

        Parameters
        ----------
        ebv : `float` or `None`
            The E(B-V) to give for every id, or `None` for "no value".
        fail : `bool`, optional
            Raise `ExternalServiceError` instead of answering.
        """
        self._ebv = ebv
        self._fail = fail
        self.calls: list[int] = []

    def get_reddening(self, gaia_source_id: int) -> ReddeningEstimate | None:
        """Record the call and answer.

        Returns
        -------
        estimate : `ReddeningEstimate` or `None`
            The fixed answer.

        Raises
        ------
        ExternalServiceError
            When the fake was built to fail.
        """
        self.calls.append(gaia_source_id)
        if self._fail:
            raise ExternalServiceError("fake outage")
        if self._ebv is None:
            return None
        return ReddeningEstimate(ebv=self._ebv, source="fake catalog", gaia_source_id=gaia_source_id)


def build_pipeline(driver: ReddeningDriver) -> SpectroscopyPipeline:
    """Build a pipeline for the test camera with a given reddening source.

    Parameters
    ----------
    driver : `ReddeningDriver`
        The reddening source.

    Returns
    -------
    pipeline : `SpectroscopyPipeline`
        The pipeline.
    """
    camera = CameraConfig(
        name=CAMERA_NAME,
        pixel_size_um=3.76,
        sensor_width_px=900,
        sensor_height_px=900,
        sensor_min_wavelength=300.0,
        sensor_max_wavelength=1000.0,
    )
    config = SpectroscopyConfig(camera=camera, grating_distance_mm=16.5)
    return SpectroscopyPipeline(config=config, drivers=Drivers(reddening=driver))


def extraction_result(spectral_type: str, ebv: float) -> dict[str, object]:
    """Build the extraction result a reddened star of known type would give.

    The template is blurred to the instrument's line spread, reddened, then
    multiplied by the sensor's quantum efficiency and the instrument response,
    which the pipeline divides out again.

    Parameters
    ----------
    spectral_type : `str`
        The template's label.
    ebv : `float`
        The reddening to apply, in magnitudes.

    Returns
    -------
    result : `dict`
        An extraction result for `_apply_result_to_stellar_object`.
    """
    wavelength, flux = _get_reference_templates()[spectral_type]
    profile = load_line_spread_profile(CAMERA_NAME)
    assert profile is not None
    reddened = redden_spectrum(wavelength, blur_to_resolution_profile(wavelength, flux, profile), ebv)
    curve = curve_from_profile_record(resolve_camera_profile(CAMERA_NAME).quantum_efficiency)
    response = load_instrument_response(CAMERA_NAME)
    quantum_efficiency = interpolate_quantum_efficiency(wavelength / 10.0, curve)
    counts = reddened * quantum_efficiency * response.value_at(wavelength)
    return {
        "detected_angle": 0.0,
        "wavelengths": (wavelength / 10.0).tolist(),
        "intensities": counts.tolist(),
        "target_pos": (100.0, 200.0),
        "trail_centerline_px": None,
        "trail_width_px": None,
    }


def metrics_of(checkpoint: StageQualityCheckpoint) -> dict[str, StageQualityMetric]:
    """Index a checkpoint's metrics by name.

    Returns
    -------
    metrics : `dict` [`str`, `StageQualityMetric`]
        The metrics keyed by name.
    """
    return {entry.name: entry for entry in checkpoint.metrics}


def processing_checkpoint_of(star: StellarObject) -> StageQualityCheckpoint:
    """Pick the ``processing`` checkpoint out of a star's spectrum.

    Returns
    -------
    checkpoint : `StageQualityCheckpoint`
        Checkpoint 2.
    """
    return next(entry for entry in star.spectroscopy.stage_quality if entry.stage == "processing")


# ------------------------------------------------------- finding the id


@pytest.mark.parametrize(
    ("star", "expected"),
    [
        (StellarObject(id=f"Gaia DR3 {GAIA_ID}", name=f"Gaia DR3 {GAIA_ID}"), GAIA_ID),
        (StellarObject(id="Star_4", name=f"Gaia DR3 {GAIA_ID}"), GAIA_ID),
        (StellarObject(id="HD 172167", name="Vega", target_ids=[f"Gaia DR3 {GAIA_ID}"]), GAIA_ID),
        (StellarObject(id="HD 172167", name="Vega", gaia_dr3_source_id=GAIA_ID), GAIA_ID),
        (StellarObject(id="HD 172167", name="Vega"), None),
        (StellarObject(id="Gaia DR3 J279.2347+38.7837", name="Gaia DR3 J279.2347+38.7837"), None),
        (StellarObject(id="FIELD_J2792+3878", name=""), None),
    ],
)
def test_the_gaia_id_is_read_from_the_star_names(star: StellarObject, expected: int | None) -> None:
    """Verify only a ``Gaia DR3 <digits>`` name gives a source id."""
    assert gaia_dr3_source_id_of(star) == expected


def test_a_star_with_no_gaia_id_is_not_looked_up() -> None:
    """Verify the driver is never asked about a SIMBAD-named star."""
    driver = FakeReddening(0.3)

    assert look_up_star_reddening(StellarObject(id="HD 172167", name="Vega"), driver) is None
    assert driver.calls == []


def test_a_failed_lookup_gives_no_reddening_and_no_error() -> None:
    """Verify a catalog outage leaves the star unreddened, without an error."""
    driver = FakeReddening(0.3, fail=True)

    star = StellarObject(id=f"Gaia DR3 {GAIA_ID}", name=f"Gaia DR3 {GAIA_ID}")

    assert look_up_star_reddening(star, driver) is None
    assert driver.calls == [GAIA_ID]


# --------------------------------------------- the pipeline-built result


def test_the_pipeline_dereddens_a_gaia_star_and_checkpoint_2_reports_it() -> None:
    """Verify a reddened G0V with a Gaia id is typed G0V and reported."""
    driver = FakeReddening(EBV)
    pipeline = build_pipeline(driver)
    star = StellarObject(id=f"Gaia DR3 {GAIA_ID}", name=f"Gaia DR3 {GAIA_ID}")

    pipeline._apply_result_to_stellar_object(star, extraction_result("G0V", EBV), _FAKE_IMAGE)

    result = star.spectroscopy
    assert driver.calls == [GAIA_ID]
    assert result.self_determined_spectral_type == "G0V"
    assert result.reddening is not None
    assert result.reddening.ebv == pytest.approx(EBV)
    assert result.reddening.ebv_source == "fake catalog"
    assert result.reddening.gaia_source_id == str(GAIA_ID)
    assert result.reddening.dereddened_best_type == "G0V"
    assert result.reddening.observed_best_type != "G0V"
    assert "dereddened with E(B-V) = 0.30" in result.self_determined_spectral_type_note

    checkpoint = processing_checkpoint_of(star)
    metrics = metrics_of(checkpoint)
    assert metrics["ebv_used"].value == pytest.approx(EBV)
    assert metrics["ebv_used"].unit == "magnitude"
    assert "fake catalog" in metrics["ebv_used"].note
    assert metrics["ebv_used"].limit is None
    assert metrics["dereddening_type_shift_steps"].value is not None
    assert metrics["dereddening_type_shift_steps"].value >= 3
    assert metrics["dereddening_type_shift_steps"].limit is None
    index_metric = metrics["index_vs_template_type_steps"]
    assert result.line_index_classification is not None
    assert index_metric.value == pytest.approx(result.line_index_classification.steps_from_template_fit)
    assert index_metric.limit == DIFFERS_FROM_CATALOG_SUBTYPES
    assert index_metric.passed is True
    assert "slope_and_lines_disagree" not in checkpoint.flags


def test_a_star_without_a_gaia_id_is_classified_as_observed_and_the_metrics_are_empty() -> None:
    """Verify the metrics are present with no value when there is no E(B-V)."""
    driver = FakeReddening(EBV)
    pipeline = build_pipeline(driver)
    star = StellarObject(id="HD 172167", name="Vega")

    pipeline._apply_result_to_stellar_object(star, extraction_result("G0V", EBV), _FAKE_IMAGE)

    assert driver.calls == []
    assert star.spectroscopy.reddening is None
    assert star.spectroscopy.self_determined_spectral_type not in ("G0V", "Unknown")
    metrics = metrics_of(processing_checkpoint_of(star))
    assert metrics["ebv_used"].value is None
    assert "no catalog E(B-V)" in metrics["ebv_used"].note
    assert metrics["dereddening_type_shift_steps"].value is None
    # The line indices do not depend on the tilt, so they still give a type.
    assert metrics["index_vs_template_type_steps"].value is not None


def test_a_gaia_star_the_catalog_has_no_value_for_is_classified_as_observed() -> None:
    """Verify a `None` answer from the catalog leaves the spectrum observed."""
    driver = FakeReddening(None)
    pipeline = build_pipeline(driver)
    star = StellarObject(id=f"Gaia DR3 {GAIA_ID}", name=f"Gaia DR3 {GAIA_ID}")

    pipeline._apply_result_to_stellar_object(star, extraction_result("G0V", EBV), _FAKE_IMAGE)

    assert driver.calls == [GAIA_ID]
    assert star.spectroscopy.reddening is None


# ------------------------------------ the metrics and flag, built directly


def checkpoint_with_index_distance(steps: float) -> StageQualityCheckpoint:
    """Build checkpoint 2 with a line-index type `steps` from the template fit.

    Parameters
    ----------
    steps : `float`
        Steps on the ladder between the two types.

    Returns
    -------
    checkpoint : `StageQualityCheckpoint`
        The processing checkpoint.
    """
    return assess_processing_quality(
        classification={
            "spectral_type": "G2V",
            "classification_rms": 0.05,
            "rms_gap_to_second_best": 0.05,
            "rms_gap_to_next_class": 0.08,
        },
        features=[],
        synthetic_b_minus_v=0.6,
        emission_lines=[],
        is_emission_line_source=False,
        second_order_blue_to_red_ratio=None,
        reddening=ReddeningRecord(
            ebv=0.12,
            ebv_source="Gaia DR3",
            observed_best_type="G8V",
            dereddened_best_type="G2V",
            type_shift_steps=-6.0,
        ),
        line_index_classification=LineIndexClassification(
            best_type="B9V",
            distance=0.3,
            template_fit_type="G2V",
            steps_from_template_fit=steps,
        ),
    )


def test_a_line_index_type_far_from_the_template_fit_raises_the_flag() -> None:
    """Verify more than 20 steps fails the metric and sets the flag."""
    checkpoint = checkpoint_with_index_distance(DIFFERS_FROM_CATALOG_SUBTYPES + 1.0)

    metric = metrics_of(checkpoint)["index_vs_template_type_steps"]
    assert metric.passed is False
    assert metric.limit == DIFFERS_FROM_CATALOG_SUBTYPES
    assert "slope_and_lines_disagree" in checkpoint.flags
    assert checkpoint.has_failed_metric


@pytest.mark.parametrize("steps", [0.0, 7.0, DIFFERS_FROM_CATALOG_SUBTYPES])
def test_a_line_index_type_within_the_limit_does_not_raise_the_flag(steps: float) -> None:
    """Verify a distance at or under the limit passes and sets no flag."""
    checkpoint = checkpoint_with_index_distance(steps)

    assert metrics_of(checkpoint)["index_vs_template_type_steps"].passed is True
    assert "slope_and_lines_disagree" not in checkpoint.flags


def test_the_report_only_metrics_have_no_limit_and_the_shift_is_an_absolute_distance() -> None:
    """Verify ebv_used and dereddening_type_shift_steps never pass or fail."""
    metrics = metrics_of(checkpoint_with_index_distance(5.0))

    assert metrics["ebv_used"].value == pytest.approx(0.12)
    assert metrics["ebv_used"].passed is None
    assert "Gaia DR3" in metrics["ebv_used"].note
    assert metrics["dereddening_type_shift_steps"].value == pytest.approx(6.0)
    assert metrics["dereddening_type_shift_steps"].passed is None
    assert metrics["dereddening_type_shift_steps"].unit == "subtype steps"


def test_without_either_cross_check_the_metrics_are_present_and_empty() -> None:
    """Verify a checkpoint built without the checks lists the metrics empty."""
    checkpoint = assess_processing_quality(
        classification={"spectral_type": "Unknown"},
        features=[],
        synthetic_b_minus_v=None,
        emission_lines=[],
        is_emission_line_source=False,
        second_order_blue_to_red_ratio=None,
    )

    metrics = metrics_of(checkpoint)
    for name in ("ebv_used", "dereddening_type_shift_steps", "index_vs_template_type_steps"):
        assert metrics[name].value is None
        assert metrics[name].passed is None
    assert "slope_and_lines_disagree" not in checkpoint.flags


def test_the_values_the_model_stores_are_plain_floats() -> None:
    """Verify no NumPy scalar reaches the stored records."""
    record = ReddeningRecord(
        ebv=float(np.float64(0.2)), ebv_source="x", type_shift_steps=float(np.float64(-3.0))
    )
    dumped = record.model_dump(by_alias=True)

    assert type(dumped["ebv"]) is float
    assert type(dumped["typeShiftSteps"]) is float
