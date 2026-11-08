"""Purpose: Turn measurements into INDI simulator properties.

Description: `fit_values` takes the candidates that
`simulator.fit.measurements` found and the facts about the simulated cameras
(`simulator.fit.world_setup`), and produces one `FittedValue` for each
property of section 5 of the simulator plan, plus the guide controller
settings for PHD2.

For every row the best candidate in the fallback order wins (decision D3):
the newest real record, a fit to the recorded history, the performance
envelope, then the simulator's own default. A row without any candidate falls
back one step: where the plan says "zero" (periodic error, polar error,
go-to error and Dec backlash) the property is written as zero, where it
says "simulator default" nothing is written and the INDI simulator keeps
its own value. Each value is clamped to its property's range
(`simulator.fit.ranges`), and the clamp is recorded. Conversions use the
drivers' own formulas, inverted by hand (`simulator.fit.driver_model`), so
there is no start-up loop of exposures.

Two things the INDI simulators cannot do are recorded, not applied: the
focus drift with temperature (no property couples focus to temperature) and
the download delay (the stand-in ssh has no delay option).
"""

import math
from dataclasses import dataclass, field
from typing import Any

from simulator import device_settings
from simulator.fit import driver_model
from simulator.fit.measurements import SOURCE_ORDER, Candidate, Measurements, SourceKind
from simulator.fit.ranges import GUIDE_CAMERA, MAIN_CAMERA, MOUNT, clamp_to_range, range_of
from simulator.fit.world_setup import (
    DEFAULT_LIMITING_MAGNITUDE,
    DEFAULT_SATURATION_MAGNITUDE,
    WorldSetup,
)

__all__ = [
    "FitResult",
    "FittedValue",
    "fit_values",
]

#: The smallest controller settings PHD2 profile ranges; copied from
#: `simulator.phd2_launch` at fit time so the two never disagree.
DEFAULT_SOURCE: SourceKind = "simulator default"
#: The polar error's "minutes of drift" element. Both drivers add a constant
#: offset ``drift_minutes * error_arcmin * cos(dec) / 3.81`` arcseconds in
#: declination (``ccd_simulator.cpp:692``): there is no time in the formula, so
#: this is not a rate. One minute of drift keeps the offset to the size the
#: real error drifts in a minute.
POLAR_DRIFT_MINUTES = 1.0
#: The sidereal guide rate unit of the telescope simulator.
GUIDE_RATE_UNIT_ARCSEC_PER_S = driver_model.SIDEREAL_GUIDE_RATE_ARCSEC_PER_S
#: The unit of one arcminute in arcseconds.
ARCSEC_PER_ARCMIN = 60.0
#: The telescope simulator's ``MOUNT_MODEL`` terms are in ARCMINUTES.
#: Measured live on 2026-11-08 (INDI 2.2.5): ``MM_IH`` = 1 moved the true
#: pointing (``EQUATORIAL_PE``) by exactly 0.001111 h of right ascension, one
#: arcminute of hour angle, and all zeros gave exactly zero offset. The
#: older source read from disk (degrees, -5 to 5) does not apply.
#: Arcseconds in one arcminute: see `ARCSEC_PER_ARCMIN`.
#:
#: Sign of each term: the term in arcminutes is the sign times the median
#: first-attempt offset (solved minus commanded), as an angle on each axis, in
#: arcminutes.
#:
#: * ``MM_IH`` is -1, MEASURED live on 2026-11-08: with ``MM_IH`` = +1 the true
#:   right ascension was lower than the reported one (13.515409 h against
#:   13.516520 h at M 51, declination unchanged), so solved minus commanded in
#:   right ascension is minus ``MM_IH``.
#: * ``MM_ID`` is +1, NOT MEASURED. Best derivation: the measured ``MM_IH``
#:   behaves as true = reported + term (the opposite of the older source,
#:   where the instrument is the observed position minus the term); the same
#:   change for declination makes solved minus commanded in declination equal
#:   to plus ``MM_ID``. The live check decides; a flip is one number here.
MOUNT_MODEL_SIGNS = {"MM_IH": -1.0, "MM_ID": 1.0}
#: The terms that are always written as zero: the app's pointing model is not
#: used (its hour angle is computed from right ascension alone, so the polar
#: and flexure terms are fitted against a made-up angle and the index and cone
#: terms cancel each other), so only a constant offset is simulated.
MOUNT_MODEL_ZERO_TERMS = ("MM_CH", "MM_NP", "MM_MA", "MM_ME")
#: ``random() % (2 * scale * R) - scale * R`` is uniform on -R to R, whose
#: standard deviation is R / sqrt(3) (``guide_simulator.cpp:580-589``).
UNIFORM_RMS_FACTOR = math.sqrt(3.0)
#: Display decimals of each element's INDI number format, so that a
#: read-back is compared at the precision the server echoes it.
DISPLAY_DECIMALS = {
    "SIM_NOISE": 0,
    "SIM_SKYGLOW": 1,
    "SIM_MAXVAL": 0,
    "SIM_POLAR": 1,
    "SIM_POLARDRIFT": 1,
    "SIM_PEPERIOD": 1,
    "SIM_PEMAX": 1,
    "SIM_BIAS": 0,
    "SIM_RA_DRIFT": 3,
    "SIM_DEC_DRIFT": 3,
    "SIM_RA_RAND": 3,
    "SIM_DEC_RAND": 3,
    "SIM_PE_PERIOD": 1,
    "SIM_PE_MAX": 1,
    "OFFSET": 0,
    "DEC_BACKLASH": 0,
    "MM_IH": 3,
    "MM_ID": 3,
    "MM_CH": 3,
    "MM_NP": 3,
    "MM_MA": 3,
    "MM_ME": 3,
}


@dataclass
class FittedValue:
    """One value of the fit, with where it came from.

    Attributes
    ----------
    row : `str`
        The row of section 5 of the plan, such as ``"sky_glow"``.
    label : `str`
        A short name for people.
    device, property_name, element : `str`
        The INDI property. ``device`` is ``PHD2 profile`` for a controller
        setting, which is not an INDI property.
    unit : `str`
        The unit of the written value.
    value : `float` or `None`
        The value written, or `None` when nothing is written.
    applied : `bool`
        Whether the value is written to the simulator.
    source_kind : `SourceKind`
        The step of the fallback order that supplied it.
    call, parameters, nights, note, date
        The public call and parameters, the nights, one sentence and the
        date of the measurement. Empty for a default.
    conversion : `str`
        How the measurement became this value.
    requested : `float` or `None`
        The value before clamping.
    clamp_note : `str` or `None`
        What was clamped, when anything was.
    display_decimals : `int` or `None`
        The decimals the server echoes the value with.
    """

    row: str
    label: str
    device: str
    property_name: str
    element: str
    unit: str
    value: float | None
    applied: bool
    source_kind: SourceKind
    call: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    nights: list[str] = field(default_factory=list)
    note: str = ""
    date: str | None = None
    conversion: str = ""
    requested: float | None = None
    clamp_note: str | None = None
    display_decimals: int | None = None


@dataclass
class FitResult:
    """Everything the fit decided.

    Attributes
    ----------
    values : `list` [`FittedValue`]
        Every value, applied or not.
    controller_settings : `dict` [`str`, `float`] or `None`
        The PHD2 guide controller settings (see
        `simulator.phd2_launch.CONTROLLER_SETTING_KEYS`), or `None` for
        PHD2's own defaults.
    guide_rate_fraction : `dict` [`str`, `float`] or `None`
        The mount's ``GUIDE_RATE_WE`` and ``GUIDE_RATE_NS`` as written, so
        the simulated guider calibration agrees with them.
    notes : `list` [`str`]
        Statuses from the measurements and the conversions.
    """

    values: list[FittedValue] = field(default_factory=list)
    controller_settings: dict[str, float] | None = None
    guide_rate_fraction: dict[str, float] | None = None
    notes: list[str] = field(default_factory=list)

    def value_of(self, device: str, property_name: str, element: str) -> float | None:
        """Give the value written for a property.

        Parameters
        ----------
        device, property_name, element : `str`
            The INDI names.

        Returns
        -------
        value : `float` or `None`
            The applied value, or `None` when none is written.
        """
        for entry in self.values:
            if (entry.device, entry.property_name, entry.element) == (device, property_name, element):
                return entry.value if entry.applied else None
        return None


def _best(measurements: Measurements, row: str) -> Candidate | None:
    """Pick the best candidate of a row in the fallback order.

    Returns
    -------
    candidate : `Candidate` or `None`
        The candidate whose source ranks first, or `None` when there is none.
    """
    candidates = measurements.candidates.get(row, [])
    return min(candidates, key=lambda candidate: SOURCE_ORDER.index(candidate.kind), default=None)


def _make(
    row: str,
    label: str,
    device: str,
    property_name: str,
    element: str,
    requested: float,
    candidate: Candidate | None,
    conversion: str,
    source_kind: SourceKind | None = None,
    note: str = "",
) -> FittedValue:
    """Build an applied value, clamped to its property's range.

    Parameters
    ----------
    row, label, device, property_name, element : `str`
        As in `FittedValue`.
    requested : `float`
        The converted value before clamping.
    candidate : `Candidate` or `None`
        Where the measurement came from; `None` for a value that is not
        a measurement (a zero fallback, a camera profile value).
    conversion : `str`
        How the value was converted.
    source_kind : `SourceKind`, optional
        Overrides the candidate's kind.
    note : `str`, optional
        Overrides the candidate's note.

    Returns
    -------
    value : `FittedValue`
        The value, clamped.
    """
    clamped, clamp_note = clamp_to_range(device, property_name, element, requested)
    row_range = range_of(device, property_name, element)
    return FittedValue(
        row=row,
        label=label,
        device=device,
        property_name=property_name,
        element=element,
        unit=row_range.unit,
        value=clamped,
        applied=True,
        source_kind=source_kind or (candidate.kind if candidate else DEFAULT_SOURCE),
        call=candidate.call if candidate else "",
        parameters=dict(candidate.parameters) if candidate else {},
        nights=list(candidate.nights) if candidate else [],
        note=note or (candidate.note if candidate else ""),
        date=candidate.date if candidate else None,
        conversion=conversion,
        requested=float(requested),
        clamp_note=clamp_note,
        display_decimals=DISPLAY_DECIMALS.get(element),
    )


def _not_applied(
    row: str, label: str, device: str, property_name: str, element: str, note: str
) -> FittedValue:
    """Build an entry for a value that is not written.

    Returns
    -------
    value : `FittedValue`
        An entry with ``applied`` false, from the simulator default.
    """
    return FittedValue(
        row=row,
        label=label,
        device=device,
        property_name=property_name,
        element=element,
        unit="",
        value=None,
        applied=False,
        source_kind=DEFAULT_SOURCE,
        note=note,
    )


def _guide_rate(measurements: Measurements, result: FitResult) -> None:
    """Fit the mount's guide rate.

    Parameters
    ----------
    measurements : `Measurements`
        The candidates.
    result : `FitResult`
        Receives the values.
    """
    candidate = _best(measurements, "guide_rate")
    if candidate is None:
        for element in ("GUIDE_RATE_WE", "GUIDE_RATE_NS"):
            result.values.append(
                _not_applied(
                    "guide_rate",
                    "Guide rate",
                    MOUNT,
                    "GUIDE_RATE",
                    element,
                    "No guide speed is recorded; the mount keeps 0.5.",
                )
            )
        return
    fraction = candidate.values["dec_rate_arcsec_per_s"] / GUIDE_RATE_UNIT_ARCSEC_PER_S
    conversion = (
        f"{candidate.values['dec_rate_arcsec_per_s']:.4g} arcsec/s divided by "
        f"{GUIDE_RATE_UNIT_ARCSEC_PER_S:g} arcsec/s, the speed of one unit of GUIDE_RATE "
        "(scopesim_helper.cpp:127); the Dec speed is used on both "
        "axes because the right ascension speed in a log includes cos(dec)"
    )
    written: dict[str, float] = {}
    for element in ("GUIDE_RATE_WE", "GUIDE_RATE_NS"):
        entry = _make(
            "guide_rate", "Guide rate", MOUNT, "GUIDE_RATE", element, fraction, candidate, conversion
        )
        result.values.append(entry)
        written[element] = float(entry.value)
    result.guide_rate_fraction = written


def _periodic_error(measurements: Measurements, setup: WorldSetup, result: FitResult) -> None:
    """Fit the periodic error of both cameras' simulators.

    Parameters
    ----------
    measurements : `Measurements`
        The candidates.
    setup : `WorldSetup`
        Supplies the declination.
    result : `FitResult`
        Receives the values.

    Notes
    -----
    The drivers add the amount to right ascension without a ``cos(dec)``
    factor, so the amount is divided by ``cos(dec)``. The main camera
    simulator's period is limited to 60 seconds. When a period is clamped, the
    amplitude is scaled by the same fraction so the peak speed of the error
    (amplitude over period) is kept, because that is what guiding must
    follow; the note says so.
    """
    candidate = _best(measurements, "periodic_error")
    targets = (
        (MAIN_CAMERA, "SIMULATOR_SETTINGS", "SIM_PEPERIOD", "SIM_PEMAX"),
        (GUIDE_CAMERA, "SIMULATOR_SETTINGS", "SIM_PE_PERIOD", "SIM_PE_MAX"),
    )
    for device, property_name, period_element, amplitude_element in targets:
        if candidate is None:
            result.values.append(
                _make(
                    "periodic_error",
                    "Periodic error amplitude",
                    device,
                    property_name,
                    amplitude_element,
                    0.0,
                    None,
                    "no measurement: zero",
                    note="No significant periodic error was measured; it is set to zero.",
                )
            )
            continue
        amplitude = driver_model.on_sky_to_ra_coordinate(
            candidate.values["amplitude_arcsec"], setup.declination_degrees
        )
        period = candidate.values["period_seconds"]
        maximum_period = range_of(device, property_name, period_element).maximum
        conversion = "amplitude (zero to peak, on the sky) divided by cos(dec); period as measured"
        if period > maximum_period:
            amplitude *= maximum_period / period
            conversion += (
                f"; the period {period:.0f} s is above the {maximum_period:g} s this simulator accepts, "
                "so the period is clamped and the amplitude scaled by the same fraction "
                "to keep the peak speed"
            )
        result.values.append(
            _make(
                "periodic_error",
                "Periodic error period",
                device,
                property_name,
                period_element,
                period,
                candidate,
                conversion,
            )
        )
        result.values.append(
            _make(
                "periodic_error",
                "Periodic error amplitude",
                device,
                property_name,
                amplitude_element,
                amplitude,
                candidate,
                conversion,
            )
        )


def _guiding_disturbance(measurements: Measurements, setup: WorldSetup, result: FitResult) -> None:
    """Fit the guide simulator's random and slow disturbance.

    Parameters
    ----------
    measurements : `Measurements`
        The candidates.
    setup : `WorldSetup`
        Supplies the declination.
    result : `FitResult`
        Receives the values.
    """
    candidate = _best(measurements, "guiding_disturbance")
    names = ("SIM_RA_RAND", "SIM_DEC_RAND", "SIM_RA_DRIFT", "SIM_DEC_DRIFT")
    if candidate is None:
        result.values.extend(
            _not_applied(
                "guiding_disturbance",
                "Guiding disturbance",
                GUIDE_CAMERA,
                "SIMULATOR_SETTINGS",
                name,
                "No measurement; the guide simulator keeps its own default.",
            )
            for name in names
        )
        return
    values = candidate.values
    random_conversion = (
        "frame-to-frame random RMS times sqrt(3) (the driver adds a new uniform number from -R to R "
        "each frame); "
        "right ascension divided by cos(dec)"
    )
    drift_conversion = "drift in arcsec/min divided by 60; right ascension divided by cos(dec)"
    plan = (
        ("SIM_RA_RAND", "ra_random_rms_arcsec", UNIFORM_RMS_FACTOR, True, random_conversion),
        ("SIM_DEC_RAND", "dec_random_rms_arcsec", UNIFORM_RMS_FACTOR, False, random_conversion),
        ("SIM_RA_DRIFT", "ra_drift_arcsec_per_min", 1.0 / 60.0, True, drift_conversion),
        ("SIM_DEC_DRIFT", "dec_drift_arcsec_per_min", 1.0 / 60.0, False, drift_conversion),
    )
    for element, key, factor, is_right_ascension, conversion in plan:
        amount = values[key] * factor
        if is_right_ascension:
            amount = driver_model.on_sky_to_ra_coordinate(amount, setup.declination_degrees)
        result.values.append(
            _make(
                "guiding_disturbance",
                "Guiding disturbance",
                GUIDE_CAMERA,
                "SIMULATOR_SETTINGS",
                element,
                amount,
                candidate,
                conversion,
            )
        )


def _polar_error(measurements: Measurements, result: FitResult) -> None:
    """Fit the polar error of both cameras' simulators.

    Parameters
    ----------
    measurements : `Measurements`
        The candidates.
    result : `FitResult`
        Receives the values.
    """
    candidate = _best(measurements, "polar_error")
    for device in (MAIN_CAMERA, GUIDE_CAMERA):
        if candidate is None:
            for element in ("SIM_POLAR", "SIM_POLARDRIFT"):
                result.values.append(
                    _make(
                        "polar_error",
                        "Polar error",
                        device,
                        "SIMULATOR_SETTINGS",
                        element,
                        0.0,
                        None,
                        "no measurement: zero",
                        note="No polar alignment record; set to zero.",
                    )
                )
            continue
        error = candidate.values["total_error_arcsec"] / ARCSEC_PER_ARCMIN
        result.values.append(
            _make(
                "polar_error",
                "Polar error",
                device,
                "SIMULATOR_SETTINGS",
                "SIM_POLAR",
                error,
                candidate,
                "total error in arcsec divided by 60 (the property is in arcminutes)",
            )
        )
        result.values.append(
            _make(
                "polar_error",
                "Polar error drift",
                device,
                "SIMULATOR_SETTINGS",
                "SIM_POLARDRIFT",
                POLAR_DRIFT_MINUTES,
                candidate,
                "a constant: the driver's declination offset is drift minutes times error "
                "times cos(dec) / 3.81, "
                "with no time in it, so one minute is used",
                note=f"{candidate.note} The simulator turns it into a fixed declination offset, not a drift.",
            )
        )


def _mount_model(measurements: Measurements, result: FitResult) -> None:
    """Fit the telescope simulator's constant go-to offset (``MOUNT_MODEL``).

    The median first-attempt offsets (`simulator.fit.measurements`) become
    ``MM_IH`` (right ascension) and ``MM_ID`` (declination) in arcminutes,
    signed by `MOUNT_MODEL_SIGNS`. The mount then points a little away from
    the position it reports, so the app's alignment has something to
    correct. The other four terms are always zero (`MOUNT_MODEL_ZERO_TERMS`).
    With no usable measurement all six are zero.

    Parameters
    ----------
    measurements : `Measurements`
        The candidates.
    result : `FitResult`
        Receives the values.
    """
    candidate = _best(measurements, "mount_model")
    requested = {"MM_IH": 0.0, "MM_ID": 0.0}
    if candidate is not None:
        requested["MM_IH"] = (
            MOUNT_MODEL_SIGNS["MM_IH"] * candidate.values["ra_angle_median_arcsec"] / ARCSEC_PER_ARCMIN
        )
        requested["MM_ID"] = (
            MOUNT_MODEL_SIGNS["MM_ID"] * candidate.values["dec_median_arcsec"] / ARCSEC_PER_ARCMIN
        )
    for element, value in requested.items():
        result.values.append(
            _make(
                "mount_model",
                "Go-to offset",
                MOUNT,
                "MOUNT_MODEL",
                element,
                value,
                candidate,
                "no measurement: zero"
                if candidate is None
                else f"median first-attempt offset in arcsec times {MOUNT_MODEL_SIGNS[element]:+g}, "
                "divided by 60 (the property is in arcminutes)",
                note="Too few first attempts for a go-to offset; set to zero." if candidate is None else "",
            )
        )
    for element in MOUNT_MODEL_ZERO_TERMS:
        result.values.append(
            _make(
                "mount_model",
                "Go-to offset",
                MOUNT,
                "MOUNT_MODEL",
                element,
                0.0,
                None,
                "always zero: only a constant offset is simulated",
                note="Not simulated: the app's pointing model is not used (see the plan).",
            )
        )


def _backlash(measurements: Measurements, result: FitResult) -> None:
    """Fit the telescope simulator's Dec backlash.

    Parameters
    ----------
    measurements : `Measurements`
        The candidates.
    result : `FitResult`
        Receives the value.
    """
    candidate = _best(measurements, "dec_backlash")
    if candidate is None:
        result.values.append(
            _make(
                "dec_backlash",
                "Dec backlash",
                MOUNT,
                "DEC_BACKLASH",
                "DEC_BACKLASH",
                0.0,
                None,
                "no measurement: zero",
                note="No backlash estimate; set to zero.",
            )
        )
        return
    result.values.append(
        _make(
            "dec_backlash",
            "Dec backlash",
            MOUNT,
            "DEC_BACKLASH",
            "DEC_BACKLASH",
            candidate.values["backlash_ms"],
            candidate,
            "milliseconds as estimated",
        )
    )


def _controller(measurements: Measurements, result: FitResult) -> None:
    """Fit the PHD2 guide controller settings.

    Parameters
    ----------
    measurements : `Measurements`
        The candidates.
    result : `FitResult`
        Receives the settings and a record of them.
    """
    from simulator import phd2_launch

    candidate = _best(measurements, "guide_controller")
    if candidate is None:
        return
    settings: dict[str, float] = {}
    notes: list[str] = []
    for key, value in candidate.values.items():
        if key not in phd2_launch.CONTROLLER_SETTING_RANGES:
            continue
        low, high = phd2_launch.CONTROLLER_SETTING_RANGES[key]
        clamped = min(max(float(value), low), high)
        if not math.isclose(clamped, float(value)):
            notes.append(f"{key} {value:g} is outside {low:g} to {high:g}; written as {clamped:g}")
        settings[key] = clamped
        result.values.append(
            FittedValue(
                row="guide_controller",
                label="Guide controller",
                device="PHD2 profile",
                property_name="GuideAlgorithm",
                element=key,
                unit="px" if key == "minimum_move" else ("s" if key == "exposure_seconds" else "fraction"),
                value=clamped,
                applied=True,
                source_kind=candidate.kind,
                call=candidate.call,
                parameters=dict(candidate.parameters),
                nights=list(candidate.nights),
                note=candidate.note,
                date=candidate.date,
                conversion="as stated in the log header",
                requested=float(value),
                clamp_note="; ".join(notes) if not math.isclose(clamped, float(value)) else None,
            )
        )
    result.controller_settings = settings or None


def _seeing(measurements: Measurements, setup: WorldSetup, result: FitResult) -> None:
    """Fit the seeing of both cameras' simulators.

    Parameters
    ----------
    measurements : `Measurements`
        The candidates.
    setup : `WorldSetup`
        Supplies the guide camera's pixel scale.
    result : `FitResult`
        Receives the values.
    """
    candidate = _best(measurements, "seeing")
    floor = device_settings.guide_star_seeing_floor(setup.guide_pixel_scale_arcsec)
    if candidate is None:
        result.values.append(
            _not_applied(
                "seeing",
                "Seeing",
                MAIN_CAMERA,
                "SIM_FOCUSING",
                "SIM_SEEING",
                "No star width is measured; the simulator keeps its default.",
            )
        )
        result.values.append(
            _not_applied(
                "seeing",
                "Seeing",
                GUIDE_CAMERA,
                "SIMULATOR_SETTINGS",
                "SIM_SEEING",
                "No star width is measured; the guide star keeps the width the geometry step sets "
                "(at least "
                f"{device_settings.GUIDE_MINIMUM_STAR_FWHM_PIXELS:g} pixels, so PHD2 can find it).",
            )
        )
        return
    fwhm = candidate.values["fwhm_arcsec"]
    guide_seeing = driver_model.guide_fwhm_to_seeing(fwhm)
    guide_conversion = (
        "the guide driver draws exp(-1.4 d^2 / seeing^2), a Gaussian of full width "
        "1.41 times SIM_SEEING; the guide "
        "scope's own star width is not recorded, so the main camera's width is used"
    )
    guide_note = f"{candidate.note} The guide camera's own width is not recorded; this is the main camera's."
    if floor is not None and guide_seeing < floor:
        guide_seeing = floor
        guide_conversion += (
            f"; raised to a star {device_settings.GUIDE_MINIMUM_STAR_FWHM_PIXELS:g} pixels wide, because "
            "the sky width is under one guide pixel and PHD2 rejects such a star as a hot pixel"
        )
        guide_note += " Raised to the narrowest star PHD2 can use."
    result.values.append(
        _make(
            "seeing",
            "Seeing",
            MAIN_CAMERA,
            "SIM_FOCUSING",
            "SIM_SEEING",
            fwhm,
            candidate,
            "the star width in arcsec as the full width at half maximum "
            "(the driver draws a Gaussian of that width)",
        )
    )
    result.values.append(
        _make(
            "seeing",
            "Seeing",
            GUIDE_CAMERA,
            "SIMULATOR_SETTINGS",
            "SIM_SEEING",
            guide_seeing,
            candidate,
            guide_conversion,
            note=guide_note,
        )
    )


def _camera_noise(
    label: str, device: str, camera: Any, maximum_element: str, result: FitResult, bias_element: str | None
) -> int | None:
    """Fit the noise, bias and maximum value of one camera's simulator.

    Parameters
    ----------
    label : `str`
        A name for people.
    device : `str`
        The simulator device.
    camera : `CameraSetup`
        The camera's facts from the profile.
    maximum_element : `str`
        ``SIM_MAXVAL``.
    result : `FitResult`
        Receives the values.
    bias_element : `str` or `None`
        ``SIM_BIAS`` for the guide camera, `None` for the main camera (whose
        pedestal is ``CCD_OFFSET``).

    Returns
    -------
    noise_limit : `int` or `None`
        The ``SIM_NOISE`` written, or `None` when the profile has no read
        noise.
    """
    note = "Read noise, bias level and clip ceiling of the camera profile."
    profile_candidate = Candidate(
        "newest real record", {}, "camera profile in the settings file", {}, [], note, None
    )
    noise_limit = None
    if camera.read_noise_adu:
        noise_limit = driver_model.noise_limit_for_rms(camera.read_noise_adu)
        result.values.append(
            _make(
                "noise",
                f"{label} noise",
                device,
                "SIMULATOR_SETTINGS",
                "SIM_NOISE",
                noise_limit,
                profile_candidate,
                "the driver adds a whole number from 0 to N-1; "
                f"N = round(sqrt(12 * {camera.read_noise_adu:.3g}^2 + 1)) "
                f"gives an RMS of {driver_model.noise_rms(noise_limit):.3g} ADU",
            )
        )
    if camera.clip_ceiling_adu:
        result.values.append(
            _make(
                "noise",
                f"{label} maximum value",
                device,
                "SIMULATOR_SETTINGS",
                maximum_element,
                camera.clip_ceiling_adu,
                profile_candidate,
                "the real camera's clip ceiling",
            )
        )
    if camera.bias_level_adu is not None and noise_limit is not None:
        pedestal = camera.bias_level_adu - driver_model.noise_mean(noise_limit)
        bias_device_element = (
            (device, "SIMULATOR_SETTINGS", bias_element) if bias_element else (device, "CCD_OFFSET", "OFFSET")
        )
        result.values.append(
            _make(
                "noise",
                f"{label} bias",
                *bias_device_element,
                pedestal,
                profile_candidate,
                "the real bias level minus the mean of the noise, (N-1)/2, "
                "so frames sit at the real bias level",
            )
        )
    return noise_limit


def _sky_glow(
    measurements: Measurements, setup: WorldSetup, maximum_adu: float | None, result: FitResult
) -> None:
    """Fit the main camera's sky glow to the predicted sky rate.

    Parameters
    ----------
    measurements : `Measurements`
        The candidates.
    setup : `WorldSetup`
        Supplies the frame size and the gain.
    maximum_adu : `float` or `None`
        The ``SIM_MAXVAL`` the simulator will have.
    result : `FitResult`
        Receives the values.
    """
    candidate = _best(measurements, "sky_glow")
    camera = setup.main
    if candidate is None or camera.width_px is None or camera.height_px is None:
        result.values.append(
            _not_applied(
                "sky_glow",
                "Sky glow",
                MAIN_CAMERA,
                "SIMULATOR_SETTINGS",
                "SIM_SKYGLOW",
                "No sky rate prediction or no frame size; the simulator keeps its default.",
            )
        )
    else:
        maximum = maximum_adu or range_of(MAIN_CAMERA, "SIMULATOR_SETTINGS", "SIM_MAXVAL").maximum
        rate = candidate.values["sky_rate_adu_per_s_per_px"]
        glow = driver_model.glow_for_main_camera(
            rate,
            camera.gain,
            camera.width_px,
            camera.height_px,
            maximum,
            DEFAULT_LIMITING_MAGNITUDE,
            DEFAULT_SATURATION_MAGNITUDE,
        )
        result.values.append(
            _make(
                "sky_glow",
                "Sky glow",
                MAIN_CAMERA,
                "SIMULATOR_SETTINGS",
                "SIM_SKYGLOW",
                glow,
                candidate,
                f"solved from flux(1.3 * glow) * (1 + sqrt({camera.gain:g})) * median vignetting "
                f"= {rate:.4g} ADU/s/px "
                f"with SIM_MAXVAL {maximum:g}, limiting magnitude {DEFAULT_LIMITING_MAGNITUDE:g}, "
                f"saturation magnitude {DEFAULT_SATURATION_MAGNITUDE:g}",
            )
        )
    result.values.append(
        _not_applied(
            "sky_glow",
            "Sky glow",
            GUIDE_CAMERA,
            "SIMULATOR_SETTINGS",
            "SIM_SKYGLOW",
            "The library has no sky measurement for the guide camera; the main camera's rate does not "
            "transfer (different aperture, pixel size, filter and gain). The simulator keeps its default.",
        )
    )


def fit_values(measurements: Measurements, setup: WorldSetup) -> FitResult:
    """Turn measurements into the simulator's settings.

    Parameters
    ----------
    measurements : `Measurements`
        What `simulator.fit.measurements.collect_measurements` found.
    setup : `WorldSetup`
        The simulated cameras and the target's declination.

    Returns
    -------
    result : `FitResult`
        The values in a fixed order, each with its source, clamping and
        conversion, and the controller settings for PHD2.
    """
    result = FitResult(notes=list(measurements.notes))
    _guide_rate(measurements, result)
    _periodic_error(measurements, setup, result)
    _guiding_disturbance(measurements, setup, result)
    _polar_error(measurements, result)
    _mount_model(measurements, result)
    _backlash(measurements, result)
    _controller(measurements, result)
    _seeing(measurements, setup, result)
    _camera_noise("Main camera", MAIN_CAMERA, setup.main, "SIM_MAXVAL", result, None)
    _camera_noise("Guide camera", GUIDE_CAMERA, setup.guide, "SIM_MAXVAL", result, "SIM_BIAS")
    maximum = result.value_of(MAIN_CAMERA, "SIMULATOR_SETTINGS", "SIM_MAXVAL")
    _sky_glow(measurements, setup, maximum, result)
    focus = _best(measurements, "focus_drift")
    result.values.append(
        FittedValue(
            row="focus_drift",
            label="Focus drift with temperature",
            device=MAIN_CAMERA,
            property_name="SIM_FOCUSING",
            element="SIM_FOCUS_POSITION",
            unit="steps",
            value=None,
            applied=False,
            source_kind=focus.kind if focus else DEFAULT_SOURCE,
            call=focus.call if focus else "",
            nights=list(focus.nights) if focus else [],
            note=(focus.note + " " if focus else "")
            + "Not settable: the camera simulator's best focus (SIM_FOCUS_POSITION) is a fixed number "
            "(ccd_simulator.cpp:1330) and the focuser simulator's FOCUS_TEMPERATURE is only a reading "
            "(focus_simulator.cpp:90-91, 208-213); nothing couples focus to temperature.",
            date=focus.date if focus else None,
        )
    )
    result.values.append(
        FittedValue(
            row="download_time",
            label="Download time",
            device="stand-in telescope computer",
            property_name="transfer delay",
            element="delay",
            unit="s",
            value=None,
            applied=False,
            source_kind=DEFAULT_SOURCE,
            note="Not applied: the stand-in ssh (simulator/standin_ssh.sh) has no delay option.",
        )
    )
    return result
