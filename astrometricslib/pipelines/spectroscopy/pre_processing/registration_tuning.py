"""Tune the star-registration limits of the spectral pipeline from stored data.

The spectral pipeline names the stars of a spectral stack by sliding the
stack onto a plate-solved normal stack of the same target (see
`spectral_star_registration`). Six limits shape that search: how far it
looks, how wide a vote bin is, how close a star must land to count as a match
and how many stars must agree. Each was set by hand on a few targets.

This tuner measures them on the library. A star with a stored spectrum has
two positions that are known to belong to the same star: where the plate
solution of the normal stack places it, and where its zero order (the
undispersed image) sits in the spectral stack. The difference between the two
is the real shift between the stacks. Over the stars of one target the shifts
agree on one value (the target's shift) apart from centroid scatter. Over
many targets the shifts give the window, the bin and the match distance. The
count comes from the highest vote that stars piled up by chance, away from
the real shift.

The numbers this method itself uses (which share of cases to cover, how much
margin to leave) are declared below, each with the reason for its value.
"""

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.tuning import Tuner, TuningInputs, TuningResult, TuningSkippedError
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.target_center_hint import resolve_solved_stack_wcs
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_star_registration import (
    _DEFAULT_MAX_MATCH_DISTANCE_PX,
    _DEFAULT_MAX_TRANSLATION_OFFSET_PX,
    _MIN_CONTROL_POINTS,
    _SOLUTION_MAX_TRANSLATION_OFFSET_PX,
    _TRANSLATION_BIN_PX,
    _positions_through_wcs,
)
from astrometricslib.pipelines.spectroscopy.tuning_data import (
    quantile_of,
    spectral_target_stars,
)

logger = logging.getLogger(__name__)

# The share of the real star-to-star scatter that one vote bin must hold. A
# bin that held every star would have to be as wide as the worst outlier, and
# one centroid gone wrong would widen it for all targets. Nine in ten leaves
# the worst tenth to the median step that follows the vote (see
# `_estimate_translation_offset`).
_BIN_COVERAGE = TunedParameter(
    name="spectroscopy.registration_tuning.bin_coverage",
    default=0.9,
    unit="fraction",
    description="The share of real star-to-star scatter that one vote bin must hold.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="SPE-D50",
)

# The share of real matches that must land within the match distance. A
# star that is a true match but lands outside is left unnamed, so this is
# kept high. Chance pairings are limited by the star count instead (see
# `_CHANCE_VOTE_COVERAGE`).
_MATCH_COVERAGE = TunedParameter(
    name="spectroscopy.registration_tuning.match_coverage",
    default=0.99,
    unit="fraction",
    description="The share of real star matches that must land within the match distance.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="SPE-D50",
)

# The share of targets whose shift the blind search window must contain. The
# plate-solved search has its own, wider window that contains every target;
# the blind window is the narrower choice that keeps chance pairings down.
_BLIND_WINDOW_COVERAGE = TunedParameter(
    name="spectroscopy.registration_tuning.blind_window_coverage",
    default=0.9,
    unit="fraction",
    description="The share of targets whose real shift the blind search window must contain.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="SPE-D50",
)

# How much wider than the largest measured shift a window is made. The next
# mount pointing or grating swap can shift farther than any shift seen so
# far, and a window that just fits the sample would fail on the first new
# one. Half as much again is a design margin.
_WINDOW_MARGIN = TunedParameter(
    name="spectroscopy.registration_tuning.window_margin",
    default=1.5,
    unit="× largest shift",
    description="How much wider than the measured shifts a search window is made.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="SPE-D50",
)

# The share of chance votes that the required vote count must exceed. The
# count is one more than this quantile of the tallest vote that unrelated
# stars piled up, so a chance peak seldom reaches it.
_CHANCE_VOTE_COVERAGE = TunedParameter(
    name="spectroscopy.registration_tuning.chance_vote_coverage",
    default=0.95,
    unit="fraction",
    description="The share of chance vote peaks that the required vote count must exceed.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="SPE-D50",
)

# How many bin widths around the real shift are left out when the tallest
# chance vote is looked for. The stars that belong to the real shift fill the
# bin of the real shift and its neighbors, so those bins do not measure
# chance.
_TRUE_PEAK_EXCLUSION_BINS = TunedParameter(
    name="spectroscopy.registration_tuning.true_peak_exclusion_bins",
    default=2.0,
    unit="× bin width",
    description="How far around the real shift the chance-vote search leaves out, in bin widths.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="SPE-D50",
)

# The fewest stars with a stored spectrum a target needs before its shift and
# scatter count. A median and a spread need three values to mean anything.
_MINIMUM_STARS_PER_TARGET = TunedParameter(
    name="spectroscopy.registration_tuning.minimum_stars_per_target",
    default=3,
    unit="count",
    description="The fewest stars with a stored spectrum a target needs to count in the tuning.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="SPE-D50",
)


@dataclass(frozen=True)
class TargetRegistration:
    """What the stored stars say about one target's registration.

    Attributes
    ----------
    target_id : `str`
        The target.
    shift : `tuple` [`float`, `float`]
        The median shift, in pixels, that takes the plate solution's pixel
        positions onto the spectral stack.
    residuals : `numpy.ndarray`
        Each star's shift minus the median shift, as ``(count, 2)`` pixels.
    reference_points : `numpy.ndarray`
        Where the plate solution places every catalog star of the target.
    spectral_points : `numpy.ndarray`
        Where the zero orders of the stars with spectra sit in the spectral
        stack.
    """

    target_id: str
    shift: tuple[float, float]
    residuals: np.ndarray
    reference_points: np.ndarray
    spectral_points: np.ndarray


def measure_target_registration(
    target_id: str,
    placed_positions: dict[str, tuple[float, float]],
    spectral_positions: dict[str, tuple[float, float]],
    minimum_stars: int,
) -> TargetRegistration | None:
    """Measure the shift between a plate solution and a spectral stack.

    Parameters
    ----------
    target_id : `str`
        The target.
    placed_positions : `dict` [`str`, `tuple` [`float`, `float`]]
        Every star's pixel position from the plate solution, by star id.
    spectral_positions : `dict` [`str`, `tuple` [`float`, `float`]]
        Each star's zero-order pixel position in the spectral stack, by id.
    minimum_stars : `int`
        The fewest stars present in both sets for the target to count.

    Returns
    -------
    registration : `TargetRegistration` or `None`
        The measurement, or `None` when fewer than `minimum_stars` stars
        are in both sets.
    """
    shared_ids = sorted(set(placed_positions) & set(spectral_positions))
    if len(shared_ids) < minimum_stars:
        return None
    shifts = np.array([
        (
            spectral_positions[star_id][0] - placed_positions[star_id][0],
            spectral_positions[star_id][1] - placed_positions[star_id][1],
        )
        for star_id in shared_ids
    ])
    median_shift = np.median(shifts, axis=0)
    return TargetRegistration(
        target_id=target_id,
        shift=(float(median_shift[0]), float(median_shift[1])),
        residuals=shifts - median_shift,
        reference_points=np.array(list(placed_positions.values()), dtype=float),
        spectral_points=np.array([spectral_positions[star_id] for star_id in shared_ids], dtype=float),
    )


def vote_bin_width(registrations: list[TargetRegistration], coverage: float) -> float:
    """Find the vote bin width that holds a share of the real scatter.

    Parameters
    ----------
    registrations : `list` [`TargetRegistration`]
        The measured targets.
    coverage : `float`
        The share of star-to-star scatter one bin must hold.

    Returns
    -------
    bin_px : `float`
        Twice the coverage quantile of the absolute scatter along an axis,
        so a star within that distance of the shift on both sides is in the
        same bin.
    """
    scatter = np.abs(np.concatenate([entry.residuals.ravel() for entry in registrations]))
    return 2.0 * quantile_of(scatter, coverage)


def match_distance(registrations: list[TargetRegistration], coverage: float) -> float:
    """Find the match distance that accepts a share of the real matches.

    Parameters
    ----------
    registrations : `list` [`TargetRegistration`]
        The measured targets.
    coverage : `float`
        The share of real matches that must be accepted.

    Returns
    -------
    distance_px : `float`
        The coverage quantile of each star's distance from its target's
        median shift.
    """
    distances = np.concatenate([
        np.hypot(entry.residuals[:, 0], entry.residuals[:, 1]) for entry in registrations
    ])
    return quantile_of(distances, coverage)


def search_windows(
    registrations: list[TargetRegistration], blind_coverage: float, margin: float
) -> tuple[float, float]:
    """Find the blind and the plate-solved search windows.

    Parameters
    ----------
    registrations : `list` [`TargetRegistration`]
        The measured targets.
    blind_coverage : `float`
        The share of targets whose shift the blind window must contain.
    margin : `float`
        How much wider than the shift the windows are made.

    Returns
    -------
    blind_window_px, solution_window_px : `tuple` [`float`, `float`]
        The half-width of each window along an axis. The blind window covers
        the coverage share of targets; the plate-solved window covers all of
        them.
    """
    largest_axis_shifts = np.array([max(abs(entry.shift[0]), abs(entry.shift[1])) for entry in registrations])
    return (
        margin * quantile_of(largest_axis_shifts, blind_coverage),
        margin * float(largest_axis_shifts.max()),
    )


def chance_vote_peak(
    registration: TargetRegistration, window_px: float, bin_px: float, exclusion_px: float
) -> int:
    """Find the tallest vote that unrelated star pairs make.

    Every pairing of a spectral zero order with a plate-solved star votes for
    the shift between them, as the registration does. The votes near the real
    shift are left out; the tallest bin that remains is a chance pile-up.

    Parameters
    ----------
    registration : `TargetRegistration`
        The measured target.
    window_px : `float`
        The search window half-width.
    bin_px : `float`
        The vote bin width.
    exclusion_px : `float`
        How far around the real shift the bins are ignored.

    Returns
    -------
    votes : `int`
        The tallest vote outside the real shift's neighborhood.
    """
    differences = (
        registration.spectral_points[:, np.newaxis, :] - registration.reference_points[np.newaxis, :, :]
    ).reshape(-1, 2)
    inside = (np.abs(differences[:, 0]) <= window_px) & (np.abs(differences[:, 1]) <= window_px)
    differences = differences[inside]
    if len(differences) == 0:
        return 0
    edges = np.arange(-window_px, window_px + bin_px, bin_px)
    histogram, x_edges, y_edges = np.histogram2d(differences[:, 0], differences[:, 1], bins=[edges, edges])
    x_centers = (x_edges[:-1] + x_edges[1:]) / 2.0
    y_centers = (y_edges[:-1] + y_edges[1:]) / 2.0
    near_true = (np.abs(x_centers[:, np.newaxis] - registration.shift[0]) <= exclusion_px) & (
        np.abs(y_centers[np.newaxis, :] - registration.shift[1]) <= exclusion_px
    )
    return int(histogram[~near_true].max()) if (~near_true).any() else 0


def required_vote_count(chance_peaks: list[int], coverage: float) -> int:
    """Find the fewest agreeing stars that a chance pile-up seldom reaches.

    Parameters
    ----------
    chance_peaks : `list` [`int`]
        The tallest chance vote of each target.
    coverage : `float`
        The share of chance peaks the count must exceed.

    Returns
    -------
    count : `int`
        One more than the coverage quantile of the chance peaks, rounded up.
    """
    return math.ceil(quantile_of(chance_peaks, coverage)) + 1


def tune_registration_search(inputs: TuningInputs) -> TuningResult:
    """Tune the registration limits from the library's stored stars.

    Parameters
    ----------
    inputs : `TuningInputs`
        The setup and the ``"astrometrics"`` service.

    Returns
    -------
    result : `TuningResult`
        The five limits, from the targets with a plate-solved normal stack
        and stars with stored spectra.

    Raises
    ------
    TuningSkippedError
        If no target of the setup's camera has both.
    """
    astrometrics: Any = inputs.service("astrometrics")
    minimum_stars = int(resolve(_MINIMUM_STARS_PER_TARGET).value)
    registrations: list[TargetRegistration] = []
    for entry in spectral_target_stars(astrometrics, inputs.setup.camera_name, inputs.configuration):
        wcs = resolve_solved_stack_wcs(entry.target, entry.stack_path)
        if wcs is None:
            continue
        spectral_positions = {
            star.id: (
                float(star.spectroscopy.star_position_px[0]),
                float(star.spectroscopy.star_position_px[1]),
            )
            for star in entry.stars
            if star.has_spectra
            and star.spectroscopy.star_position_px is not None
            and len(star.spectroscopy.star_position_px) >= 2
        }
        registration = measure_target_registration(
            entry.target_id, _positions_through_wcs(entry.stars, wcs), spectral_positions, minimum_stars
        )
        if registration is not None:
            registrations.append(registration)
    if not registrations:
        raise TuningSkippedError(
            "no target of this camera has a plate-solved normal stack and stars with stored spectra"
        )

    bin_px = vote_bin_width(registrations, resolve(_BIN_COVERAGE).value)
    distance_px = match_distance(registrations, resolve(_MATCH_COVERAGE).value)
    blind_window_px, solution_window_px = search_windows(
        registrations, resolve(_BLIND_WINDOW_COVERAGE).value, resolve(_WINDOW_MARGIN).value
    )
    exclusion_px = resolve(_TRUE_PEAK_EXCLUSION_BINS).value * bin_px
    chance_peaks = [
        chance_vote_peak(entry, solution_window_px, bin_px, exclusion_px) for entry in registrations
    ]
    count = required_vote_count(chance_peaks, resolve(_CHANCE_VOTE_COVERAGE).value)
    return TuningResult(
        values={
            _MIN_CONTROL_POINTS.name: count,
            _DEFAULT_MAX_MATCH_DISTANCE_PX.name: round(distance_px, 1),
            _DEFAULT_MAX_TRANSLATION_OFFSET_PX.name: round(blind_window_px, 0),
            _TRANSLATION_BIN_PX.name: round(bin_px, 1),
            _SOLUTION_MAX_TRANSLATION_OFFSET_PX.name: round(solution_window_px, 0),
        },
        sample=len(registrations),
        source=(
            f"Shift, scatter and chance votes of the zero orders of stars with stored spectra against "
            f"the plate solution of the normal stack, {len(registrations)} targets"
        ),
    )


REGISTRATION_SEARCH_TUNER = Tuner(
    name="spectroscopy.registration_search",
    parameters=(
        _MIN_CONTROL_POINTS,
        _DEFAULT_MAX_MATCH_DISTANCE_PX,
        _DEFAULT_MAX_TRANSLATION_OFFSET_PX,
        _TRANSLATION_BIN_PX,
        _SOLUTION_MAX_TRANSLATION_OFFSET_PX,
    ),
    method=(
        "For each target with a plate-solved normal stack, takes the shift between each star's plate-solved "
        "position and its stored zero-order position. The bin is twice the scatter quantile, the match "
        "distance its radial quantile, the windows the shifts with a margin, and the vote count one more "
        "than the tallest chance vote."
    ),
    tune=tune_registration_search,
)
