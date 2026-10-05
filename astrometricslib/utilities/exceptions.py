"""Purpose: Errors that are specific to astrometricslib's processing.

Description: The shared error categories live in
`astrometricslib.foundation.errors`. This module adds the subclasses that
only astrometricslib raises, so a caller can tell one kind of processing
failure from another.
"""

from astrometricslib.foundation.errors import ProcessingError


class PlateSolveFailedError(ProcessingError):
    """A plate solve could not match the image to a sky position.

    A plate solve finds where in the sky an image points by matching its stars
    to a catalog. It fails when the image has too few stars or too much noise.
    """
