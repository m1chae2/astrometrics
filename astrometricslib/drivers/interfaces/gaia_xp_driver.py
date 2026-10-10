"""Purpose: The interface to Gaia DR3's low-resolution XP spectra.

Description: The Gaia satellite measured a low-resolution spectrum for about
220 million stars. These are the "XP" spectra (BP and RP prism spectra put
together). Their resolving power is about 30 to 100, close to that of a
slitless grating such as the Star Analyser, so they are a good outside
reference for a spectrum the library has calibrated itself.

`GaiaXpDriver` is the abstract base class a Gaia XP driver implements.
`AstroqueryGaiaXpDriver` in `drivers/gaia_xp_driver.py` is the one in use.
"""

from abc import ABC, abstractmethod

import numpy as np


class GaiaXpDriver(ABC):
    """What the library needs from the Gaia DR3 XP spectra."""

    @abstractmethod
    def sampled_spectrum(self, source_id: int) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """Give the sampled XP spectrum of one Gaia DR3 source.

        The spectrum is Gaia's own "sampled" version of the XP spectrum:
        the flux at fixed wavelengths from 336 to 1020 nm in steps of 2 nm.

        Parameters
        ----------
        source_id : `int`
            The Gaia DR3 source id, the long number in a name such as
            ``Gaia DR3 1328045433153485824``.

        Returns
        -------
        spectrum : `tuple` or `None`
            Three `numpy.ndarray` of equal length: the wavelengths in
            Angstroms (increasing), the flux in W m^-2 nm^-1 (energy per
            second per square metre per nanometre of wavelength) and the
            flux's one-sigma error in the same unit. `None` when the source
            has no XP spectrum, or when the spectrum could not be fetched
            (for example, the network is down). A driver never raises for
            these cases, so a spectrum check can not stop a pipeline.
        """
