"""Purpose: A synthetic guided run with a known mount error, for tests.

Description: Builds the samples a guide log would hold when a proportional
guider corrects a mount whose own error is known: a sine of chosen period,
amplitude and phase, a linear drift and a small random walk. Tests of the
unguided-error measurement use it to check that the inputs are recovered.
"""

import numpy as np

from wayfindinglib.analytics.unguided_mount_error import GuideRunSeries

START = 1.7e9
"""Start of the default synthetic run, in seconds since the epoch."""

RATE = 7.0
"""Guide rate of the synthetic mount, in arcseconds per second."""


def synthetic_run(
    *,
    seed: int = 1,
    duration: float = 2400.0,
    cadence: float = 3.0,
    period: float = 480.0,
    amplitude: float = 4.0,
    phase: float = 1.1,
    drift_per_minute: float = 0.5,
    noise: float = 0.3,
    walk: float = 0.05,
    dither_every: int | None = None,
    dither: float = 4.0,
    aggressiveness: float = 0.7,
    rate: float | None = RATE,
    run_id: str = "run",
    night: str = "2026-01-01",
    start: float = START,
) -> GuideRunSeries:
    """Simulate a run guided by a proportional guider.

    The mount's own error is a sine, a drift and a small random walk. The
    guider measures the total error with noise and sends a pulse that
    removes a share of it. Dithers move the guider's lock position.

    Returns
    -------
    series : `GuideRunSeries`
        The samples a guide log would hold.
    """
    generator = np.random.default_rng(seed)
    count = int(duration / cadence)
    relative = np.arange(count) * cadence
    mount = (
        amplitude * np.sin(2.0 * np.pi * relative / period + phase)
        + drift_per_minute / 60.0 * relative
        + np.cumsum(generator.normal(0.0, walk, count))
    )
    applied = 0.0
    lock = 0.0
    measured = []
    pulses = []
    for index in range(count):
        if dither_every and index and index % dither_every == 0:
            lock += dither
        error = mount[index] + applied - lock + generator.normal(0.0, noise)
        pulse = round(-aggressiveness * error / RATE * 1000.0) if abs(error) > 0.15 else 0.0
        measured.append(error)
        pulses.append(pulse)
        applied += pulse / 1000.0 * RATE
    measured_array = np.array(measured)
    pulse_array = np.array(pulses, dtype=float)
    return GuideRunSeries(
        run_id=run_id,
        session_id=night,
        times=start + relative,
        ra_error_arcsec=measured_array,
        dec_error_arcsec=0.1 * generator.normal(size=count) + 0.2 / 60.0 * relative,
        ra_pulse_ms=pulse_array,
        dec_pulse_ms=np.zeros(count),
        ra_rate_arcsec_per_second=rate,
        dec_rate_arcsec_per_second=rate,
    )
