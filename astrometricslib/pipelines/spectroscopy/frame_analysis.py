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

    from astrometricslib.drivers import disk_interface
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

    existing = disk_interface.load_stellar_objects(config) or []
    existing_map = {obj.id: obj for obj in existing}

    for obj in stellar_objects:
        if obj.id in existing_map:
            for tid in obj.target_ids:
                if tid not in existing_map[obj.id].target_ids:
                    existing_map[obj.id].target_ids.append(tid)
            existing_map[obj.id].spectrum_data_processed = obj.spectrum_data_processed
        else:
            existing_map[obj.id] = obj

    disk_interface.save_stellar_objects(config, list(existing_map.values()))

    return context, stellar_objects
