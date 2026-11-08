"""Purpose: Focus Model Measurement.

Description: Derives the pieces of a `FocusModel` from measured
calibration runs, per `Wayfinding_Library_Architecture.md` Section 3.2:
`backlash_steps` by reversing direction and recording lost motion,
`thermal_coefficient_steps_per_c` by regressing focus positions
recorded against temperature across sessions, and per-filter offsets
by focusing through each filter at one temperature.

`measure_focus_temperature_coefficient` fits that slope, with its uncertainty,
on the library's own focus history: the positions Ekos chose in recorded
autofocus runs and the position and temperature written in light-frame
headers each time the focuser position changed.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Collection, Iterable, Sequence
from typing import TYPE_CHECKING, NamedTuple

import numpy as np

from astrometricslib.exceptions import InvalidArgumentError
from astrometricslib.reports import observing_night_id
from wayfindinglib.analytics.capture_history import equipment_light_frames, normalize_name
from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog
from wayfindinglib.models.equipment_and_site.focus_model import FilterFocusOffset
from wayfindinglib.models.equipment_and_site.focus_temperature_fit import FocusSource, FocusTemperatureFit

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext
    from wayfindinglib.models.session.ekos_session import EkosSessionContext

MINIMUM_FOCUS_PAIRS = 6
"""Fewest (temperature, best-focus position) pairs for a fit. The fit has one
slope and one offset per filter, and the uncertainty needs at least three
degrees of freedom. Design estimate, register row WAY-D121."""

MINIMUM_TEMPERATURE_SPAN_C = 3.0
"""Narrowest temperature range a fit may rest on, in degrees Celsius. A
focuser step is a fraction of a micrometer, and a slope measured over less
than this is mostly the scatter of the focus runs. Design estimate, register
row WAY-D121."""

MINIMUM_FILTER_SHARE_PAIRS = 2
"""A filter with fewer pairs than this adds an offset the data cannot place,
so its pairs are dropped. Design estimate, register row WAY-D121."""


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
    InvalidArgumentError
        If fewer than 2 pairs are given -- a slope cannot be fit.
    """
    if len(temperature_position_pairs) < 2:
        raise InvalidArgumentError("fit_thermal_coefficient_steps_per_c requires at least 2 recorded pairs")
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
    InvalidArgumentError
        If `baseline_filter` is not present in `focus_position_by_filter`.
    """
    if baseline_filter not in focus_position_by_filter:
        raise InvalidArgumentError(f"baseline_filter {baseline_filter!r} has no measured position")
    baseline_position = focus_position_by_filter[baseline_filter]
    return [
        FilterFocusOffset(filter=filter_name, offset_steps=position - baseline_position)
        for filter_name, position in focus_position_by_filter.items()
        if filter_name != baseline_filter
    ]


class FocusPair(NamedTuple):
    """One best-focus position with the temperature it was found at.

    Attributes
    ----------
    night : `str`
        The observing night.
    filter_name : `str`
        The filter the focus was found through.
    temperature_c : `float`
        The temperature, in degrees Celsius.
    position : `float`
        The focuser position, in steps.
    """

    night: str
    filter_name: str
    temperature_c: float
    position: float


def fit_thermal_coefficient_with_uncertainty(
    pairs: Sequence[FocusPair], nights_excluded: Collection[str] = ()
) -> FocusTemperatureFit:
    """Fit the focus slope with its uncertainty, one offset per filter.

    The model is ``position = offset[filter] + slope * temperature``. A filter
    changes the best position by a fixed amount, so each filter gets its own
    offset and all share one slope. The uncertainty is the standard error of
    the slope from the scatter about the fit.

    Parameters
    ----------
    pairs : `Sequence` [`FocusPair`]
        The best-focus positions found.
    nights_excluded : `Collection` [`str`], optional
        Nights that were left out, copied into the result.

    Returns
    -------
    fit : `FocusTemperatureFit`
        The fit, or a result with no coefficient and a message when there are
        fewer than `MINIMUM_FOCUS_PAIRS` pairs, they span less than
        `MINIMUM_TEMPERATURE_SPAN_C`, or too few degrees of freedom remain.
    """
    excluded = sorted(set(nights_excluded))
    counts: dict[str, int] = defaultdict(int)
    for pair in pairs:
        counts[pair.filter_name] += 1
    kept = [pair for pair in pairs if counts[pair.filter_name] >= MINIMUM_FILTER_SHARE_PAIRS]
    if len(kept) < MINIMUM_FOCUS_PAIRS:
        return FocusTemperatureFit(
            pairs_used=len(kept),
            nights_excluded=excluded,
            message=f"The focus history has {len(kept)} usable pairs; a fit needs {MINIMUM_FOCUS_PAIRS}.",
        )
    temperatures = np.array([pair.temperature_c for pair in kept], dtype=float)
    positions = np.array([pair.position for pair in kept], dtype=float)
    span = float(temperatures.max() - temperatures.min())
    if span < MINIMUM_TEMPERATURE_SPAN_C:
        return FocusTemperatureFit(
            pairs_used=len(kept),
            nights_excluded=excluded,
            message=(f"The pairs span {span:.1f} C; a fit needs at least {MINIMUM_TEMPERATURE_SPAN_C:g} C."),
        )
    filters = sorted({pair.filter_name for pair in kept})
    columns = [np.array([pair.filter_name == name for pair in kept], dtype=float) for name in filters]
    design = np.column_stack([*columns, temperatures])
    freedom = len(kept) - design.shape[1]
    if freedom < 3:
        return FocusTemperatureFit(
            pairs_used=len(kept),
            nights_excluded=excluded,
            message=f"Only {freedom} degrees of freedom remain after the fit; a fit needs 3.",
        )
    coefficients, *_ = np.linalg.lstsq(design, positions, rcond=None)
    residual = positions - design @ coefficients
    variance = float(residual @ residual) / freedom
    covariance = np.linalg.pinv(design.T @ design) * variance
    slope = float(coefficients[-1])
    uncertainty = math.sqrt(max(float(covariance[-1, -1]), 0.0))
    nights = sorted({pair.night for pair in kept})
    return FocusTemperatureFit(
        coefficient_steps_per_c=slope,
        uncertainty_steps_per_c=uncertainty,
        pairs_used=len(kept),
        nights_used=nights,
        nights_excluded=excluded,
        filters_used=filters,
        temperature_range_c=(float(temperatures.min()), float(temperatures.max())),
        residual_rms_steps=math.sqrt(float(residual @ residual) / len(kept)),
        message=(
            f"Best focus moves {slope:+.1f} +/- {uncertainty:.1f} steps per degree C, from {len(kept)} "
            f"focus positions on {len(nights)} night(s)."
        ),
    )


def pairs_from_autofocus_runs(
    contexts: Iterable[EkosSessionContext], telescope_name: str, camera_name: str
) -> list[FocusPair]:
    """List the best-focus positions of recorded autofocus runs.

    Parameters
    ----------
    contexts : `Iterable` [`EkosSessionContext`]
        The recorded Ekos sessions.
    telescope_name, camera_name : `str`
        The imaging equipment. A session counts only if its frames show it
        used this telescope and camera.

    Returns
    -------
    pairs : `list` [`FocusPair`]
        One for each successful run that has a position and a temperature.
    """
    telescope, camera = normalize_name(telescope_name), normalize_name(camera_name)
    pairs = []
    for context in contexts:
        equipment = context.equipment
        if equipment is None:
            continue
        if normalize_name(equipment.imaging_telescope_name) != telescope:
            continue
        if normalize_name(equipment.imaging_camera_name) != camera:
            continue
        for run in context.autofocus_runs:
            if run.succeeded and run.final_position is not None and run.temperature_c is not None:
                pairs.append(
                    FocusPair(
                        night=observing_night_id(run.timestamp),
                        filter_name=run.filter_name or "",
                        temperature_c=float(run.temperature_c),
                        position=float(run.final_position),
                    )
                )
    return pairs


def pairs_from_frames(frames: Iterable[tuple[object, object]]) -> list[FocusPair]:
    """List the best-focus positions the light-frame headers show.

    The focuser only moves when focus is run again, so the position in a
    frame's header equals the previous frame's unless focus changed. A pair is
    taken from the first frame of each night and filter, and from every frame
    whose position differs from the one before it. Pairs from the other
    frames would repeat one position at drifting temperatures and pull the
    slope toward zero.

    Parameters
    ----------
    frames : `Iterable` [`tuple`]
        ``(target, frame)`` pairs as `equipment_light_frames` yields them. A
        frame needs ``timestamp``, ``focuser_position`` and
        ``focuser_temperature_c``.

    Returns
    -------
    pairs : `list` [`FocusPair`]
        The pairs, with the filter named as the frame record names it.
    """
    grouped: dict[tuple[str, str], list[object]] = defaultdict(list)
    for _target, frame in frames:
        if (
            getattr(frame, "focuser_position", None) is None
            or getattr(frame, "focuser_temperature_c", None) is None
        ):
            continue
        filter_name = str(getattr(getattr(frame, "filter", ""), "value", getattr(frame, "filter", "")))
        grouped[observing_night_id(frame.timestamp), filter_name].append(frame)
    pairs = []
    for (night, filter_name), group in grouped.items():
        previous = None
        for frame in sorted(group, key=lambda item: item.timestamp):
            if previous is None or frame.focuser_position != previous:
                pairs.append(
                    FocusPair(
                        night, filter_name, float(frame.focuser_temperature_c), float(frame.focuser_position)
                    )
                )
            previous = frame.focuser_position
    return pairs


def measure_focus_temperature_coefficient(
    context: ControlContext,
    exclude_nights: Collection[str] | None = None,
    source: FocusSource = "both",
) -> FocusTemperatureFit:
    """Fit the focus slope on the active equipment's own focus history.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the active equipment, the recorded Ekos sessions and the
        science library.
    exclude_nights : `Collection` [`str`], optional
        Nights to leave out, for a leave-one-out check.
    source : `str`, optional
        ``"autofocus_runs"``, ``"frames"`` or ``"both"``. With ``"both"``, a
        night that has autofocus runs uses them, and the frame headers fill
        the nights that have none.

    Returns
    -------
    fit : `FocusTemperatureFit`
        The slope in steps per degree Celsius with its uncertainty and the
        nights used, or a message saying why there is none.

    Raises
    ------
    InvalidArgumentError
        If `source` is not one of the three names.
    """
    if source not in ("autofocus_runs", "frames", "both"):
        raise InvalidArgumentError("source must be 'autofocus_runs', 'frames' or 'both'.")
    excluded = set(exclude_nights or ())
    catalog = get_equipment_catalog(context.config)
    telescope, camera = catalog.active_telescope(), catalog.active_camera()
    if telescope is None or camera is None:
        return FocusTemperatureFit(
            nights_excluded=sorted(excluded), message="No imaging telescope and camera are active."
        )
    run_pairs: list[FocusPair] = []
    if source in ("autofocus_runs", "both"):
        run_pairs = pairs_from_autofocus_runs(
            context.butler.get_all("ekos_session_context"), telescope.name, camera.name
        )
    frame_pairs: list[FocusPair] = []
    if source in ("frames", "both"):
        frame_pairs = pairs_from_frames(
            equipment_light_frames(context.astrometrics, telescope.name, camera.name)
        )
    nights_with_runs = {pair.night for pair in run_pairs}
    pairs = run_pairs + [pair for pair in frame_pairs if pair.night not in nights_with_runs]
    fit = fit_thermal_coefficient_with_uncertainty(
        [pair for pair in pairs if pair.night not in excluded], excluded
    )
    return fit.model_copy(update={"source": source})
