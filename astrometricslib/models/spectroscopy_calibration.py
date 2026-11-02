"""What the spectroscopy calibration tuner returns: the fit and how good it is.

- `CalibrationLineFit` is one hydrogen line used in the fit.
- `SpectroscopyCalibrationResult` is the whole report.
"""

from pydantic import BaseModel, Field

__all__ = ["CalibrationLineFit", "SpectroscopyCalibrationResult"]


class CalibrationLineFit(BaseModel):
    """One absorption line used to tune the calibration.

    Parameters
    ----------
    feature : `str`
        The line's name, such as ``"H-beta"``.
    extracted_index : `int`
        The nearest sample of the extracted spectrum.
    pixel_offset : `float`
        The line's distance from the zero order, in pixels.
    target_wavelength_nm : `float`
        The line's true wavelength, in nanometers.
    calibrated_wavelength_nm : `float`
        The wavelength the tuned calibration gives, in nanometers.
    deviation_nm : `float`
        Calibrated minus true wavelength, in nanometers.
    """

    feature: str
    extracted_index: int
    pixel_offset: float
    target_wavelength_nm: float
    calibrated_wavelength_nm: float
    deviation_nm: float


class SpectroscopyCalibrationResult(BaseModel):
    """What fitting the spectroscope's physical model to one image found.

    Parameters
    ----------
    status : `str`
        ``"success"`` when the fit was made and saved.
    camera_name : `str`
        The camera the calibration belongs to.
    fitted_grating_distance_mm : `float`
        The fitted distance from the grating to the sensor, in millimeters.
    fitted_dispersion_start_px : `float`
        The fitted distance from the star to the start of the spectrum, in
        pixels.
    rms_error_nm : `float`
        The RMS difference between the fitted and the true wavelengths of the
        lines, in nanometers.
    detected_angle_degrees : `float`
        The angle of the spectrum trail, in degrees.
    detailed_calibration : `list` [`CalibrationLineFit`]
        The lines used in the fit.
    use_flare_mask_extraction : `bool`
        Whether the tuned settings extract the spectrum with a flare mask.
    max_extraction_length_px : `float` or `None`
        The longest extraction length found, in pixels, or `None` for no
        limit.
    """

    status: str
    camera_name: str
    fitted_grating_distance_mm: float
    fitted_dispersion_start_px: float
    rms_error_nm: float
    detected_angle_degrees: float
    detailed_calibration: list[CalibrationLineFit] = Field(default_factory=list)
    use_flare_mask_extraction: bool = False
    max_extraction_length_px: float | None = None
