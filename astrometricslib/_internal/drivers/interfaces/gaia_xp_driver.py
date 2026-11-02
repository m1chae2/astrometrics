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


class GaiaXpDownloadError(Exception):
    """A Gaia XP spectrum could not be fetched.

    This is different from a source that has no XP spectrum. A missing
    spectrum is a normal answer (`GaiaXpDriver.sampled_spectrum` gives
    `None`). A download error means the answer is not known, for example
    because the network is down, so the caller must not treat the source as
    having no spectrum.
    """


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
            flux's one-sigma error in the same unit. `None` only when the
            source has no XP spectrum.

        Raises
        ------
        GaiaXpDownloadError
            If the spectrum could not be fetched (for example, the network
            is down), so it is not known whether the source has one. A
            caller that must not stop on this catches it.
        """
