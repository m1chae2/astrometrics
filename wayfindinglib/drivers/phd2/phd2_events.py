"""Purpose: Pure parsing of PHD2 event-stream JSON messages.

Description: Maps PHD2's GuideStep event fields onto GuidingSample's real
field names, and adapts GuidingSample back to the flat dict shape
observatory_guiding_telemetry.py already expects from
get_telescope_status()'s guidingHistory. No I/O -- these are pure functions
over already-decoded JSON, fully unit-testable with synthetic events.
"""

from typing import Any

from wayfindinglib.models.session.telemetry import GuidingSample

# PHD2 reports guide-pulse duration and direction as separate fields
# (RADuration/RADirection, DECDuration/DECDirection); this sign convention
# turns each pair into the single signed pulse_ra/pulse_dec value
# GuidingSample expects.
_NEGATIVE_RA_DIRECTIONS = {"West"}
_NEGATIVE_DEC_DIRECTIONS = {"South"}


def parse_guide_step_event(event: dict[str, Any]) -> GuidingSample | None:
    """Parse a PHD2 GuideStep event into a GuidingSample.

    Parameters
    ----------
    event : `dict`
        One decoded JSON message from PHD2's event stream.

    Returns
    -------
    guiding_sample : `Optional[GuidingSample]`
        Maps Timestamp/RADistanceGuide (or RADistanceRaw)/DECDistanceGuide
        (or DECDistanceRaw)/RADuration+RADirection/DECDuration+DECDirection
        /SNR into GuidingSample's real field names (time, dra, ddec,
        pulse_ra, pulse_dec, snr). Returns `None` for any event whose
        `"Event"` key isn't `"GuideStep"` -- Phase 0 only records guide
        steps.
    """
    if event.get("Event") != "GuideStep":
        return None

    ra_duration = float(event.get("RADuration", 0.0))
    if event.get("RADirection") in _NEGATIVE_RA_DIRECTIONS:
        ra_duration = -ra_duration

    dec_duration = float(event.get("DECDuration", 0.0))
    if event.get("DECDirection") in _NEGATIVE_DEC_DIRECTIONS:
        dec_duration = -dec_duration

    return GuidingSample(
        time=float(event.get("Timestamp", 0.0)),
        dra=float(event.get("RADistanceGuide", event.get("RADistanceRaw", 0.0))),
        ddec=float(event.get("DECDistanceGuide", event.get("DECDistanceRaw", 0.0))),
        pulse_ra=ra_duration,
        pulse_dec=dec_duration,
        snr=event.get("SNR"),
    )


def to_legacy_history_entry(guiding_sample: GuidingSample) -> dict[str, Any]:
    """Adapt a GuidingSample to the legacy guiding-telemetry history shape.

    Parameters
    ----------
    guiding_sample : `GuidingSample`
        The sample to convert.

    Returns
    -------
    history_entry : `dict`
        Keyed `timestamp`/`raDrift`/`decDrift`/`raPulse`/`decPulse` -- the
        pre-existing shape `get_telescope_status()`'s `guidingHistory`
        already returns, so that consumer keeps working unmodified rather
        than the shape silently changing underneath it.
    """
    return {
        "timestamp": guiding_sample.time,
        "raDrift": guiding_sample.dra,
        "decDrift": guiding_sample.ddec,
        "raPulse": guiding_sample.pulse_ra,
        "decPulse": guiding_sample.pulse_dec,
    }
