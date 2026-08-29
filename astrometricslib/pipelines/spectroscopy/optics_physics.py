r"""The math behind how a spectrograph splits light into a rainbow.

Provides the physics equations to figure out exactly what color of light
is hitting each pixel on the camera sensor.

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

"""

import numpy as np

# Type alias to support both float scalars and NumPy float arrays
Numeric = float | np.ndarray


def calculate_wavelength(
    pixel_offset_px: Numeric, grating_distance_mm: float, lines_per_mm: float, pixel_size_um: float
) -> Numeric:
    """Figure out what color (wavelength) is hitting a specific pixel.

    Parameters
    ----------
    pixel_offset_px : `Numeric`
        How many pixels away we are from the main star image.
    grating_distance_mm : `float`
        How far the grating is from the camera sensor (in mm).
    lines_per_mm : `float`
        How many lines are etched into the grating per mm (like 100 or 200).
    pixel_size_um : `float`
        How big each pixel is on the camera sensor (in micrometers).

    Returns
    -------
    wavelength_nm : `Numeric`
        The color of the light at that pixel, in nanometers.
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
    """Figure out which pixel will see a specific color of light.

    This is the reverse of `calculate_wavelength`. If we want to find where
    the red light is, this tells us how many pixels away to look.

    Parameters
    ----------
    wavelength_nm : `Numeric`
        The color we are looking for, in nanometers.
    grating_distance_mm : `float`
        How far the grating is from the camera sensor (in mm).
    lines_per_mm : `float`
        How many lines are etched into the grating per mm.
    pixel_size_um : `float`
        How big each pixel is on the camera sensor (in micrometers).

    Returns
    -------
    pixel_offset_px : `Numeric`
        How many pixels away from the star that color will land.
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
