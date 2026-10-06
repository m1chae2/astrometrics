"""Purpose: Errors that are specific to astrometricslib's processing.

Description: The shared error categories live in
`astrometricslib.foundation.errors`. This module adds the subclasses that
only astrometricslib raises, so a caller can tell one kind of processing
failure from another. It also names `DATA_ERRORS`, the built-in errors that
measuring unusable data can raise.
"""

from astropy.units import UnitsError

from astrometricslib.foundation.errors import ProcessingError

#: The errors that numeric work on measured data can raise when the data is
#: unusable: too few points, a NaN, a fit that does not converge, a missing
#: column, or an array of the wrong shape. ``ValueError`` also covers numpy's
#: ``LinAlgError`` and astropy's WCS errors. ``ArithmeticError`` covers a
#: division by zero or an overflow. ``UnitsError`` comes from astropy values
#: whose units do not match. Code that measures data catches this tuple
#: instead of every exception, so a real bug (such as an ``AttributeError``)
#: still shows up.
DATA_ERRORS: tuple[type[Exception], ...] = (
    ValueError,
    TypeError,
    IndexError,
    KeyError,
    ArithmeticError,
    UnitsError,
)


class PlateSolveFailedError(ProcessingError):
    """A plate solve could not match the image to a sky position.

    A plate solve finds where in the sky an image points by matching its stars
    to a catalog. It fails when the image has too few stars or too much noise.
    """
