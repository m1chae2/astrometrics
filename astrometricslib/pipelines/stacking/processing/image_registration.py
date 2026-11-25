"""Fit and apply the move that lines one image up with another.

This is the shared geometry engine for lining images up. It works on lists of
matched star positions and on arrays, never on files. It knows nothing about
exposure groups, so any caller that has two images of the same sky can use it.

The move has two parts:

1. A similarity: a shift, a rotation (any angle, including the half turn of a
   mount flip) and one scale. A mirror image is not supported.
2. An optional smooth correction, a quadratic polynomial in the position. It
   describes lens distortion, which a similarity cannot. Two exposures of one
   field taken with the frame offset by hundreds of pixels see each star
   through a different part of the lens, so the stars disagree by a pixel or
   two even after the best similarity. The correction is added to the
   similarity's result.

What is here:

- `fit_transform` fits a `RegistrationTransform` to matched star positions and
  reports how well the stars agree afterward.
- `RegistrationTransform` moves points (`map_points`, `moving_positions`) and
  `warp` moves whole images, with any number of channels, and says which
  output pixels hold real data.
- `RegistrationTransform.to_record` and `from_record` store and read the move
  without the image.

Where the stars come from is the caller's job. `group_alignment` finds them
with `detect_star_centroids`, pairs them (`pair_nearest`, or triangle patterns
through `search_star_patterns`) and calls `fit_transform`. Today only the
exposure groups of one camera use this. The same calls serve the next three
jobs:

- Filter stacks of one mono camera (L, R, G, B, Ha, OIII) onto each other:
  detect the stars of each stack, pair them by the shift that phase correlation
  gives, then `fit_transform` and `warp`. Expect a sub-pixel move.
- One camera's stack onto another's, for LRGB with different pixel scales
  (for example 1.92 and 2.00 arcsec per pixel) and any rotation: call
  `search_star_patterns` with a wider `maximum_scale_deviation`, then
  `fit_transform` and `warp` with the output shape of the reference stack.
  `warp` already accepts an output shape that differs from the input.
- The red and blue channels of a color stack onto the green one: detect and
  pair each channel's stars with green's, fit a similarity (the scale term is
  the small magnification difference from lateral color, the shift is the
  sub-pixel offset) and `warp` the channel.

Positions are written (row, column) as arrays, the order NumPy uses. Inside the
fit they are also written as complex numbers ``column + 1j * row``, so that
multiplying by a complex number ``a`` turns by ``angle(a)`` and enlarges by
``abs(a)``.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.ndimage import affine_transform, map_coordinates, spline_filter

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)

# An output pixel counts as holding real data when, resampling an image of
# ones onto it (linearly), the result is at least this. A pixel that is only
# partly inside the moving image's edge falls below it.
COVERED_PIXEL_THRESHOLD = 0.999

# The polynomial correction is fitted only when stars are matched in every
# quarter of the moving image, at least this many in each. A quadratic has six
# terms per axis. If the stars all sit on one side, the polynomial is pinned
# there and runs away on the other side. Five in each quarter is about the
# six terms of one quadrant, so a fit that passes cannot hang on a single
# star's position. On the three NGC 7023 group pairs (2026-11-24) there were
# 135 to 246 pairs, and the least populated quarter held about 20.
POLYNOMIAL_MINIMUM_PAIRS_PER_QUADRANT = TunedParameter(
    name="stacking.polynomial_minimum_pairs_per_quadrant",
    default=5,
    unit="pairs",
    description="Fewest matched stars needed in each quarter of the image to fit the distortion correction.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D85",
)

# The move that maps a moving position to a reference position is found by
# starting from the similarity alone and correcting by the polynomial at the
# latest guess. The polynomial changes by far less than a pixel per pixel, so
# each round shrinks the error by a factor of hundreds; four rounds leave far
# less than a thousandth of a pixel.
_INVERSE_ROUNDS = 4

# An image is warped this many rows at a time, so the sample positions of a
# 24 megapixel frame do not need gigabytes at once.
_WARP_ROWS_PER_BLOCK = 256

# Number of terms of the quadratic correction: 1, u, v, u^2, u*v, v^2.
_TERM_COUNT = 6


@dataclass(frozen=True)
class PolynomialDistortion:
    """A quadratic correction added to a similarity.

    The correction at a moving-image position is
    ``c0 + c1 * u + c2 * v + c3 * u**2 + c4 * u * v + c5 * v**2`` along each
    axis, where ``u`` and ``v`` are the column and row offsets from the
    origin, divided by the half size.

    Attributes
    ----------
    origin_row_pixels, origin_column_pixels : `float`
        The moving-image position where ``u`` and ``v`` are zero.
    half_size_pixels : `float`
        The length that ``u`` and ``v`` are measured in. It keeps the six
        coefficients of similar size.
    box : `tuple` [`float`, `float`, `float`, `float`]
        The first and last row, then the first and last column, of the area
        the matched stars cover, in moving-image pixels. Outside it the
        correction is held at its value on the box edge, so it never runs
        away where no star measured it.
    row_coefficients, column_coefficients : `tuple` [`float`, ...]
        Six coefficients each, for the correction along the rows and along the
        columns.
    """

    origin_row_pixels: float
    origin_column_pixels: float
    half_size_pixels: float
    box: tuple[float, float, float, float]
    row_coefficients: tuple[float, ...]
    column_coefficients: tuple[float, ...]

    def correction(self, rows: np.ndarray, columns: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Evaluate the correction at moving-image positions.

        Positions outside `box` are first moved to the nearest point of the
        box, so the correction stays at its edge value beyond the stars.

        Parameters
        ----------
        rows, columns : `numpy.ndarray`
            Positions in the moving image, any (equal) shape.

        Returns
        -------
        row_correction, column_correction : `numpy.ndarray`
            How far to move each position along the rows and the columns.
        """
        first_row, last_row, first_column, last_column = self.box
        terms = _polynomial_terms(
            np.clip(rows, first_row, last_row),
            np.clip(columns, first_column, last_column),
            self.origin_row_pixels,
            self.origin_column_pixels,
            self.half_size_pixels,
        )
        return terms @ np.asarray(self.row_coefficients), terms @ np.asarray(self.column_coefficients)

    def to_record(self) -> dict[str, Any]:
        """Describe the correction with plain numbers, to store it.

        Returns
        -------
        record : `dict`
            Numbers and lists of numbers only, so it can be saved as JSON.
        """
        return {
            "origin_row_pixels": self.origin_row_pixels,
            "origin_column_pixels": self.origin_column_pixels,
            "half_size_pixels": self.half_size_pixels,
            "box": list(self.box),
            "row_coefficients": list(self.row_coefficients),
            "column_coefficients": list(self.column_coefficients),
        }

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> PolynomialDistortion | None:
        """Read a correction stored by `to_record`.

        Parameters
        ----------
        record : `dict` or `None`
            The stored record. `None` (what a record made before the
            correction existed holds) gives no correction.

        Returns
        -------
        distortion : `PolynomialDistortion` or `None`
            The correction, or `None` when there is none.
        """
        if not record:
            return None
        first_row, last_row, first_column, last_column = (float(value) for value in record["box"])
        return cls(
            origin_row_pixels=float(record["origin_row_pixels"]),
            origin_column_pixels=float(record["origin_column_pixels"]),
            half_size_pixels=float(record["half_size_pixels"]),
            box=(first_row, last_row, first_column, last_column),
            row_coefficients=tuple(float(value) for value in record["row_coefficients"]),
            column_coefficients=tuple(float(value) for value in record["column_coefficients"]),
        )


@dataclass(frozen=True)
class RegistrationTransform:
    """The move that takes a position in a moving image to the reference image.

    A position ``z`` (column + 1j * row) goes to
    ``a * (z - o) + o + t``, where ``o`` is the origin, ``t`` the shift of the
    origin and ``a = scale * exp(1j * rotation)``; then the optional
    distortion correction is added.

    Attributes
    ----------
    shift_rows_pixels, shift_columns_pixels : `float`
        How far the origin moves along the rows and along the columns.
    rotation_degrees : `float`
        The turn about the origin. Positive turns from the +column axis toward
        the +row axis.
    scale : `float`
        The enlargement about the origin.
    origin_row_pixels, origin_column_pixels : `float`
        The point the rotation and scale are about. `fit_transform` uses the
        moving image's center.
    distortion : `PolynomialDistortion` or `None`
        The polynomial correction, when the move has one.
    """

    shift_rows_pixels: float
    shift_columns_pixels: float
    rotation_degrees: float
    scale: float
    origin_row_pixels: float
    origin_column_pixels: float
    distortion: PolynomialDistortion | None = None

    @property
    def turn(self) -> complex:
        """The complex number ``a`` that turns and enlarges.

        Returns
        -------
        turn : `complex`
            ``scale * exp(1j * rotation)``.
        """
        return self.scale * np.exp(1j * np.radians(self.rotation_degrees))

    def _similarity_positions(self, rows: np.ndarray, columns: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Move positions by the similarity part alone.

        Returns
        -------
        rows, columns : `numpy.ndarray`
            The positions in the reference image, without the correction.
        """
        origin = complex(self.origin_column_pixels, self.origin_row_pixels)
        shift = complex(self.shift_columns_pixels, self.shift_rows_pixels)
        moved = self.turn * (columns + 1j * rows - origin) + origin + shift
        return moved.imag, moved.real

    def map_points(self, rows: np.ndarray, columns: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Move positions from the moving image to the reference image.

        Parameters
        ----------
        rows, columns : `numpy.ndarray`
            Positions in the moving image, any (equal) shape.

        Returns
        -------
        rows, columns : `numpy.ndarray`
            The same positions in the reference image, with the correction.
        """
        rows = np.asarray(rows, dtype=np.float64)
        columns = np.asarray(columns, dtype=np.float64)
        moved_rows, moved_columns = self._similarity_positions(rows, columns)
        if self.distortion is not None:
            row_correction, column_correction = self.distortion.correction(rows, columns)
            moved_rows, moved_columns = moved_rows + row_correction, moved_columns + column_correction
        return moved_rows, moved_columns

    def moving_positions(
        self, reference_rows: np.ndarray, reference_columns: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Find where positions of the reference image came from.

        This is the inverse of `map_points`. Without a correction it is exact.
        With one, it starts from the inverse of the similarity and removes the
        correction at the latest guess a few times.

        Parameters
        ----------
        reference_rows, reference_columns : `numpy.ndarray`
            Positions in the reference image, any (equal) shape.

        Returns
        -------
        rows, columns : `numpy.ndarray`
            The positions in the moving image that land on them.
        """
        reference_rows = np.asarray(reference_rows, dtype=np.float64)
        reference_columns = np.asarray(reference_columns, dtype=np.float64)
        origin = complex(self.origin_column_pixels, self.origin_row_pixels)
        shift = complex(self.shift_columns_pixels, self.shift_rows_pixels)
        turn = self.turn
        target_rows, target_columns = reference_rows, reference_columns
        rows = columns = None
        for _round_index in range(_INVERSE_ROUNDS if self.distortion is not None else 1):
            if rows is not None and self.distortion is not None:
                row_correction, column_correction = self.distortion.correction(rows, columns)
                target_rows = reference_rows - row_correction
                target_columns = reference_columns - column_correction
            moving = (target_columns + 1j * target_rows - origin - shift) / turn + origin
            rows, columns = moving.imag, moving.real
        return rows, columns


@dataclass(frozen=True)
class TransformFit:
    """The result of `fit_transform`.

    Attributes
    ----------
    transform : `RegistrationTransform`
        The fitted move.
    kept : `numpy.ndarray`
        A boolean mask over the input pairs: the pairs the final fit used.
    residual_pixels : `float`
        How far the kept stars are from their partners after `transform`
        (root mean square).
    similarity_residual_pixels : `float`
        The same for the similarity alone, over the pairs its own fit kept.
    model : `str`
        ``"similarity"`` or ``"polynomial"``: what `transform` holds.
    polynomial_note : `str` or `None`
        When the polynomial was asked for and not used, why not.
    """

    transform: RegistrationTransform
    kept: np.ndarray
    residual_pixels: float
    similarity_residual_pixels: float
    model: str
    polynomial_note: str | None = None


def _polynomial_terms(
    rows: np.ndarray, columns: np.ndarray, origin_row: float, origin_column: float, half_size: float
) -> np.ndarray:
    """Build the terms 1, u, v, u^2, u*v, v^2 at positions.

    Returns
    -------
    terms : `numpy.ndarray`
        An array of shape ``(..., 6)``.
    """
    u = (np.asarray(columns, dtype=np.float64) - origin_column) / half_size
    v = (np.asarray(rows, dtype=np.float64) - origin_row) / half_size
    return np.stack([np.ones_like(u), u, v, u * u, u * v, v * v], axis=-1)


def fit_similarity(
    reference_points: np.ndarray,
    moving_points: np.ndarray,
    *,
    minimum_pairs: int,
    clip_sigmas: float,
    clip_passes: int,
) -> tuple[complex, complex, np.ndarray] | None:
    """Fit ``reference = a * moving + b`` to matched star positions.

    The fit is repeated after dropping stars far from it, so a wrongly paired
    star does not tilt it.

    Parameters
    ----------
    reference_points, moving_points : `numpy.ndarray`
        The matched (row, column) positions, one row per pair.
    minimum_pairs : `int`
        The fewest pairs a fit may use.
    clip_sigmas : `float`
        A pair farther than this many robust sigmas from the fit is dropped.
    clip_passes : `int`
        The most times the fit is repeated.

    Returns
    -------
    fit : `tuple` or `None`
        ``(a, b, kept)``: the two complex numbers and a boolean mask of the
        pairs the final fit used. `None` when fewer than `minimum_pairs` pairs
        remain.
    """
    moving_complex = moving_points[:, 1] + 1j * moving_points[:, 0]
    reference_complex = reference_points[:, 1] + 1j * reference_points[:, 0]
    kept = np.ones(len(moving_complex), dtype=bool)
    a = b = 0j
    for _ in range(clip_passes):
        if kept.sum() < minimum_pairs:
            return None
        design = np.vstack([moving_complex[kept], np.ones(int(kept.sum()), dtype=complex)]).T
        (a, b), *_ = np.linalg.lstsq(design, reference_complex[kept], rcond=None)
        residual = np.abs(reference_complex - (a * moving_complex + b))
        spread = 1.4826 * float(np.median(residual[kept]))
        limit = max(clip_sigmas * spread, 1e-6)
        new_kept = residual <= limit
        if np.array_equal(new_kept, kept):
            break
        kept = new_kept
    if kept.sum() < minimum_pairs:
        return None
    return complex(a), complex(b), kept


def _root_mean_square(offsets: np.ndarray) -> float:
    """Return the root mean square length of (row, column) offsets.

    Returns
    -------
    rms : `float`
        The root mean square, in pixels.
    """
    return float(np.sqrt(np.mean((offsets**2).sum(axis=1))))


def _every_quarter_has_pairs(
    moving_points: np.ndarray, origin_row: float, origin_column: float, minimum: int
) -> bool:
    """Say whether each quarter of the moving image holds enough pairs.

    Returns
    -------
    spread : `bool`
        `True` when all four quarters about the origin hold `minimum` points.
    """
    below_row = moving_points[:, 0] < origin_row
    below_column = moving_points[:, 1] < origin_column
    return all(
        int(np.sum(first & second)) >= minimum
        for first in (below_row, ~below_row)
        for second in (below_column, ~below_column)
    )


def fit_transform(
    reference_points: np.ndarray,
    moving_points: np.ndarray,
    model: str,
    moving_shape: tuple[int, ...],
    *,
    minimum_pairs: int,
    clip_sigmas: float,
    clip_passes: int,
    minimum_pairs_per_quadrant: int | None = None,
) -> TransformFit | None:
    """Fit the move that takes the moving stars onto their reference partners.

    A similarity is always fitted first. With ``model="polynomial"`` the
    quadratic correction is then fitted to what the similarity leaves, but only
    when enough stars are matched in all four quarters of the moving image (see
    `POLYNOMIAL_MINIMUM_PAIRS_PER_QUADRANT`); otherwise the similarity is
    returned and `TransformFit.polynomial_note` says why.

    Parameters
    ----------
    reference_points, moving_points : `numpy.ndarray`
        The matched (row, column) positions, one row per pair.
    model : `str`
        ``"similarity"`` or ``"polynomial"``.
    moving_shape : `tuple` [`int`, ...]
        The moving image's shape; its last two numbers are the rows and the
        columns. Its center is the origin of the rotation and the scale.
    minimum_pairs : `int`
        The fewest pairs a fit may use.
    clip_sigmas : `float`
        A pair farther than this many robust sigmas from a fit is dropped.
    clip_passes : `int`
        The most times each fit is repeated.
    minimum_pairs_per_quadrant : `int`, optional
        By default `POLYNOMIAL_MINIMUM_PAIRS_PER_QUADRANT`.

    Returns
    -------
    fit : `TransformFit` or `None`
        The fit, or `None` when the similarity has too few pairs.

    Raises
    ------
    ValueError
        If `model` is neither name above.
    """
    if model not in ("similarity", "polynomial"):
        raise ValueError(f"Unknown registration model {model!r}; use 'similarity' or 'polynomial'.")
    fitted = fit_similarity(
        reference_points,
        moving_points,
        minimum_pairs=minimum_pairs,
        clip_sigmas=clip_sigmas,
        clip_passes=clip_passes,
    )
    if fitted is None:
        return None
    a, b, kept = fitted
    origin_row, origin_column = (moving_shape[-2] - 1) / 2.0, (moving_shape[-1] - 1) / 2.0
    origin = complex(origin_column, origin_row)
    shift = a * origin + b - origin
    transform = RegistrationTransform(
        shift_rows_pixels=float(shift.imag),
        shift_columns_pixels=float(shift.real),
        rotation_degrees=float(np.degrees(np.angle(a))),
        scale=float(abs(a)),
        origin_row_pixels=origin_row,
        origin_column_pixels=origin_column,
    )
    after_similarity = np.column_stack(transform.map_points(moving_points[:, 0], moving_points[:, 1]))
    similarity_residual = _root_mean_square((reference_points - after_similarity)[kept])
    if model == "similarity":
        return TransformFit(transform, kept, similarity_residual, similarity_residual, "similarity")

    if minimum_pairs_per_quadrant is None:
        minimum_pairs_per_quadrant = int(resolve(POLYNOMIAL_MINIMUM_PAIRS_PER_QUADRANT).value)

    def similarity_only(note: str) -> TransformFit:
        """Return the similarity fit with a note on why no polynomial was used.

        Returns
        -------
        fit : `TransformFit`
            The similarity fit.
        """
        return TransformFit(transform, kept, similarity_residual, similarity_residual, "similarity", note)

    if len(moving_points) < minimum_pairs:
        return similarity_only(f"only {len(moving_points)} pairs")
    half_size = max(moving_shape[-2], moving_shape[-1]) / 2.0
    wanted = reference_points - after_similarity
    kept_polynomial = np.ones(len(moving_points), dtype=bool)
    row_coefficients = column_coefficients = np.zeros(_TERM_COUNT)
    terms = _polynomial_terms(moving_points[:, 0], moving_points[:, 1], origin_row, origin_column, half_size)
    for _ in range(clip_passes):
        if kept_polynomial.sum() < max(minimum_pairs, _TERM_COUNT):
            return similarity_only("too few pairs after dropping the poorly matched ones")
        row_coefficients, *_ = np.linalg.lstsq(terms[kept_polynomial], wanted[kept_polynomial, 0], rcond=None)
        column_coefficients, *_ = np.linalg.lstsq(
            terms[kept_polynomial], wanted[kept_polynomial, 1], rcond=None
        )
        leftover = wanted - np.column_stack([terms @ row_coefficients, terms @ column_coefficients])
        distance = np.sqrt((leftover**2).sum(axis=1))
        spread = 1.4826 * float(np.median(distance[kept_polynomial]))
        new_kept = distance <= max(clip_sigmas * spread, 1e-6)
        if np.array_equal(new_kept, kept_polynomial):
            break
        kept_polynomial = new_kept
    if kept_polynomial.sum() < max(minimum_pairs, _TERM_COUNT):
        return similarity_only("too few pairs after dropping the poorly matched ones")
    if not _every_quarter_has_pairs(
        moving_points[kept_polynomial], origin_row, origin_column, minimum_pairs_per_quadrant
    ):
        return similarity_only(
            f"fewer than {minimum_pairs_per_quadrant} pairs in at least one quarter of the image"
        )
    used = moving_points[kept_polynomial]
    distortion = PolynomialDistortion(
        origin_row_pixels=origin_row,
        origin_column_pixels=origin_column,
        half_size_pixels=half_size,
        box=(
            float(used[:, 0].min()),
            float(used[:, 0].max()),
            float(used[:, 1].min()),
            float(used[:, 1].max()),
        ),
        row_coefficients=tuple(float(value) for value in row_coefficients),
        column_coefficients=tuple(float(value) for value in column_coefficients),
    )
    corrected = RegistrationTransform(
        shift_rows_pixels=transform.shift_rows_pixels,
        shift_columns_pixels=transform.shift_columns_pixels,
        rotation_degrees=transform.rotation_degrees,
        scale=transform.scale,
        origin_row_pixels=origin_row,
        origin_column_pixels=origin_column,
        distortion=distortion,
    )
    after_polynomial = np.column_stack(corrected.map_points(moving_points[:, 0], moving_points[:, 1]))
    polynomial_residual = _root_mean_square((reference_points - after_polynomial)[kept_polynomial])
    return TransformFit(corrected, kept_polynomial, polynomial_residual, similarity_residual, "polynomial")


def warp(
    image: np.ndarray,
    transform: RegistrationTransform,
    order: int,
    output_shape: tuple[int, int] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Move an image onto the reference grid.

    A move without a correction is one affine resampling. With a correction,
    each output pixel is traced back to the moving image (see
    `RegistrationTransform.moving_positions`) and the image is sampled there,
    a block of rows at a time.

    Parameters
    ----------
    image : `numpy.ndarray`
        The moving image, 2-D or colour (channels first).
    transform : `RegistrationTransform`
        The move, from `fit_transform`.
    order : `int`
        The spline order of the resampling: 3 (cubic) keeps stars sharp; use 1
        for a map of counts that must not overshoot.
    output_shape : `tuple` [`int`, `int`], optional
        The (rows, columns) of the output, the reference image's size. By
        default the moving image's own size.

    Returns
    -------
    moved : `numpy.ndarray`
        The moved image (`float32`), zero where the move brings in nothing.
    covered : `numpy.ndarray`
        A 2-D boolean mask on the output grid, `True` where `moved` holds real
        data from inside the moving image.
    """
    array = np.asarray(image, dtype=np.float32)
    planes = array[np.newaxis] if array.ndim == 2 else array
    input_shape = planes.shape[-2:]
    out_shape = tuple(output_shape) if output_shape is not None else tuple(input_shape)
    if transform.distortion is None:
        # The move maps z to a * (z - o) + o + t. `affine_transform` wants the
        # inverse: for each output position, where to read the input, as a
        # matrix and an offset in (row, column).
        inverse = 1.0 / transform.turn
        matrix = np.array([[inverse.real, inverse.imag], [-inverse.imag, inverse.real]])
        origin = np.array([transform.origin_row_pixels, transform.origin_column_pixels])
        shift = np.array([transform.shift_rows_pixels, transform.shift_columns_pixels])
        offset = origin - matrix @ (origin + shift)
        moved = np.stack([
            affine_transform(
                plane, matrix, offset=offset, output_shape=out_shape, order=order, mode="constant", cval=0.0
            )
            for plane in planes
        ]).astype(np.float32)
        covered = (
            affine_transform(
                np.ones(input_shape, dtype=np.float32),
                matrix,
                offset=offset,
                output_shape=out_shape,
                order=1,
                mode="constant",
                cval=0.0,
            )
            > COVERED_PIXEL_THRESHOLD
        )
        return (moved[0] if array.ndim == 2 else moved), covered
    filtered = [
        spline_filter(plane.astype(np.float64), order=order, mode="constant") if order > 1 else plane
        for plane in planes
    ]
    moved = np.zeros((len(planes), *out_shape), dtype=np.float32)
    covered = np.zeros(out_shape, dtype=bool)
    margin = 1.0 - COVERED_PIXEL_THRESHOLD
    columns = np.arange(out_shape[1], dtype=np.float64)
    for first in range(0, out_shape[0], _WARP_ROWS_PER_BLOCK):
        rows = np.arange(first, min(first + _WARP_ROWS_PER_BLOCK, out_shape[0]), dtype=np.float64)
        grid_columns, grid_rows = np.meshgrid(columns, rows)
        source_rows, source_columns = transform.moving_positions(grid_rows, grid_columns)
        block = slice(first, first + len(rows))
        for channel, plane in enumerate(filtered):
            moved[channel, block] = map_coordinates(
                plane,
                [source_rows, source_columns],
                order=order,
                mode="constant",
                cval=0.0,
                prefilter=False,
            )
        covered[block] = (
            (source_rows >= margin)
            & (source_rows <= input_shape[0] - 1 - margin)
            & (source_columns >= margin)
            & (source_columns <= input_shape[1] - 1 - margin)
        )
    # Interpolation near the edge reads outside the image as zero; keep zero
    # wherever the output pixel has no real data behind it.
    moved[:, ~covered] = 0.0
    return (moved[0] if array.ndim == 2 else moved), covered
