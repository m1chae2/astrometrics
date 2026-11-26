"""Purpose: Put every observing session of a target on one brightness scale.

Description: Each session divides a star's flux by its own comparison
ensemble, so the same star can sit at a different normalized level in each
session. When two sessions share no comparison star, the whole field moves
by one common factor (on M 57, about 1.97). This module finds that factor
from the stars both sessions measured and scales the later session onto the
reference session before the light curves are joined. A single star's own
change survives, because the factor is the middle value of hundreds of
other stars' ratios and one star cannot move it. The method follows the
idea of inhomogeneous ensemble photometry (Honeycutt 1992, PASP 104, 435):
tie every night to the same field of stars before comparing nights.

The module holds the rules (`measure_session_tie`) and the changes to a
session's stars (`scale_session_stars`). The matching of stars by sky
position stays in `batch.py`, which owns the cross-session match.
"""

import logging
import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.quality.quality_summary import SessionTieRecord
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.utilities.robust_statistics import robust_sigma

logger = logging.getLogger(__name__)

TIE_STATUS_REFERENCE = "reference"
TIE_STATUS_TIED = "tied"
TIE_STATUS_UNTIED = "untied"

# The fewest tie stars a session needs before its factor is trusted. The
# factor is the median of the stars' level ratios, and the standard error of
# a median is 1.2533 x spread / sqrt(N) (1.2533 = sqrt(pi / 2)). The ratio of
# a faint star's level between two sessions scatters by about 10% (its own
# noise in each session's median). With N = 20 the factor is then good to
# 1.2533 x 0.10 / sqrt(20) = 2.8%, about 0.03 mag, the size of the smallest
# change the between-session search flags (0.02 mag). With fewer stars the
# factor would hide a change that size. 20 is also the size of one
# comparison ensemble, so it asks for as many stars as the ensemble itself
# has. Design estimate (PHO-D37): not yet checked on a real target with
# known constant stars.
MINIMUM_TIE_STARS = TunedParameter(
    name="photometry.minimum_tie_stars",
    default=20,
    unit="stars",
    description="Fewest stars two sessions must share before one is scaled onto the other.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="PHO-D37",
)

# A star whose level ratio is farther than this many robust standard
# deviations from the median ratio is left out of the factor. For normally
# distributed ratios 3 sigma keeps 99.7% of the stars, so clipping costs
# almost nothing, and it removes the few stars that really changed (a
# variable, a blend, a bad match). Design estimate (PHO-D37).
TIE_CLIP_SIGMA = TunedParameter(
    name="photometry.tie_clip_sigma",
    default=3.0,
    unit="sigma",
    description="Distance, in robust standard deviations, beyond which a tie star's ratio is clipped.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="PHO-D37",
)

# The clipping repeats until no star is clipped, or this many rounds have
# run. Each round removes at least one star, and with hundreds of stars the
# loop settles in two or three rounds, so five is a safety limit and not a
# tuned value.
_MAXIMUM_CLIP_ROUNDS = 5

# The standard error of a sample median of normally distributed values is
# sqrt(pi / 2) = 1.2533 times the standard error of their mean.
_MEDIAN_STANDARD_ERROR_FACTOR = 1.2533


def measure_session_tie(
    session_id: str,
    reference_session_id: str,
    reference_levels: Sequence[float],
    session_levels: Sequence[float],
) -> SessionTieRecord:
    """Find the factor that puts one session on the reference session's scale.

    Each pair of values is one tie star: its median normalized flux in the
    reference session and in this session. The ratio reference over session
    is the same for every constant star when the two ensembles differ by one
    common factor. The factor is the median of those ratios, found in the
    logarithm so that a ratio of 2 and one of 0.5 are equally far from 1.
    Ratios farther than `TIE_CLIP_SIGMA` robust standard deviations from the
    median are clipped, and the median is found again, until none is
    clipped.

    Parameters
    ----------
    session_id : `str`
        The session to scale.
    reference_session_id : `str`
        The session it is scaled onto.
    reference_levels : `Sequence` [`float`]
        The tie stars' median normalized flux in the reference session. Each
        value is positive. No units.
    session_levels : `Sequence` [`float`]
        The same stars' median normalized flux in this session, in the same
        order. Each value is positive. No units.

    Returns
    -------
    record : `SessionTieRecord`
        `tied` with the factor, its uncertainty and the spread when there
        are at least `MINIMUM_TIE_STARS` stars after clipping, else
        `untied` with the reason. The uncertainty and the spread are
        fractions of the level (0.02 is 2%).
    """
    minimum_stars = int(resolve(MINIMUM_TIE_STARS).value)
    clip_sigma = float(resolve(TIE_CLIP_SIGMA).value)
    reference = np.asarray(reference_levels, dtype=float)
    session = np.asarray(session_levels, dtype=float)
    valid = np.isfinite(reference) & np.isfinite(session) & (reference > 0) & (session > 0)
    log_ratios = np.log(reference[valid] / session[valid])
    candidate_count = int(log_ratios.size)
    if candidate_count == 0:
        return SessionTieRecord(
            session_id=session_id,
            reference_session_id=reference_session_id,
            status=TIE_STATUS_UNTIED,
            tie_star_count=0,
            reason="no star was measured in both this session and the reference session",
        )

    kept = log_ratios
    for _ in range(_MAXIMUM_CLIP_ROUNDS):
        median = float(np.median(kept))
        spread = robust_sigma(kept)
        if spread <= 0:
            break
        survivors = kept[np.abs(kept - median) <= clip_sigma * spread]
        if survivors.size == kept.size or survivors.size == 0:
            break
        kept = survivors
    median = float(np.median(kept))
    spread = robust_sigma(kept)
    star_count = int(kept.size)
    if star_count < minimum_stars:
        return SessionTieRecord(
            session_id=session_id,
            reference_session_id=reference_session_id,
            status=TIE_STATUS_UNTIED,
            tie_star_count=star_count,
            spread=spread,
            reason=(
                f"only {star_count} tie star(s) were usable and at least {minimum_stars} are needed, "
                "so the factor cannot be found well enough"
            ),
        )
    return SessionTieRecord(
        session_id=session_id,
        reference_session_id=reference_session_id,
        status=TIE_STATUS_TIED,
        factor=math.exp(median),
        # The logarithm of a ratio close to 1 equals the fractional change,
        # so the spread and the uncertainty of the log are fractions.
        uncertainty=_MEDIAN_STANDARD_ERROR_FACTOR * spread / math.sqrt(star_count),
        spread=spread,
        tie_star_count=star_count,
    )


def scale_session_stars(stellar_objects: Sequence[Any], factor: float) -> None:
    """Scale one session's normalized levels by the tie factor, in place.

    The normalized and detrended fluxes and their errors are multiplied by
    `factor`, so a point's signal-to-noise ratio does not change. The mean
    flux is scaled the same way. The coefficient of variation and the
    variability indices are ratios of a scatter to a level, so they do not
    change. The raw flux (ADU per second) is never scaled.

    Parameters
    ----------
    stellar_objects : `Sequence` [`StellarObject`]
        Every star of the session, matched or not, so all levels end on the
        same scale. Stars with no light curve are skipped.
    factor : `float`
        The tie factor. Positive, no units.
    """
    for star in stellar_objects:
        photometry = star.photometry
        if photometry is None:
            continue
        photometry.fluxes_normalized = [value * factor for value in photometry.fluxes_normalized]
        photometry.fluxes_detrended = [value * factor for value in photometry.fluxes_detrended]
        photometry.fluxes_normalized_errors = [
            value * factor for value in photometry.fluxes_normalized_errors
        ]
        photometry.fluxes_detrended_errors = [value * factor for value in photometry.fluxes_detrended_errors]
        if photometry.mean_flux is not None:
            photometry.mean_flux = photometry.mean_flux * factor


def log_tied_session(record: SessionTieRecord, target_id: str) -> None:
    """Log one INFO line for a session that was tied to the field.

    Parameters
    ----------
    record : `SessionTieRecord`
        The tied session's record, with its factor and spread.
    target_id : `str`
        The target, named in the line.
    """
    logger.info(
        "[%s] Session %s tied to %s: factor %.3f from %d shared stars, spread %.2f%%.",
        target_id,
        record.session_id,
        record.reference_session_id,
        record.factor,
        record.tie_star_count,
        100.0 * (record.spread or 0.0),
    )


def warn_about_untied_session(record: SessionTieRecord, target_id: str) -> None:
    """Log one warning for a session that could not be tied to the field.

    The run collects every logged warning, so the job lists it and the
    reason is not lost.

    Parameters
    ----------
    record : `SessionTieRecord`
        The untied session's record.
    target_id : `str`
        The target, named in the warning.
    """
    logger.warning(
        "[%s] Session %s was not tied to the field (reference session %s): %s. Its levels are joined "
        "unscaled and its change between sessions is not reported as comparable.",
        target_id,
        record.session_id,
        record.reference_session_id,
        record.reason,
    )
