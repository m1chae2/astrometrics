"""A spectrum's own measured line spread, stored on its `SpectroscopyResult`.

A slitless spectrum is blurred. The blur can change along the spectrum, for
example when the red end is out of focus. This record holds how wide the blur
is, band by band, as measured from this one spectrum's trail (see
`pipelines/spectroscopy/pre_processing/measured_line_spread.py` for how the
numbers are made). It describes one spectrum on one night. The stored,
camera-wide profile (`load_line_spread_profile`) is a separate thing.
"""

from pydantic import BaseModel, ConfigDict, Field


class MeasuredLineSpread(BaseModel):
    """The blur of one spectrum, measured in wavelength bands.

    Every list has one entry per band, in order of increasing wavelength.
    A band that had too few working trail-width fits is left out of all the
    lists, so the lists always have the same length.
    """

    model_config = ConfigDict(populate_by_name=True)

    # The centre of each band, in Angstroms.
    wavelength_angstrom: list[float] = Field(default_factory=list, alias="wavelengthAngstrom")
    # The blur width in each band, as a full width at half maximum (FWHM), in
    # Angstroms. This is `fwhm_px` times the Angstroms one pixel covers in
    # that band.
    fwhm_angstrom: list[float] = Field(default_factory=list, alias="fwhmAngstrom")
    # The same blur width in pixels. It is 2.355 times the median sigma of
    # the trail's cross-section across the dispersion.
    fwhm_px: list[float] = Field(default_factory=list, alias="fwhmPx")
    # How much the single-step width fits scatter inside each band, in
    # pixels of FWHM. It is 1.4826 times the median absolute deviation, so
    # for a normal spread it equals the standard deviation. A large value
    # means the band's median is less certain.
    scatter_px: list[float] = Field(default_factory=list, alias="scatterPx")
    # How many working width fits each band's median is made from.
    sample_count: list[int] = Field(default_factory=list, alias="sampleCount")
