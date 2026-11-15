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
from typing import Any

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
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
# which cannot use the width it is about to measure). The default is the
# median FWHM of the reference setup's frames; a tuner (Phase E3) derives it
# from the stored frame measurements of each setup.
TYPICAL_STAR_WIDTH_PX = TunedParameter(
    name="foundation.typical_star_width_px",
    default=3.0,
    unit="pixels",
    description="Typical star width (FWHM) of the setup's frames, used before a run measures its own.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.CAMERA, EquipmentKind.TELESCOPE}),
    register_entry="AST-17",
    pending_conversion=True,
)

# The typical width of a spectrum's streak across the dispersion, used as the
# trail-width basis before the extraction measures it. The register (SPE-01)
# measured a FWHM of 3.92 px across the whole trail on the reference setup.
TYPICAL_TRAIL_WIDTH_PX = TunedParameter(
    name="foundation.typical_trail_width_px",
    default=3.92,
    unit="pixels",
    description="Typical width of a spectrum's streak across the dispersion, used before a run measures it.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.CAMERA, EquipmentKind.FILTER_OR_GRATING}),
    register_entry="SPE-01",
    pending_conversion=True,
)


# The bases on the reference setup (ZWO ASI533MM Pro, 3.76 µm pixels, 3008 px
# square, on the Apertura 75Q, 405 mm, with the Star Analyzer 200 at 16.5 mm),
# which every factor is fitted to: factor = reference value ÷ reference basis.
# On the reference setup, with no basis measured, `resolve` then gives back
# the reference value exactly. The plate scale, image size, field of view and
# dispersion follow from those numbers; the star and trail widths equal the
# defaults of `TYPICAL_STAR_WIDTH_PX` and `TYPICAL_TRAIL_WIDTH_PX`; the
# spectral resolution is the 43.3 Å resolution element of SPE-01. The guide
# cycle and the periodic-error period are not recorded yet (Phase E4 reads
# them from the stored guiding data); a value with those bases gives its
# reference basis explicitly to `factor_for`.
REFERENCE_SETUP_BASES: dict[RunBasis, float] = {
    RunBasis.STAR_WIDTH_PX: 3.0,
    RunBasis.TRAIL_WIDTH_PX: 3.92,
    RunBasis.PLATE_SCALE_ARCSEC_PER_PX: PLATE_SCALE_CONSTANT * 3.76 / 405.0,
    RunBasis.IMAGE_SHORT_SIDE_PX: 3008.0,
    RunBasis.FIELD_OF_VIEW_DEG: PLATE_SCALE_CONSTANT * 3.76 / 405.0 * 3008.0 / ARCSEC_PER_DEGREE,
    RunBasis.DISPERSION_ANGSTROM_PER_PX: ANGSTROM_PER_MILLIMETER / (200.0 * 16.5) * 3.76 / 1000.0,
    RunBasis.SPECTRAL_RESOLUTION_ANGSTROM: 43.3,
}


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
    "REFERENCE_SETUP_BASES",
    "TYPICAL_STAR_WIDTH_PX",
    "TYPICAL_TRAIL_WIDTH_PX",
    "SetupBases",
    "factor_for",
    "scale_by_basis",
]
