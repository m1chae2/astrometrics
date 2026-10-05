"""Takes a bright neighbour's blurred light out of each star's spectrum.

This is the part of the neighbour-wing stage that knows about the pipeline. The
maths of the blur is in `neighbor_trail_deblending`, which works on plain
arrays. This file does what is left:

* It keeps the camera's stored blur profile, measured once from an isolated
  bright star (`load_cross_trail_blur`), so a pair of stars in a crowded image
  does not have to supply its own.
* It puts every star's streak into one frame where the spectrum always runs
  down the rows in the direction of increasing wavelength, however the camera
  is turned (`WorkingFrame`).
* It finds which stars have a neighbour close enough to matter, fits the
  stored blur to their band profiles, and removes each neighbour's light from
  the box flux (`correct_neighbor_wings`).

The result for every star says what was done ("applied", or "skipped" and
why) and, sample by sample, what share of the box light was taken out, so a
reader can see how large the correction was at each wavelength.

What it does NOT do:

* It leaves a star alone when it has no neighbour within
  `MAXIMUM_NEIGHBOR_DISTANCE_PX` across the streak, when the fit is not
  trustworthy (see `neighbor_trail_deblending`), or when the extraction did not
  record the geometry it needs (the flare-mask extraction).
* Where a streak runs past the wavelengths the stored blur was measured over,
  the blur at the nearest measured wavelength is used.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from astrometricslib.foundation.camera_names import normalize_camera_name
from astrometricslib.pipelines.spectroscopy.pre_processing.neighbor_trail_deblending import (
    EmpiricalBlur,
    NeighborFit,
    build_band_profiles,
    fit_neighbor_amplitudes,
    neighbor_wing_flux,
    subtract_neighbor_wings,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor import APERTURE_SIGMA_MULTIPLIER

_DATA_DIR = Path(__file__).parent.parent / "data"

# A neighbour this far or nearer across the streak, in pixels, is fitted
# together with the star. The stored blur reaches 75 px either side, so a
# neighbour farther than 60 px would put its centre near the edge of what can
# be fitted, and its wing at the star is already tiny.
MAXIMUM_NEIGHBOR_DISTANCE_PX = 60.0

# Two streaks closer than this across the streak are treated as the same star
# (for example a star listed twice), not as a neighbour.
MINIMUM_NEIGHBOR_DISTANCE_PX = 3.0

# The share of a star's samples that a neighbour's streak must run alongside
# for the neighbour to count. A streak that only touches the star's end
# leaves most of the spectrum unaffected.
MINIMUM_OVERLAP_FRACTION = 0.5

# What the status of a star that has no neighbour, and so was not corrected,
# is called. The pipeline records nothing for these stars.
STATUS_NOT_NEEDED = "not needed"
STATUS_APPLIED = "applied"


@dataclass(frozen=True)
class StoredCrossTrailBlur:
    """A camera's stored blur profile and where it came from.

    Attributes
    ----------
    blur : `EmpiricalBlur`
        The measured blur.
    band_edges_px : `numpy.ndarray`
        Where the bands the blur was measured in begin and end, in pixels along
        the streak from the zero-order star. Consecutive bands share an edge.
    camera_name : `str`
        The camera the blur was measured with.
    source : `str`
        A sentence saying what star, night and frames it was measured from.
    focuser_temperature_c : `float` or `None`
        The focuser temperature at the time, in degrees Celsius.
    """

    blur: EmpiricalBlur
    band_edges_px: np.ndarray
    camera_name: str
    source: str
    focuser_temperature_c: float | None


@dataclass(frozen=True)
class StarTrace:
    """What the correction needs to know about one extracted star.

    Attributes
    ----------
    x_px, y_px : `numpy.ndarray`
        Where each sample of the spectrum was measured on the image.
    distance_px : `numpy.ndarray`
        How far along the streak each sample is from the zero-order star.
    box_flux : `numpy.ndarray`
        The box flux at each sample (raw counts, sky already taken out).
    box_half_width_px : `numpy.ndarray`
        Half the width of the box that measured each sample.
    zero_order_x_px, zero_order_y_px : `float`
        Where the extraction placed the zero-order star.
    """

    x_px: np.ndarray
    y_px: np.ndarray
    distance_px: np.ndarray
    box_flux: np.ndarray
    box_half_width_px: np.ndarray
    zero_order_x_px: float
    zero_order_y_px: float


@dataclass(frozen=True)
class NeighborWingOutcome:
    """What the correction did for one star.

    Attributes
    ----------
    status : `str`
        `STATUS_NOT_NEEDED`, `STATUS_APPLIED`, or "skipped: " and the reason.
    corrected_flux : `numpy.ndarray` or `None`
        The box flux with the neighbours' light removed, only when applied.
    wing_fraction : `numpy.ndarray` or `None`
        The share of the box light taken out at each sample, only when applied.
    fit : `NeighborFit` or `None`
        The fit, when one was made.
    """

    status: str
    corrected_flux: np.ndarray | None = None
    wing_fraction: np.ndarray | None = None
    fit: NeighborFit | None = None


@dataclass(frozen=True)
class WorkingFrame:
    """A way of laying the image so spectra run down the rows.

    Attributes
    ----------
    is_transposed : `bool`
        `True` when the spectrum runs left to right, so the image is
        turned on its side.
    is_flipped : `bool`
        `True` when wavelength falls with the row number, so the rows are
        reversed.
    along_length : `int`
        How many rows the laid-out image has.
    """

    is_transposed: bool
    is_flipped: bool
    along_length: int

    def image(self, plane: np.ndarray) -> np.ndarray:
        """Lay the image out in this frame.

        Parameters
        ----------
        plane : `numpy.ndarray`
            The image as it came off the camera.

        Returns
        -------
        image : `numpy.ndarray`
            The image with rows running along the streaks.
        """
        image = plane.T if self.is_transposed else plane
        return image[::-1] if self.is_flipped else image

    def coordinates(self, x_px: np.ndarray, y_px: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Turn image positions into positions along and across the streak.

        Parameters
        ----------
        x_px, y_px : `numpy.ndarray`
            Positions on the image as it came off the camera.

        Returns
        -------
        along_px, cross_px : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
            The row and column in the laid-out image.
        """
        along = np.asarray(x_px if self.is_transposed else y_px, dtype=float)
        cross = np.asarray(y_px if self.is_transposed else x_px, dtype=float)
        if self.is_flipped:
            along = (self.along_length - 1) - along
        return along, cross


def working_frame_for(dispersion_vector: tuple[float, float], plane_shape: tuple[int, int]) -> WorkingFrame:
    """Choose how to lay the image out for a dispersion direction.

    Parameters
    ----------
    dispersion_vector : `tuple` [`float`, `float`]
        The direction the spectrum runs on the image, as (x, y).
    plane_shape : `tuple` [`int`, `int`]
        The image's shape, (rows, columns).

    Returns
    -------
    frame : `WorkingFrame`
        The frame in which the spectrum runs down the rows, wavelength rising.
    """
    vx, vy = float(dispersion_vector[0]), float(dispersion_vector[1])
    is_transposed = abs(vx) > abs(vy)
    step = vx if is_transposed else vy
    return WorkingFrame(
        is_transposed=is_transposed,
        is_flipped=step < 0,
        along_length=plane_shape[1] if is_transposed else plane_shape[0],
    )


def load_cross_trail_blur(camera_name: str) -> StoredCrossTrailBlur | None:
    """Read the stored blur profile for a camera, if one exists.

    Parameters
    ----------
    camera_name : `str`
        The camera's name.

    Returns
    -------
    stored : `StoredCrossTrailBlur` or `None`
        The stored blur, or `None` when none has been derived for this camera.
    """
    wanted = normalize_camera_name(camera_name)
    for path in sorted(_DATA_DIR.glob("cross_trail_blur_*.json")):
        record = json.loads(path.read_text())
        if normalize_camera_name(record["camera_name"]) == wanted:
            edges = np.array(record["band_edges_px"], dtype=float)
            profiles = np.array(record["profiles"], dtype=float)
            blur = EmpiricalBlur(
                offsets_px=np.array(record["offsets_px"], dtype=float),
                profiles=profiles,
                band_positions_px=0.5 * (edges[:-1] + edges[1:]) - 0.5,
            )
            return StoredCrossTrailBlur(
                blur=blur,
                band_edges_px=edges,
                camera_name=record["camera_name"],
                source=record["source"],
                focuser_temperature_c=record.get("focuser_temperature_c"),
            )
    return None


def cross_trail_blur_record(
    blur: EmpiricalBlur,
    band_edges_px: np.ndarray,
    camera_name: str,
    source: str,
    focuser_temperature_c: float | None,
) -> dict[str, object]:
    """Put a measured blur into the form that is stored on disk.

    Parameters
    ----------
    blur : `EmpiricalBlur`
        The blur measured on an isolated star.
    band_edges_px : `numpy.ndarray`
        Where its bands begin and end, along the streak from the zero order.
    camera_name : `str`
        The camera the blur was measured with.
    source : `str`
        A sentence saying what star, night and frames it was measured from.
    focuser_temperature_c : `float` or `None`
        The focuser temperature at the time, in degrees Celsius.

    Returns
    -------
    record : `dict`
        A record that `json.dumps` can write and `load_cross_trail_blur` reads.
    """
    return {
        "camera_name": camera_name,
        "source": source,
        "focuser_temperature_c": focuser_temperature_c,
        "band_edges_px": [float(edge) for edge in band_edges_px],
        "offsets_px": [float(offset) for offset in blur.offsets_px],
        "profiles": [[round(float(value), 8) for value in row] for row in blur.profiles],
    }


def box_half_widths_px(
    trail_width_px: list[float] | None, extraction_radius: int, sample_count: int
) -> np.ndarray:
    """Work out half the width of the box that measured each sample.

    The traced extraction reads a box of `round(2.5 x trail width)` pixels
    either side of the trail's centre (at least 1), and falls back to the
    configured radius where the trail was not found (a width of 0). The
    untraced extraction always reads the configured radius. A box of `r` pixels
    either side is `2r + 1` pixels wide, so its edges are `r + 0.5` from its
    centre.

    Parameters
    ----------
    trail_width_px : `list` [`float`] or `None`
        The trail width at each sample, or `None` for an untraced extraction.
    extraction_radius : `int`
        The configured box radius, in pixels.
    sample_count : `int`
        How many samples there are.

    Returns
    -------
    half_widths : `numpy.ndarray`
        Half the box width at each sample, in pixels.
    """
    if trail_width_px is None:
        return np.full(sample_count, extraction_radius + 0.5)
    widths = np.asarray(trail_width_px, dtype=float)
    radii = np.where(
        widths > 0, np.maximum(1.0, np.round(widths * APERTURE_SIGMA_MULTIPLIER)), extraction_radius
    )
    return radii + 0.5


def star_trace_from_result(result: dict[str, Any]) -> StarTrace | None:
    """Build a star's trace from its extraction result.

    Parameters
    ----------
    result : `dict`
        One star's result from `SpectroscopyPipeline._process_single_star`.

    Returns
    -------
    trace : `StarTrace` or `None`
        The trace, or `None` when the result does not carry the geometry the
        extraction used (for example the flare-mask extraction).
    """
    required = ("distances_from_zero_order_px", "base_position_px", "dispersion_vector", "intensities")
    if any(result.get(key) is None for key in required):
        return None
    distances = np.asarray(result["distances_from_zero_order_px"], dtype=float)
    intensities = np.asarray(result["intensities"], dtype=float)
    base_x, base_y = (float(value) for value in result["base_position_px"])
    vx, vy = (float(value) for value in result["dispersion_vector"])
    centreline = result.get("trail_centerline_px")
    offsets = np.zeros(distances.size) if centreline is None else np.asarray(centreline, dtype=float)
    # The traced extraction moves each box off the nominal line along the
    # perpendicular (-vy, vx) by the centreline offset.
    x_px = base_x + distances * vx - vy * offsets
    y_px = base_y + distances * vy + vx * offsets
    return StarTrace(
        x_px=x_px,
        y_px=y_px,
        distance_px=distances,
        box_flux=intensities,
        box_half_width_px=box_half_widths_px(
            result.get("trail_width_px"),
            int(result.get("extraction_radius") or 0),
            distances.size,
        ),
        zero_order_x_px=base_x,
        zero_order_y_px=base_y,
    )


def correct_neighbor_wings(
    plane: np.ndarray,
    traces: list[StarTrace | None],
    stored: StoredCrossTrailBlur | None,
    dispersion_vector: tuple[float, float],
) -> list[NeighborWingOutcome]:
    """Take each star's neighbours' blurred light out of its box flux.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The image the spectra were extracted from.
    traces : `list` [`StarTrace` or `None`]
        One trace per extracted star, in a fixed order; `None` for a star
        whose geometry is not known.
    stored : `StoredCrossTrailBlur` or `None`
        The camera's stored blur, or `None` when there is none.
    dispersion_vector : `tuple` [`float`, `float`]
        The direction the spectra run on the image, as (x, y). Used to lay
        the image out with wavelength rising down the rows.

    Returns
    -------
    outcomes : `list` [`NeighborWingOutcome`]
        One outcome per trace, in the same order.
    """
    frame = working_frame_for(dispersion_vector, plane.shape)
    working_image = frame.image(plane)
    placed = [_place_trace(frame, trace) for trace in traces]
    outcomes = []
    for target_index, target in enumerate(placed):
        if target is None:
            outcomes.append(NeighborWingOutcome("skipped: the extraction did not record its geometry"))
            continue
        neighbors = []
        for other_index, other in enumerate(placed):
            if other_index == target_index or other is None:
                continue
            cross_at_target = np.interp(
                target.along_px, other.along_px, other.cross_px, left=np.nan, right=np.nan
            )
            offset = cross_at_target - target.cross_px
            covered = np.isfinite(offset)
            if covered.mean() < MINIMUM_OVERLAP_FRACTION:
                continue
            median_offset = float(np.median(offset[covered]))
            if MINIMUM_NEIGHBOR_DISTANCE_PX <= abs(median_offset) <= MAXIMUM_NEIGHBOR_DISTANCE_PX:
                neighbors.append((other, offset))
        if not neighbors:
            outcomes.append(NeighborWingOutcome(STATUS_NOT_NEEDED))
        elif stored is None:
            outcomes.append(NeighborWingOutcome("skipped: no blur profile is stored for this camera"))
        else:
            outcomes.append(_correct_one_star(working_image, target, neighbors, stored))
    return outcomes


@dataclass(frozen=True)
class _PlacedTrace:
    """A star's trace, with its samples placed in the laid-out image.

    Attributes
    ----------
    trace : `StarTrace`
        The star's trace.
    along_px, cross_px : `numpy.ndarray`
        Each sample's row and column in the laid-out image.
    zero_order_along_px : `float`
        The row of the zero-order star in the laid-out image.
    """

    trace: StarTrace
    along_px: np.ndarray
    cross_px: np.ndarray
    zero_order_along_px: float


def _place_trace(frame: WorkingFrame, trace: StarTrace | None) -> _PlacedTrace | None:
    """Put a trace's samples into the laid-out image.

    Parameters
    ----------
    frame : `WorkingFrame`
        How the image was laid out.
    trace : `StarTrace` or `None`
        The trace, or `None` when the star has no known geometry.

    Returns
    -------
    placed : `_PlacedTrace` or `None`
        The placed trace, or `None` when there was no trace.
    """
    if trace is None:
        return None
    along, cross = frame.coordinates(trace.x_px, trace.y_px)
    zero_along, _ = frame.coordinates(np.array([trace.zero_order_x_px]), np.array([trace.zero_order_y_px]))
    return _PlacedTrace(trace, along, cross, float(zero_along[0]))


def _correct_one_star(
    working_image: np.ndarray,
    target: _PlacedTrace,
    neighbors: list[tuple[_PlacedTrace, np.ndarray]],
    stored: StoredCrossTrailBlur,
) -> NeighborWingOutcome:
    """Fit the stored blur to one star and its neighbours; subtract them.

    Parameters
    ----------
    working_image : `numpy.ndarray`
        The image laid out with the spectra running down the rows.
    target : `_PlacedTrace`
        The star to correct.
    neighbors : `list` [`tuple` [`_PlacedTrace`, `numpy.ndarray`]]
        The neighbouring stars, each with its cross-streak offset from the
        star at every sample.
    stored : `StoredCrossTrailBlur`
        The stored blur.

    Returns
    -------
    outcome : `NeighborWingOutcome`
        What was done.
    """
    edges = np.round(target.zero_order_along_px + stored.band_edges_px).astype(int)
    half_width = int(stored.blur.offsets_px[-1])
    if edges[0] < 0 or edges[-1] > working_image.shape[0]:
        return NeighborWingOutcome("skipped: the blur's bands fall off the image")

    rows = np.arange(working_image.shape[0], dtype=float)
    reference_cross = np.interp(rows, target.along_px, target.cross_px)
    band_cross = reference_cross[edges[0] : edges[-1]]
    if band_cross.min() - half_width < 0 or band_cross.max() + half_width >= working_image.shape[1]:
        return NeighborWingOutcome("skipped: too close to the edge of the image to fit the blur")

    offsets, profiles, band_positions = build_band_profiles(
        working_image, True, reference_cross, edges, target.zero_order_along_px, half_width_px=half_width
    )
    outer = np.concatenate([profiles[:, :10], profiles[:, -10:]], axis=1)
    noise_floor = max(1.4826 * float(np.median(np.abs(outer - np.median(outer)))), 1e-9)
    star_offsets = np.array([0.0, *[float(np.nanmedian(offset)) for _, offset in neighbors]])
    fit = fit_neighbor_amplitudes(stored.blur, offsets, profiles, band_positions, star_offsets, noise_floor)
    if not fit.is_reliable:
        return NeighborWingOutcome(
            f"skipped: the blur did not fit well enough ({100 * fit.relative_residual:.1f}% of the peak "
            f"left over, stretch {fit.dilation:.2f})",
            fit=fit,
        )

    wing_fluxes = []
    for other, offset in neighbors:
        neighbor_trace = other.trace
        neighbor_flux = np.interp(
            target.along_px, other.along_px, neighbor_trace.box_flux, left=0.0, right=0.0
        )
        neighbor_half_width = np.interp(
            target.along_px,
            other.along_px,
            neighbor_trace.box_half_width_px,
            left=neighbor_trace.box_half_width_px[0],
            right=neighbor_trace.box_half_width_px[-1],
        )
        wing_fluxes.append(
            neighbor_wing_flux(
                stored.blur,
                fit.dilation,
                target.trace.distance_px,
                np.nan_to_num(neighbor_flux),
                neighbor_half_width,
                np.where(np.isfinite(offset), offset, 0.0),
                target.trace.box_half_width_px,
                np.zeros(target.trace.distance_px.size),
            )
        )
    corrected, fraction = subtract_neighbor_wings(target.trace.box_flux, wing_fluxes)
    return NeighborWingOutcome(STATUS_APPLIED, corrected_flux=corrected, wing_fraction=fraction, fit=fit)
