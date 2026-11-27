"""Tests that converted wayfindinglib values keep their numbers.

Each value was reclassified as a hardcoded choice, a processing setting or an
equipment-profile value. None of them has a basis, so a recorded basis must
leave them alone, and with no config or run they resolve to their old
defaults.
"""

import pytest

from astrometricslib._internal.parameters import parameter_context, record_basis
from astrometricslib.parameters import (
    ParameterSource,
    RunBasis,
    TunedParameter,
    resolve_parameter,
)
from wayfindinglib.analytics import envelope_assembly
from wayfindinglib.analytics.result_quality import capture_overheads, focus_drift
from wayfindinglib.session_analysis.guiding.processing import (
    measure_exposure_feasibility,
    measure_guiding_performance,
)
from wayfindinglib.tasks.control_tasks import focus_model_tasks, live_guiding
from wayfindinglib.tasks.planning_tasks import tonight_ranking_tasks

CONVERTED: list[tuple[TunedParameter, float | int]] = [
    (envelope_assembly.SESSION_WINDOW_MARGIN_SECONDS, 1800.0),
    (capture_overheads.DITHER_EVERY_FRAMES, 3),
    (capture_overheads.AUTOFOCUS_RUNS_PER_HOUR, 1),
    (focus_drift.MINIMUM_TEMPERATURE_CHANGE_DEGC, 1.0),
    (measure_exposure_feasibility.LOST_STAR_CYCLES, 4),
    (measure_exposure_feasibility._STRIDE_FRACTION, 0.25),
    (measure_exposure_feasibility._MINIMUM_WINDOWS, 10),
    (measure_guiding_performance.MINIMUM_SAMPLES_PER_RUN, 20),
    (focus_model_tasks.MINIMUM_TEMPERATURE_SPAN_C, 3.0),
    (live_guiding.TRACKING_CHECKS, 5),
    (live_guiding.TRACKING_RETRY_SECONDS, 0.5),
    (live_guiding.LOST_TRACKING_LIMIT, 3),
    (tonight_ranking_tasks.THIN_DATASET_SECONDS, 7200.0),
]


@pytest.mark.parametrize(("parameter", "old_value"), CONVERTED)
def test_converted_values_resolve_to_their_old_defaults(
    parameter: TunedParameter, old_value: float | int
) -> None:
    """With no providers, each converted value resolves to its old default."""
    with parameter_context():
        assert resolve_parameter(parameter).value == old_value


@pytest.mark.parametrize(("parameter", "old_value"), CONVERTED)
def test_a_recorded_basis_does_not_scale_a_value_without_a_basis(
    parameter: TunedParameter, old_value: float | int
) -> None:
    """A measured star width leaves a value that has no basis unchanged."""
    assert parameter.basis is None
    assert parameter.source is not ParameterSource.DETERMINED_EACH_RUN
    with parameter_context():
        record_basis(RunBasis.STAR_WIDTH_PX, 6.0)
        assert resolve_parameter(parameter).value == old_value
