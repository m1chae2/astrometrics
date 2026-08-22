"""SIMBAD online catalog query driver.

Queries the CDS SIMBAD database via astroquery for stellar objects in a
sky region. Returns only StellarObject instances (non-stellar SIMBAD objects
such as galaxies and nebulae are skipped — they are not appropriate for the
star overlay rendering pipeline).

REQ: PLN-3.1
"""

import logging

import astropy.units as u
from astropy.coordinates import SkyCoord

from astrometricslib import StellarObject
from wayfindinglib.drivers.catalog.base_catalog_driver import CatalogDriver

logger = logging.getLogger(__name__)

_SIMBAD_QUERY_TIMEOUT_SECONDS = 10
_SIMBAD_MAGNITUDE_LIMIT = 17.0


def resolve_simbad_radec(raw_ra, raw_dec) -> tuple[float, float]:  # ruff: ignore[missing-type-function-argument]
    """Resolve a SIMBAD row's RA/Dec value into decimal degrees.

    Tries the fast numeric-degree path first — recent astroquery versions
    return 'ra'/'dec' as decimal-degree floats — falling back to sexagesimal
    parsing (older versions return 'RA'/'DEC' as hourangle-RA/degree-Dec
    strings). Shared by every caller that resolves SIMBAD coordinates so they
    can't independently drift out of sync with astroquery's version behavior.

    Parameters
    ----------
    raw_ra : Any
        The raw value from a SIMBAD result row's RA column.
    raw_dec : Any
        The raw value from a SIMBAD result row's Dec column.

    Returns
    -------
    Tuple[float, float]
        (ra_deg, dec_deg).

    Raises
    ------
    ValueError
        If neither the numeric nor sexagesimal parse succeeds:
        propagated from `astropy.coordinates.SkyCoord` when it
        cannot parse `raw_ra`/`raw_dec` as hourangle/degree strings.
    """  # ruff: ignore[docstring-extraneous-exception] -- ValueError is genuinely raised by SkyCoord() on malformed input; not visible to static analysis
    try:
        return float(raw_ra), float(raw_dec)
    except TypeError, ValueError:
        resolved_coordinate = SkyCoord(str(raw_ra), str(raw_dec), unit=(u.hourangle, u.deg), frame="icrs")
        return resolved_coordinate.ra.deg, resolved_coordinate.dec.deg


def _find_column(colnames: list[str], *candidates: str) -> str | None:
    """Return the actual column name matching a candidate, case-insensitively.

    astroquery has changed SIMBAD's returned column casing/names
    across versions (e.g. 'MAIN_ID' -> 'main_id', 'FLUX_V' -> 'V'),
    so column lookups must not assume a fixed case or name.

    Parameters
    ----------
    colnames : List[str]
        Column names present on the result table.
    candidates : str
        Acceptable column names to match against, in priority order.

    Returns
    -------
    str
        The matching actual column name, or None if none of the
        candidates are present.
    """
    lookup = {name.upper(): name for name in colnames}
    for candidate in candidates:
        actual = lookup.get(candidate.upper())
        if actual is not None:
            return actual
    return None


class SimbadCatalogDriver(CatalogDriver):
    """Query driver for the CDS SIMBAD online astronomical database.

    Uses astroquery.simbad to retrieve stars classified by OTYPE or
    spectral type within a circular search region, down to
    V < _SIMBAD_MAGNITUDE_LIMIT. Non-stellar objects (galaxies,
    nebulae, clusters) are intentionally excluded.

    REQ: PLN-3.1
    """

    @property
    def driver_name(self) -> str:
        """The internal identifier for this driver."""
        return "simbad"

    @property
    def display_name(self) -> str:
        """The human-readable name for this driver."""
        return "SIMBAD"

    @property
    def maximum_query_radius_degrees(self) -> float:
        """The maximum search radius, in degrees, SIMBAD accepts."""
        return 5.0

    def query_region(
        self,
        ra_degrees: float,
        dec_degrees: float,
        radius_degrees: float,
    ) -> list[StellarObject]:
        """Query SIMBAD for stellar objects in a circular sky region.

        Parameters
        ----------
        ra_degrees : float
            Center Right Ascension in degrees (ICRS).
        dec_degrees : float
            Center Declination in degrees (ICRS).
        radius_degrees : float
            Search radius in degrees.

        Returns
        -------
        List[StellarObject]
            Transient StellarObject instances. Never persisted to the database.
        """
        results: list[StellarObject] = []

        try:
            from astroquery.simbad import Simbad
        except ImportError:
            logger.error("astroquery is not installed; cannot query SIMBAD catalog.")
            return results

        custom_simbad = Simbad()
        custom_simbad.TIMEOUT = _SIMBAD_QUERY_TIMEOUT_SECONDS
        custom_simbad.add_votable_fields("otype", "sp", "flux(V)")

        center = SkyCoord(ra=ra_degrees, dec=dec_degrees, unit=(u.deg, u.deg), frame="icrs")

        try:
            table = custom_simbad.query_region(
                center,
                radius=radius_degrees * u.deg,
                criteria=f"V < {_SIMBAD_MAGNITUDE_LIMIT}",
            )
            if table is None or len(table) == 0:
                return results

            # Column names/casing have changed across astroquery versions
            # (e.g. 'MAIN_ID' -> 'main_id', 'FLUX_V' -> 'V'), so resolve them
            # by candidate name rather than assuming a fixed name.
            colnames = table.colnames
            main_id_col = _find_column(colnames, "MAIN_ID")
            ra_col = _find_column(colnames, "RA")
            dec_col = _find_column(colnames, "DEC")
            otype_col = _find_column(colnames, "OTYPE")
            sp_type_col = _find_column(colnames, "SP_TYPE")
            flux_v_col = _find_column(colnames, "FLUX_V", "V")

            for row in table:
                main_id = str(row[main_id_col]) if main_id_col else ""

                # SIMBAD short OTYPE for an individual star is '*',
                # 'V*', '**', 'WD*', etc. Star clusters use 'Cl*'.
                # Verbose form is 'Star', 'Neutron Star', etc.
                otype = ""
                if otype_col:
                    otype_val = row[otype_col]
                    if otype_val is not None and not getattr(otype_val, "mask", False):
                        otype = str(otype_val).strip().upper()

                spectral_type = ""
                if sp_type_col:
                    try:
                        sp_val = row[sp_type_col]
                        if sp_val is not None and not getattr(sp_val, "mask", False):
                            spectral_type = str(sp_val).strip()
                    except Exception as exc:
                        logger.debug("Failed to parse spectral type from SIMBAD row: %s", exc)

                magnitude = None
                if flux_v_col:
                    try:
                        flux_value = row[flux_v_col]
                        if not getattr(flux_value, "mask", False):
                            magnitude = float(flux_value)
                    except ValueError, TypeError:
                        pass

                # Short OTYPEs for individual stars end with '*'
                # (e.g. '*', 'V*', 'WD*'). 'Cl*' (star cluster) ends
                # with '*' but is not an individual star.
                is_individual_star = otype.endswith("*") and "CL" not in otype
                is_stellar = is_individual_star or "STAR" in otype or bool(spectral_type)
                if not is_stellar:
                    continue

                raw_ra = row[ra_col] if ra_col else None
                raw_dec = row[dec_col] if dec_col else None
                try:
                    ra_value, dec_value = resolve_simbad_radec(raw_ra, raw_dec)
                except Exception as coordinate_error:
                    logger.debug(
                        "Skipping SIMBAD object %s — invalid coordinates: %s",
                        main_id,
                        coordinate_error,
                    )
                    continue

                results.append(
                    StellarObject(
                        id=main_id,
                        name=main_id,
                        ra=ra_value,
                        dec=dec_value,
                        magnitude=magnitude if magnitude is not None else "",
                        spectralType=spectral_type or "",
                    )
                )

        except Exception as query_error:
            logger.warning("SIMBAD catalog query failed or timed out: %s", query_error)

        return results
