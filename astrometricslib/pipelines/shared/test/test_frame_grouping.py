"""Unit tests for frame grouping and spectral classification utilities.

Verifies that frame_is_spectral accurately detects spectroscopy frames from
both object models and dictionaries across various filter representations
(FilterType.SPEC, 'SPEC', 'Star Analyzer 200', 'Spectroscopy').
"""

from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.utilities.enums import FilterType


def test_frame_is_spectral_with_objects() -> None:
    """Verify frame_is_spectral with FrameRecord object instances."""
    spec_frame = FrameRecord(path="/path/spec.fits", filter=FilterType.SPEC)
    assert frame_is_spectral(spec_frame) is True

    l_frame = FrameRecord(path="/path/lum.fits", filter=FilterType.L)
    assert frame_is_spectral(l_frame) is False


def test_frame_is_spectral_with_dictionaries() -> None:
    """Verify frame_is_spectral with dictionary frame representations."""
    dict_spec = {"path": "/path/spec.fits", "filter": "SPEC"}
    assert frame_is_spectral(dict_spec) is True

    dict_sa200 = {"path": "/path/sa200.fits", "filter": "Star Analyzer 200"}
    assert frame_is_spectral(dict_sa200) is True

    dict_spectroscopy = {"path": "/path/spec2.fits", "filter": "Spectroscopy"}
    assert frame_is_spectral(dict_spectroscopy) is True

    dict_lum = {"path": "/path/lum.fits", "filter": "Luminance"}
    assert frame_is_spectral(dict_lum) is False

    dict_empty = {"path": "/path/unknown.fits"}
    assert frame_is_spectral(dict_empty) is False
