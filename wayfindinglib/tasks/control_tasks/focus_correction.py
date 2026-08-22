"""Purpose: Autofocus Curve Fitting and Position Selection.

Description: `compute_focus_correction` per
`Wayfinding_Library_Architecture.md` §2.5.5 -- a pure function that
fits a parabola to a sampled focus curve and selects its minimum,
clamped to the sampled span so an ill-conditioned fit cannot command
an extrapolated position. Star-size measurement (`measure_image_fwhm`,
exposed through `TargetCatalog.measure_stack_fwhm`) requires no
telescope and belongs on the science side per the litmus test;
converting a measured curve into a focuser position requires backlash
direction, per-filter offsets, and thermal behavior, none of which
mean anything without the instrument, so that step lives here.

`sample_focus_curve` is the orchestration half: it drives the focuser
through `move_focuser`/`get_position` (injected so this module carries
no hardware import) always approaching from `FocusModel.approach_direction`,
per the "Single Approach Direction" invariant -- a curve sampled with
mixed approach directions has backlash folded into its shape and
produces a minimum that is an artifact of traversal order.
"""

from collections.abc import Callable

import numpy as np

from wayfindinglib.models.equipment_and_site.focus_model import ApproachDirection
from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.models.session.correction_result import FocusCorrection, FocusCurvePoint


def sample_focus_curve(
    move_focuser: Callable[[int], None],
    get_position: Callable[[], int],
    measure_fwhm: Callable[[], tuple[float, int] | None],
    starting_position: int,
    sample_count: int,
    sample_span_steps: int,
    approach_direction: ApproachDirection,
) -> list[FocusCurvePoint]:
    """Sample a focus curve, approaching every point from one direction.

    Parameters
    ----------
    move_focuser : callable
        Commands an absolute focuser move; takes the target position.
    get_position : callable
        Returns the focuser's current absolute position.
    measure_fwhm : callable
        Captures a frame at the current position and returns
        ``(measured_fwhm_px, star_count)``, or `None` if measurement
        failed at that position (skipped from the curve).
    starting_position : `int`
        The model-derived starting position (§2.5.5, step 2).
    sample_count : `int`
        Number of positions to sample, evenly spaced across the span.
    sample_span_steps : `int`
        Total focuser-step span the samples cover.
    approach_direction : `ApproachDirection`
        The single direction every sample is reached from.

    Returns
    -------
    curve : `list` [`FocusCurvePoint`]
        One point per position where measurement succeeded, in
        sampled order.
    """
    half_span = sample_span_steps // 2
    span_start = starting_position - half_span
    positions = [
        span_start + round(i * sample_span_steps / max(1, sample_count - 1)) for i in range(sample_count)
    ]

    # Always enter the sampled span from one side and sweep monotonically
    # in `approach_direction`, so backlash is taken up identically before
    # every sample rather than differently depending on which position
    # happens to be visited first. The run-up move must overshoot on the
    # far side of the first sampled position -- the side the sweep is
    # moving away from -- so the transition into the sweep itself never
    # reverses direction.
    sweep_decreasing = approach_direction == ApproachDirection.INWARD
    positions = sorted(positions, reverse=sweep_decreasing)
    overshoot = max(1, half_span // 4)
    run_up_position = positions[0] + overshoot if sweep_decreasing else positions[0] - overshoot
    move_focuser(run_up_position)

    curve: list[FocusCurvePoint] = []
    for position in positions:
        move_focuser(position)
        measured = measure_fwhm()
        if measured is None:
            continue
        measured_fwhm_px, star_count = measured
        curve.append(
            FocusCurvePoint(
                focuser_position=get_position(),
                measured_fwhm_px=measured_fwhm_px,
                star_count=star_count,
            )
        )
    return curve


def compute_focus_correction(
    comparison_input_id: str,
    curve: list[FocusCurvePoint],
    starting_position: int,
    trigger_reason: str,
    config: CorrectionConfig,
) -> FocusCorrection:
    """Fit a parabola to a sampled focus curve and select its minimum.

    Parameters
    ----------
    comparison_input_id : `str`
        Identifier of the shared measurement set this correction was
        computed from, for divergence pairing.
    curve : `list` [`FocusCurvePoint`]
        The sampled (position, FWHM) points, at least 3 required to
        fit a parabola.
    starting_position : `int`
        The position the sweep started from -- returned unchanged as
        `selected_position` when the fit is rejected.
    trigger_reason : `str`
        Why this autofocus run was triggered (temperature delta,
        filter change, elapsed time, or measured degradation).
    config : `CorrectionConfig`
        Supplies `focus_fit_quality_floor`.

    Returns
    -------
    correction : `FocusCorrection`
        The sampled curve, the fitted minimum, and the selected
        position -- unmoved (`selected_position == starting_position`)
        and `converged=False` when `fit_quality` falls below
        `focus_fit_quality_floor`. Issues nothing.

    Raises
    ------
    ValueError
        If fewer than 3 points were sampled -- a parabola cannot be
        fit, and this indicates a sampling failure the caller must
        handle, not a normal outcome to silently paper over.
    """
    if len(curve) < 3:
        raise ValueError(f"compute_focus_correction requires at least 3 sampled points, got {len(curve)}")

    positions = np.array([point.focuser_position for point in curve], dtype=float)
    fwhms = np.array([point.measured_fwhm_px for point in curve], dtype=float)

    coefficients = np.polyfit(positions, fwhms, 2)
    predicted = np.polyval(coefficients, positions)
    residual_sum_squares = float(np.sum((fwhms - predicted) ** 2))
    total_sum_squares = float(np.sum((fwhms - np.mean(fwhms)) ** 2))
    fit_quality = 1.0 - (residual_sum_squares / total_sum_squares) if total_sum_squares > 0 else 0.0
    fit_quality = max(0.0, min(1.0, fit_quality))

    quadratic_coefficient, linear_coefficient, _ = coefficients
    if quadratic_coefficient <= 0.0 or fit_quality < config.focus_fit_quality_floor:
        return FocusCorrection(
            comparison_input_id=comparison_input_id,
            curve=curve,
            starting_position=starting_position,
            selected_position=starting_position,
            fitted_minimum_fwhm_px=float(np.min(fwhms)),
            fit_quality=fit_quality,
            trigger_reason=trigger_reason,
            converged=False,
        )

    vertex_position = -linear_coefficient / (2.0 * quadratic_coefficient)
    clamped_position = min(max(vertex_position, float(np.min(positions))), float(np.max(positions)))
    fitted_minimum_fwhm = float(np.polyval(coefficients, clamped_position))

    return FocusCorrection(
        comparison_input_id=comparison_input_id,
        curve=curve,
        starting_position=starting_position,
        selected_position=round(clamped_position),
        fitted_minimum_fwhm_px=fitted_minimum_fwhm,
        fit_quality=fit_quality,
        trigger_reason=trigger_reason,
        converged=True,
    )
