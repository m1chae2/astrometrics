"""Purpose: Derive how measured targets pull the tracking-risk map.

Description: The tracking-risk map (`wayfindinglib.analytics.tracking_risk`)
blends a score from geometry with the scores of measured targets. Three
numbers decide the blend: how wide a measured target's pull is, how many
plate solves give a target its full weight, and the largest share of a score
that measured targets may decide.

This module chooses them by leave-one-out prediction. For each measured
target, the map is built from all the other targets and asked for the score at
the held-out target's own place; the squared difference from the score that
the held-out target's measured jitter gives is its error. The three numbers
are searched on a grid that spans their allowed ranges, and the combination
with the smallest mean squared error is kept.

The cutoffs of the map (`CAUTION_SCORE`, `HIGH_SCORE`) and the jitter used
without a plate scale are not derived here. The cutoffs are the scores that
the safe and trailing jitter limits (0.75 and 1.25 pixels) map to, so they
are fixed by the scoring formula. The fallback jitters are used only when the
plate scale is unknown, and then there is nothing to derive them from.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence

import numpy as np

from astrometricslib.parameters import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    Tuner,
    TuningInputs,
    TuningResult,
    TuningSkippedError,
    resolve_parameter,
)
from wayfindinglib.analytics.alignment_sessions import group_alignment_attempts
from wayfindinglib.analytics.tracking_risk import (
    FULL_CONFIDENCE_SOLVES,
    INFLUENCE_RADIUS_DEG,
    MAX_MEASURED_SHARE,
    BlendSettings,
    TrackedTarget,
    blended_score,
    rms_risk_score,
)
from wayfindinglib.analytics.tuning_support import records_of

__all__ = [
    "SEARCH_POINTS_PER_NUMBER",
    "TRACKING_BLEND_TUNER",
    "best_blend_settings",
    "leave_one_out_error",
]

SEARCH_POINTS_PER_NUMBER = TunedParameter(
    name="wayfinding.search_points_per_number",
    default=8,
    unit="points",
    description="How many trial values the blend search tries for each of its three numbers.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="WAY-D130",
)
"""How many trial values the blend search tries for each of its three numbers.

Eight values per number is 512 combinations, each scored on every held-out
target. The search is a grid, so the answer is one of its points, and a finer
grid would only claim more precision than the measured targets can support.
Register entry WAY-D130."""


def leave_one_out_error(
    targets: Sequence[TrackedTarget],
    settings: BlendSettings,
    latitude_deg: float,
    plate_scale_arcsec_per_px: float | None,
) -> float:
    """Measure how well a blend predicts each target from the others.

    Parameters
    ----------
    targets : `~collections.abc.Sequence` [`TrackedTarget`]
        The measured targets, each with a jitter and a solve count.
    settings : `BlendSettings`
        The trial numbers.
    latitude_deg : `float`
        Observer latitude.
    plate_scale_arcsec_per_px : `float` or `None`
        Camera plate scale, for scoring a jitter.

    Returns
    -------
    error : `float`
        The mean squared difference, over targets, between the blended score
        at a target's place without that target and the score of its own
        jitter.
    """
    total = 0.0
    for index, held_out in enumerate(targets):
        others = [target for position, target in enumerate(targets) if position != index]
        predicted = blended_score(
            held_out.ha_deg, held_out.dec_deg, latitude_deg, others, plate_scale_arcsec_per_px, settings
        )
        measured = rms_risk_score(held_out.rms_arcsec, plate_scale_arcsec_per_px)
        total += (predicted - measured) ** 2
    return total / len(targets)


def best_blend_settings(
    targets: Sequence[TrackedTarget],
    latitude_deg: float,
    plate_scale_arcsec_per_px: float | None,
    points_per_number: int,
) -> tuple[BlendSettings, float]:
    """Search a grid for the blend numbers with the smallest prediction error.

    Parameters
    ----------
    targets : `~collections.abc.Sequence` [`TrackedTarget`]
        The measured targets. At least two.
    latitude_deg : `float`
        Observer latitude.
    plate_scale_arcsec_per_px : `float` or `None`
        Camera plate scale.
    points_per_number : `int`
        How many trial values to try for each number.

    Returns
    -------
    settings, error : `tuple` [`BlendSettings`, `float`]
        The best numbers and their leave-one-out error. The radius and the
        solve count are spaced evenly on a logarithmic scale between their
        allowed limits, and the share evenly on a linear scale.

    Raises
    ------
    ValueError
        If no combination gives a finite error.
    """
    radius_low, radius_high = INFLUENCE_RADIUS_DEG.allowed_range
    solves_low, solves_high = FULL_CONFIDENCE_SOLVES.allowed_range
    share_low, share_high = MAX_MEASURED_SHARE.allowed_range
    radii = np.geomspace(radius_low, radius_high, points_per_number)
    solves = np.geomspace(solves_low, solves_high, points_per_number)
    shares = np.linspace(share_low, share_high, points_per_number)
    best_settings: BlendSettings | None = None
    best_error = float("inf")
    for radius, solve_count, share in itertools.product(radii, solves, shares):
        settings = BlendSettings(float(radius), float(solve_count), float(share))
        error = leave_one_out_error(targets, settings, latitude_deg, plate_scale_arcsec_per_px)
        if error < best_error:
            best_settings, best_error = settings, error
    if best_settings is None:
        raise ValueError("the search found no finite error")
    return best_settings, best_error


def _plate_scale(inputs: TuningInputs, config: object) -> float | None:
    """Work out the plate scale of the setup being tuned.

    Parameters
    ----------
    inputs : `TuningInputs`
        The setup's camera and optic names.
    config : `AppConfiguration`
        The loaded configuration, for the equipment catalog.

    Returns
    -------
    plate_scale : `float` or `None`
        Arcseconds per pixel, or `None` when the catalog lacks the camera or
        the optic.
    """
    from astrometricslib import ObservatoryConfiguration
    from wayfindinglib.data_access.equipment_catalog_reader import camera_key, get_equipment_catalog

    catalog = get_equipment_catalog(config)  # type: ignore[arg-type]
    camera = next(
        (c for c in catalog.cameras if camera_key(c.name) == camera_key(inputs.setup.camera_name)), None
    )
    telescope = next((t for t in catalog.telescopes if t.name == inputs.setup.optic_name), None)
    if camera is None or telescope is None:
        return None
    return ObservatoryConfiguration(telescope=telescope, camera=camera).plate_scale_arcsec_per_px


def tune_tracking_blend(inputs: TuningInputs) -> TuningResult:
    """Derive the blend numbers of the tracking-risk map.

    Parameters
    ----------
    inputs : `TuningInputs`
        The tuning run's inputs. Reads the ``"wayfinder"`` service.

    Returns
    -------
    result : `TuningResult`
        The pull width, the full-confidence solve count and the largest
        measured share.

    Raises
    ------
    TuningSkippedError
        If the plate scale or the observer location is unknown, or fewer
        than two targets have a measured jitter.
    """
    from wayfindinglib.tasks.control_tasks.alignment_history import attempt_models
    from wayfindinglib.tasks.control_tasks.performance_envelope_tasks import _mount_hour_angles_deg

    wayfinder = inputs.service("wayfinder")
    context = wayfinder.control._context
    plate_scale = _plate_scale(inputs, wayfinder.config)
    if plate_scale is None:
        raise TuningSkippedError("the setup's camera or optic is not in the equipment catalog")
    location = context.observer_location()
    if location is None:
        raise TuningSkippedError("the observer location is unknown")
    rows = records_of(wayfinder).get_session_alignment_attempts("all", limit=10_000_000)
    sessions = [
        session
        for session in group_alignment_attempts(attempt_models(rows))
        if session.frame_count >= 2 and session.rms_total_arcsec > 0
    ]
    targets = [
        TrackedTarget(
            ha_deg=hour_angle,
            dec_deg=session.mean_dec_deg,
            rms_arcsec=session.rms_total_arcsec,
            solve_count=session.frame_count,
        )
        for session, hour_angle in zip(
            sessions, _mount_hour_angles_deg(sessions, location["longitude"]), strict=True
        )
        if hour_angle is not None
    ]
    if len(targets) < 2:
        raise TuningSkippedError("fewer than two targets have a measured tracking jitter")
    settings, error = best_blend_settings(
        targets, location["latitude"], plate_scale, resolve_parameter(SEARCH_POINTS_PER_NUMBER).value
    )
    return TuningResult(
        values={
            INFLUENCE_RADIUS_DEG.name: settings.influence_radius_deg,
            FULL_CONFIDENCE_SOLVES.name: settings.full_confidence_solves,
            MAX_MEASURED_SHARE.name: settings.max_measured_share,
        },
        sample=len(targets),
        source=(
            f"The blend with the smallest leave-one-out score error ({error:.4f} mean squared) over "
            f"{len(targets)} measured targets, searched on a grid across the allowed ranges."
        ),
    )


TRACKING_BLEND_TUNER = Tuner(
    name="wayfinding.tracking_blend",
    parameters=(INFLUENCE_RADIUS_DEG, FULL_CONFIDENCE_SOLVES, MAX_MEASURED_SHARE),
    method=(
        "Groups every stored plate solve into measured targets with a jitter and a place on the mount. "
        "Predicts each target's score from the others and keeps the pull width, solve count and measured "
        "share with the smallest mean squared error, searched on a grid."
    ),
    tune=tune_tracking_blend,
)
