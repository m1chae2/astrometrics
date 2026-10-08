"""Purpose: The interface to the SIMBAD astronomical database.

Description: SIMBAD is an online database of named astronomical objects
run by the Strasbourg astronomical Data Centre (CDS). The astrometry
pipeline uses it to name the stars in an image and to look up a target's
position. `SimbadDriver` is the abstract base class a SIMBAD driver
implements. `AstroquerySimbadDriver` in
`drivers/astroquery_simbad_driver.py` is the one in use.
"""

from abc import ABC, abstractmethod
from typing import Any


class SimbadDriver(ABC):
    """What the library needs from the SIMBAD database."""

    @abstractmethod
    def query_region(
        self,
        coordinates: Any,
        radius: str,
        *,
        votable_fields: tuple[str, ...] = (),
        row_limit: int | None = None,
    ) -> Any:
        """Find every catalog object within `radius` of a point.

        Parameters
        ----------
        coordinates : `astropy.coordinates.SkyCoord`
            Centre of the search cone.
        radius : `str`
            Cone radius as an angle string, e.g. ``"0.5d"``.
        votable_fields : `tuple` [`str`], optional
            Extra columns to request beyond SIMBAD's defaults.
        row_limit : `int`, optional
            Maximum rows to return.

        Returns
        -------
        result_table : `astropy.table.Table` or `None`
            The matching rows, or `None` if SIMBAD returned nothing.
        """

    @abstractmethod
    def query_object(self, object_name: str, *, votable_fields: tuple[str, ...] = ()) -> Any:
        """Look one named object up.

        Parameters
        ----------
        object_name : `str`
            Catalog identifier to resolve, e.g. ``"M 13"``.
        votable_fields : `tuple` [`str`], optional
            Extra columns to request beyond SIMBAD's defaults.

        Returns
        -------
        result_table : `astropy.table.Table` or `None`
            The matching row, or `None` if the name did not resolve.
        """
