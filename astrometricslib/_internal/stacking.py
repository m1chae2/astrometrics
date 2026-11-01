"""Purpose: Private `stacking` names of `astrometricslib`.

Description: The private names of the `stacking` group: stacking limits and
weight helpers. Only `astrometricslib` itself, and the tests of either library,
may import this module. Each name is imported from the module where its code
lives today, so moving the code later changes this file only.
"""

from astrometricslib._internal.drivers.calibration_library import DEFAULT_DARK_TEMPERATURE_TOLERANCE_C
from astrometricslib.models.processing_results import StackWeightPrediction
from astrometricslib.models.stellar_source import AMBIGUOUS_RMS_GAP, NO_GOOD_MATCH_RMS
from astrometricslib.pipelines.stacking.post_processing.empty_apertures import (
    aperture_identity_from_number,
    aperture_identity_to_number,
)
from astrometricslib.pipelines.stacking.post_processing.exposure_saturation import (
    SATURATED_BLOB_MINIMUM_PIXELS,
    SATURATED_FRAME_FRACTION,
)
from astrometricslib.pipelines.stacking.post_processing.odd_even_apertures import (
    load_odd_even_aperture_record,
)
from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
    group_frame_weights,
    wfwhm_weights,
)
from astrometricslib.pipelines.stacking.pre_processing.exposure_weighting import (
    MINIMUM_PENALTY_TO_WEIGHT,
    NOISE_WEIGHT_MODE,
    choose_stack_weight,
    equal_weight_noise_penalty,
)
from astrometricslib.pipelines.stacking.processing.exposure_groups import fold_exposure_lengths

__all__ = [
    "AMBIGUOUS_RMS_GAP",
    "DEFAULT_DARK_TEMPERATURE_TOLERANCE_C",
    "MINIMUM_PENALTY_TO_WEIGHT",
    "NOISE_WEIGHT_MODE",
    "NO_GOOD_MATCH_RMS",
    "SATURATED_BLOB_MINIMUM_PIXELS",
    "SATURATED_FRAME_FRACTION",
    "StackWeightPrediction",
    "aperture_identity_from_number",
    "aperture_identity_to_number",
    "choose_stack_weight",
    "equal_weight_noise_penalty",
    "fold_exposure_lengths",
    "group_frame_weights",
    "load_odd_even_aperture_record",
    "wfwhm_weights",
]
