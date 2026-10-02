"""Judges how good the raw data behind a spectrum was.

This only looks at signals that come from extraction itself -- the
instrument's resolution, saturation, coverage, and signal-to-noise -- so
it can run before anything is found in the spectrum, and never needs to
change when the classifier or feature tests do.
"""

from astrometricslib.models.spectroscopy_quality import InputQualityAssessment


def assess_input_quality(
    *,
    resolution_element_angstrom: float,
    is_resolution_measured: bool,
    zero_order_saturated_pixel_fraction: float | None,
    valid_fraction: float | None,
    signal_to_noise: float | None,
) -> InputQualityAssessment:
    """Build the input-quality assessment for one extracted spectrum.

    Every parameter here is already computed elsewhere (during extraction,
    or as one of `analyze_spectrum`'s own results) -- this just gathers
    them into one structured object instead of leaving them as separate
    fields a caller has to know to look for.

    Returns
    -------
    assessment : `InputQualityAssessment`
        The structured data-quality record; see that class for what each
        field means.
    """
    return InputQualityAssessment(
        resolution_element_angstrom=resolution_element_angstrom,
        is_resolution_measured=is_resolution_measured,
        zero_order_saturated_pixel_fraction=zero_order_saturated_pixel_fraction,
        valid_fraction=valid_fraction,
        signal_to_noise=signal_to_noise,
    )
