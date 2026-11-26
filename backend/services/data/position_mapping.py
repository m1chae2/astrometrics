"""Follow one pixel of an image to the same spot on another image.

The Star profile panel pins a measured star. When the viewer shows another
image, the panel asks where that star is on the new one. The library answers
(`Astrometrics.quality.map_position`: the sky position, then a stack's
registration record, then the same pixel as a guess). This module only checks
that both images are inside the folders the app may read, and turns the
library's ``"none"`` answer into `CannotMapError` for the image service.

All positions are 1-based FITS pixels (the center of the first pixel is 1, 1).
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from astrometricslib.drivers import is_path_inside
from astrometricslib.exceptions import NotFoundError

__all__ = ["PROCESSED_PICTURE_ENDING", "CannotMapError", "Mapped", "PositionMapper"]

# The picture the app shows for a stack is saved next to it with this ending.
PROCESSED_PICTURE_ENDING = "_processed"


class CannotMapError(Exception):
    """A position cannot be followed; the message says why."""


@dataclass(frozen=True)
class Mapped:
    """A position on the new image and how it was found.

    Attributes
    ----------
    x, y : `float`
        The position, 1-based.
    method : `str`
        "sky", "stack alignment", "stack alignment + sky" or "same pixel".
    note : `str`
        A sentence about the result.
    """

    x: float
    y: float
    method: str
    note: str


class PositionMapper:
    """Maps a pixel of one image to another image, through the library.

    Both images must be inside the folders the app may read.

    Parameters
    ----------
    allowed_roots : `Callable` [[], `Iterable` [`str`]]
        Gives the real paths of the folders images may be read from.
    map_position : `Callable`
        The library's ``quality.map_position(from_path, x, y, to_path)``.
    """

    def __init__(
        self,
        allowed_roots: Callable[[], Iterable[str]],
        map_position: Callable[[str, float, float, str], Any],
    ) -> None:
        self._allowed_roots = allowed_roots
        self._map_position = map_position

    def _inside(self, path: str) -> bool:
        """Say whether a real path is inside an allowed folder.

        Returns
        -------
        inside : `bool`
            `True` when it is.
        """
        return any(is_path_inside(root, path) for root in self._allowed_roots())

    def map_position(self, from_path: str, x: float, y: float, to_path: str) -> Mapped:
        """Find where a pixel of one image is on another image.

        Parameters
        ----------
        from_path, to_path : `str`
            The two FITS images, as real paths inside the allowed folders.
        x, y : `float`
            The pixel on the first image, 1-based.

        Returns
        -------
        mapped : `Mapped`
            The pixel on the second image and how it was found.

        Raises
        ------
        CannotMapError
            If a file is outside the allowed folders or missing, or the
            library cannot follow the pixel (its note says why).
        """
        for path in (from_path, to_path):
            if not self._inside(path):
                raise CannotMapError("The image must be inside the library folders.")
        try:
            mapping = self._map_position(from_path, x, y, to_path)
        except NotFoundError as error:
            raise CannotMapError(f"An image could not be read: {error}") from error
        if mapping.x is None or mapping.y is None:
            raise CannotMapError(mapping.note)
        return Mapped(float(mapping.x), float(mapping.y), mapping.method, mapping.note)
