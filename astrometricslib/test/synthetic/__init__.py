"""Purpose: Public names of the synthetic truth-known frame generators.

Description: Re-exports the generators that build artificial photometry
and spectroscopy frames with known answers, for use in tests. See the
README.md in this folder for what each one produces and how to extend it.
"""

from astrometricslib.test.synthetic.photometry_frame import (
    SyntheticStar,
    drifted_stars,
    make_drifted_sequence,
    make_photometry_fits,
    make_photometry_frame,
    render_stars,
    star_flux_multipliers,
)
from astrometricslib.test.synthetic.spectral_frame import (
    LINE_FWHM_PX,
    SyntheticSpectralFrame,
    make_spectral_frame,
)

__all__ = [
    "LINE_FWHM_PX",
    "SyntheticSpectralFrame",
    "SyntheticStar",
    "drifted_stars",
    "make_drifted_sequence",
    "make_photometry_fits",
    "make_photometry_frame",
    "make_spectral_frame",
    "render_stars",
    "star_flux_multipliers",
]
