"""Running the spectroscopy pipeline on one image frame at a time.

Most spectroscopy processing works through a whole observing session at
once, but some callers just want to point at a single FITS file and get
its spectrum. This runs the astrometry step (to find the frame's stars
and orientation) followed by the spectroscopy step, then saves whatever
stars it found.
"""

from typing import Any

from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.frame_grouping import add_frame


def analyze_frame_spectroscopy(target: Target, path: str, limit: int = 10) -> tuple[Any, list[Any]]:
    """Run spectroscopy analysis on a single frame in this target's context.

    Returns
    -------
    result : `tuple[Any, list[Any]]`
        A tuple ``(context, stellar_objects)`` of the astrometry
        pipeline context and the extracted stellar objects.
    """
    if not any(f.path == path for f in target.frames):
        add_frame(target, path)

    from astrometricslib.data_access.catalog_access import CatalogAccess
    from astrometricslib.pipelines.astrometry.pipeline import AstrometryPipeline
    from astrometricslib.pipelines.spectroscopy.pipeline import (
        SpectroscopyPipeline,
    )
    from astrometricslib.utilities.config_loader import get_configuration

    config = get_configuration()
    astrometry = AstrometryPipeline(config)
    context = astrometry.prepare_image(path, attempt_plate_solving=False)

    from astrometricslib.utilities import ConfigLoader

    spec_config = ConfigLoader.load_spectroscopy_config(app_config=config)
    spectroscopy = SpectroscopyPipeline(spec_config)
    stellar_objects = spectroscopy.process(context, limit=limit)

    for obj in stellar_objects:
        if target.id not in obj.target_ids:
            obj.target_ids.append(target.id)

    # Touches only the rows for this frame's own stars, gap-filling
    # their target_ids and spectrum onto whatever was already recorded
    # for that id rather than replacing the row outright -- a plain
    # get()-then-put() here would race a concurrent writer, and a
    # full-catalog replace would needlessly rewrite every other star.
    def _merge_frame_star(existing: Any | None, updated: Any) -> Any:
        if existing is None:
            return updated
        for target_id in updated.target_ids:
            if target_id not in existing.target_ids:
                existing.target_ids.append(target_id)
        existing.spectrum_data_processed = updated.spectrum_data_processed
        return existing

    CatalogAccess(config).merge_and_record("stellar_catalog", stellar_objects, _merge_frame_star)

    return context, stellar_objects
