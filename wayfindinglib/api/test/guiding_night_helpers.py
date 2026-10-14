"""Purpose: Helpers that record synthetic nights for the facade tests.

Description: Fills the log database, the Butler and the guiding-run store of
an isolated `ObservatoryControl` with nights of guiding on the rig its
fixture configures, so the session-analysis tests can state only what makes
a night different.
"""

import numpy as np

from astrometricslib import observing_night_id
from wayfindinglib import ObservatoryControl
from wayfindinglib.models.equipment_and_site.equipment_fingerprint import build_equipment_fingerprint
from wayfindinglib.models.session.ekos_session import EkosSessionContext, SessionEquipmentAttribution
from wayfindinglib.models.session.guiding_run import GuidingRunSummary

FIRST_NIGHT = 1790217108.0
"""2026-09-23 20:31:48 MDT, as seconds since the epoch."""


def active_fingerprint() -> str:
    """Build the fingerprint of the rig `control` configures.

    Returns
    -------
    fingerprint : `str`
        Telescope, camera and guide optics of the active rig.
    """
    return build_equipment_fingerprint("Short", "Main A", 120.0, 206.265 * 3.75 / 120.0)


def record_guiding_night(
    control: ObservatoryControl,
    day: int,
    snr: float = 300.0,
    sigma: float = 1.0,
    lost: int = 0,
    fingerprint: str | None = None,
) -> str:
    """Record one synthetic night of guiding with its session record and run.

    Returns
    -------
    night : `str`
        The observing-night id the data was filed under.
    """
    start = FIRST_NIGHT + day * 86400.0
    night = observing_night_id(start)
    generator = np.random.default_rng(day)
    control._context.records.replace_guiding_samples([
        {
            "timestamp": start + 60.0 + 3.2 * index,
            "dra": float(generator.normal(0.0, sigma)),
            "ddec": float(generator.normal(0.0, sigma)),
            "pulse_ra": 0.0,
            "pulse_dec": 0.0,
            "snr": snr,
            "source": "ekos_guide_log",
        }
        for index in range(400)
    ])
    control._context.butler.put(
        EkosSessionContext(
            id=f"session-{night}",
            session_id=night,
            started_at=start,
            ended_at=start + 7200.0,
            equipment=SessionEquipmentAttribution(
                equipment_fingerprint=fingerprint or active_fingerprint(),
                guide_scale_matches_configuration=True,
            ),
        ),
        "ekos_session_context",
        {"id": f"session-{night}"},
    )
    control.guiding.save_run(
        GuidingRunSummary(
            id=f"guide_log-{night}.txt#0",
            session_id=night,
            source_file_name=f"guide_log-{night}.txt",
            written_by_ekos=True,
            started_at=start + 60.0,
            ended_at=start + 60.0 + 1280.0,
            pixel_scale_arcsec_per_px=6.445,
            focal_length_mm=120.0,
            ra_rate_arcsec_per_second=5.0,
            dec_rate_arcsec_per_second=7.5,
            frames_total=400 + lost,
            frames_lost=lost,
            samples_stored=400,
        )
    )
    return night


def record_good_history(control: ObservatoryControl, nights: int = 6) -> list[str]:
    """Record several good nights with slightly varied quality.

    Returns
    -------
    nights : `list` [`str`]
        The night ids, oldest first.
    """
    return [
        record_guiding_night(control, day, snr=280.0 + 10.0 * day, sigma=1.0 + 0.05 * day, lost=day % 2)
        for day in range(nights)
    ]
