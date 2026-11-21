"""Tests of the star profile measurement on synthetic stars.

Each test draws stars of known width on a flat noisy sky, writes a FITS
file, and checks what `measure_star_profile_in_file` reports.
"""

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy.io import fits
from scipy.ndimage import maximum_filter

from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.quality.star_profile import StarProfileMeasurement
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import (
    APERTURE_RADIUS_PX,
    CROWDING_MARGIN_PX,
    CROWDING_NEIGHBOR_PEAK_FRACTION,
    SKY_ANNULUS_INNER_RADIUS_PX,
    SKY_ANNULUS_OUTER_RADIUS_PX,
)
from astrometricslib.pipelines.shared.quality.star_profile import PhotometryGeometry, _judge_core_shape
from astrometricslib.pipelines.shared.quality.star_profile import measure_star_profile_in_file as _measure

SIZE = 101
SKY = 500.0


def measure_star_profile_in_file(*args: Any, **kwargs: Any) -> StarProfileMeasurement:
    """Measure a star, passing the photometry geometry the API passes.

    Returns
    -------
    measurement : `StarProfileMeasurement`
        What `measure_star_profile_in_file` returns.
    """
    geometry = PhotometryGeometry(
        aperture_radius_px=float(resolve(APERTURE_RADIUS_PX).value),
        sky_inner_px=float(resolve(SKY_ANNULUS_INNER_RADIUS_PX).value),
        sky_outer_px=float(resolve(SKY_ANNULUS_OUTER_RADIUS_PX).value),
        crowding_margin_px=float(resolve(CROWDING_MARGIN_PX).value),
        crowding_neighbor_peak_fraction=float(resolve(CROWDING_NEIGHBOR_PEAK_FRACTION).value),
    )
    return _measure(*args, photometry_geometry=geometry, **kwargs)


def _write(tmp_path: Path, stars: list[tuple[float, float, float, float]], clip: float | None = None) -> str:
    """Write a sky with Gaussian stars to a FITS file.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The folder for the file.
    stars : `list` of `tuple`
        Each star as 0-based column, row, peak and sigma.
    clip : `float`, optional
        A ceiling the pixel values are cut at.

    Returns
    -------
    path : `str`
        The file.
    """
    rng = np.random.default_rng(3)
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    image = np.full((SIZE, SIZE), SKY) + rng.normal(0.0, 2.0, (SIZE, SIZE))
    for column, row, peak, sigma in stars:
        image += peak * np.exp(-((columns - column) ** 2 + (rows - row) ** 2) / (2 * sigma**2))
    if clip is not None:
        image = np.minimum(image, clip)
    header = fits.Header()
    header["SECPIX1"] = 2.0
    path = tmp_path / "stars.fits"
    fits.PrimaryHDU(image.astype(np.float32), header=header).writeto(path)
    return str(path)


def test_gaussian_width_is_recovered(tmp_path: Path) -> None:
    """A Gaussian star of known width is measured within a few percent."""
    sigma = 1.8
    path = _write(tmp_path, [(50.0, 50.0, 3000.0, sigma)])
    result = measure_star_profile_in_file(path, 52.0, 49.0)
    true_fwhm = 2.3548 * sigma
    assert result.gaussian.fwhm_px == pytest.approx(true_fwhm, rel=0.03)
    assert result.moffat.fwhm_px == pytest.approx(true_fwhm, rel=0.05)
    assert result.gaussian.fwhm_arcsec == pytest.approx(true_fwhm * 2.0, rel=0.03)
    assert result.center_x == pytest.approx(51.0, abs=0.3)
    assert result.center_y == pytest.approx(51.0, abs=0.3)
    assert result.clicked_x == pytest.approx(52.0)
    assert not result.clipped


def test_sky_is_subtracted(tmp_path: Path) -> None:
    """The sky is found in the ring and removed from the profile."""
    path = _write(tmp_path, [(50.0, 50.0, 3000.0, 1.8)])
    result = measure_star_profile_in_file(path, 51.0, 51.0)
    assert result.sky == pytest.approx(SKY, abs=1.0)
    assert result.peak == pytest.approx(3000.0, rel=0.05)
    far = [v for r, v in zip(result.profile_radius_px, result.profile_values, strict=True) if r > 9]
    assert abs(float(np.mean(far))) < 5.0


def test_pair_shows_two_peaks_along_its_line(tmp_path: Path) -> None:
    """Two stars 5 px apart in rows appear as two peaks in a cut at angle 0."""
    path = _write(tmp_path, [(50.0, 48.0, 3000.0, 1.2), (50.0, 53.0, 3000.0, 1.2)])
    result = measure_star_profile_in_file(
        path, 51.0, 49.0, radius_px=10.0, cut_angle_degrees=0.0, cut_half_length_px=10.0
    )
    values = np.array(result.cut_values)
    peaks = [
        i
        for i in range(1, len(values) - 1)
        if values[i] > values[i - 1] and values[i] > values[i + 1] and values[i] > 500
    ]
    assert len(peaks) == 2
    distances = np.array(result.cut_distance_px)[peaks]
    assert abs(distances[1] - distances[0]) == pytest.approx(5.0, abs=0.6)


def test_cut_angle_runs_from_y_toward_x(tmp_path: Path) -> None:
    """Angle 90 runs along a row: a star offset in columns shows up there."""
    path = _write(tmp_path, [(50.0, 50.0, 3000.0, 1.2), (56.0, 50.0, 1500.0, 1.2)])
    result = measure_star_profile_in_file(
        path, 51.0, 51.0, radius_px=4.0, cut_angle_degrees=90.0, cut_half_length_px=10.0
    )
    distances = np.array(result.cut_distance_px)
    values = np.array(result.cut_values)
    assert distances[int(np.argmax(values[distances > 3]) + np.sum(distances <= 3))] == pytest.approx(
        6.0, abs=0.6
    )


def test_clipped_star_is_flagged(tmp_path: Path) -> None:
    """A star with a flat top at the image maximum is reported as clipped."""
    path = _write(tmp_path, [(50.0, 50.0, 30000.0, 2.0)], clip=4000.0)
    result = measure_star_profile_in_file(path, 51.0, 51.0)
    assert result.clipped
    assert math.isclose(result.peak_raw, 4000.0)


def test_bad_input_is_refused(tmp_path: Path) -> None:
    """A position off the image, a tiny radius or a missing file is refused."""
    path = _write(tmp_path, [(50.0, 50.0, 3000.0, 1.8)])
    with pytest.raises(InvalidArgumentError):
        measure_star_profile_in_file(path, 500.0, 5.0)
    with pytest.raises(InvalidArgumentError):
        measure_star_profile_in_file(path, 50.0, 50.0, radius_px=1.0)
    with pytest.raises(InvalidArgumentError):
        measure_star_profile_in_file(path, float("nan"), 50.0)
    with pytest.raises(NotFoundError):
        measure_star_profile_in_file(str(tmp_path / "missing.fits"), 50.0, 50.0)


def test_color_image_is_averaged_and_says_so(tmp_path: Path) -> None:
    """A three-channel image is averaged and the result carries a note."""
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    plane = SKY + 3000.0 * np.exp(-((columns - 50) ** 2 + (rows - 50) ** 2) / (2 * 1.8**2))
    path = tmp_path / "color.fits"
    fits.PrimaryHDU(np.stack([plane, plane, plane]).astype(np.float32)).writeto(path)
    result = measure_star_profile_in_file(str(path), 51.0, 51.0)
    assert result.color_note
    assert result.gaussian.fwhm_px == pytest.approx(2.3548 * 1.8, rel=0.03)


def test_click_beside_a_star_snaps_to_it(tmp_path: Path) -> None:
    """With a search radius, a click a few pixels off finds the star."""
    path = _write(tmp_path, [(50.0, 50.0, 3000.0, 1.8)])
    result = measure_star_profile_in_file(path, 56.0, 51.0, search_radius_px=8.0)
    assert result.snapped
    assert result.center_x == pytest.approx(51.0, abs=0.3)
    assert result.snap_distance_px == pytest.approx(5.0, abs=0.5)
    assert result.star_found
    assert result.not_a_star_reason is None


def test_default_does_not_snap(tmp_path: Path) -> None:
    """Without a search radius the click is only refined in the small box."""
    path = _write(tmp_path, [(50.0, 50.0, 3000.0, 1.8)])
    result = measure_star_profile_in_file(path, 56.0, 51.0)
    assert not result.snapped
    assert 52.0 < result.center_x < 54.0
    assert result.snap_distance_px == pytest.approx(abs(result.center_x - 56.0), abs=0.5)


def test_hot_pixel_does_not_beat_a_faint_star(tmp_path: Path) -> None:
    """A hot pixel near a faint star loses to the star after smoothing."""
    path = _write(tmp_path, [(50.0, 50.0, 300.0, 1.8)])
    with fits.open(path, mode="update") as hdul:
        hdul[0].data[50, 58] += 600.0
    result = measure_star_profile_in_file(path, 54.0, 51.0, search_radius_px=8.0)
    assert result.center_x == pytest.approx(51.0, abs=0.5)


def test_noise_is_not_a_star(tmp_path: Path) -> None:
    """Pure sky gives star_found false with the signal-to-noise sentence."""
    path = _write(tmp_path, [])
    result = measure_star_profile_in_file(path, 50.0, 50.0, search_radius_px=5.0)
    assert not result.star_found
    assert "times the sky noise" in (result.not_a_star_reason or "")


def test_hot_pixel_is_too_narrow(tmp_path: Path) -> None:
    """A lone bright pixel is reported as too narrow."""
    path = _write(tmp_path, [])
    with fits.open(path, mode="update") as hdul:
        hdul[0].data[50, 50] += 5000.0
    result = measure_star_profile_in_file(path, 51.0, 51.0)
    assert not result.star_found
    assert "Too narrow" in (result.not_a_star_reason or "")


def test_broad_blob_is_too_wide(tmp_path: Path) -> None:
    """A smooth blob much wider than a star is reported as too wide."""
    path = _write(tmp_path, [(50.0, 50.0, 3000.0, 12.0)])
    result = measure_star_profile_in_file(path, 51.0, 51.0, radius_px=30.0)
    assert not result.star_found
    assert "Too wide" in (result.not_a_star_reason or "")


def _cut_from_cutout(
    result: object, width: int, height: int, angle_degrees: float
) -> tuple[list[float], list[float]]:
    """Rebuild a cut from the cutout with the recipe in the model docstring.

    Returns
    -------
    distances, values : `list` [`float`]
        The kept distances and their sky-subtracted values.
    """
    from scipy import ndimage

    cutout = np.array(
        [[np.nan if v is None else v for v in row] for row in result.cutout],  # type: ignore[attr-defined]
        dtype=float,
    )
    step = result.cut_step_px  # type: ignore[attr-defined]
    count = math.floor(result.cut_half_length_px / step)  # type: ignore[attr-defined]
    angle = math.radians(angle_degrees)
    distances, values = [], []
    for k in range(-count, count + 1):
        t = k * step
        x = result.center_x + t * math.sin(angle)  # type: ignore[attr-defined]
        y = result.center_y + t * math.cos(angle)  # type: ignore[attr-defined]
        if not (1.0 <= x <= width and 1.0 <= y <= height):
            continue
        column = x - result.cutout_origin_x  # type: ignore[attr-defined]
        row = y - result.cutout_origin_y  # type: ignore[attr-defined]
        value = ndimage.map_coordinates(cutout, [[row], [column]], order=1, mode="nearest")[0]
        distances.append(t)
        values.append(float(value))
    return distances, values


@pytest.mark.parametrize("angle", [0.0, 30.0, 90.0, 135.0, 250.0])
def test_cut_rebuilds_from_the_cutout(tmp_path: Path, angle: float) -> None:
    """The documented recipe on the cutout gives the same cut as the result."""
    path = _write(tmp_path, [(50.0, 50.0, 3000.0, 1.8), (55.0, 52.0, 1500.0, 1.4)])
    result = measure_star_profile_in_file(
        path, 51.0, 51.0, radius_px=10.0, cut_angle_degrees=angle, cut_half_length_px=12.0
    )
    distances, values = _cut_from_cutout(result, SIZE, SIZE, angle)
    assert distances == pytest.approx(result.cut_distance_px)
    assert values == pytest.approx(result.cut_values, abs=1e-3)
    assert len(result.cutout) == 2 * 13 + 1


def test_cutout_pads_outside_the_image_and_is_limited(tmp_path: Path) -> None:
    """Pixels off the image are None, and an oversized request is refused."""
    path = _write(tmp_path, [(5.0, 5.0, 3000.0, 1.8)])
    result = measure_star_profile_in_file(path, 6.0, 6.0, radius_px=6.0, cut_half_length_px=8.0)
    assert result.cutout_origin_x < 1
    assert result.cutout[0][0] is None
    with pytest.raises(InvalidArgumentError):
        measure_star_profile_in_file(path, 6.0, 6.0, radius_px=500.0)
    with pytest.raises(InvalidArgumentError):
        measure_star_profile_in_file(path, 6.0, 6.0, cut_half_length_px=500.0)


def test_surroundings_report_edge_and_signal(tmp_path: Path) -> None:
    """A star near the edge reports its edge distance and a high S/N."""
    path = _write(tmp_path, [(6.0, 50.0, 3000.0, 1.8)])
    result = measure_star_profile_in_file(path, 7.0, 51.0)
    assert result.edge_distance_px == pytest.approx(6.0, abs=0.5)
    assert result.coverage_fraction < 0.9
    assert result.signal_to_noise > 100


def test_surroundings_count_neighbors_in_circle_and_sky_ring(tmp_path: Path) -> None:
    """A bright star in the circle and one in the sky ring count apart."""
    path = _write(tmp_path, [(50.0, 50.0, 3000.0, 1.8), (56.0, 50.0, 1000.0, 1.8), (50.0, 59.0, 2000.0, 1.8)])

    def finder(box: np.ndarray) -> list[tuple[float, float]]:
        """Find the local brightness peaks in a piece of the image.

        Returns
        -------
        stars : `list` [`tuple` [`float`, `float`]]
            The 0-based column and row of each peak.
        """
        peaks = (box == maximum_filter(box, size=5)) & (box > SKY + 100.0)
        rows, columns = np.nonzero(peaks)
        return [(float(column), float(row)) for column, row in zip(columns, rows, strict=True)]

    result = measure_star_profile_in_file(path, 51.0, 51.0, find_neighbors=finder)
    assert result.circle_neighbor_count == 1
    assert result.sky_ring_neighbor_count == 1
    alone = measure_star_profile_in_file(path, 51.0, 51.0)
    assert alone.circle_neighbor_count == 0


def _write_stretched_star(
    tmp_path: Path, peak: float, sigma_long: float, sigma_short: float, angle_deg: float
) -> str:
    """Write one stretched Gaussian star on a noisy sky.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The folder for the file.
    peak : `float`
        The peak above the sky.
    sigma_long, sigma_short : `float`
        The widths along and across the long axis, in pixels.
    angle_deg : `float`
        The long axis, from the x axis toward the y axis.

    Returns
    -------
    path : `str`
        The file.
    """
    rng = np.random.default_rng(5)
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    cosine, sine = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    along = (columns - 50.3) * cosine + (rows - 49.8) * sine
    across = -(columns - 50.3) * sine + (rows - 49.8) * cosine
    image = SKY + rng.normal(0.0, 2.0, (SIZE, SIZE))
    image += peak * np.exp(-(along**2 / (2 * sigma_long**2) + across**2 / (2 * sigma_short**2)))
    path = tmp_path / "stretched.fits"
    fits.PrimaryHDU(image.astype(np.float32)).writeto(path)
    return str(path)


def test_round_star_core_shape_is_round(tmp_path: Path) -> None:
    """A round star reads an elongation near 1 and is judgeable."""
    path = _write(tmp_path, [(50.0, 50.0, 3000.0, 1.8)])
    result = measure_star_profile_in_file(path, 51.0, 51.0)
    assert result.star_shape_judgeable
    assert result.star_shape_note is None
    assert result.star_elongation == pytest.approx(1.0, abs=0.03)


def test_stretched_star_gives_its_ratio_and_angle(tmp_path: Path) -> None:
    """A star stretched 4:3 at 30 degrees reads about 1.33 along 30 degrees."""
    path = _write_stretched_star(tmp_path, 3000.0, 2.4, 1.8, 30.0)
    result = measure_star_profile_in_file(path, 51.0, 51.0)
    assert result.star_shape_judgeable
    assert result.star_elongation == pytest.approx(2.4 / 1.8, abs=0.06)
    assert result.star_elongation_angle_deg == pytest.approx(30.0, abs=4.0)


def test_faint_star_shape_is_not_judgeable(tmp_path: Path) -> None:
    """A star barely above the noise gives no elongation and says why."""
    path = _write(tmp_path, [(50.0, 50.0, 8.0, 1.8)])
    result = measure_star_profile_in_file(path, 51.0, 51.0)
    assert not result.star_shape_judgeable
    assert result.star_elongation is None
    assert result.star_elongation_angle_deg is None
    assert result.star_shape_note is not None


def test_star_at_the_edge_is_not_judgeable(tmp_path: Path) -> None:
    """A star whose fit box leaves the image is not judged."""
    path = _write(tmp_path, [(6.0, 50.0, 3000.0, 1.8)])
    result = measure_star_profile_in_file(path, 7.0, 51.0)
    assert not result.star_shape_judgeable
    assert "edge" in (result.star_shape_note or "")


def _star_image(peak: float = 3000.0) -> np.ndarray:
    """Draw one round, clean star at the middle of a noisy sky.

    Parameters
    ----------
    peak : `float`, optional
        The star's peak above the sky.

    Returns
    -------
    image : `numpy.ndarray`
        The image, with the star at column 50 and row 50.
    """
    rng = np.random.default_rng(3)
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    image = SKY + rng.normal(0.0, 2.0, (SIZE, SIZE))
    return image + peak * np.exp(-((columns - 50.0) ** 2 + (rows - 50.0) ** 2) / (2 * 1.8**2))


def test_shape_note_for_a_clean_star_is_empty() -> None:
    """A clean, bright star is judged and has no note or detail."""
    image = _star_image()
    elongation, _, judgeable, note, detail = _judge_core_shape(image, 50.0, 50.0, 500.0, False, True, 0)
    assert judgeable and elongation is not None
    assert note is None and detail is None


@pytest.mark.parametrize(
    ("case", "note"),
    [
        ("not_star", "not a single star"),
        ("faint", "too faint"),
        ("clipped", "the core is clipped"),
        ("neighbor", "a neighbor is too close"),
        ("edge", "too near the edge"),
        ("gaps", "gaps in the image"),
        ("fit", "the fit did not hold"),
    ],
)
def test_shape_note_names_the_failing_check(case: str, note: str) -> None:
    """Each refusing check gives its own short reason and a sentence."""
    image = _star_image()
    column = row = 50.0
    signal_to_noise, clipped, found, neighbors = 500.0, False, True, 0
    if case == "not_star":
        found = False
    elif case == "faint":
        signal_to_noise = 2.0
    elif case == "clipped":
        clipped = True
    elif case == "neighbor":
        neighbors = 1
    elif case == "edge":
        column = 4.0
    elif case == "gaps":
        image[48, 52] = np.nan
    elif case == "fit":
        image = SKY + np.random.default_rng(9).normal(0.0, 2.0, (SIZE, SIZE))
    elongation, _, judgeable, found_note, detail = _judge_core_shape(
        image, column, row, signal_to_noise, clipped, found, neighbors
    )
    assert not judgeable and elongation is None
    assert found_note == note
    assert detail and detail.endswith(".")
