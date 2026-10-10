"""Purpose: Compare measurements of the real M 13 frames with pinned values.

Description: Each test measures one stage of the pure-Python pipeline on the
sample frames in ``documentation/notebooks/astrometrics/sample_data/M 13/``
and compares the numbers with ``golden_values.json``. A refactor that moves a
number fails here, and an intended change updates the JSON with
``--update-golden`` so the diff shows which measurement moved. Stacking and
plate solving need Siril and astrometry.net, so they are not covered.
"""

from typing import Any

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.test.golden import measurements

pytestmark = pytest.mark.golden


@pytest.fixture(scope="module")
def raw_frame_quality() -> dict[str, dict[str, float]]:
    """Measure the five luminance lights once for the module.

    Returns
    -------
    measured : `dict` [`str`, `dict` [`str`, `float`]]
        Numbers by frame name, plus a ``"batch"`` group.
    """
    return measurements.measure_raw_frame_quality()


@pytest.mark.parametrize("group", [*measurements.LUMINANCE_FRAMES, "batch"])
def test_raw_frame_quality_matches_pins(
    golden: Any, raw_frame_quality: dict[str, dict[str, float]], group: str
) -> None:
    """Star count, FWHM, sky, noise and saturation of a light match the pins.

    Parameters
    ----------
    golden : `GoldenStore`
        The pinned numbers.
    raw_frame_quality : `dict`
        The measurements of all five lights.
    group : `str`
        One light's name, or ``"batch"`` for the batch medians.
    """
    golden.check("raw_frame_quality", group, raw_frame_quality[group])


def test_source_detection_matches_pins(golden: Any, detected_sources: list[dict[str, Any]]) -> None:
    """The source count and the ten brightest sources match the pins.

    Parameters
    ----------
    golden : `GoldenStore`
        The pinned numbers.
    detected_sources : `list` [`dict`]
        The detector's output on the detection frame.
    """
    golden.check(
        "source_detection",
        measurements.DETECTION_FRAME,
        measurements.measure_source_detection(detected_sources),
    )


def test_fwhm_of_brightest_stars_matches_pins(
    golden: Any, detection_frame: tuple[np.ndarray, fits.Header]
) -> None:
    """The median FWHM fit of the five brightest stars matches the pin.

    Parameters
    ----------
    golden : `GoldenStore`
        The pinned numbers.
    detection_frame : `tuple`
        The frame and header of the detection frame.
    """
    golden.check("fwhm_top5", measurements.DETECTION_FRAME, measurements.measure_fwhm(detection_frame[0]))


def test_spectral_frame_check_matches_pins(golden: Any) -> None:
    """Zero-order finder result, tilt, streak width and peak-to-sky match.

    Parameters
    ----------
    golden : `GoldenStore`
        The pinned numbers.
    """
    data, _ = measurements.load_frame(measurements.SPECTRAL_FRAME)
    path = measurements.frame_path(measurements.SPECTRAL_FRAME)
    golden.check(
        "spectral_frame", measurements.SPECTRAL_FRAME, measurements.measure_spectral_frame(data, path)
    )


def test_photometry_pre_s2_matches_pins(
    golden: Any, detection_frame: tuple[np.ndarray, fits.Header], detected_sources: list[dict[str, Any]]
) -> None:
    """Aperture fluxes on whole-pixel centres match the pre-S2 pins.

    Parameters
    ----------
    golden : `GoldenStore`
        The pinned numbers.
    detection_frame : `tuple`
        The frame and header of the detection frame.
    detected_sources : `list` [`dict`]
        The detector's output on the detection frame.
    """
    data, header = detection_frame
    values = measurements.measure_photometry(data, header, detected_sources, whole_pixel_centres=True)
    golden.check("photometry_pre_S2", measurements.DETECTION_FRAME, values)


def test_photometry_sub_pixel_matches_pins(
    golden: Any, detection_frame: tuple[np.ndarray, fits.Header], detected_sources: list[dict[str, Any]]
) -> None:
    """Aperture fluxes on exact detected positions match the sub-pixel pins.

    Parameters
    ----------
    golden : `GoldenStore`
        The pinned numbers.
    detection_frame : `tuple`
        The frame and header of the detection frame.
    detected_sources : `list` [`dict`]
        The detector's output on the detection frame.
    """
    data, header = detection_frame
    values = measurements.measure_photometry(data, header, detected_sources, whole_pixel_centres=False)
    golden.check("photometry_sub_pixel", measurements.DETECTION_FRAME, values)


def test_photometry_sequence_matches_pins(golden: Any) -> None:
    """Shifts, unsaturated star count and flux scatter match the pins.

    The photometry worker measures about 60 stars on all five luminance
    lights. The pins hold the shift of lights 020 to 023 from 019, the number
    of stars that stayed unsaturated, and the median scatter of their fluxes.

    Parameters
    ----------
    golden : `GoldenStore`
        The pinned numbers.
    """
    golden.check(
        "photometry_sequence",
        f"{measurements.LUMINANCE_FRAMES[0]}_to_{measurements.LUMINANCE_FRAMES[-1][-3:]}",
        measurements.measure_photometry_sequence(),
    )
