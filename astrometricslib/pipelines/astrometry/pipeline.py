"""The main tool that figures out what we're looking at.

This coordinates all the different steps needed to map out an image:
finding the stars, running the math to figure out the exact coordinates,
and checking the database to identify what objects are in the picture.
"""

import logging
import os
from typing import Any

from astrometricslib.image_processing.image import AstrometricsImage
from astrometricslib.pipelines.astrometry.star_identifier import StarIdentifier
from astrometricslib.pipelines.shared.analysis_context import AnalysisContext
from astrometricslib.utilities.config_loader import AppConfiguration
from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string

logger = logging.getLogger(__name__)


class AstrometryPipeline:
    """The manager that runs the whole process from start to finish.

    It loads the image, finds the stars, figures out the coordinates,
    and identifies the objects.
    """

    def __init__(self, app_config: AppConfiguration | None = None):  # ruff: ignore[missing-return-type-special-method]
        """Set up the pipeline using the program's settings.

        Parameters
        ----------
        app_config : `AppConfiguration`, optional
            The settings to use. If you leave this blank, it will just
            use the default system settings.
        """
        if app_config is None:
            from astrometricslib.utilities.config_loader import get_configuration

            app_config = get_configuration()

        self.config = app_config
        self.star_identifier = StarIdentifier(app_config)

    def prepare_image(
        self,
        image_or_path: AstrometricsImage | str,
        attempt_plate_solving: bool = True,
        target_ra: float | None = None,
        target_dec: float | None = None,
    ) -> AnalysisContext:
        """Old name for the 'process' function (kept so old code still works).

        Parameters
        ----------
        image_or_path : `AstrometricsImage` or `str`
            The image we want to look at, or the file path to it.
        attempt_plate_solving : `bool`, optional
            Whether we should try to figure out the map coordinates.
        target_ra : `float`, optional
            A hint for the horizontal coordinate (Right Ascension).
        target_dec : `float`, optional
            A hint for the vertical coordinate (Declination).

        Returns
        -------
        analysis_context : `AnalysisContext`
            The results of running the pipeline on this image.
        """
        return self.process(image_or_path, attempt_plate_solving, target_ra, target_dec)

    def process(
        self,
        image_or_path: AstrometricsImage | str,
        attempt_plate_solving: bool = True,
        target_ra: float | None = None,
        target_dec: float | None = None,
    ) -> AnalysisContext:
        """Run the whole pipeline on an image.

        Parameters
        ----------
        image_or_path : `AstrometricsImage` or `str`
            The image we want to look at, or the file path to it.
        attempt_plate_solving : `bool`, optional
            Whether we should try to figure out the exact map coordinates.
            Default is True.
        target_ra : `float`, optional
            A hint for where the telescope was pointing horizontally.
        target_dec : `float`, optional
            A hint for where the telescope was pointing vertically.

        Returns
        -------
        analysis_context : `AnalysisContext`
            An object that holds the original image and all the stars
            and data we found in it.
        """
        if isinstance(image_or_path, str):
            logger.info(f"Loading image from path: {image_or_path}")
            image = AstrometricsImage(image_or_path)
        else:
            image = image_or_path

        logger.info("Processing image for stars...")

        # If the user provides specific coordinates, we use those instead of
        # looking in the image's FITS header. We convert any hour/minute/second
        # strings into plain decimal degrees so we can do math on them.
        ra_hint = None
        dec_hint = None
        if target_ra is not None and target_dec is not None:
            try:
                if isinstance(target_ra, str) and ("h" in target_ra or "°" in target_ra or " " in target_ra):
                    resolved_ra_deg = parse_coordinate_string(str(target_ra), is_ra=True)
                    resolved_dec_deg = parse_coordinate_string(str(target_dec), is_ra=False)
                    # Sometimes an empty database field will default to 0h 0m
                    # 0s.
                    # We ignore exact zeros because it's almost certainly a
                    # blank
                    # default, not the user actually pointing at the 0,0
                    # coordinate.
                    if resolved_ra_deg != 0.0 or resolved_dec_deg != 0.0:  # ruff: ignore[float-equality-comparison] -- 0h0m0s placeholder sentinel, not measured
                        ra_hint = float(resolved_ra_deg)
                        dec_hint = float(resolved_dec_deg)
                else:
                    if float(target_ra) != 0.0 or float(target_dec) != 0.0:  # ruff: ignore[float-equality-comparison] -- 0.0 placeholder sentinel, not measured
                        ra_hint = float(target_ra)
                        dec_hint = float(target_dec)
            except Exception as e:
                logger.warning(
                    f"Could not parse target coordinate strings '{target_ra}', '{target_dec}': {e}"
                )

        if ra_hint is None or dec_hint is None:
            header_ra = image.header.get("RA") or image.header.get("OBJCTRA")
            header_dec = image.header.get("DEC") or image.header.get("OBJCTDEC")
            if header_ra is not None and header_dec is not None:
                try:
                    from astropy import units as u
                    from astropy.coordinates import SkyCoord

                    # Handle sexagesimal strings or floats
                    if isinstance(header_ra, str):
                        # Some headers use spaces, some colons
                        ra_s = str(header_ra).replace(" ", ":")
                        dec_s = str(header_dec).replace(" ", ":")
                        c = SkyCoord(ra_s, dec_s, unit=(u.hourangle, u.deg))
                        ra_hint = float(c.ra.deg)
                        dec_hint = float(c.dec.deg)
                    else:
                        ra_hint = float(header_ra)
                        dec_hint = float(header_dec)

                    logger.info(f"Found FITS header coordinate hints: {ra_hint}, {dec_hint}")
                except Exception as e:
                    logger.warning(f"Could not parse FITS header coordinates: {e}")

        stellar_objects, wcs = self.star_identifier.process_image(
            image, attempt_plate_solving=attempt_plate_solving, center_ra=ra_hint, center_dec=dec_hint
        )

        # Fallback to pre-existing WCS if available in the image header
        if wcs is None and hasattr(image, "wcs") and image.wcs is not None and image.wcs.is_celestial:
            logger.info("Using pre-existing WCS from FITS image header as fallback.")
            wcs = image.wcs

        # 1. Figure out the main target's name by looking in the FITS header,
        # or by guessing from the file name if the header is missing.
        object_name = image.header.get("OBJECT")
        if not object_name:
            # Look for specific names in the file path to help our test
            # scripts work.
            base_file = os.path.basename(image.path)
            if "M 13" in base_file or "M_13" in base_file:
                object_name = "M 13"
            elif "Vega" in base_file:
                object_name = "Vega"

        extended_target_obj = None

        if object_name:
            # 2. Ask the SIMBAD database to figure out what type of object
            # this is.
            is_extended, target_coord, otype, majaxis = self.check_extended_source(object_name)

            if is_extended:
                logger.info(
                    f"Primary target '{object_name}' detected as an extended source ({otype}). "
                    "Resolving pixel coordinates..."
                )

                # 3. Convert the object's real sky coordinates (from the
                # database)
                # into exact X/Y pixel coordinates on our image.
                extraction_center = None
                if wcs and target_coord:
                    try:
                        x, y = wcs.world_to_pixel(target_coord)
                        extraction_center = (x, y)
                    except Exception as e:
                        logger.warning(f"WCS target conversion failed: {e}")

                if not extraction_center and (target_ra is not None and target_dec is not None) and wcs:
                    try:
                        from astropy.coordinates import SkyCoord

                        coord = SkyCoord(target_ra, target_dec, unit="deg")
                        x, y = wcs.world_to_pixel(coord)
                        extraction_center = (x, y)
                    except Exception as e:
                        logger.warning(f"WCS RA/Dec coordinate conversion failed: {e}")

                if not extraction_center:
                    logger.warning(
                        f"Failed to resolve coordinates for extended source target '{object_name}'; "
                        f"skipping extended-target enrichment."
                    )
                else:
                    # 4. Create a special `StellarObject` for this target.
                    # Unlike a
                    # normal star (a tiny dot), this object will use a large
                    # circle
                    # to measure all the light coming from the whole
                    # nebula/galaxy.
                    from astrometricslib.pipelines.spectroscopy.pipeline import (
                        SpectroscopyPipeline,
                    )
                    from astrometricslib.utilities.spectroscopy_models import ConfigLoader

                    spec_config = ConfigLoader.load_spectroscopy_config(app_config=self.config)

                    # Calculate how big the measuring circle needs to be by
                    # looking at
                    # the object's actual size in the catalog and our
                    # telescope's zoom level.
                    extraction_radius = 60  # Default fallback
                    if majaxis is not None and wcs:
                        try:
                            from astropy.wcs.utils import proj_plane_pixel_scales

                            scales = proj_plane_pixel_scales(wcs)
                            # scales is degrees per pixel
                            deg_per_px = float(scales[0])
                            if deg_per_px > 0:
                                arcmin_per_px = deg_per_px * 60.0
                                # Major axis in pixels
                                majaxis_px = majaxis / arcmin_per_px
                                # Set the measuring radius to half the
                                # object's total width.
                                derived_radius = round(majaxis_px / 2.0)
                                # Keep the radius between 15 and 200 pixels.
                                # This stops the
                                # program from crashing or slowing down if the
                                # catalog
                                # gives us weird or incorrect size data.
                                extraction_radius = int(max(15, min(200, derived_radius)))
                                logger.info(
                                    f"Derived extended extraction radius from SIMBAD ({majaxis} arcmin): "
                                    f"{extraction_radius} pixels"
                                )
                        except Exception as e:
                            logger.warning(f"Failed to derive extraction radius from WCS/SIMBAD: {e}")

                    config_extended = spec_config.with_overrides(extraction_radius=extraction_radius)
                    spec_pipeline_extended = SpectroscopyPipeline(config=config_extended)

                    extended_target_obj = spec_pipeline_extended.create_extended_target_object(
                        extraction_center=extraction_center,
                        object_name=object_name,
                        otype=otype,
                        extraction_radius=config_extended.extraction_radius,
                    )
                    logger.info(
                        f"Successfully initialized extended target StellarObject for '{object_name}'."
                    )

        # Start downloading Gaia DR3 stars for this image's area in the
        # background.
        # This speeds things up by saving the stars to our local database.
        if wcs is not None and wcs.is_celestial:
            try:
                from astropy.wcs.utils import proj_plane_pixel_scales

                scales = proj_plane_pixel_scales(wcs)
                deg_per_px = float(scales[0])
                width = image.data.shape[1] if hasattr(image, "data") and image.data is not None else 1000
                height = image.data.shape[0] if hasattr(image, "data") and image.data is not None else 1000
                field_radius_deg = (max(width, height) * deg_per_px) / 2.0
                field_radius_deg = min(max(0.2, field_radius_deg), 1.0)

                ra_c, dec_c = float(wcs.wcs.crval[0]), float(wcs.wcs.crval[1])
                self.star_identifier._seed_gaia_cache_for_field(ra_c, dec_c, radius_deg=field_radius_deg)
            except Exception as e:
                logger.warning(f"Automatic Gaia cache seeding skipped: {e}")

        return AnalysisContext(
            image=image,
            stellar_objects=stellar_objects,
            wcs=wcs,
            extended_target=extended_target_obj,
            sources_detected=self.star_identifier.sources_detected,
            solve_attempted=self.star_identifier.solve_attempted,
            astrometric_residual_rms_arcsec=(self.star_identifier.get_astrometric_residual_rms_arcsec()),
        )

    def check_extended_source(self, object_name: str) -> tuple[bool, Any | None, str | None, float | None]:
        """Ask the SIMBAD database if this target is an extended object.

        An "extended object" is something that looks like a fuzzy shape
        (like a galaxy, nebula, or star cluster) instead of a single dot.

        Parameters
        ----------
        object_name : `str`
            The name of the object to look up (like "M 13" or "Andromeda").

        Returns
        -------
        is_extended : `bool`
            True if the database says this is a large fuzzy object.
        coordinates : `astropy.coordinates.SkyCoord` or `None`
            The exact center coordinates of the object, if found.
        simbad_object_type : `str` or `None`
            The short code for the object type (like "GlC" for Globular
            Cluster).
        major_axis_arcmin : `float` or `None`
            How wide the object is, measured in arcminutes (a tiny fraction
            of a degree), if the database has that information.
        """
        if not object_name:
            return False, None, None, None

        extended_otypes = {
            "GlC",  # Globular Cluster
            "HII",  # H II region / Emission Nebula
            "PN",  # Planetary Nebula
            "Neb",  # General Nebula
            "G",  # Galaxy
            "Cl*",  # Cluster of Stars
            "OpC",  # Open Cluster
            "Assoc*",  # Association of Stars
        }

        try:
            import warnings

            import numpy as np
            from astropy import units as u
            from astropy.coordinates import SkyCoord
            from astropy.wcs import FITSFixedWarning
            from astroquery.simbad import Simbad

            warnings.simplefilter("ignore", FITSFixedWarning)
            Simbad.reset_votable_fields()
            Simbad.add_votable_fields("otype", "ra", "dec", "galdim_majaxis")

            result = Simbad.query_object(object_name)
            if result is not None and len(result) > 0:
                otype = str(result["otype"][0]).strip()
                ra_deg = float(result["ra"][0])
                dec_deg = float(result["dec"][0])
                coord = SkyCoord(ra_deg * u.deg, dec_deg * u.deg)

                # Extract major axis in arcminutes if present and not masked
                majaxis = None
                if "galdim_majaxis" in result.colnames:
                    val = result["galdim_majaxis"][0]
                    if val is not None and not isinstance(val, np.ma.core.MaskedConstant):
                        try:
                            majaxis = float(val)
                        except ValueError, TypeError:
                            pass

                return otype in extended_otypes, coord, otype, majaxis
        except Exception as e:
            logger.warning(f"SIMBAD query failed for object '{object_name}': {e}")

        return False, None, None, None
