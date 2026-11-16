"""What `QualityRecords.measure_star_profile` returns: one star's profile.

The result holds the radial profile of a star (brightness above the sky
against distance from the star's center), the Moffat and Gaussian curves
fitted to it, and a cut through the star along a straight line. Every number
is a plain Python float, int, bool, string or list, so it can be sent to the
app as it is.
"""

from pydantic import BaseModel

__all__ = ["ProfileFit", "StarProfileMeasurement"]


class ProfileFit(BaseModel):
    """One fitted curve of a star's radial profile.

    Parameters
    ----------
    model : `str`
        ``"moffat"`` or ``"gaussian"``.
    fitted : `bool`
        `True` when the fit worked. When `False`, the numbers are `None`
        and ``reason`` says why.
    reason : `str` or `None`
        Why the fit failed.
    fwhm_px : `float` or `None`
        Full width at half maximum, in pixels.
    fwhm_arcsec : `float` or `None`
        The same width in arcseconds, when the header gives a pixel scale.
    peak : `float` or `None`
        The curve's value at the center, above the sky, in image units.
    beta : `float` or `None`
        Moffat power index (the Gaussian has none).
    center_x, center_y : `float` or `None`
        The fitted center, in 1-based pixels.
    curve_radius_px, curve_values : `list` [`float`]
        The curve drawn along the distance from the center, for plotting.
    r_squared : `float` or `None`
        How much of the spread of the pixel values the curve explains.
    """

    model: str
    fitted: bool
    reason: str | None = None
    fwhm_px: float | None = None
    fwhm_arcsec: float | None = None
    peak: float | None = None
    beta: float | None = None
    center_x: float | None = None
    center_y: float | None = None
    curve_radius_px: list[float] = []
    curve_values: list[float] = []
    r_squared: float | None = None


class StarProfileMeasurement(BaseModel):
    """The radial profile and a straight cut for one star.

    A cut at any angle can be rebuilt from ``cutout``. With ``a`` the angle in
    radians, and ``center_x``, ``center_y`` as above, take for each
    ``k = -n .. n`` (``n = floor(cut_half_length_px / cut_step_px)``) the
    distance ``t = k * cut_step_px`` and the 1-based image position
    ``(center_x + t sin a, center_y + t cos a)``. Convert it to a position in
    the cutout, ``column = x - cutout_origin_x`` and ``row = y -
    cutout_origin_y`` (0-based), and read the value by bilinear
    interpolation of the four surrounding cutout pixels. Keep a point only
    when it lies inside the image (1-based x from 1 to the image width, y from
    1 to the height; a point whose neighbors are `None` has no value). The
    value is already sky-subtracted, and ``cut_distance_px`` is ``t``.

    Pixel positions are 1-based, as in FITS and DS9: the center of the first
    pixel is (1, 1). ``x`` runs along the columns and ``y`` along the rows.

    Parameters
    ----------
    path : `str`
        The image that was measured.
    color_note : `str` or `None`
        Says that a color image was averaged over its channels. `None` for
        a single-channel image.
    clicked_x, clicked_y : `float`
        The position that was asked for.
    center_x, center_y : `float`
        The center after refining to the brightness-weighted centroid.
    radius_px : `float`
        The radius of the profile.
    radius_from_star_width : `bool`
        `True` when the radius, or the cut length, was taken from the star
        width instead of from the caller.
    sky : `float`
        The sky level (sigma-clipped median of the annulus), in image units.
    sky_noise : `float`
        The sky scatter (sigma-clipped standard deviation of the annulus).
    sky_pixel_count : `int`
        How many annulus pixels were kept for the sky.
    pixel_scale_arcsec : `float` or `None`
        The pixel scale from the header.
    profile_radius_px, profile_values : `list` [`float`]
        For each pixel within the radius: its distance from the center and
        its value minus the sky.
    peak : `float`
        The brightest pixel within the radius, minus the sky.
    peak_raw : `float`
        The same pixel's raw value.
    image_maximum : `float`
        The largest value in the whole image.
    clipped : `bool`
        `True` when the peak looks clipped: at least two pixels share the
        peak value and the peak is at or near the image maximum.
    clipped_reason : `str`
        A plain sentence about the clipped check.
    moffat, gaussian : `ProfileFit`
        The two fits.
    cut_angle_degrees : `float`
        The angle of the cut, measured from +y toward +x.
    cut_half_length_px : `float`
        How far the cut reaches each way from the center.
    cut_distance_px, cut_values : `list` [`float`]
        The cut: signed distance from the center (positive toward the
        direction of the angle) and value minus the sky.
    cut_step_px : `float`
        The spacing of the cut samples.
    cutout : `list` [`list` [`float` or `None`]]
        A square piece of the image, value minus the sky, as a list of rows.
        Its side is ``2 * ceil(cut_half_length_px) + 3`` pixels, centered on
        the pixel nearest the center. Rows increase with the image's y, as
        in the file (the first row is the lowest y), and columns with x. A
        pixel outside the image, or not finite, is `None`.
    cutout_origin_x, cutout_origin_y : `int`
        The 1-based image pixel of the cutout's first column and first row.
        They may be zero or negative when the star is near an edge.
    snapped : `bool`
        `True` when a search radius was given and the center moved at least
        one pixel from the click.
    snap_distance_px : `float`
        How far the final center is from the clicked position.
    star_found : `bool`
        `False` when the profile does not look like a star. The data are
        still returned.
    not_a_star_reason : `str` or `None`
        The sentence to show when ``star_found`` is `False`.
    """

    path: str
    color_note: str | None = None
    clicked_x: float
    clicked_y: float
    center_x: float
    center_y: float
    radius_px: float
    radius_from_star_width: bool
    sky: float
    sky_noise: float
    sky_pixel_count: int
    pixel_scale_arcsec: float | None = None
    profile_radius_px: list[float]
    profile_values: list[float]
    peak: float
    peak_raw: float
    image_maximum: float
    clipped: bool
    clipped_reason: str
    moffat: ProfileFit
    gaussian: ProfileFit
    cut_angle_degrees: float
    cut_half_length_px: float
    cut_distance_px: list[float]
    cut_values: list[float]
    cut_step_px: float
    cutout: list[list[float | None]]
    cutout_origin_x: int
    cutout_origin_y: int
    snapped: bool
    snap_distance_px: float
    star_found: bool
    not_a_star_reason: str | None = None
