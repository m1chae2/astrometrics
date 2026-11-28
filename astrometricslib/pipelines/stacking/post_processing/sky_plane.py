"""Chooses between a flat sky tilt and GraXpert, and removes the tilt.

GraXpert's AI model of the sky background can take the faint outer glow of a
large nebula for sky. It then subtracts a hump around the nebula and leaves a
dark moat in the picture (measured on NGC 2244; see `STK-61` in the
validation register). Most stacks do not need such a model. Their sky is
flat, or tilted by a small amount that a plane describes completely, and a
plane cannot make a ring.

This module decides, before GraXpert runs, which of the two the stack gets.

1. The stack is cut into the same 64 by 64 grid of bin medians that
   `background_gradient` uses. A median ignores the few pixels of each star.
2. The bins that are robustly sky are picked. A plane is fitted to all of the
   bins, and the bins that sit well above it are dropped, because a bright
   object or a nebula lifts them. The fit and the drop are repeated until the
   same bins stay. The spread used for "well above" comes from the bins
   below the plane only, since the object cannot make a bin dimmer. The bin
   medians and the clipped fit are `pipelines/shared/binned_sky_plane.py`,
   which the exposure-group sky matching also uses (with clipping on both
   sides).
3. The sky bins are measured with `measure_sky_background`. The same
   numbers (level, spread, tilt, round term, residuals) are stored as the
   stack's background measurements, whichever method is chosen
   (`BackgroundDecision.sky_background`; a color stack's stored numbers are
   those of its planes' mean). The tilt (the change of the fitted plane
   across the frame, as a fraction of the sky level) is the gradient of the
   stack. A color stack takes the largest tilt of its planes.
4. A gradient below `SKY_PLANE_GRADIENT_LIMIT` takes the plane path: a plane
   is fitted to the sky bins of each color plane and subtracted. The level of
   the sky is kept, so the picture's black point does not move. A gradient at
   or above the limit, or a stack that could not be measured, takes the
   GraXpert path as before.

5. The same sky bins can also show a slow darkening toward the corners, the
   vignetting that the flat frames did not remove completely. A plane cannot
   follow it, so a radial part is fitted too: ``d2 * r^2 + d4 * r^4``, where
   ``r`` is the distance from the frame center in units of the half-width.
   Both parts are the same in every direction, so they cannot follow an
   off-center or ragged object, and both must make the corners darker (never
   brighter). Of the three shapes (``r^2``, ``r^4``, both) the one that
   leaves the least misfit is used. The radial part is used only when the sky
   bins cover the outer rings of the frame, when all four quarters of the
   frame agree that the corners are not brighter, and when its size at the
   corners is no larger than `RADIAL_FALLOFF_LIMIT`. Otherwise it is left
   out, and the plane is subtracted alone. Measured on 13 library stacks
   (`STK-61`): the falloff at the corners is 0.34% to 0.94% of the sky level
   on the galaxy and cluster fields, and 1.5% to 5.1% on the three fields
   where a nebula or a big galaxy fills the frame and the "sky" bins are
   really its glow.

6. Vignetting is nearly the same in every color channel. If the radial
   change at the corners differs between the channels by more than
   `RADIAL_FALLOFF_LIMIT`, the sky has a color gradient that is not
   vignetting (one channel brightening toward the corners, as on the NGC 7023
   Nikon stack, where red rises by 20% while green falls by 6%). Neither a
   plane nor the one radial shape can follow it, and the luminance mask that
   picks the sky bins also drops the bright corners. Such a stack takes the
   GraXpert path, which models each channel by itself. A monochrome stack has
   one channel, so this never applies to it.

The stack file is never changed. The plane (and the radial part, when it is
used) is subtracted from the scratch copy that the preview works on.
"""

import logging
import math
from dataclasses import dataclass, field

import numpy as np
from astropy.io import fits

from astrometricslib._internal.drivers.fits_access import read_data, read_header, write_image
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.binned_sky_plane import (
    block_medians,
    even_block_edges,
    fit_clipped_plane,
    least_squares_fit,
)
from astrometricslib.pipelines.stacking.post_processing.background_gradient import (
    BACKGROUND_BIN_COUNT,
    BackgroundGradientOutcome,
    crop_cards_of,
    measure_sky_background,
    not_measured_outcome,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RADIAL_FALLOFF_LIMIT",
    "SKY_PLANE_GRADIENT_LIMIT",
    "BackgroundDecision",
    "choose_background_method",
    "choose_background_method_for_file",
    "subtract_sky_plane_in_file",
]

# A stack whose sky tilts by less than this share of its own sky level, from
# one side of the frame to the other, gets a fitted plane instead of GraXpert.
# On 38 library stacks (2026-11-23) the mono-camera deep-sky stacks read 0.2%
# to 1.7% and GraXpert's own models of the well-behaved ones were planes of the
# same size (the model's departure from a plane was 0.1% to 0.3%). The stacks
# where GraXpert took the target for sky (NGC 2244, M 27, M 57, NGC 2903)
# also read 0.5% to 1.7%, so no stack needed more than a plane to be flat.
# Stacks with a strong tilt, from a bright star in the frame or a town's glow,
# read 3.5% and more (register entry STK-61). 3% is above the flat stacks and
# below the glowing ones, so a stack with a real gradient still gets GraXpert.
SKY_PLANE_GRADIENT_LIMIT = TunedParameter(
    name="stacking.sky_plane_gradient_limit",
    default=0.03,
    unit="fraction of the sky level",
    description="Largest sky tilt, across the frame, for which a fitted plane replaces GraXpert.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-61",
)

# The largest radial falloff the preview will take as vignetting, as a share
# of the sky level. It is the size of the fitted radial part, d2 * r^2 +
# d4 * r^4, at the frame corners (r^2 = 2), where it is largest. Measured on
# the 13 stacks of STK-61 (2026-11-23): galaxy, cluster and small-nebula
# fields read 0.34% to 0.94% (M 81 0.34, M 101 0.40, M 13 0.40, NGC 4438
# 0.52, NGC 7331 0.52, NGC 1893 0.54, NGC 2903 0.56, M 52 0.64, NGC 2403
# 0.74, M 45 0.94). The fields where a nebula or a large galaxy fills the
# frame read 1.5% to 5.1% (M 31 1.5, M 42 1.7, NGC 2244 5.1): their sky bins
# hold the object's own glow, which also gets fainter toward the edges. 1.2%
# lies between the two groups. A fit above it is not applied at all (it is
# not cut back), so a glow can never be taken for vignetting.
RADIAL_FALLOFF_LIMIT = TunedParameter(
    name="stacking.sky_radial_falloff_limit",
    default=0.012,
    unit="fraction of the sky level",
    description="Largest radial falloff (at the frame corners) taken as vignetting and removed.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-61",
)

# A fitted falloff smaller than this share of the sky level, at the corners,
# is left out: the bin medians scatter by about 0.1% to 0.3% (the rms of the
# 13 stacks of STK-61 about their fits), so a smaller falloff cannot be told
# from noise, and leaving it out keeps a flat sky exactly as the plane left
# it. The smallest falloff measured is 0.34%. Not tuned (STK-61).
MINIMUM_RADIAL_FALLOFF = 0.002

# Each of the two outer rings of bins (r^2 from 0.64 to 1, and from 1 to 2,
# with r in half-widths) must be at least this share sky, among the bins that
# hold pixels. The falloff is read mostly from those rings, so a frame whose
# outer rings are nebula or galaxy cannot give it. The 13 stacks of STK-61
# read 0.70 to 1.00 in both rings (lowest: M 31 0.70, M 45 0.71, M 42 0.79).
# 0.6 is a plain majority with a little margin; not tuned (STK-61).
MINIMUM_RING_SKY_SHARE = 0.6

# The two outer rings used by `MINIMUM_RING_SKY_SHARE`, as ranges of r^2.
_OUTER_RING_RANGES = ((0.64, 1.0), (1.0, 2.0))

# Each quarter of the frame must hold at least this many sky bins (of 1024)
# for its own check of the falloff's sign. Plain sample-size guard (STK-61).
MINIMUM_QUARTER_SKY_BINS = 100

# A quarter of the frame may show its own ``r^2`` term this much above zero
# (rising toward its corner), as a share of the sky level, before the radial
# part is refused. Noise alone gives about 0.1% on the library stacks (the
# quarter values of the 13 stacks ranged from -1.24% to +0.06%). Not tuned
# (STK-61).
QUARTER_RISE_TOLERANCE = 0.001

# Bins more than this many spreads above the fitted plane are not sky. The
# spread is measured on the bins below the plane, so a nebula cannot widen
# it. 2 keeps about 98% of a clean Gaussian sky and drops a bright object's
# bins (STK-61).
SKY_CLIP_SPREADS = 2.0

# The fit and the drop of bright bins are repeated at most this many times.
# On the library's stacks the kept bins stopped changing after 4 to 6 rounds.
SKY_CLIP_ROUNDS = 8

# At least this share of the measurable bins must stay as sky. Below it the
# clip is treated as having gone wrong (a stack that is nearly all object) and
# the last good set is kept. A plain minority guard; not tuned (STK-61).
MINIMUM_SKY_BIN_SHARE = 0.3


@dataclass(frozen=True)
class BackgroundDecision:
    """Which background removal a stack gets, and the numbers behind it.

    Attributes
    ----------
    method : `str`
        ``"plane"`` when a fitted plane is subtracted, ``"graxpert"`` when
        GraXpert runs.
    gradient_fraction : `float` or `None`
        The sky tilt across the frame as a fraction of the sky level, or
        `None` when the stack could not be measured.
    limit_fraction : `float`
        The limit the gradient was compared with.
    sky_bin_count : `int`
        How many of the 64 by 64 bins were taken as sky.
    reason : `str`
        A plain sentence for the record. For a stack that could not be
        measured, it says why GraXpert is used.
    plane_coefficients : `list` [`tuple` [`float`, `float`]]
        For each color plane, the tilt terms of the fitted plane: the change
        from the center to the right and to the bottom edge, in the stack's
        units. Empty on the GraXpert path.
    radial_coefficients : `list` [`tuple` [`float`, `float`]]
        For each color plane, the coefficients ``(d2, d4)`` of the radial part
        ``d2 * r^2 + d4 * r^4`` in the stack's units (both zero or negative,
        for vignetting), or empty when the radial part is not used.
    radial_fraction : `float` or `None`
        The radial part at the frame corners as a fraction of the sky level
        (negative; the mean over the color planes), or `None` when it was
        not fitted.
    radial_reason : `str`
        A plain sentence saying why the radial part was used or left out.
        Empty on the GraXpert path.
    sky_background : `BackgroundGradientOutcome`
        The stack's own sky bins, measured (level, spread, tilt, round term,
        residuals, and the 64 by 64 map), or the reason they could not be.
        It is there on both paths, so every stack that gets a preview has its
        background measured.
    """

    method: str
    gradient_fraction: float | None
    limit_fraction: float
    sky_bin_count: int
    reason: str
    plane_coefficients: list[tuple[float, float]]
    radial_coefficients: list[tuple[float, float]] = field(default_factory=list)
    radial_fraction: float | None = None
    radial_reason: str = ""
    sky_background: BackgroundGradientOutcome = field(
        default_factory=lambda: not_measured_outcome("the sky was not measured")
    )

    @property
    def radial_used(self) -> bool:
        """Say whether the radial part is subtracted.

        Returns
        -------
        used : `bool`
            `True` when there is a radial coefficient for each color plane.
        """
        return bool(self.radial_coefficients)

    def record(self) -> dict[str, object]:
        """Give the decision as a plain dictionary for the preview record.

        Returns
        -------
        record : `dict` [`str`, `object`]
            The method, the gradient, the limit, the number of sky bins and
            the reason, as numbers and text a JSON file can hold. On the
            plane path it also holds whether the radial part was used, its
            size at the frame corners as a fraction of the sky level (or
            `None`) and the reason.
        """
        record: dict[str, object] = {
            "method": self.method,
            "gradient_fraction": self.gradient_fraction,
            "limit_fraction": self.limit_fraction,
            "sky_bins": self.sky_bin_count,
            "reason": self.reason,
        }
        if self.method == "plane":
            record["radial_used"] = self.radial_used
            record["radial_fraction"] = self.radial_fraction
            record["radial_reason"] = self.radial_reason
        return record

    def step_sentence(self) -> str:
        """Say which background step ran, for the list of preview steps.

        Returns
        -------
        sentence : `str`
            "Background: plane fit (gradient 0.8%, limit 3.0%)" for the plane
            path, with ", radial falloff 0.3% removed" when the radial part
            was used. For the GraXpert path, the sentence of the step itself is
            added by the caller, so this returns the empty string.
        """
        if self.method != "plane" or self.gradient_fraction is None:
            return ""
        sentence = (
            f"Background: plane fit (gradient {100 * self.gradient_fraction:.1f}%, "
            f"limit {100 * self.limit_fraction:.1f}%)"
        )
        if self.radial_used and self.radial_fraction is not None:
            sentence += f", radial falloff {100 * abs(self.radial_fraction):.2f}% removed"
        return sentence


def _color_planes(data: np.ndarray) -> list[np.ndarray] | None:
    """Split a stack into two-dimensional planes.

    Parameters
    ----------
    data : `numpy.ndarray`
        The stack: two-dimensional, or color planes in front.

    Returns
    -------
    planes : `list` [`numpy.ndarray`] or `None`
        One two-dimensional array of floating-point numbers for each plane,
        or `None` when the array is not an image.
    """
    array = np.squeeze(np.asarray(data))
    if array.ndim == 2:
        planes = [array]
    elif array.ndim == 3 and array.shape[0] <= 4:
        planes = list(array)
    else:
        return None
    if min(planes[0].shape) < BACKGROUND_BIN_COUNT:
        return None
    return [np.asarray(plane, dtype=np.float64) for plane in planes]


def _bin_medians(plane: np.ndarray) -> np.ndarray:
    """Cut a plane into the 64 by 64 grid and take the median of each bin.

    Parameters
    ----------
    plane : `numpy.ndarray`
        One color plane.

    Returns
    -------
    binned : `numpy.ndarray`
        The 64 by 64 map. Empty pixels (zero or not a number, as at a blank
        border) are left out of each median, and a bin with none holds NaN.
    """
    return block_medians(
        np.where(np.isfinite(plane) & (plane > 0.0), plane, np.nan),
        even_block_edges(plane.shape[0], BACKGROUND_BIN_COUNT),
        even_block_edges(plane.shape[1], BACKGROUND_BIN_COUNT),
    )


def _bin_coordinates() -> tuple[np.ndarray, np.ndarray]:
    """Give the centers of the bins on the fit's -1 to +1 scale.

    Returns
    -------
    x, y : `numpy.ndarray`
        The column and row coordinate of each bin, as 64 by 64 arrays.
    """
    rows, columns = np.indices((BACKGROUND_BIN_COUNT, BACKGROUND_BIN_COUNT))
    return (
        (columns + 0.5) / BACKGROUND_BIN_COUNT * 2.0 - 1.0,
        (rows + 0.5) / BACKGROUND_BIN_COUNT * 2.0 - 1.0,
    )


def select_sky_bins(binned: np.ndarray) -> np.ndarray:
    """Pick the bins that are robustly sky.

    A plane is fitted to the kept bins and the bins more than
    `SKY_CLIP_SPREADS` spreads above it are dropped, over and over until the
    set stops changing. Bins below the plane are kept: only a bright object
    makes a bin brighter than the sky, and a dark lane that lowers a bin is
    left to the fit.

    Parameters
    ----------
    binned : `numpy.ndarray`
        The 64 by 64 map of bin medians, with NaN for an empty bin.

    Returns
    -------
    sky : `numpy.ndarray`
        A boolean 64 by 64 map, `True` for each bin taken as sky. Empty bins
        are `False`.
    """
    measurable = np.isfinite(binned)
    x, y = _bin_coordinates()
    _plane, kept = fit_clipped_plane(
        x[measurable],
        y[measurable],
        binned[measurable],
        side="above",
        clip_sigmas=SKY_CLIP_SPREADS,
        rounds=SKY_CLIP_ROUNDS,
        minimum_kept=MINIMUM_SKY_BIN_SHARE * int(measurable.sum()),
    )
    sky = np.zeros_like(measurable)
    sky[measurable] = kept
    return sky


def _plane_terms(binned: np.ndarray, sky: np.ndarray) -> tuple[float, float, float]:
    """Fit a plane to the sky bins of one color plane.

    Parameters
    ----------
    binned : `numpy.ndarray`
        The 64 by 64 map of bin medians.
    sky : `numpy.ndarray`
        The bins taken as sky.

    Returns
    -------
    level, column_term, row_term : `float`
        The plane's value at the center of the frame and its change from the
        center to the right and to the bottom edge.
    """
    use = sky & np.isfinite(binned)
    x, y = _bin_coordinates()
    coefficients, _rms = least_squares_fit([np.ones(int(use.sum())), x[use], y[use]], binned[use])
    return float(coefficients[0]), float(coefficients[1]), float(coefficients[2])


def _radial_fit(
    binned_planes: list[np.ndarray], sky: np.ndarray
) -> tuple[list[tuple[float, float]], list[float], str]:
    """Fit the radial part ``d2 * r^2 + d4 * r^4`` and decide if to use it.

    A plane plus a radial part is fitted to the sky bins of each color plane.
    Three shapes are tried (``r^2`` alone, ``r^4`` alone, both), and only the
    ones whose terms all make the corners darker are kept. The kept shape
    with the smallest misfit wins. Then the checks listed in the module text
    are applied: the sky covers the outer rings, each quarter of the frame
    has enough sky bins and does not brighten toward its corner, and the
    falloff at the corners is not larger than `RADIAL_FALLOFF_LIMIT`. If any
    color plane fails a check, no plane gets the radial part (vignetting is
    nearly the same in every channel).

    Parameters
    ----------
    binned_planes : `list` [`numpy.ndarray`]
        The 64 by 64 map of bin medians of each color plane.
    sky : `numpy.ndarray`
        The bins taken as sky.

    Returns
    -------
    coefficients : `list` [`tuple` [`float`, `float`]]
        The ``(d2, d4)`` of each color plane, or an empty list when the radial
        part is not used.
    fractions : `list` [`float`]
        The falloff at the corners as a fraction of each plane's sky level
        (negative), empty when no fit could be made.
    reason : `str`
        A plain sentence saying why the radial part was used or left out.
    """
    x, y = _bin_coordinates()
    radius_squared = x * x + y * y
    limit = float(resolve(RADIAL_FALLOFF_LIMIT).value)
    measurable = np.isfinite(binned_planes[0])
    for low, high in _OUTER_RING_RANGES:
        ring = measurable & (radius_squared >= low) & (radius_squared < high)
        share = float((sky & ring).sum()) / max(int(ring.sum()), 1)
        if share < MINIMUM_RING_SKY_SHARE:
            return (
                [],
                [],
                f"the sky covers only {100 * share:.0f}% of the outer ring at r^2 {low:g} to {high:g}, "
                f"under {100 * MINIMUM_RING_SKY_SHARE:.0f}%, so the radial part is not fitted",
            )
    coefficients: list[tuple[float, float]] = []
    fractions: list[float] = []
    for binned in binned_planes:
        use = sky & np.isfinite(binned)
        plane_columns = [np.ones(int(use.sum())), x[use], y[use]]
        best: tuple[float, np.ndarray, tuple[float, float]] | None = None
        for powers in ((1,), (2,), (1, 2)):
            terms = [radius_squared[use] ** power for power in powers]
            solution, rms = least_squares_fit([*plane_columns, *terms], binned[use])
            if np.any(solution[3:] > 0.0) or (best is not None and rms >= best[0]):
                continue
            radial = dict(zip(powers, solution[3:], strict=True))
            best = (rms, solution, (float(radial.get(1, 0.0)), float(radial.get(2, 0.0))))
        if best is None:
            return (
                [],
                fractions,
                "no radial shape darkens the corners, so the sky has no vignetting to remove",
            )
        _rms, solution, (second_power, fourth_power) = best
        level = float(solution[0])
        if not math.isfinite(level) or level <= 0.0:
            return [], fractions, "the sky level of the fit is not above zero, so the radial part is not used"
        fraction = (2.0 * second_power + 4.0 * fourth_power) / level
        fractions.append(fraction)
        residual = binned - (solution[0] + solution[1] * x + solution[2] * y)
        for column_sign in (-1.0, 1.0):
            for row_sign in (-1.0, 1.0):
                quarter = use & (np.sign(x) == column_sign) & (np.sign(y) == row_sign)
                if int(quarter.sum()) < MINIMUM_QUARTER_SKY_BINS:
                    return [], fractions, "a quarter of the frame has too few sky bins to check the falloff"
                quarter_fit, _quarter_rms = least_squares_fit(
                    [np.ones(int(quarter.sum())), radius_squared[quarter]], residual[quarter]
                )
                if quarter_fit[1] / level > QUARTER_RISE_TOLERANCE:
                    return (
                        [],
                        fractions,
                        "a quarter of the frame brightens toward its corner, which is not vignetting",
                    )
        if -fraction < MINIMUM_RADIAL_FALLOFF:
            return (
                [],
                fractions,
                f"the fitted falloff of {100 * -fraction:.2f}% at the corners is under "
                f"{100 * MINIMUM_RADIAL_FALLOFF:.1f}%, which the bin noise can make, so it is left out",
            )
        if -fraction > limit:
            return (
                [],
                fractions,
                f"the fitted falloff of {100 * -fraction:.2f}% at the corners is above the "
                f"{100 * limit:.1f}% limit, so it is more likely an object's glow than vignetting",
            )
        coefficients.append((second_power, fourth_power))
    mean_fraction = float(np.mean(fractions))
    return (
        coefficients,
        fractions,
        f"the sky falls toward the corners by {100 * -mean_fraction:.2f}%, within the vignetting limit",
    )


def _channel_radial_spread(binned_planes: list[np.ndarray], sky: np.ndarray) -> float:
    """Measure how much the corner change differs between channels.

    A plane plus ``d2 * r^2 + d4 * r^4`` is fitted to the sky bins of each
    color plane, with no limit on the sign of the radial terms. The value at
    the frame corners (``r^2 = 2``) is taken as a fraction of the plane's
    level at the center.

    Parameters
    ----------
    binned_planes : `list` [`numpy.ndarray`]
        The 64 by 64 map of bin medians of each color plane.
    sky : `numpy.ndarray`
        The bins taken as sky.

    Returns
    -------
    spread : `float`
        The largest minus the smallest corner fraction over the color planes.
        Zero for one plane, and also zero when a plane has no usable fit (the
        other checks handle that case).
    """
    if len(binned_planes) < 2:
        return 0.0
    x, y = _bin_coordinates()
    radius_squared = x * x + y * y
    fractions: list[float] = []
    for binned in binned_planes:
        use = sky & np.isfinite(binned)
        if int(use.sum()) < MINIMUM_QUARTER_SKY_BINS:
            return 0.0
        solution, _rms = least_squares_fit(
            [np.ones(int(use.sum())), x[use], y[use], radius_squared[use], radius_squared[use] ** 2],
            binned[use],
        )
        level = float(solution[0])
        if not math.isfinite(level) or level <= 0.0:
            return 0.0
        fractions.append((2.0 * float(solution[3]) + 4.0 * float(solution[4])) / level)
    return max(fractions) - min(fractions)


def choose_background_method(data: np.ndarray, header: fits.Header | None = None) -> BackgroundDecision:
    """Measure a stack's sky tilt and choose its background removal.

    Parameters
    ----------
    data : `numpy.ndarray`
        The stack as the preview sees it: two-dimensional, or color planes in
        front. Its pixels are not changed.
    header : `astropy.io.fits.Header`, optional
        The stack's header. Its crop cards go with the saved sky map, and its
        sky coordinates give the tilt's position angle.

    Returns
    -------
    decision : `BackgroundDecision`
        ``"plane"`` with the plane of each color plane when the gradient is
        below the limit. ``"graxpert"`` when it is not, and also when the
        stack cannot be measured (too few sky bins, no sky level above zero,
        not an image), and when the radial change at the corners differs
        between the color channels by more than `RADIAL_FALLOFF_LIMIT`.
        GraXpert is the method that was used before, so a stack that cannot
        be judged keeps it. The decision carries the stack's sky measurement
        on both paths. This never raises.
    """
    limit = float(resolve(SKY_PLANE_GRADIENT_LIMIT).value)
    sky_background: BackgroundGradientOutcome | None = None

    def graxpert(reason: str, gradient: float | None = None, sky_bins: int = 0) -> BackgroundDecision:
        """Build the decision for the GraXpert path.

        Returns
        -------
        decision : `BackgroundDecision`
            The decision, with no plane.
        """
        return BackgroundDecision(
            "graxpert",
            gradient,
            limit,
            sky_bins,
            reason,
            [],
            sky_background=sky_background or not_measured_outcome(reason),
        )

    planes = _color_planes(data)
    if planes is None:
        return graxpert("the stack is not an image the sky tilt can be measured on")
    binned_planes = [_bin_medians(plane) for plane in planes]
    luminance = np.nanmean(np.stack(binned_planes), axis=0) if len(planes) > 1 else binned_planes[0]
    if not np.isfinite(luminance).any():
        return graxpert("the stack has no pixel above zero")
    sky = select_sky_bins(luminance)
    measured_sky = measure_sky_background(
        np.where(sky, luminance, np.nan), header, picture_shape=(planes[0].shape[0], planes[0].shape[1])
    )
    sky_background = BackgroundGradientOutcome(
        gradient=measured_sky.gradient,
        reason=measured_sky.reason,
        binned_sky=measured_sky.binned_sky,
        crop_cards={} if header is None else crop_cards_of(header),
    )
    tilts: list[float] = []
    for binned in binned_planes:
        measured = measure_sky_background(np.where(sky, binned, np.nan)).gradient
        if measured is None:
            return graxpert("the sky bins of the stack could not be measured")
        tilts.append(measured.tilt_fraction)
    gradient = max(tilts)
    if not math.isfinite(gradient):
        return graxpert("the sky tilt of the stack is not a number")
    sky_bins = int(sky.sum())
    if gradient >= limit:
        return graxpert(
            f"the sky tilts by {100 * gradient:.1f}%, at or above the {100 * limit:.1f}% limit for a plane",
            gradient,
            sky_bins,
        )
    channel_spread = _channel_radial_spread(binned_planes, sky)
    if channel_spread > float(resolve(RADIAL_FALLOFF_LIMIT).value):
        return graxpert(
            f"the color channels change by {100 * channel_spread:.1f}% differently toward the corners, "
            "more than vignetting does, which a plane cannot follow",
            gradient,
            sky_bins,
        )
    terms = [_plane_terms(binned, sky) for binned in binned_planes]
    radial_coefficients, radial_fractions, radial_reason = _radial_fit(binned_planes, sky)
    return BackgroundDecision(
        "plane",
        gradient,
        limit,
        sky_bins,
        f"the sky tilts by {100 * gradient:.1f}%, below the {100 * limit:.1f}% limit, so a plane is fitted",
        [(column_term, row_term) for _level, column_term, row_term in terms],
        radial_coefficients,
        float(np.mean(radial_fractions)) if radial_fractions else None,
        radial_reason,
        sky_background,
    )


def choose_background_method_for_file(path: str) -> BackgroundDecision:
    """Choose the background removal for a stack file, keeping any failure in.

    Parameters
    ----------
    path : `str`
        The FITS stack (the scratch copy the preview works on).

    Returns
    -------
    decision : `BackgroundDecision`
        The decision. A file that cannot be read gives the GraXpert path,
        with the reason, and a warning in the log.
    """
    limit = float(resolve(SKY_PLANE_GRADIENT_LIMIT).value)

    def unreadable(reason: str) -> BackgroundDecision:
        """Build the GraXpert decision for a stack that cannot be measured.

        Returns
        -------
        decision : `BackgroundDecision`
            The decision, with the reason as the sky measurement's reason.
        """
        return BackgroundDecision(
            "graxpert", None, limit, 0, reason, [], sky_background=not_measured_outcome(reason)
        )

    try:
        header = read_header(path)
        data = read_data(path)
    except Exception as error:  # the gate is a by-product of the preview: no failure may stop it
        logger.warning("The sky tilt of '%s' could not be measured: %s", path, error, exc_info=True)
        return unreadable(f"the stack could not be read: {error}")
    if data is None:
        return unreadable("the stack file holds no pixels")
    return choose_background_method(data, header)


def subtract_sky_plane(
    data: np.ndarray,
    plane_coefficients: list[tuple[float, float]],
    radial_coefficients: list[tuple[float, float]] | None = None,
) -> np.ndarray:
    """Subtract each color plane's fitted sky terms from the pixels.

    Only the tilt terms and the radial part are subtracted. Both are zero at
    the center of the frame, so the sky keeps its level there. Pixels that are
    zero or not a number stay as they are (they are empty border).

    Parameters
    ----------
    data : `numpy.ndarray`
        The stack: two-dimensional, or color planes in front.
    plane_coefficients : `list` [`tuple` [`float`, `float`]]
        For each color plane, the change of its plane from the center to the
        right and to the bottom edge.
    radial_coefficients : `list` [`tuple` [`float`, `float`]], optional
        For each color plane, ``(d2, d4)`` of ``d2 * r^2 + d4 * r^4``, with
        ``r`` the distance from the frame center in half-widths. `None` or an
        empty list subtracts no radial part.

    Returns
    -------
    flattened : `numpy.ndarray`
        A new array of the same shape and type.

    Raises
    ------
    ValueError
        If the number of color planes does not match the coefficients.
    """
    planes = _color_planes(data)
    if planes is None or len(planes) != len(plane_coefficients):
        raise ValueError("the stack and the fitted planes do not match")
    radial = list(radial_coefficients or [])
    if radial and len(radial) != len(planes):
        raise ValueError("the stack and the fitted radial parts do not match")
    if not radial:
        radial = [(0.0, 0.0)] * len(planes)
    rows, columns = planes[0].shape
    x = (np.arange(columns) + 0.5) / columns * 2.0 - 1.0
    y = (np.arange(rows) + 0.5) / rows * 2.0 - 1.0
    flattened = []
    radius_squared = x[np.newaxis, :] ** 2 + y[:, np.newaxis] ** 2
    for plane, (column_term, row_term), radial_term in zip(planes, plane_coefficients, radial, strict=True):
        tilt = (
            column_term * x[np.newaxis, :]
            + row_term * y[:, np.newaxis]
            + radial_term[0] * radius_squared
            + radial_term[1] * radius_squared**2
        )
        keep = ~np.isfinite(plane) | ~plane.astype(bool)
        flattened.append(np.where(keep, plane, plane - tilt))
    result = np.stack(flattened) if np.squeeze(np.asarray(data)).ndim == 3 else flattened[0]
    return result.reshape(np.asarray(data).shape).astype(np.asarray(data).dtype)


def subtract_sky_plane_in_file(
    input_path: str,
    output_path: str,
    plane_coefficients: list[tuple[float, float]],
    radial_coefficients: list[tuple[float, float]] | None = None,
) -> bool:
    """Write a copy of a stack with its fitted sky tilt subtracted.

    Parameters
    ----------
    input_path : `str`
        The scratch copy of the stack. It is not changed.
    output_path : `str`
        Where to write the flattened FITS file. The header is kept.
    plane_coefficients : `list` [`tuple` [`float`, `float`]]
        The tilt terms from the decision (`BackgroundDecision`).
    radial_coefficients : `list` [`tuple` [`float`, `float`]], optional
        The radial parts from the decision, or `None` for none.

    Returns
    -------
    written : `bool`
        `True` if the file was written. A failure is logged as a warning.
    """
    try:
        header: fits.Header = read_header(input_path)
        data = read_data(input_path)
        if data is None:
            return False
        write_image(output_path, subtract_sky_plane(data, plane_coefficients, radial_coefficients), header)
    except (OSError, ValueError) as error:  # ValueError: no pixels, or planes that do not match
        logger.warning("The sky plane could not be subtracted from '%s': %s", input_path, error)
        return False
    return True
