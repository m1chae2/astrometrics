"""Purpose: The groups of outside errors that `wayfindinglib` catches.

Description: Two tuples of built-in and third-party error types. They are
copies of the tuples the science library keeps private, so this library does
not reach into the science library's private package. Only `wayfindinglib`
itself, and the tests of either library, may import this module.
"""

from astropy.units import UnitsError
from pyvo.dal.exceptions import DALAccessError

__all__ = ["DATA_ERRORS", "ONLINE_QUERY_ERRORS"]

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

#: The errors an online astroquery catalog query (SIMBAD or Gaia) can raise
#: when the network or the remote service fails. ``OSError`` covers lost
#: connections and timeouts, including every `requests` error such as an
#: HTTP error reply. ``DALAccessError`` is the base of the errors from pyvo,
#: the table-query library astroquery uses. ``ValueError`` comes from a
#: reply that is empty or cannot be parsed.
ONLINE_QUERY_ERRORS: tuple[type[Exception], ...] = (OSError, ValueError, DALAccessError)
