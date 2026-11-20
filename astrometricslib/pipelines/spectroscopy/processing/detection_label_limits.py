"""Builds the record of limits behind a spectrum's detection labels.

The absorption-feature test and the emission-line test each give an entry a
label such as "detected". The label comes from a statistic (a p-value, a depth
or a significance) compared with a limit. The limits are module constants, the
same for every entry, so storing them on each entry would repeat them. This
module reads the constants and the validation register ids once and returns
one small record to store with the spectrum. It changes no limit and no label.
"""

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.quality.spectroscopy_quality import (
    AbsorptionFeatureLimits,
    DetectionLabelLimits,
    EmissionLineLimits,
)
from astrometricslib.pipelines.spectroscopy.processing import (
    emission_line_detector,
    spectral_feature_detector,
)

# The validation register entries (documentation/library_design/validation/
# spectroscopy.md) that check each detector's limits.
ABSORPTION_FEATURE_REGISTER_ENTRY = "SPE-24"
EMISSION_LINE_REGISTER_ENTRY = "SPE-25"


def build_detection_label_limits() -> DetectionLabelLimits:
    """Collect the limits the two detectors use to label their entries.

    Returns
    -------
    limits : `DetectionLabelLimits`
        The current limits of `spectral_feature_detector` and
        `emission_line_detector`, with the register entry for each.
    """
    return DetectionLabelLimits(
        absorption_features=AbsorptionFeatureLimits(
            detected_p_value=resolve(spectral_feature_detector.DETECTED_P_VALUE).value,
            possible_p_value=resolve(spectral_feature_detector.POSSIBLE_P_VALUE).value,
            inconclusive_p_value=resolve(spectral_feature_detector.INCONCLUSIVE_P_VALUE).value,
            inconclusive_minimum_depth=resolve(spectral_feature_detector.INCONCLUSIVE_MINIMUM_DEPTH).value,
            minimum_reported_depth=resolve(spectral_feature_detector.MINIMUM_REPORTED_DEPTH).value,
            maximum_depth_uncertainty_for_label=resolve(
                spectral_feature_detector.MAXIMUM_UNCERTAINTY_FOR_A_VERDICT
            ).value,
            register_entry=ABSORPTION_FEATURE_REGISTER_ENTRY,
        ),
        emission_lines=EmissionLineLimits(
            detected_significance=resolve(emission_line_detector.DETECTED_SIGNIFICANCE).value,
            unclear_significance=resolve(emission_line_detector.UNCLEAR_SIGNIFICANCE).value,
            minimum_covered_fraction=resolve(emission_line_detector.MINIMUM_COVERED_FRACTION).value,
            minimum_detected_for_emission_source=resolve(
                emission_line_detector.MINIMUM_DETECTED_FOR_EMISSION_SOURCE
            ).value,
            register_entry=EMISSION_LINE_REGISTER_ENTRY,
        ),
    )
