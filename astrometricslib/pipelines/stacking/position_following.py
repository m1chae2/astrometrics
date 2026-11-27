"""Purpose: Follow positions from one image to another, by records or stars.

Description: `map_positions` is the owner behind
``astrometrics.quality.frames.map_positions``. It follows each position with
the plate solutions and the stack records first
(`pipelines.shared.position_mapping.map_position`), which read only headers
and small files. When they give only a guess between two different frames of
one size, it matches the two frames' bright stars instead
(`pre_processing.blink_pair.match_frame_positions`), which reads their
pixels. The answer says which method was used. Nothing is stored.
"""

import os
from collections.abc import Iterable, Sequence

from astrometricslib._internal.drivers.fits_access import read_header
from astrometricslib.models.quality.position_mapping import MappedPosition, MappedPositions, PositionMapping
from astrometricslib.pipelines.shared.position_mapping import RecordCache, map_position
from astrometricslib.pipelines.stacking.pre_processing.blink_pair import match_frame_positions

__all__ = ["map_positions"]


def _image_size(path: str) -> tuple[int, int]:
    """Read an image's width and height from its header.

    Returns
    -------
    size : `tuple` [`int`, `int`]
        The number of columns and rows (0 when the header does not say).
    """
    header = read_header(path)
    return int(header.get("NAXIS1", 0) or 0), int(header.get("NAXIS2", 0) or 0)


def map_positions(
    from_path: str,
    to_path: str,
    points: Sequence[tuple[float, float]] | None,
    record_roots: Iterable[str],
    cache: RecordCache | None = None,
) -> MappedPositions:
    """Find where positions of one image lie on another image.

    The owner of ``astrometrics.quality.frames.map_positions``. The plate
    solutions and the stack records are tried first (`map_position`), since
    they read only headers and small files. When they give only a guess
    (``"same pixel"`` between two different files) and the two images are
    frames of one size, the bright stars of both frames are matched instead
    (`match_frame_positions`), which reads their pixels.

    Parameters
    ----------
    from_path, to_path : `str`
        The two FITS images.
    points : `Sequence` [`tuple` [`float`, `float`]] or `None`
        ``(x, y)`` positions on the first image, 1-based. `None` follows the
        center of the first image.
    record_roots : `Iterable` [`str`]
        The folders searched for stack registration records.
    cache : `RecordCache`, optional
        Records read by earlier calls.

    Returns
    -------
    mapping : `MappedPositions`
        Each position on the second image and the method used.

    Raises
    ------
    NotFoundError
        If an image file does not exist.
    """
    from astrometricslib.foundation.errors import NotFoundError

    for path in (from_path, to_path):
        if not os.path.isfile(path):
            raise NotFoundError(f"No image at {path}.")
    if points is None:
        width, height = _image_size(from_path)
        points = [((width + 1) / 2.0, (height + 1) / 2.0)]
    asked = [(float(x), float(y)) for x, y in points]
    roots = list(record_roots)
    cache = cache if cache is not None else RecordCache()
    found = [map_position(from_path, x, y, to_path, roots, cache) for x, y in asked]
    width, height = _image_size(to_path)

    def inside(x: float | None, y: float | None) -> bool:
        """Say whether a 1-based position lies on the second image.

        Returns
        -------
        inside : `bool`
            `True` inside the image's edges.
        """
        return x is not None and y is not None and 0.5 <= x <= width + 0.5 and 0.5 <= y <= height + 0.5

    first = found[0] if found else PositionMapping(x=None, y=None, method="same pixel", note="")
    guessed = first.method == "same pixel" and os.path.realpath(from_path) != os.path.realpath(to_path)
    if guessed and _image_size(from_path) == (width, height):
        match = match_frame_positions(from_path, to_path, asked)
        if match.matched:
            return MappedPositions(
                method="star matching",
                note=(
                    f"Matched the bright stars of the two frames ({match.votes} votes"
                    f"{', the second turned half a revolution' if match.turned else ''})."
                ),
                points=[
                    MappedPosition(
                        x=point.x,
                        y=point.y,
                        mapped_x=point.mapped_x,
                        mapped_y=point.mapped_y,
                        inside=point.inside,
                    )
                    for point in match.points
                ],
            )
    return MappedPositions(
        method=first.method,
        note=first.note,
        points=[
            MappedPosition(x=x, y=y, mapped_x=result.x, mapped_y=result.y, inside=inside(result.x, result.y))
            for (x, y), result in zip(asked, found, strict=True)
        ],
    )
