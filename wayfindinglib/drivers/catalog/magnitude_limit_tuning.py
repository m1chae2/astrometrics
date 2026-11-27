"""Purpose: Derive how faint a star the deep star catalog should download.

Description: The deep star catalog (`deep_star_catalog_builder`) downloads
Gaia stars down to a limiting Gaia G magnitude. A limit that is too bright
leaves out stars the frames can measure; one that is too faint makes the
catalog much larger for stars no frame can measure. The limit should follow
what this telescope and camera actually measure.

The rule in this module reads the stars in the library that have a measured
light curve and a Gaia G magnitude. The ideal rule would find the magnitude at
which the stored photometric error of the measured stars reaches 5 percent
(signal-to-noise 20). The library stores no per-star magnitude error, only the
scatter of each light curve, and that scatter is mixed with real variability,
saturation and the different fields and cameras the stars come from, so it
does not rise steadily with magnitude. The rule therefore uses the fallback:
the 95th percentile of the Gaia G magnitudes of the stars with measured
photometry, rounded up to the next half magnitude.

Only the statistic (`magnitude_limit_from_magnitudes`) and the star filter
(`gaia_magnitudes_of`) are pure functions; the stars come from the library
through the public `Astrometrics.stars.query`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

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
from wayfindinglib.analytics.tuning_support import percentile
from wayfindinglib.drivers.catalog.deep_star_catalog_builder import DEFAULT_MAGNITUDE_LIMIT

__all__ = [
    "MAGNITUDE_LIMIT_PERCENTILE",
    "MAGNITUDE_LIMIT_STEP",
    "MAGNITUDE_LIMIT_TUNER",
    "gaia_magnitudes_of",
    "magnitude_limit_from_magnitudes",
]

MAGNITUDE_LIMIT_PERCENTILE = TunedParameter(
    name="wayfinding.magnitude_limit_percentile",
    default=95.0,
    unit="percent",
    description="Share of the measured stars that the deep star catalog's magnitude limit must reach.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="WAY-D132",
)
"""Share of the measured stars the magnitude limit must reach.

The faintest few percent of the measured stars are the noisiest and include
stars whose catalog magnitude is wrong, so a limit set by the faintest one
would follow outliers. The 95th percentile keeps nineteen of every twenty
measured stars inside the download. Register entry WAY-D132."""

MAGNITUDE_LIMIT_STEP = TunedParameter(
    name="wayfinding.magnitude_limit_step",
    default=0.5,
    unit="magnitude",
    description="The magnitude limit is rounded up to a multiple of this step.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="WAY-D132",
)
"""The magnitude limit is rounded up to a multiple of this step.

The 95th percentile of a few thousand stars is known to a few hundredths of a
magnitude, but the catalog is downloaded once and its depth is shown to the
user, so a half magnitude is a round, readable depth that is finer than the
half-magnitude uncertainty of the design estimate. Register entry WAY-D132."""


def gaia_magnitudes_of(rows: Iterable[Mapping[str, Any]]) -> list[float]:
    """Pick the Gaia G magnitudes of stars with a measured light curve.

    Parameters
    ----------
    rows : `~collections.abc.Iterable` [`~collections.abc.Mapping`]
        Star summary rows with ``id``, ``magnitude`` and
        ``hasCatalogMagnitude``, built from the stars of
        `Astrometrics.stars.query`.

    Returns
    -------
    magnitudes : `list` [`float`]
        The magnitude of each star whose id starts with ``Gaia`` (so its
        catalog magnitude is Gaia G, not another band) and that has a real
        catalog magnitude.
    """
    magnitudes = []
    for row in rows:
        magnitude = row.get("magnitude")
        if (
            str(row.get("id", "")).startswith("Gaia")
            and row.get("hasCatalogMagnitude")
            and isinstance(magnitude, int | float)
            and math.isfinite(magnitude)
        ):
            magnitudes.append(float(magnitude))
    return magnitudes


def magnitude_limit_from_magnitudes(magnitudes: Sequence[float], percent: float, step: float) -> float:
    """Take a percentile of magnitudes and round it up to a step.

    Parameters
    ----------
    magnitudes : `~collections.abc.Sequence` [`float`]
        The Gaia G magnitudes of the measured stars. Must not be empty.
    percent : `float`
        The percentile, from 0 to 100.
    step : `float`
        The rounding step, in magnitudes.

    Returns
    -------
    limit : `float`
        The percentile rounded up to the next multiple of `step`.
    """
    return math.ceil(round(percentile(magnitudes, percent) / step, 9)) * step


def tune_magnitude_limit(inputs: TuningInputs) -> TuningResult:
    """Derive the faintest Gaia G magnitude to download.

    Parameters
    ----------
    inputs : `TuningInputs`
        The tuning run's inputs. Reads the ``"wayfinder"`` service (and,
        through its `astrometrics`, the library's stored stars).

    Returns
    -------
    result : `TuningResult`
        The limit, resting on the number of Gaia stars with photometry.

    Raises
    ------
    TuningSkippedError
        If the stars cannot be read or none has a Gaia magnitude.
    """
    wayfinder = inputs.service("wayfinder")
    try:
        stars = wayfinder.astrometrics.stars.query(has_photometry=True, has_catalog_magnitude=True)
    except (AttributeError, OSError, ValueError) as error:
        raise TuningSkippedError(f"the library's stars cannot be read: {error}") from error
    magnitudes = gaia_magnitudes_of(
        {"id": star.id, "magnitude": star.magnitude, "hasCatalogMagnitude": star.has_catalog_magnitude}
        for star in stars
    )
    if not magnitudes:
        raise TuningSkippedError("no star with a measured light curve has a Gaia magnitude")
    percent = resolve_parameter(MAGNITUDE_LIMIT_PERCENTILE).value
    step = resolve_parameter(MAGNITUDE_LIMIT_STEP).value
    limit = magnitude_limit_from_magnitudes(magnitudes, percent, step)
    return TuningResult(
        values={DEFAULT_MAGNITUDE_LIMIT.name: limit},
        sample=len(magnitudes),
        source=(
            f"The {percent:g}th percentile of the Gaia G magnitudes of {len(magnitudes)} stars with a "
            f"measured light curve, rounded up to {step:g} mag. The library stores no per-star "
            "magnitude error, so the 5 percent error rule was not used."
        ),
    )


MAGNITUDE_LIMIT_TUNER = Tuner(
    name="wayfinding.deep_catalog_magnitude_limit",
    parameters=(DEFAULT_MAGNITUDE_LIMIT,),
    method=(
        "Reads the stored stars that have a measured light curve and a Gaia G magnitude. Takes the 95th "
        "percentile of their magnitudes and rounds it up to the next half magnitude. (The preferred rule, "
        "the magnitude at which the stored photometric error reaches 5 percent, needs a per-star error "
        "that the library does not store.)"
    ),
    tune=tune_magnitude_limit,
)
