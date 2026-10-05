"""Purpose: Boil a star's stored analysis down to the facts a person reads.

Description: A star record can hold a whole spectrum and a light curve with
thousands of numbers. The Astronomy Manager shows only what they mean: the
star's own spectral type and how well it matched, the absorption features
and emission lines that were found, and whether the brightness repeats. This
module builds that same short summary from a `StellarObject`, so a client
that cannot draw a plot can still read the result. It also holds the
spectral-class grouping the Astronomy Manager's class browser uses. The
backend's `stellar_service` keeps its own copy of that grouping, because it
may import only the library's top-level names.

The wording of each verdict follows the app's own tests: a feature or cycle
is "detected" or "possible" by the false-alarm probability the pipeline
recorded, and nothing is re-measured here.
"""

from typing import Any

SPECTRAL_CLASS_LABELS: dict[str, str] = {
    "O": "Blue supergiants",
    "B": "Blue giants",
    "A": "White stars",
    "F": "Yellow-white stars",
    "G": "Yellow dwarfs",
    "K": "Orange dwarfs",
    "M": "Red dwarfs",
    "C": "Carbon stars",
    "W": "Wolf-Rayet stars",
}
"""The spectral classes the class browser shows, and short descriptions."""

SPECTRAL_CLASS_ALIASES: dict[str, str] = {"R": "C", "N": "C"}
"""R and N are older names for carbon stars, which are now class C."""

NO_GOOD_MATCH_RMS = 0.15
"""A star's own spectrum is called "no good match" when its closest reference
spectrum differs from it by more than this fraction (the same limit the
Astronomy Manager uses)."""

WELL_SEPARATED_POINTS = 2.0
"""The runner-up reference spectrum must be at least this many percentage
points worse than the best for the match to count as well separated."""

DIFFERS_FROM_CATALOG_SUBTYPES = 8
"""A measured type more than this many subtypes from the catalog type (on
the O-to-M ladder, ten subtypes to a class) is called different."""

LADDER_ORDER = "OBAFGKM"
"""Spectral classes from hottest to coolest."""

TOP_CANDIDATES = 3
"""How many closest reference types are listed."""

MAXIMUM_ROWS_PER_LIST = 12
"""Most features or lines listed for one star."""


def spectral_class_letter(spectral_type: str) -> str:
    """Reduce a catalog spectral type to its class letter.

    Parameters
    ----------
    spectral_type : `str`
        A type such as ``"G2V"``.

    Returns
    -------
    letter : `str`
        The class letter ("R" and "N" become "C"), or an empty string when
        the type is empty, "Unknown", or not a class the browser shows.
    """
    trimmed = (spectral_type or "").strip()
    if not trimmed or trimmed.lower() == "unknown":
        return ""
    letter = trimmed[0].upper()
    if not letter.isalpha():
        return ""
    letter = SPECTRAL_CLASS_ALIASES.get(letter, letter)
    return letter if letter in SPECTRAL_CLASS_LABELS else ""


def ladder_position(spectral_type: str | None) -> int | None:
    """Place a spectral type on the O-to-M ladder, ten subtypes to a class.

    Parameters
    ----------
    spectral_type : `str`, optional
        A type such as ``"A3V"``.

    Returns
    -------
    position : `int` or `None`
        For example 23 for ``"A3V"``, or `None` for a type that is not on
        the ladder (such as a carbon star).
    """
    trimmed = (spectral_type or "").strip().upper()
    if not trimmed or trimmed[0] not in LADDER_ORDER:
        return None
    digits = ""
    for character in trimmed[1:]:
        if character.isdigit() or (character == "." and digits):
            digits += character
        else:
            break
    return LADDER_ORDER.index(trimmed[0]) * 10 + (round(float(digits)) if digits else 0)


def _field(item: Any, name: str) -> Any:
    """Read a field from a record that may be a dict or a model.

    Returns
    -------
    value : `Any`
        The field's value, or `None` if it is missing.
    """
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _plain(value: Any) -> Any:
    """Turn a model into a plain dict, or give back what was passed.

    Returns
    -------
    plain : `Any`
        A dict for a model, otherwise the value unchanged.
    """
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _round(value: Any, digits: int = 3) -> Any:
    """Round a number, leaving `None` and non-numbers alone.

    Returns
    -------
    value : `Any`
        The rounded number or the original.
    """
    return (
        round(float(value), digits)
        if isinstance(value, int | float) and not isinstance(value, bool)
        else value
    )


def _spectrum_summary(spectroscopy: Any, catalog_type: str | None) -> dict[str, Any]:
    """Summarize a star's extracted spectrum and what was found in it.

    Parameters
    ----------
    spectroscopy : `SpectroscopyResult`
        The stored spectrum record.
    catalog_type : `str`, optional
        The type the catalog gives the star, to say whether the two differ.

    Returns
    -------
    summary : `dict` [`str`, `Any`]
        The star's own type with its match quality, features, lines and
        quality notes.
    """
    wavelengths = list(spectroscopy.wavelengths_angstrom or [])
    own_type = spectroscopy.self_determined_spectral_type
    rms = spectroscopy.self_determined_spectral_type_rms
    candidates = [_plain(item) for item in (spectroscopy.self_determined_spectral_type_candidates or [])]
    ranked = sorted(candidates, key=lambda item: item.get("rms", 9.0))
    gap = (ranked[1]["rms"] - ranked[0]["rms"]) * 100.0 if len(ranked) > 1 else None
    own_position, catalog_position = ladder_position(own_type), ladder_position(catalog_type)
    differs = (
        abs(own_position - catalog_position) > DIFFERS_FROM_CATALOG_SUBTYPES
        if own_position is not None and catalog_position is not None
        else None
    )
    features = [
        {
            "feature": _field(item, "feature"),
            "kind": _field(item, "kind"),
            "verdict": _field(item, "verdict"),
            "depth_percent": _round(100.0 * (_field(item, "depth") or 0.0), 1),
            "p_value": _round(_field(item, "p_value"), 4),
        }
        for item in (spectroscopy.probable_spectral_features or [])
        if _field(item, "verdict") not in ("not_detected", "not_covered", "not_seen")
    ]
    lines = [
        {
            "line": _field(item, "line"),
            "verdict": _field(item, "verdict"),
            "significance": _round(_field(item, "significance"), 1),
        }
        for item in (spectroscopy.emission_lines or [])
        if _field(item, "verdict") in ("detected", "unclear")
    ]
    widths = [width for width in (spectroscopy.trail_width_px or []) if width]
    return {
        "points": len(wavelengths),
        "wavelength_range_angstrom": [round(min(wavelengths)), round(max(wavelengths))]
        if wavelengths
        else None,
        "requested_range_angstrom": spectroscopy.requested_wavelength_range_angstrom,
        "valid_fraction": _round(spectroscopy.valid_fraction),
        "own_spectral_type": own_type,
        "own_type_percent_off": _round(None if rms is None else rms * 100.0, 1),
        "no_good_match": None if rms is None else rms > NO_GOOD_MATCH_RMS,
        "confidence": _round(spectroscopy.self_determined_spectral_type_confidence),
        "note": spectroscopy.self_determined_spectral_type_note or None,
        "closest_reference_types": [
            {"type": item.get("spectral_type"), "percent_off": _round(item.get("rms", 0.0) * 100.0, 1)}
            for item in ranked[:TOP_CANDIDATES]
        ],
        "runner_up_gap_points": _round(gap, 1),
        "well_separated": None if gap is None else gap >= WELL_SEPARATED_POINTS,
        "differs_from_catalog": differs,
        "emission_line_source": spectroscopy.is_emission_line_source,
        "emission_lines": lines[:MAXIMUM_ROWS_PER_LIST],
        "absorption_features": features[:MAXIMUM_ROWS_PER_LIST],
        "dispersion_angle_degrees": _round(spectroscopy.dispersion_angle, 2),
        "median_trail_width_px": _round(sorted(widths)[len(widths) // 2], 2) if widths else None,
        "neighbor_wing_status": spectroscopy.neighbor_wing_status,
        "possible_neighbor_contamination": spectroscopy.possible_neighbor_contamination,
        "catalog_comparison": _plain(spectroscopy.catalog_comparison),
        "input_quality": _plain(spectroscopy.input_quality),
        "output_quality": _plain(spectroscopy.output_quality),
    }


def _photometry_summary(photometry: Any) -> dict[str, Any]:
    """Summarize a star's light curve and the repeating patterns found in it.

    Parameters
    ----------
    photometry : `PhotometryResult`
        The stored light-curve record.

    Returns
    -------
    summary : `dict` [`str`, `Any`]
        How many measurements, the time span, the scatter, and the cycle
        and repeating-dip verdicts with their false-alarm probabilities.
    """
    stamps = list(photometry.timestamps or [])
    cycle = _plain(photometry.periodogram) or {}
    dips = _plain(photometry.transit_candidate) or {}
    return {
        "points": len(stamps),
        "first": stamps[0].isoformat() if stamps else None,
        "last": stamps[-1].isoformat() if stamps else None,
        "saturated_points": sum(1 for flag in (photometry.is_saturated or []) if flag),
        "mean_flux": _round(photometry.mean_flux, 1),
        "scatter_percent": _round(
            None
            if photometry.coefficient_of_variation is None
            else photometry.coefficient_of_variation * 100.0,
            1,
        ),
        "smooth_cycle": {
            "verdict": cycle.get("verdict"),
            "period_days": _round(cycle.get("best_period_days"), 5),
            "false_alarm_probability": _round(cycle.get("false_alarm_probability"), 4),
            "cycles_observed": _round(cycle.get("cycles_observed"), 1),
            "note": cycle.get("note") or None,
        }
        if cycle
        else None,
        "repeating_dips": {
            "verdict": dips.get("verdict"),
            "period_days": _round(dips.get("period_days"), 5),
            "depth_mag": _round(dips.get("transit_depth_mag"), 3),
            "duration_hours": _round(dips.get("transit_duration_hours"), 2),
            "dips_seen": dips.get("transit_count"),
            "false_alarm_probability": _round(dips.get("false_alarm_probability"), 4),
            "note": dips.get("note") or None,
        }
        if dips
        else None,
        "input_quality": _plain(photometry.input_quality),
        "output_quality": _plain(photometry.output_quality),
    }


def summarize_star(star: Any) -> dict[str, Any]:
    """Describe one star and its stored analysis in a short record.

    Parameters
    ----------
    star : `StellarObject`
        The star, with whatever photometry and spectroscopy it holds.

    Returns
    -------
    summary : `dict` [`str`, `Any`]
        Identity, position, magnitude and catalog type; then ``spectrum``
        and ``photometry`` summaries (each `None` if the star has none).
        No arrays are included, so the record is about a kilobyte.
    """
    return {
        "id": star.id,
        "name": star.name,
        "ra": _round(star.right_ascension, 5),
        "dec": _round(star.declination, 5),
        "magnitude": _round(star.magnitude, 2),
        "b_minus_v": _round(star.b_minus_v, 2),
        "catalog_spectral_type": star.spectral_type,
        "catalog_identified": star.is_catalog_identified,
        "catalog_match_quality": _plain(star.catalog_match_quality),
        "target_ids": list(star.target_ids or []),
        "spectrum": _spectrum_summary(star.spectroscopy, star.spectral_type) if star.has_spectra else None,
        "photometry": _photometry_summary(star.photometry) if star.has_photometry else None,
    }
