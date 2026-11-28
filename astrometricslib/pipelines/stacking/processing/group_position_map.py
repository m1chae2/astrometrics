"""Where each pixel of the combined grid comes from in a group's own stack.

Each exposure group is stacked on its own grid. To combine the groups, the
pipeline moves every group stack onto one common grid. The move can be as
plain as a shift (a spectral group lined up by its zero-order star, or a Moon
group lined up by correlation) or as general as the sky-based move Siril makes
(a rotation, a scale and the lens distortion of both stacks).

`GroupPositionMap` describes the move the same way in both cases: for any
pixel of the common grid, it says the row and column to read in the group's
own stack. Three jobs share it:

- moving the group's other maps, such as its rejection map, the same way
  (`resample`);
- finding which frames cover a pixel of the combined stack (`stack_crop`
  calls `source_positions`);
- reporting the move as a shift, a turn and a scale at the image center, for
  the group manifest.

A shift is stored as two numbers. A sky-based move is stored as a coarse grid
of source positions, one every `NODE_SPACING_PIXELS` pixels, and the positions
between the nodes are read by linear interpolation. The lens distortion
changes over hundreds of pixels, so over 32 pixels it is straight to far less
than a hundredth of a pixel.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
from astropy.wcs import WCS
from scipy.ndimage import map_coordinates

from astrometricslib.pipelines.stacking.processing.image_registration import resample_at_positions

__all__ = ["NODE_SPACING_PIXELS", "GroupPositionMap"]

# The distance, in pixels, between the nodes of a sky-based move. Siril's
# distortion fit is a cubic polynomial over the whole frame, so between two
# nodes 32 pixels apart the map differs from a straight line by far less than
# 0.01 pixel. A 6000 x 4000 frame needs 188 x 126 nodes, a few dozen
# kilobytes in the manifest.
NODE_SPACING_PIXELS = 32

# The mask of covered pixels is worked out this many rows at a time, so a
# 24 megapixel grid does not need gigabytes of positions at once.
_ROWS_PER_BLOCK = 256

# How far from the center, in pixels, the turn and the scale are measured.
_JACOBIAN_STEP_PIXELS = 50.0


@dataclass(frozen=True)
class GroupPositionMap:
    """The move that puts a group stack onto the common grid.

    Attributes
    ----------
    grid_shape : `tuple` [`int`, `int`]
        The (rows, columns) of the common grid, which is also the size of the
        group stack.
    shift_rows_pixels, shift_columns_pixels : `float`
        For a move that is only a shift: how far the image moves along the
        rows and along the columns. Zero for a sky-based move.
    node_rows, node_columns : `numpy.ndarray` or `None`
        For a sky-based move: the row and the column in the group stack that
        lie under each node of the common grid. The nodes are spread evenly
        over the grid, first and last pixel included. `None` for a shift.
    """

    grid_shape: tuple[int, int]
    shift_rows_pixels: float = 0.0
    shift_columns_pixels: float = 0.0
    node_rows: np.ndarray | None = None
    node_columns: np.ndarray | None = None

    @classmethod
    def from_shift(
        cls, grid_shape: tuple[int, int], shift_rows_pixels: float, shift_columns_pixels: float
    ) -> GroupPositionMap:
        """Describe a move that is a shift only.

        Parameters
        ----------
        grid_shape : `tuple` [`int`, `int`]
            The (rows, columns) of the grid.
        shift_rows_pixels, shift_columns_pixels : `float`
            How far the image moves along the rows and the columns.

        Returns
        -------
        position_map : `GroupPositionMap`
            The move.
        """
        return cls(tuple(grid_shape), float(shift_rows_pixels), float(shift_columns_pixels))

    @classmethod
    def from_sky_maps(
        cls, grid_shape: tuple[int, int], grid_wcs: WCS, group_wcs: WCS, spacing: int = NODE_SPACING_PIXELS
    ) -> GroupPositionMap:
        """Describe the move between two plate solutions.

        Each node of the common grid is turned into a sky position with the
        grid's sky map, and that position into a pixel of the group stack
        with the group's sky map (which holds the lens distortion).

        Parameters
        ----------
        grid_shape : `tuple` [`int`, `int`]
            The (rows, columns) of the common grid.
        grid_wcs : `astropy.wcs.WCS`
            The sky map of the common grid.
        group_wcs : `astropy.wcs.WCS`
            The sky map of the group stack, with its distortion terms.
        spacing : `int`, optional
            The distance between nodes in pixels. Default
            `NODE_SPACING_PIXELS`.

        Returns
        -------
        position_map : `GroupPositionMap`
            The move.
        """
        rows, columns = grid_shape
        node_row_count = max(2, math.ceil((rows - 1) / spacing) + 1)
        node_column_count = max(2, math.ceil((columns - 1) / spacing) + 1)
        grid_rows, grid_columns = np.meshgrid(
            np.linspace(0.0, rows - 1.0, node_row_count),
            np.linspace(0.0, columns - 1.0, node_column_count),
            indexing="ij",
        )
        sky = grid_wcs.all_pix2world(grid_columns.ravel(), grid_rows.ravel(), 0)
        source_columns, source_rows = group_wcs.all_world2pix(sky[0], sky[1], 0, quiet=True)
        return cls(
            tuple(grid_shape),
            node_rows=np.asarray(source_rows, dtype=np.float64).reshape(grid_rows.shape),
            node_columns=np.asarray(source_columns, dtype=np.float64).reshape(grid_rows.shape),
        )

    @property
    def is_shift(self) -> bool:
        """Whether the move is a shift only.

        Returns
        -------
        is_shift : `bool`
            `True` for a move made with `from_shift`.
        """
        return self.node_rows is None

    def source_positions(self, rows: np.ndarray, columns: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Find where the group stack is read for pixels of the common grid.

        Parameters
        ----------
        rows, columns : `numpy.ndarray`
            The rows and columns of pixels of the common grid; the two arrays
            have one shape.

        Returns
        -------
        source_rows, source_columns : `numpy.ndarray`
            The rows and columns of the group stack to read there, in the
            shape of the inputs. A position outside the group stack means
            that pixel has no data from this group.
        """
        row_array = np.asarray(rows, dtype=np.float64)
        column_array = np.asarray(columns, dtype=np.float64)
        if self.node_rows is None or self.node_columns is None:
            return row_array - self.shift_rows_pixels, column_array - self.shift_columns_pixels
        node_row_count, node_column_count = self.node_rows.shape
        node_row_index = row_array * ((node_row_count - 1) / (self.grid_shape[0] - 1))
        node_column_index = column_array * ((node_column_count - 1) / (self.grid_shape[1] - 1))
        coordinates = [node_row_index, node_column_index]
        return (
            map_coordinates(self.node_rows, coordinates, order=1, mode="nearest"),
            map_coordinates(self.node_columns, coordinates, order=1, mode="nearest"),
        )

    def resample(self, image: np.ndarray, order: int) -> tuple[np.ndarray, np.ndarray]:
        """Move an image of the group's grid onto the common grid.

        Parameters
        ----------
        image : `numpy.ndarray`
            The image, 2-D or colour (channels first), the size of the grid.
        order : `int`
            The spline order of the resampling. Use 1 for a map of counts that
            must not overshoot.

        Returns
        -------
        moved : `numpy.ndarray`
            The moved image (`float32`), zero where the move brings in
            nothing.
        covered : `numpy.ndarray`
            A 2-D boolean mask, `True` where `moved` holds real data.
        """
        return resample_at_positions(image, self.source_positions, order, self.grid_shape)

    def covered_mask(self, margin: float) -> np.ndarray:
        """Find the pixels of the common grid that read inside the group stack.

        Parameters
        ----------
        margin : `float`
            How many pixels from the group stack's edge a read position must
            keep. A resampling kernel reaches this far, so a position nearer
            the edge mixes in what lies beyond it.

        Returns
        -------
        covered : `numpy.ndarray`
            A 2-D boolean mask on the common grid, `True` where the position
            read is at least `margin` pixels inside the group stack.
        """
        rows, columns = self.grid_shape
        covered = np.zeros(self.grid_shape, dtype=bool)
        column_index = np.arange(columns, dtype=np.float64)
        for first in range(0, rows, _ROWS_PER_BLOCK):
            block_rows = np.arange(first, min(first + _ROWS_PER_BLOCK, rows), dtype=np.float64)
            grid_columns, grid_rows = np.meshgrid(column_index, block_rows)
            source_rows, source_columns = self.source_positions(grid_rows, grid_columns)
            covered[first : first + len(block_rows)] = (
                (source_rows >= margin)
                & (source_rows <= rows - 1 - margin)
                & (source_columns >= margin)
                & (source_columns <= columns - 1 - margin)
            )
        return covered

    def center_shift(self) -> tuple[float, float]:
        """Say how far the move shifts the center of the image.

        Returns
        -------
        shift_rows_pixels, shift_columns_pixels : `float`
            How far the pixel at the center of the group stack lands from the
            center of the common grid, along the rows and the columns. For a
            move that is only a shift, the shift itself.
        """
        if self.is_shift:
            return self.shift_rows_pixels, self.shift_columns_pixels
        center_row, center_column = (self.grid_shape[0] - 1) / 2.0, (self.grid_shape[1] - 1) / 2.0
        source_row, source_column = self.source_positions(np.array([center_row]), np.array([center_column]))
        return float(center_row - source_row[0]), float(center_column - source_column[0])

    def turn_and_scale(self) -> tuple[float, float]:
        """Say how much the move turns and enlarges the image at its center.

        Returns
        -------
        rotation_degrees : `float`
            How far the group stack turns, in degrees, positive from the
            +column axis toward the +row axis. A half turn (a meridian flip)
            reads about 180.
        scale : `float`
            The factor by which the group stack is enlarged. One for a shift.
        """
        if self.is_shift:
            return 0.0, 1.0
        center_row, center_column = (self.grid_shape[0] - 1) / 2.0, (self.grid_shape[1] - 1) / 2.0
        step = _JACOBIAN_STEP_PIXELS
        row_at, column_at = self.source_positions(
            np.array([center_row, center_row, center_row]),
            np.array([center_column, center_column + step, center_column - step]),
        )
        row_by, column_by = self.source_positions(
            np.array([center_row + step, center_row - step]), np.array([center_column, center_column])
        )
        column_axis_rows = (row_at[1] - row_at[2]) / (2 * step)
        column_axis_columns = (column_at[1] - column_at[2]) / (2 * step)
        row_axis_rows = (row_by[0] - row_by[1]) / (2 * step)
        row_axis_columns = (column_by[0] - column_by[1]) / (2 * step)
        determinant = column_axis_columns * row_axis_rows - column_axis_rows * row_axis_columns
        scale = 1.0 / math.sqrt(abs(determinant)) if determinant else 1.0
        turn = -math.degrees(math.atan2(column_axis_rows, column_axis_columns))
        return turn, scale

    def to_record(self) -> dict[str, Any]:
        """Give the move as plain numbers for a JSON record.

        Returns
        -------
        record : `dict`
            The grid shape and either the shift or the node positions
            (rounded to 0.001 pixel).
        """
        record: dict[str, Any] = {"grid_shape": list(self.grid_shape)}
        if self.node_rows is None or self.node_columns is None:
            record["shift"] = [self.shift_rows_pixels, self.shift_columns_pixels]
        else:
            record["node_rows"] = np.round(self.node_rows, 3).tolist()
            record["node_columns"] = np.round(self.node_columns, 3).tolist()
        return record

    @classmethod
    def from_record(cls, record: Mapping[str, Any] | None) -> GroupPositionMap | None:
        """Read a move back from `to_record`.

        Parameters
        ----------
        record : `Mapping` or `None`
            What `to_record` gave, or `None`.

        Returns
        -------
        position_map : `GroupPositionMap` or `None`
            The move, or `None` for no record (a group that was not moved).
        """
        if record is None:
            return None
        grid_shape = (int(record["grid_shape"][0]), int(record["grid_shape"][1]))
        if "shift" in record:
            return cls.from_shift(grid_shape, record["shift"][0], record["shift"][1])
        return cls(
            grid_shape,
            node_rows=np.asarray(record["node_rows"], dtype=np.float64),
            node_columns=np.asarray(record["node_columns"], dtype=np.float64),
        )
