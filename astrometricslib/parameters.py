"""Purpose: The tuned parameters and how their values are found.

Description: A tuned parameter is a number a pipeline uses that was chosen
rather than measured on the data in hand: a limit, a window, a factor or a
default. Each is declared once as a `TunedParameter`, which says what the
software does with it (its role), where its value comes from (its source),
which equipment it depends on and which register entry holds its evidence.
`collect_parameters` gathers a library's declarations into a
`ParameterRegistry`, and `resolve_parameter` finds the value to use, from the
equipment, a tuning run, the configuration or the default. A run sets a
`ParameterContext` (`frames_parameter_context`) naming the setup whose
equipment and tuned values it reads. The two spectrum
match limits are public so that wayfindinglib judges spectra with the same
limits the pipeline uses, and the stretched-star warning level so that its
trailed-stars rule warns where the frame status flags, and the dark
temperature tolerance so that a reader of calibration counts quotes the
tolerance the library matched darks with. A `Tuner`
derives one family of tuned values. `tune` is the one entry point that picks a
library's tuners and a setup, runs them and stores the values with their
provenance in the config; both libraries' ``measure_tuned_parameters`` call
it. A script and
`wayfindinglib` import these names from here. Each is imported from the module
where its code lives today.
"""

from typing import NoReturn

from astrometricslib._internal.drivers.calibration_library import DEFAULT_DARK_TEMPERATURE_TOLERANCE_C
from astrometricslib._internal.renames import moved_module_name
from astrometricslib.foundation.parameter_context import (
    ParameterContext,
    SetupEquipmentValues,
    equipment_values_to_write,
    frames_parameter_context,
    run_basis_value,
)
from astrometricslib.foundation.parameters import (
    ConfigurationValues,
    EquipmentValues,
    ParameterRegistry,
    ResolvedValue,
    TunedValues,
    collect_parameters,
)
from astrometricslib.foundation.parameters import resolve as resolve_parameter
from astrometricslib.foundation.run_bases import (
    MOUNT_PERIODIC_ERROR_PERIOD_SECONDS,
    TYPICAL_GUIDE_CYCLE_SECONDS,
    factor_for,
)
from astrometricslib.foundation.tuning import (
    ChangeNotice,
    ConfigTunedValues,
    TunedRecord,
    Tuner,
    TuningInputs,
    TuningReport,
    TuningResult,
    TuningSkippedError,
    tune,
)
from astrometricslib.models.stellar_source import AMBIGUOUS_RMS_GAP, NO_GOOD_MATCH_RMS
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    RunBasis,
    TunedParameter,
    WhenMissing,
)
from astrometricslib.pipelines.shared.quality.raw_frame_check import LOW_STAR_FRACTION
from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
    STRETCHED_STAR_WARNING_ELONGATION,
)

__all__ = [
    "AMBIGUOUS_RMS_GAP",
    "DEFAULT_DARK_TEMPERATURE_TOLERANCE_C",
    "LOW_STAR_FRACTION",
    "MOUNT_PERIODIC_ERROR_PERIOD_SECONDS",
    "NO_GOOD_MATCH_RMS",
    "STRETCHED_STAR_WARNING_ELONGATION",
    "TYPICAL_GUIDE_CYCLE_SECONDS",
    "ChangeNotice",
    "ConfigTunedValues",
    "ConfigurationValues",
    "EquipmentKind",
    "EquipmentValues",
    "HardcodedKind",
    "ParameterContext",
    "ParameterRegistry",
    "ParameterRole",
    "ParameterSource",
    "ResolvedValue",
    "RunBasis",
    "SetupEquipmentValues",
    "TunedParameter",
    "TunedRecord",
    "TunedValues",
    "Tuner",
    "TuningInputs",
    "TuningReport",
    "TuningResult",
    "TuningSkippedError",
    "WhenMissing",
    "collect_parameters",
    "equipment_values_to_write",
    "factor_for",
    "frames_parameter_context",
    "resolve_parameter",
    "run_basis_value",
    "tune",
]


def __getattr__(name: str) -> NoReturn:
    """Refuse a name this module no longer offers, saying where it went.

    Parameters
    ----------
    name : `str`
        The name that was read.

    Notes
    -----
    Always raises: an `ImportError` that says where the name went for a
    name in the rename table, and an `AttributeError` for any other name.
    """
    moved_module_name(__name__, name)
