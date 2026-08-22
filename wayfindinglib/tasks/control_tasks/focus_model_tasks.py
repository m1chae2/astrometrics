"""Purpose: Focus Model Measurement.

Description: Derives the pieces of a `FocusModel` from measured
calibration runs, per `Wayfinding_Library_Architecture.md` §2.5.2:
`backlash_steps` by reversing direction and recording lost motion,
`thermal_coefficient_steps_per_c` by regressing focus positions
recorded against temperature across sessions, and per-filter offsets
by focusing through each filter at one temperature.
"""

import numpy as np

from wayfindinglib.models.equipment_and_site.focus_model import FilterFocusOffset


def measure_backlash_steps(expected_position: int, actual_position: int) -> int:
    """Compute lost motion from a direction-reversal backlash measurement.

    The procedure: move the focuser out to take up backlash in one
    direction, then command a move of a known magnitude in the
    reverse direction. `expected_position` is where that command
    should land absent backlash; `actual_position` is where the
    focuser is actually measured afterward. The gap between them is
    the motion the reversal lost to backlash before the mechanism
    began moving again.

    Returns
    -------
    backlash_steps : `int`
        The absolute difference between expected and actual position.
    """
    return abs(expected_position - actual_position)


def fit_thermal_coefficient_steps_per_c(temperature_position_pairs: list[tuple[float, int]]) -> float:
    """Regress recorded focus positions against temperature.

    Parameters
    ----------
    temperature_position_pairs : `list` [`tuple` [`float`, `int`]]
        `(temperature_c, focuser_position)` pairs recorded across
        sessions, all from the same approach direction so backlash
        does not contaminate the trend.

    Returns
    -------
    thermal_coefficient_steps_per_c : `float`
        The slope of the best-fit line: focuser steps per degree C.

    Raises
    ------
    ValueError
        If fewer than 2 pairs are given -- a slope cannot be fit.
    """
    if len(temperature_position_pairs) < 2:
        raise ValueError("fit_thermal_coefficient_steps_per_c requires at least 2 recorded pairs")
    temperatures = np.array([pair[0] for pair in temperature_position_pairs], dtype=float)
    positions = np.array([pair[1] for pair in temperature_position_pairs], dtype=float)
    slope, _intercept = np.polyfit(temperatures, positions, 1)
    return float(slope)


def measure_filter_offsets(
    baseline_filter: str, focus_position_by_filter: dict[str, int]
) -> list[FilterFocusOffset]:
    """Compute per-filter focuser offsets relative to a baseline filter.

    Parameters
    ----------
    baseline_filter : `str`
        The filter every other offset is measured relative to; it is
        omitted from the result since its offset is zero by
        definition.
    focus_position_by_filter : `dict` [`str`, `int`]
        The in-focus position measured through each filter, at one
        temperature. Must include `baseline_filter`.

    Returns
    -------
    offsets : `list` [`FilterFocusOffset`]
        One entry per non-baseline filter.

    Raises
    ------
    ValueError
        If `baseline_filter` is not present in `focus_position_by_filter`.
    """
    if baseline_filter not in focus_position_by_filter:
        raise ValueError(f"baseline_filter {baseline_filter!r} has no measured position")
    baseline_position = focus_position_by_filter[baseline_filter]
    return [
        FilterFocusOffset(filter=filter_name, offset_steps=position - baseline_position)
        for filter_name, position in focus_position_by_filter.items()
        if filter_name != baseline_filter
    ]
