"""The measured quantities that determined-each-run values scale with.

A value that is determined each run is declared as a factor times a basis
(`models.tuned_parameter.RunBasis`), such as "3 × the star width". When a run
measures a basis (the star width of a stack, the plate scale of a solution),
it records it once with `record_basis`, and every `resolve` in the run then
returns factor × basis. Before the run has measured it, the setup supplies
the basis: `SetupBases` works out the plate scale, the image size and the
field of view from the optic and camera, the dispersion from the grating,
and reads the typical star and trail widths as tuned values of the setup.
When neither is known, `resolve` uses the parameter's ``reference_value`` (its
value on the reference setup) and records a notice.
"""

import logging
import math
from enum import StrEnum
from typing import Any

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    ParameterValue,
    RunBasis,
    TunedParameter,
)

logger = logging.getLogger(__name__)

# Arcseconds per radian divided by 1000, so a pixel pitch in micrometers over a
# focal length in millimeters gives arcseconds per pixel (standard plate-scale
# formula).
PLATE_SCALE_CONSTANT = 206.265

ARCSEC_PER_DEGREE = 3600.0

# Angstroms per millimeter, for the dispersion of a grating at a distance.
ANGSTROM_PER_MILLIMETER = 1.0e7

# The typical star width of a setup, used as the star-width basis before a
# run has measured its own (for example inside the star-width estimator,
# which cannot use the width it is about to measure). "Star width" means one
# scale: the FWHM of the brightest stars of one raw frame, fitted with a
# Gaussian (the stored metric `frame.star_width_gaussian_brightest_px`).
# The default is its median over the 1,008 stored frames of the reference
# setup (2.33 px); the tuner `foundation.typical_star_width` derives it for
# each setup from the same stored frames.
TYPICAL_STAR_WIDTH_PX = TunedParameter(
    name="foundation.typical_star_width_px",
    default=2.33,
    unit="pixels",
    description="Typical star width (FWHM) of the setup's frames, used before a run measures its own.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.CAMERA, EquipmentKind.TELESCOPE}),
    register_entry="AST-17",
    allowed_range=(0.5, 30.0),
    # A sample is one stored raw frame (tuner `foundation.typical_star_width`).
    minimum_sample=20,
)

# The typical width of a spectrum's streak across the dispersion, used as the
# trail-width basis before the extraction measures it. The default is the
# median, over the 249 stored star spectra of the reference setup, of each
# spectrum's median streak width (a Gaussian sigma of 2.89 px, a FWHM of
# 6.81 px; middle half 5.16 to 7.85 px), the statistic the tuner
# `foundation.typical_trail_width` takes.
TYPICAL_TRAIL_WIDTH_PX = TunedParameter(
    name="foundation.typical_trail_width_px",
    default=6.81,
    unit="pixels",
    description="Typical width of a spectrum's streak across the dispersion, used before a run measures it.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.CAMERA, EquipmentKind.FILTER_OR_GRATING}),
    register_entry="SPE-01",
    allowed_range=(1.0, 30.0),
    # A sample is one stored star spectrum (its tuner reads them all).
    minimum_sample=10,
)


# The bases on the reference setup (ZWO ASI533MM Pro, 3.76 µm pixels, 3008 px
# square, on the Apertura 75Q, 405 mm, with the Star Analyzer 200 at 16.5 mm),
# which every factor is fitted to: factor = reference value ÷ reference basis.
# On the reference setup, with no basis measured, `resolve` then gives back
# the reference value exactly. The plate scale, image size, field of view and
# dispersion follow from those numbers; the star and trail widths equal the
# defaults of `TYPICAL_STAR_WIDTH_PX` and `TYPICAL_TRAIL_WIDTH_PX`; the
# spectral resolution is the 43.3 Å resolution element of SPE-01. The guide
# cycle is the median time between the reference mount's 46,658 stored guide
# samples (3.1 s); the periodic-error period is the 600 s the guiding code
# assumed, since no guiding spectrum has been stored yet.
REFERENCE_SETUP_BASES: dict[RunBasis, float] = {
    RunBasis.STAR_WIDTH_PX: 2.33,
    RunBasis.TRAIL_WIDTH_PX: 6.81,
    RunBasis.PLATE_SCALE_ARCSEC_PER_PX: PLATE_SCALE_CONSTANT * 3.76 / 405.0,
    RunBasis.IMAGE_SHORT_SIDE_PX: 3008.0,
    RunBasis.FIELD_OF_VIEW_DEG: PLATE_SCALE_CONSTANT * 3.76 / 405.0 * 3008.0 / ARCSEC_PER_DEGREE,
    RunBasis.DISPERSION_ANGSTROM_PER_PX: ANGSTROM_PER_MILLIMETER / (200.0 * 16.5) * 3.76 / 1000.0,
    RunBasis.SPECTRAL_RESOLUTION_ANGSTROM: 43.3,
    RunBasis.GUIDE_CYCLE_SECONDS: 3.1,
    RunBasis.PERIODIC_ERROR_PERIOD_SECONDS: 600.0,
}


class StarWidthEstimator(StrEnum):
    """Which measurement of star width a run recorded.

    Each converts to the one star-width scale (the per-frame Gaussian of the
    brightest stars) with a fixed ratio, so a typical run gets today's values
    back whichever estimator it used.
    """

    FRAME_GAUSSIAN_BRIGHTEST = "frame Gaussian, brightest stars"
    FRAME_GAUSSIAN = "frame Gaussian"
    FRAME_MOFFAT = "frame Moffat"
    STACK_GAUSSIAN = "stack Gaussian"


def _ratio(name: str, value: float, description: str) -> TunedParameter:
    """Declare one estimator's ratio to the star-width scale.

    Returns
    -------
    parameter : `TunedParameter`
        A hardcoded ratio with the AST-17 evidence.
    """
    return TunedParameter(
        name=f"foundation.star_width_scale_{name}",
        default=value,
        unit="star-width scale ÷ this estimator",
        description=description,
        role=ParameterRole.MEASUREMENT,
        source=ParameterSource.HARDCODED,
        kind=HardcodedKind.RELATIVE,
        register_entry="AST-17",
    )


# Each estimator's width times its ratio gives the star-width scale. The
# ratios are the median, over the reference setup's targets, of the target's
# median per-frame Gaussian width (brightest stars) divided by the same
# target's median width from the other estimator (copy of the library
# database, 2026-11-16). Middle half of targets in brackets.
STAR_WIDTH_RATIO_FRAME_GAUSSIAN = _ratio(
    "frame_gaussian",
    0.997,
    "The scale over the frame-quality Gaussian width (14 targets; 0.96 to 1.01).",
)
STAR_WIDTH_RATIO_FRAME_MOFFAT = _ratio(
    "frame_moffat",
    1.192,
    "The scale over the frame measurement's Moffat width (15 targets; 1.15 to 1.24).",
)
STAR_WIDTH_RATIO_STACK_GAUSSIAN = _ratio(
    "stack_gaussian",
    0.938,
    "The scale over the Gaussian width measured on a stack (14 targets; 0.91 to 1.00).",
)
_RATIO_BY_ESTIMATOR = {
    StarWidthEstimator.FRAME_GAUSSIAN: STAR_WIDTH_RATIO_FRAME_GAUSSIAN,
    StarWidthEstimator.FRAME_MOFFAT: STAR_WIDTH_RATIO_FRAME_MOFFAT,
    StarWidthEstimator.STACK_GAUSSIAN: STAR_WIDTH_RATIO_STACK_GAUSSIAN,
}


def star_width_on_scale(width_px: float, estimator: StarWidthEstimator) -> float:
    """Convert a measured star width to the one star-width scale.

    Parameters
    ----------
    width_px : `float`
        The width the estimator measured, in pixels.
    estimator : `StarWidthEstimator`
        Which estimator measured it.

    Returns
    -------
    width : `float`
        The width on the scale of the per-frame Gaussian of the brightest
        stars.
    """
    from astrometricslib.foundation.parameters import resolve

    ratio = _RATIO_BY_ESTIMATOR.get(estimator)
    return width_px if ratio is None else width_px * float(resolve(ratio).value)  # type: ignore[arg-type]


def record_star_width(width_px: float | None, estimator: StarWidthEstimator) -> None:
    """Record the run's star width, converted to the one star-width scale.

    Parameters
    ----------
    width_px : `float` or `None`
        The width the estimator measured, in pixels; nothing is recorded when
        it is missing, not finite or not above zero.
    estimator : `StarWidthEstimator`
        Which estimator measured it.
    """
    from astrometricslib.foundation.parameter_context import record_basis

    if width_px is None or not math.isfinite(width_px) or width_px <= 0.0:
        return
    record_basis(RunBasis.STAR_WIDTH_PX, star_width_on_scale(float(width_px), estimator))


def factor_for(
    reference_value: ParameterValue,
    basis: RunBasis,
    reference_basis: float | None = None,
    *,
    inverse: bool = False,
) -> ParameterValue:
    """Fit a factor so the reference setup gets its reference value back.

    Write a declaration as ``default=factor_for(10, RunBasis.STAR_WIDTH_PX),
    basis=RunBasis.STAR_WIDTH_PX, reference_value=10``.

    Parameters
    ----------
    reference_value : `int`, `float` or `tuple`
        The value on the reference setup (the old absolute value).
    basis : `RunBasis`
        What the factor multiplies.
    reference_basis : `float`, optional
        The basis on the reference setup, for a basis that is not in
        `REFERENCE_SETUP_BASES` (the target size of the target the value was
        chosen on, a measured guide cycle).
    inverse : `bool`, optional
        For a value declared with ``basis_inverse=True`` (value = factor ÷
        basis): the factor is then reference value × reference basis.

    Returns
    -------
    factor : `float` or `tuple` of `float`
        ``reference_value / reference_basis`` (or times it, with
        ``inverse``), element by element.

    Raises
    ------
    ValueError
        If the reference basis is not known or not above zero.
    """
    basis_value = reference_basis if reference_basis is not None else REFERENCE_SETUP_BASES.get(basis)
    if basis_value is None or basis_value <= 0.0:
        raise ValueError(f"No reference value of the {basis} basis is known; pass reference_basis")

    def fitted(element: float) -> float:
        """Fit one element.

        Returns
        -------
        factor : `float`
            The element's factor.
        """
        return float(element) * basis_value if inverse else float(element) / basis_value

    if isinstance(reference_value, tuple):
        return tuple(fitted(element) for element in reference_value)
    return fitted(reference_value)


# The setup's typical guide cycle (one guide exposure and its correction),
# used as the guide-cycle basis. The default is the median time between the
# reference mount's stored guide samples; the tuner
# `wayfinding.typical_guide_cycle` derives it from the stored samples.
TYPICAL_GUIDE_CYCLE_SECONDS = TunedParameter(
    name="foundation.typical_guide_cycle_seconds",
    default=3.1,
    unit="seconds",
    description="Typical time of one guide cycle (exposure and correction) of the setup's guider.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="WAY-D126",
    allowed_range=(0.2, 60.0),
    # A sample is one stored interval between guide samples.
    minimum_sample=100,
)

# The mount's periodic-error period (one turn of the worm gear), used as the
# periodic-error basis. The default is the 600 s the guiding code assumed;
# the tuner `wayfinding.periodic_error_period` takes the median dominant
# period of the stored guiding spectra.
MOUNT_PERIODIC_ERROR_PERIOD_SECONDS = TunedParameter(
    name="foundation.mount_periodic_error_period_seconds",
    default=600.0,
    unit="seconds",
    description="Period of the mount's periodic error (one worm turn).",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="WAY-D135",
    allowed_range=(60.0, 3600.0),
    # A sample is one stored guiding-spectrum analysis.
    minimum_sample=2,
)


def _number(value: Any) -> float | None:
    """Read a positive number from a config value.

    Returns
    -------
    number : `float` or `None`
        The number, or `None` when it is missing, not a number or not above
        zero.
    """
    try:
        number = float(str(value).strip())
    except TypeError, ValueError:
        return None
    return number if number > 0.0 else None


class SetupBases:
    """Work out the bases a setup implies, before a run measures them.

    Parameters
    ----------
    configuration : `Any`
        The loaded config (an `AppConfiguration`).
    setup : `SetupConfig`
        The camera, optic and mount in use.
    """

    def __init__(self, configuration: Any, setup: SetupConfig) -> None:
        self._configuration = configuration
        self._setup = setup
        self._cache: dict[RunBasis, float | None] = {}

    def _camera(self) -> dict[str, Any]:
        """Read the setup camera's config section.

        Returns
        -------
        section : `dict`
            The camera's settings; empty when the config has none.
        """
        getter = getattr(self._configuration, "get_camera_config", None)
        return dict(getter(self._setup.camera_name) or {}) if callable(getter) else {}

    def _focal_length_mm(self) -> float | None:
        """Read the setup optic's focal length.

        Returns
        -------
        focal_length_mm : `float` or `None`
            The focal length, or `None` when the optic is not in the config.
        """
        getter = getattr(self._configuration, "get_observatory_setups", None)
        if not callable(getter):
            return None
        optic = getter().optic_named(self._setup.optic_name)
        return optic.focal_length_mm if optic is not None else None

    def _plate_scale(self) -> float | None:
        """Return arcseconds per pixel from the pixel pitch and focal length.

        Returns
        -------
        scale : `float` or `None`
            The plate scale, or `None` when either number is missing.
        """
        camera = self._camera()
        pixel_um = _number(camera.get("pixel_size_μm") or camera.get("pixel_size_um"))
        focal_length = self._focal_length_mm()
        if pixel_um is None or focal_length is None:
            return None
        return PLATE_SCALE_CONSTANT * pixel_um / focal_length

    def _image_short_side(self) -> float | None:
        """Return the shorter side of the sensor, in pixels.

        Returns
        -------
        side : `float` or `None`
            The shorter side, or `None` when the sensor size is missing.
        """
        camera = self._camera()
        width = _number(camera.get("sensor_width_px"))
        height = _number(camera.get("sensor_height_px"))
        return min(width, height) if width is not None and height is not None else None

    def _dispersion(self) -> float | None:
        """Return Angstroms per pixel from the grating's lines and distance.

        Returns
        -------
        dispersion : `float` or `None`
            The dispersion, or `None` when the camera has no grating settings.
        """
        camera = self._camera()
        lines_per_mm = _number(camera.get("grating_lines_per_mm"))
        distance_mm = _number(camera.get("grating_distance_mm"))
        pixel_um = _number(camera.get("pixel_size_μm") or camera.get("pixel_size_um"))
        if lines_per_mm is None or distance_mm is None or pixel_um is None:
            return None
        return ANGSTROM_PER_MILLIMETER / (lines_per_mm * distance_mm) * pixel_um / 1000.0

    def value(self, basis: RunBasis) -> float | None:
        """Return the setup's value of a basis, if it can be worked out.

        Parameters
        ----------
        basis : `RunBasis`
            The basis.

        Returns
        -------
        value : `float` or `None`
            The value, or `None` when the setup cannot supply this basis
            (for example the target size, which only a run knows).
        """
        if basis not in self._cache:
            self._cache[basis] = self._compute(basis)
        return self._cache[basis]

    def _compute(self, basis: RunBasis) -> float | None:
        """Work out one basis from the setup.

        Returns
        -------
        value : `float` or `None`
            The value, or `None` when it cannot be worked out.
        """
        from astrometricslib.foundation.parameters import resolve

        if basis is RunBasis.PLATE_SCALE_ARCSEC_PER_PX:
            return self._plate_scale()
        if basis is RunBasis.IMAGE_SHORT_SIDE_PX:
            return self._image_short_side()
        if basis is RunBasis.FIELD_OF_VIEW_DEG:
            scale, side = self._plate_scale(), self._image_short_side()
            return scale * side / ARCSEC_PER_DEGREE if scale is not None and side is not None else None
        if basis is RunBasis.DISPERSION_ANGSTROM_PER_PX:
            return self._dispersion()
        if basis is RunBasis.STAR_WIDTH_PX:
            return float(resolve(TYPICAL_STAR_WIDTH_PX).value)  # type: ignore[arg-type]
        if basis is RunBasis.TRAIL_WIDTH_PX:
            return float(resolve(TYPICAL_TRAIL_WIDTH_PX).value)  # type: ignore[arg-type]
        if basis is RunBasis.GUIDE_CYCLE_SECONDS:
            return float(resolve(TYPICAL_GUIDE_CYCLE_SECONDS).value)  # type: ignore[arg-type]
        if basis is RunBasis.PERIODIC_ERROR_PERIOD_SECONDS:
            return float(resolve(MOUNT_PERIODIC_ERROR_PERIOD_SECONDS).value)  # type: ignore[arg-type]
        return None


def scale_by_basis(parameter: TunedParameter, basis_value: float) -> ParameterValue:
    """Multiply a parameter's factor by a basis value (or divide, if inverse).

    The result has the type of the reference value: a whole number is rounded
    (and kept at least 1), a tuple is scaled element by element.

    Parameters
    ----------
    parameter : `TunedParameter`
        A declaration with a basis.
    basis_value : `float`
        The basis.

    Returns
    -------
    value : `int`, `float` or `tuple`
        Factor × basis.
    """

    def scaled(factor: float, reference: float) -> int | float:
        """Scale one element, keeping the reference element's type.

        Returns
        -------
        element : `int` or `float`
            The scaled element.
        """
        product = factor / basis_value if parameter.basis_inverse else factor * basis_value
        return max(1, round(product)) if isinstance(reference, int) else product

    default, reference = parameter.default, parameter.reference_value
    if isinstance(default, tuple) and isinstance(reference, tuple):
        return tuple(scaled(float(f), r) for f, r in zip(default, reference, strict=True))
    return scaled(float(default), reference)  # type: ignore[arg-type]


__all__ = [
    "MOUNT_PERIODIC_ERROR_PERIOD_SECONDS",
    "REFERENCE_SETUP_BASES",
    "STAR_WIDTH_RATIO_FRAME_GAUSSIAN",
    "STAR_WIDTH_RATIO_FRAME_MOFFAT",
    "STAR_WIDTH_RATIO_STACK_GAUSSIAN",
    "TYPICAL_GUIDE_CYCLE_SECONDS",
    "TYPICAL_STAR_WIDTH_PX",
    "TYPICAL_TRAIL_WIDTH_PX",
    "SetupBases",
    "StarWidthEstimator",
    "factor_for",
    "record_star_width",
    "scale_by_basis",
    "star_width_on_scale",
]
