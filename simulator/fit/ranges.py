"""Purpose: The valid range and unit of each INDI property the fit writes.

Description: The INDI simulator drivers refuse (or misread) a value outside
the range their property declares, so the fit clamps each value before it is
written, and says so in the results file. The table below lists, for each
property the fit writes, the device, the property and element names, the unit,
the range and where in the INDI 2.2.5 driver source the range is declared
(``drivers/ccd/ccd_simulator.cpp``, ``guide_simulator.cpp``,
``drivers/telescope/telescope_simulator.cpp``).

The two camera simulators name the same quantity differently. The main camera
simulator's periodic error elements are ``SIM_PEPERIOD`` and ``SIM_PEMAX``
(range 0 to 60 s and 0 to 6000 arcsec); the guide camera simulator's are
``SIM_PE_PERIOD`` and ``SIM_PE_MAX`` (0 to 1000 s and 0 to 100 arcsec).
Seeing lives in a property of its own on the main camera simulator
(``SIM_FOCUSING``, element ``SIM_SEEING``) and in ``SIMULATOR_SETTINGS`` on
the guide camera simulator.
"""

import math
from dataclasses import dataclass

__all__ = [
    "CAMERA_PROPERTY",
    "GUIDE_PROPERTY",
    "PROPERTY_RANGES",
    "PropertyRange",
    "clamp_to_range",
    "range_of",
]

MAIN_CAMERA = "CCD Simulator"
GUIDE_CAMERA = "Guide Simulator"
MOUNT = "Telescope Simulator"
#: The property that holds most of a camera simulator's settings.
CAMERA_PROPERTY = "SIMULATOR_SETTINGS"
GUIDE_PROPERTY = "SIMULATOR_SETTINGS"


@dataclass(frozen=True)
class PropertyRange:
    """The range one simulator property accepts.

    Attributes
    ----------
    device, property_name, element : `str`
        The INDI names.
    unit : `str`
        The unit the driver reads the value in.
    minimum, maximum : `float`
        The inclusive range the driver declares.
    source : `str`
        Where the range is declared: file and line of the INDI 2.2.5 source.
    """

    device: str
    property_name: str
    element: str
    unit: str
    minimum: float
    maximum: float
    source: str

    @property
    def key(self) -> tuple[str, str, str]:
        """Give the property's identity.

        Returns
        -------
        key : `tuple` [`str`, `str`, `str`]
            Device, property and element.
        """
        return (self.device, self.property_name, self.element)


def _range(
    device: str, prop: str, element: str, unit: str, low: float, high: float, source: str
) -> PropertyRange:
    """Build one table row.

    Returns
    -------
    row : `PropertyRange`
        The row.
    """
    return PropertyRange(device, prop, element, unit, low, high, source)


_CCD = "drivers/ccd/ccd_simulator.cpp"
_GUIDE = "drivers/ccd/guide_simulator.cpp"
_SCOPE = "drivers/telescope/telescope_simulator.cpp"

#: Every property the fit may write, with the range its driver declares.
PROPERTY_RANGES: dict[tuple[str, str, str], PropertyRange] = {
    row.key: row
    for row in (
        _range(
            MAIN_CAMERA, "SIMULATOR_SETTINGS", "SIM_NOISE", "ADU (0 to N-1, uniform)", 0, 6000, f"{_CCD}:135"
        ),
        _range(MAIN_CAMERA, "SIMULATOR_SETTINGS", "SIM_SKYGLOW", "magnitude", 0, 6000, f"{_CCD}:136"),
        _range(MAIN_CAMERA, "SIMULATOR_SETTINGS", "SIM_MAXVAL", "ADU", 255, 65000, f"{_CCD}:132"),
        _range(MAIN_CAMERA, "SIMULATOR_SETTINGS", "SIM_POLAR", "arcmin", -600, 600, f"{_CCD}:138"),
        _range(MAIN_CAMERA, "SIMULATOR_SETTINGS", "SIM_POLARDRIFT", "minutes of drift", 0, 60, f"{_CCD}:139"),
        _range(MAIN_CAMERA, "SIMULATOR_SETTINGS", "SIM_PEPERIOD", "s", 0, 60, f"{_CCD}:140"),
        _range(
            MAIN_CAMERA, "SIMULATOR_SETTINGS", "SIM_PEMAX", "arcsec (zero to peak)", 0, 6000, f"{_CCD}:141"
        ),
        _range(MAIN_CAMERA, "SIM_FOCUSING", "SIM_SEEING", "arcsec (FWHM)", 0, 60, f"{_CCD}:160"),
        _range(MAIN_CAMERA, "CCD_OFFSET", "OFFSET", "ADU (pedestal)", 0, 6000, f"{_CCD}:191"),
        _range(
            GUIDE_CAMERA,
            "SIMULATOR_SETTINGS",
            "SIM_NOISE",
            "ADU (0 to N-1, uniform)",
            0,
            6000,
            f"{_GUIDE}:181",
        ),
        _range(GUIDE_CAMERA, "SIMULATOR_SETTINGS", "SIM_SKYGLOW", "magnitude", 0, 6000, f"{_GUIDE}:182"),
        _range(GUIDE_CAMERA, "SIMULATOR_SETTINGS", "SIM_MAXVAL", "ADU", 0, 65000, f"{_GUIDE}:177"),
        _range(GUIDE_CAMERA, "SIMULATOR_SETTINGS", "SIM_BIAS", "ADU", 0, 6000, f"{_GUIDE}:178"),
        _range(GUIDE_CAMERA, "SIMULATOR_SETTINGS", "SIM_POLAR", "arcmin", -600, 600, f"{_GUIDE}:184"),
        _range(
            GUIDE_CAMERA, "SIMULATOR_SETTINGS", "SIM_POLARDRIFT", "minutes of drift", 0, 6000, f"{_GUIDE}:186"
        ),
        _range(GUIDE_CAMERA, "SIMULATOR_SETTINGS", "SIM_SEEING", "arcsec (see note)", 0, 20, f"{_GUIDE}:192"),
        _range(GUIDE_CAMERA, "SIMULATOR_SETTINGS", "SIM_RA_DRIFT", "arcsec/s", -2, 2, f"{_GUIDE}:193"),
        _range(GUIDE_CAMERA, "SIMULATOR_SETTINGS", "SIM_DEC_DRIFT", "arcsec/s", -2, 2, f"{_GUIDE}:194"),
        _range(
            GUIDE_CAMERA, "SIMULATOR_SETTINGS", "SIM_RA_RAND", "arcsec (uniform +-)", -2, 2, f"{_GUIDE}:195"
        ),
        _range(
            GUIDE_CAMERA, "SIMULATOR_SETTINGS", "SIM_DEC_RAND", "arcsec (uniform +-)", -2, 2, f"{_GUIDE}:196"
        ),
        _range(GUIDE_CAMERA, "SIMULATOR_SETTINGS", "SIM_PE_PERIOD", "s", 0, 1000, f"{_GUIDE}:198"),
        _range(
            GUIDE_CAMERA, "SIMULATOR_SETTINGS", "SIM_PE_MAX", "arcsec (zero to peak)", 0, 100, f"{_GUIDE}:199"
        ),
        _range(MOUNT, "GUIDE_RATE", "GUIDE_RATE_WE", "fraction of 15 arcsec/s", 0, 1, f"{_SCOPE}:121"),
        _range(MOUNT, "GUIDE_RATE", "GUIDE_RATE_NS", "fraction of 15 arcsec/s", 0, 1, f"{_SCOPE}:122"),
        _range(MOUNT, "DEC_BACKLASH", "DEC_BACKLASH", "ms", 0, 5000, f"{_SCOPE}:114"),
        # Arcminutes, measured live on 2026-11-08 (INDI 2.2.5): MM_IH = 1
        # moved the true pointing by exactly 1 arcmin of hour angle, and
        # +-60 was accepted unclamped. The older source declares -5 to 5,
        # which does not apply; 120 is a safe bound (2 degrees).
        *(
            _range(MOUNT, "MOUNT_MODEL", name, "arcmin", -120, 120, "measured live 2026-11-08")
            for name in ("MM_IH", "MM_ID", "MM_CH", "MM_NP", "MM_MA", "MM_ME")
        ),
    )
}


def range_of(device: str, property_name: str, element: str) -> PropertyRange:
    """Look up the range of a property.

    Parameters
    ----------
    device, property_name, element : `str`
        The INDI names.

    Returns
    -------
    row : `PropertyRange`
        The table row.

    """
    return PROPERTY_RANGES[device, property_name, element]


def clamp_to_range(device: str, property_name: str, element: str, value: float) -> tuple[float, str | None]:
    """Clamp a value to the range of its property.

    Parameters
    ----------
    device, property_name, element : `str`
        The INDI names.
    value : `float`
        The wanted value.

    Returns
    -------
    clamped : `float`
        The value inside the range.
    note : `str` or `None`
        A sentence saying what was clamped, or `None` when the value was
        already inside.
    """
    row = range_of(device, property_name, element)
    clamped = min(max(float(value), row.minimum), row.maximum)
    if math.isclose(clamped, float(value)):
        return clamped, None
    return clamped, (
        f"{value:.6g} is outside {row.minimum:g} to {row.maximum:g} {row.unit} "
        f"(INDI 2.2.5 {row.source}); written as {clamped:g}"
    )
