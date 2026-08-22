r"""Physical optics for first-order transmission grating spectrometers.

Provides pure, stateless physics equations for first-order
transmission grating spectrometers. Isolates the core optical model,
dispersion geometry, and coordinate conversions to make the
mathematical processing transparent, easy to audit, and highly
testable.

Notes
-----
Optical propagation geometry::

      Zero-Order Star Image
             |
             v [Plane of transmission grating]
             +-----------------------+ Grating spacing d = 1 / lines_per_mm
             | \                     |
             |   \                   |
             |     \                 |
             |       \               | Optical distance L
    (grating_distance_mm)
             |         \             |
             |           \           |
             |             \         |
             |               \       |
             |    Angle theta  \     |
             |  /               \    |
             v                   v   v
    [Zero-Order Star] -------> [Calibrated Wavelength lambda]
    Pixel Offset Index          x_mm = x_offset_px * pixel_pitch

Physical equations:

1. Linear dispersion sensor offset:

   .. math::
      x_{\text{mm}} = x_{\text{offset\_px}} \cdot \text{pitch}_{\text{mm}}

2. Dispersion angle theta (rad):

   .. math::
      \theta = \arctan\left(\frac{x_{\text{mm}}}{L}\right)

3. Transmission Grating Equation (first-order, m=1):

   .. math::
      \lambda = d \cdot \sin(\theta)

   Wavelength lambda is converted to nanometers by multiplying by 1,000,000.

4. Inverse equation (wavelength to pixel offset):

   .. math::
      \theta &= \arcsin\left(\frac{\lambda}{d}\right) \\
      x_{\text{mm}} &= L \cdot \tan(\theta) \\
      x_{\text{offset\_px}} &= \frac{x_{\text{mm}}}{\text{pitch}_{\text{mm}}}

REQ: AST-1, AST-2
"""

import numpy as np

# Type alias to support both float scalars and NumPy float arrays
Numeric = float | np.ndarray


def calculate_wavelength(
    pixel_offset_px: Numeric, grating_distance_mm: float, lines_per_mm: float, pixel_size_um: float
) -> Numeric:
    """Calculate the physical wavelength for a given pixel offset.

    Uses a standard first-order transmission grating geometric model
    to convert a pixel offset along the dispersion vector, measured
    from the zero-order anchor position, into a wavelength.

    Parameters
    ----------
    pixel_offset_px : `Numeric`
        Pixel offset along the dispersion line relative to the
        zero-order star.
    grating_distance_mm : `float`
        Physical distance (L) between the grating filter and the
        camera sensor (in mm).
    lines_per_mm : `float`
        Frequency of lines per mm of the diffraction grating.
    pixel_size_um : `float`
        Dimension of a single pixel on the sensor (in micrometers).

    Returns
    -------
    wavelength_nm : `Numeric`
        Calibrated physical wavelength(s) (in nanometers).
    """
    # 1. Convert pixel size in micrometers to millimeter pitch
    # pitch = um * 1e-3 (e.g. 3.76 um -> 0.00376 mm)
    pixel_pitch_mm = pixel_size_um * 1e-3

    # 2. Compute spatial displacement along sensor in millimeters
    # x_mm = pixels * pitch
    spatial_offset_mm = pixel_offset_px * pixel_pitch_mm

    # 3. Solve for light propagation dispersion angle theta (in radians)
    # tan(theta) = x / L  ===>  theta = arctan(x / L)
    dispersion_angle_rad = np.arctan(spatial_offset_mm / grating_distance_mm)

    # 4. Calculate grating line spacing d in millimeters
    # d_mm = 1.0 / lines_per_mm
    grating_spacing_mm = 1.0 / lines_per_mm

    # 5. Apply first-order transmission grating equation:
    # lambda = d * sin(theta)
    # Convert from millimeters to nanometers (1 mm = 1,000,000 nm)
    wavelength_nm = grating_spacing_mm * np.sin(dispersion_angle_rad) * 1e6

    return wavelength_nm


def calculate_pixel_offset(
    wavelength_nm: Numeric, grating_distance_mm: float, lines_per_mm: float, pixel_size_um: float
) -> Numeric:
    """Calculate the pixel offset for a given target wavelength.

    The mathematical inverse of `calculate_wavelength`. Given a target
    wavelength (in nm), determines the expected pixel offset along the
    dispersion vector from the zero-order anchor.

    Parameters
    ----------
    wavelength_nm : `Numeric`
        Target calibrated wavelength (in nanometers).
    grating_distance_mm : `float`
        Physical optical distance (L) from grating to sensor (in mm).
    lines_per_mm : `float`
        Frequency of lines per mm of the diffraction grating.
    pixel_size_um : `float`
        Dimension of a single pixel on the sensor (in micrometers).

    Returns
    -------
    pixel_offset_px : `Numeric`
        Solved pixel offset index along the dispersion vector.
    """
    # 1. Calculate grating line spacing d in millimeters
    # d_mm = 1.0 / lines_per_mm
    grating_spacing_mm = 1.0 / lines_per_mm

    # 2. Convert target wavelength from nanometers to millimeters
    # wl_mm = nm * 1e-6
    wavelength_mm = wavelength_nm * 1e-6

    # 3. Apply inverse grating formula to solve dispersion angle theta
    # sin(theta) = lambda / d  ===>  theta = arcsin(lambda / d)
    dispersion_angle_rad = np.arcsin(wavelength_mm / grating_spacing_mm)

    # 4. Calculate physical displacement on the sensor plane in millimeters
    # x_mm = L * tan(theta)
    spatial_offset_mm = grating_distance_mm * np.tan(dispersion_angle_rad)

    # 5. Convert spatial offset in millimeters back to pixels
    # pixels = x_mm / pitch
    pixel_pitch_mm = pixel_size_um * 1e-3
    pixel_offset_px = spatial_offset_mm / pixel_pitch_mm

    return pixel_offset_px
