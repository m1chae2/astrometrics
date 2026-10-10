"""Purpose: Errors that are specific to astrometricslib's processing.

Description: The shared error categories live in
`astrometricslib.foundation.errors`. This module adds the subclasses that
only astrometricslib raises, so a caller can tell one kind of processing
failure from another. It also names `DATA_ERRORS`, the built-in errors that
measuring unusable data can raise, and `ONLINE_QUERY_ERRORS`, the errors an
online catalog query can raise.
"""

from astropy.units import UnitsError
from pyvo.dal.exceptions import DALAccessError

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


#: The errors an online astroquery catalog query (SIMBAD or Gaia) can raise
#: when the network or the remote service fails. ``OSError`` covers lost
#: connections and timeouts, including every `requests` error such as an
#: HTTP error reply. ``DALAccessError`` is the base of the errors from pyvo,
#: the table-query library astroquery uses. ``ValueError`` comes from a
#: reply that is empty or cannot be parsed.
ONLINE_QUERY_ERRORS: tuple[type[Exception], ...] = (OSError, ValueError, DALAccessError)
