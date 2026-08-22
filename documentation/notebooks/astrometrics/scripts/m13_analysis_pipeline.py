"""Run the full M 13 analysis pipeline and save the results.

Populates the persisted stellar catalog that `m13_full_target_analysis.ipynb`
section 9 and `m13_combined_dashboard.py` read from -- both only display
already-persisted results, neither runs any pipeline stage itself. Assumes
`stacked_image` (Luminance) and `stacked_spectral_target` (SPEC) already
exist for the target (i.e. stacking, sections 3-4 of the notebook, has
already been done); this script only runs the three analysis stages and
saves.

**Architecture Note:** This script demonstrates the **Domain Logic Layer**.
The pipeline acts as a client orchestrating underlying scientific domain
tasks (`run_astrometry`, `run_photometry`, `run_spectroscopy`), managing
cross-pipeline persistence into the central `StellarCatalog`.

Run directly:

    python m13_analysis_pipeline.py [target_id]
"""

import logging
import sys

from astrometricslib import Astrometrics, FilterType

logger = logging.getLogger(__name__)


def run_pipeline(target_id: str = "M 13") -> None:
    """Run astrometry, photometry, and spectroscopy analysis, then save.

    Parameters
    ----------
    target_id : `str`, optional
        Target to analyze, default `"M 13"`.

    Raises
    ------
    ValueError
        Raised if `target_id` doesn't exist, or is missing
        `stacked_image` or `stacked_spectral_target`.
    """
    astrometrics = Astrometrics()
    target = astrometrics.targets.get(target_id)
    if not target:
        raise ValueError(f"Target {target_id!r} not found in the library.")
    if not target.stacked_image:
        raise ValueError(f"Target {target_id!r} has no stacked_image; stack the Luminance frames first.")
    if not target.stacked_spectral_target:
        raise ValueError(f"Target {target_id!r} has no stacked_spectral_target; stack the SPEC frames first.")

    l_frames = [
        frame
        for frame in target.frames
        if frame.camera == "ZWO ASI 533MM Pro"
        and frame.filter in (FilterType.L, FilterType.LUMINANCE, "L", "Luminance")
    ]
    logger.info(f"{len(l_frames)} Luminance frames for photometry.")

    logger.info("Running astrometry analysis...")
    astrometry_result = astrometrics.processing.run_astrometry(target, filter_type="L")
    logger.info(
        f"Astrometry solved: {astrometry_result.get('wcs') is not None}; "
        f"{len(astrometry_result.get('stellar_objects', []))} stars identified."
    )

    logger.info("Running photometry analysis...")
    photometry_result = astrometrics.processing.run_photometry(
        target,
        filter_type="L",
        frames=l_frames,
        use_astrometry_seed=True,
    )
    logger.info(f"Photometry status: {photometry_result.get('status')}")

    logger.info("Running spectroscopy analysis...")
    spectroscopy_result = astrometrics.processing.run_spectroscopy(target)
    spectral_stars = spectroscopy_result.get("stellar_objects", [])
    matched = sum(1 for star in spectral_stars if star.id.endswith("::spectroscopy"))
    logger.info(
        f"Spectroscopy: {len(spectral_stars)} stars extracted, {matched} registered against astrometry."
    )
    logger.info("Done.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    target_arg = sys.argv[1] if len(sys.argv) > 1 else "M 13"
    run_pipeline(target_arg)
