"""Purpose: The interface to a source of interstellar reddening values.

Description: Dust between us and a star makes it look redder than it is. The
size of the effect is the colour excess E(B-V), in magnitudes. Some star
catalogs give an estimate of E(B-V) for each star. The spectroscopy pipeline
uses that estimate to undo the reddening of a spectrum before it classifies
the star. `ReddeningDriver` is the abstract base class a reddening source
implements. `AstroqueryGaiaReddeningDriver` in
`drivers/astroquery_gaia_reddening_driver.py` is the one in use, and a test
supplies a fake.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ReddeningEstimate:
    """One catalog value of the colour excess for one star.

    Attributes
    ----------
    ebv : `float`
        The colour excess E(B-V), in magnitudes. Zero or more.
    source : `str`
        Where the number came from and how it was converted, in a short
        phrase such as ``"Gaia DR3 GSP-Phot E(BP-RP) / 1.339"``.
    gaia_source_id : `int` or `None`
        The Gaia DR3 source id the value was looked up with, or `None` when
        the source is not Gaia.
    """

    ebv: float
    source: str
    gaia_source_id: int | None = None


class ReddeningDriver(ABC):
    """What the library needs from a source of reddening values."""

    @abstractmethod
    def get_reddening(self, gaia_source_id: int) -> ReddeningEstimate | None:
        """Look up the colour excess E(B-V) of one star.

        Parameters
        ----------
        gaia_source_id : `int`
            The star's Gaia DR3 source id.

        Returns
        -------
        estimate : `ReddeningEstimate` or `None`
            The catalog's value, or `None` when the catalog has no usable
            reddening for this star.

        Raises
        ------
        ExternalServiceError
            If the catalog could not be reached or its reply could not be
            read. A star the catalog does not hold is not an error: it
            returns `None`.
        """
