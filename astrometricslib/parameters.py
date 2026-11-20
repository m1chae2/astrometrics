"""Purpose: The tuned parameters and how their values are found.

Description: A tuned parameter is a number a pipeline uses that was chosen
rather than measured on the data in hand: a limit, a window, a factor or a
default. Each is declared once as a `TunedParameter`, which says what the
software does with it (its role), where its value comes from (its source),
which equipment it depends on and which register entry holds its evidence.
`collect_parameters` gathers a library's declarations into a
`ParameterRegistry`, and `resolve_parameter` finds the value to use, from the
equipment, a tuning run, the configuration or the default. A run sets a
`ParameterContext` (`setup_parameter_context`, `frames_parameter_context`)
naming the setup whose equipment and tuned values it reads. The two spectrum
match limits are public so that wayfindinglib judges spectra with the same
limits the pipeline uses. A `Tuner`
derives one family of tuned values, and `run_tuning` stores them with their
provenance in the config. A script and
`wayfindinglib` import these names from here. Each is imported from the module
where its code lives today.
"""

from astrometricslib.foundation.parameter_context import (
    ParameterContext,
    SetupEquipmentValues,
    current_parameter_context,
    equipment_values_to_write,
    frames_parameter_context,
    parameter_context,
    record_basis,
    run_basis_value,
    setup_parameter_context,
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
    collect_tuners,
    read_tuned_record,
    run_tuning,
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

__all__ = [
    "AMBIGUOUS_RMS_GAP",
    "LOW_STAR_FRACTION",
    "MOUNT_PERIODIC_ERROR_PERIOD_SECONDS",
    "NO_GOOD_MATCH_RMS",
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
    "collect_tuners",
    "current_parameter_context",
    "equipment_values_to_write",
    "factor_for",
    "frames_parameter_context",
    "parameter_context",
    "read_tuned_record",
    "record_basis",
    "resolve_parameter",
    "run_basis_value",
    "run_tuning",
    "setup_parameter_context",
]
