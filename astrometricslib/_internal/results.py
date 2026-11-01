"""Purpose: Private `results` names of `astrometricslib`.

Description: The private names of the `results` group: result models of the
processing calls. Only `astrometricslib` itself, and the tests of either
library, may import this module. Each name is imported from the module where
its code lives today, so moving the code later changes this file only.
"""

from astrometricslib.models.moving_object import AsteroidDetectionCandidate
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.models.point_sources import PointSource, PointSourceDetection
from astrometricslib.models.processing_results import PreviewRemakeResult, ProcessTargetResult, StackResult
from astrometricslib.models.quality.excluded_frames import RestoreReport
from astrometricslib.models.quality.subset_stacks import (
    NightContribution,
    NightContributionResult,
    NoiseGrowthResult,
    OddEvenDifferenceNoiseResult,
    SubsetStackResult,
)
from astrometricslib.models.spectroscopy_calibration import CalibrationLineFit, SpectroscopyCalibrationResult
from astrometricslib.models.stellar_source import (
    AnalysisResult,
    PhotometryResult,
    PlotData,
    SpectroscopyResult,
    VariableCandidate,
)
from astrometricslib.utilities.parallel_batch import BatchRunSummary

__all__ = [
    "AnalysisResult",
    "AsteroidDetectionCandidate",
    "BatchRunSummary",
    "CalibrationLineFit",
    "MovingObjectConfig",
    "NightContribution",
    "NightContributionResult",
    "NoiseGrowthResult",
    "OddEvenDifferenceNoiseResult",
    "PhotometryResult",
    "PlotData",
    "PointSource",
    "PointSourceDetection",
    "PreviewRemakeResult",
    "ProcessTargetResult",
    "RestoreReport",
    "SpectroscopyCalibrationResult",
    "SpectroscopyResult",
    "StackResult",
    "SubsetStackResult",
    "VariableCandidate",
]
