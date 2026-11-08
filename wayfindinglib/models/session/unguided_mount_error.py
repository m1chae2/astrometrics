"""Purpose: The mount's unguided error, measured from guide logs.

Description: Results of `wayfindinglib.analytics.unguided_mount_error`. A guide
log holds the error the guide star showed while the guider was correcting it,
and every correction the guider sent. Adding the corrections back gives the
error the mount would have made on its own: its periodic error (a repeating
sway that follows the worm gear), its slow drift and its random jitter.

Every length is in arcseconds on the sky, every time in seconds, and every
phase in radians. These are the numbers a mount simulator needs to behave like
the real mount. The models are plain results and are never stored.
"""

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

_MODEL_CONFIG = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class PeriodicErrorFit(BaseModel):
    """A repeating error fitted to one guiding run, or pooled from several.

    The error is modeled as
    ``amplitude * sin(2 pi (t - t0) / period + phase)``, where t0 is the
    reference time.

    Parameters
    ----------
    period_seconds : `float`
        The period, in seconds.
    period_uncertainty_seconds : `float`
        One standard error of the period, in seconds.
    amplitude_arcsec : `float`
        The zero-to-peak size of the repeating error, in arcseconds. The
        peak-to-peak size is twice this.
    amplitude_uncertainty_arcsec : `float`
        One standard error of the amplitude, in arcseconds.
    phase_radians : `float` or `None`
        The phase at `reference_epoch_seconds`, from 0 to 2 pi. `None` for a
        pooled fit: the worm gear is at an unrelated angle at the start of
        each night, so phases of different nights cannot be averaged.
    phase_uncertainty_radians : `float` or `None`
        One standard error of the phase. `None` when `phase_radians` is.
    reference_epoch_seconds : `float` or `None`
        The time the phase is counted from, in seconds since the Unix epoch
        (the start of the run). `None` for a pooled fit.
    significance : `float`
        The amplitude divided by its uncertainty. For a pooled fit, the
        runs' significances added in quadrature.
    runs_used : `int`
        1 for a single run, otherwise the number of runs pooled. A pooled
        period and amplitude are the medians of the runs' values.
    period_scatter_seconds : `float` or `None`
        For a pooled fit with two or more runs, the robust spread (1.4826
        times the median absolute deviation) of the runs' periods, in
        seconds.
    amplitude_scatter_arcsec : `float` or `None`
        For a pooled fit with two or more runs, the robust spread of the
        runs' amplitudes, in arcseconds.
    """

    model_config = _MODEL_CONFIG

    period_seconds: float
    period_uncertainty_seconds: float
    amplitude_arcsec: float
    amplitude_uncertainty_arcsec: float
    phase_radians: float | None = None
    phase_uncertainty_radians: float | None = None
    reference_epoch_seconds: float | None = None
    significance: float
    runs_used: int = 1
    period_scatter_seconds: float | None = None
    amplitude_scatter_arcsec: float | None = None


class AxisErrorTerms(BaseModel):
    """What is left of one axis's unguided error after the repeating part.

    Parameters
    ----------
    samples : `int`
        Guide samples the terms rest on.
    drift_arcsec_per_minute : `float`
        The slow linear drift, in arcseconds per minute. The sign follows the
        guide log's axis (positive error is the direction the guider's raw
        error is positive in).
    drift_uncertainty_arcsec_per_minute : `float`
        One standard error of the drift.
    random_rms_arcsec : `float`
        The root-mean-square of what remains after the drift and repeating
        error are removed, in arcseconds. It also holds the guide star's
        measurement noise (seeing and centroid error), so it is an upper
        bound on the mount's own random error.
    random_step_rms_arcsec : `float`
        The root-mean-square change of that remainder from one guide frame
        to the next, in arcseconds. Steady measurement noise of size s gives
        about 1.4 s; a mount wandering a little each frame gives that
        little.
    """

    model_config = _MODEL_CONFIG

    samples: int
    drift_arcsec_per_minute: float
    drift_uncertainty_arcsec_per_minute: float
    random_rms_arcsec: float
    random_step_rms_arcsec: float


class PooledAxisTerms(BaseModel):
    """The `AxisErrorTerms` of several runs, pooled.

    Parameters
    ----------
    runs_used : `int`
        Runs pooled.
    samples : `int`
        Total guide samples.
    drift_median_arcsec_per_minute : `float`
        The median of the runs' drifts, in arcseconds per minute, with its
        sign. A few runs with a wild drift (a cloud, a bumped mount) do not
        move a median, so this is the typical drift. Use it, not a mean or
        a root-mean-square, to set a simulated mount.
    drift_spread_arcsec_per_minute : `float`
        How much the runs' drifts differ, in arcseconds per minute: 1.4826
        times the median absolute deviation, which equals the standard
        deviation when the drifts are normally distributed and ignores
        outliers. The sign of the drift changes with where the telescope
        points, so a spread larger than the median is normal.
    random_median_arcsec : `float`
        The median of the runs' `AxisErrorTerms.random_rms_arcsec`. It
        includes slow wander, so it is larger than the frame-to-frame
        jitter.
    random_step_median_arcsec : `float`
        The median of the runs' `AxisErrorTerms.random_step_rms_arcsec`.
        Divide it by the square root of 2 for the size of a jitter that is
        independent from frame to frame, which is what a simulator that adds
        a new random number each frame needs.
    """

    model_config = _MODEL_CONFIG

    runs_used: int
    samples: int
    drift_median_arcsec_per_minute: float
    drift_spread_arcsec_per_minute: float
    random_median_arcsec: float
    random_step_median_arcsec: float


class UnguidedRunError(BaseModel):
    """The unguided error measured on one guiding run.

    Parameters
    ----------
    run_id : `str`
        The guiding run (the guide log's file name and position in it).
    session_id : `str`
        The observing night.
    started_at : `float`
        When the run began, in seconds since the Unix epoch.
    duration_seconds : `float`
        The time the samples used cover, in seconds.
    samples_used : `int`
        Guide samples used.
    periodic_error : `PeriodicErrorFit` or `None`
        The repeating error in right ascension, as fitted. `None` when the
        run is too short or has too few samples to fit one (`notes` says
        which). Check `significant_periodic_error` before using it.
    significant_periodic_error : `bool`
        Whether `periodic_error` is large enough compared with its
        uncertainty to count as a measurement.
    ra : `AxisErrorTerms` or `None`
        Right ascension terms.
    dec : `AxisErrorTerms` or `None`
        Declination terms.
    notes : `list` [`str`]
        Plain-language remarks, such as why no fit was made.
    """

    model_config = _MODEL_CONFIG

    run_id: str
    session_id: str
    started_at: float
    duration_seconds: float
    samples_used: int
    periodic_error: PeriodicErrorFit | None = None
    significant_periodic_error: bool = False
    ra: AxisErrorTerms | None = None
    dec: AxisErrorTerms | None = None
    notes: list[str] = Field(default_factory=list)


class UnguidedMountErrorSummary(BaseModel):
    """The unguided error pooled over every run measured.

    Parameters
    ----------
    periodic_error : `PeriodicErrorFit` or `None`
        The repeating error pooled over the runs with a significant fit.
        `None` when no run has one.
    ra : `PooledAxisTerms` or `None`
        Right ascension terms over all runs.
    dec : `PooledAxisTerms` or `None`
        Declination terms over all runs.
    runs_measured : `int`
        Runs the measurement was tried on.
    nights_used : `list` [`str`]
        The observing nights those runs belong to, sorted.
    """

    model_config = _MODEL_CONFIG

    periodic_error: PeriodicErrorFit | None = None
    ra: PooledAxisTerms | None = None
    dec: PooledAxisTerms | None = None
    runs_measured: int = 0
    nights_used: list[str] = Field(default_factory=list)


class UnguidedMountError(BaseModel):
    """The mount's unguided error, run by run and pooled.

    Parameters
    ----------
    runs : `list` [`UnguidedRunError`]
        One entry per guiding run measured, oldest first.
    summary : `UnguidedMountErrorSummary`
        The pooled numbers.
    excluded_nights : `list` [`str`]
        The nights left out on request.
    message : `str`
        One plain sentence about what was measured.
    """

    model_config = _MODEL_CONFIG

    runs: list[UnguidedRunError] = Field(default_factory=list)
    summary: UnguidedMountErrorSummary = Field(default_factory=UnguidedMountErrorSummary)
    excluded_nights: list[str] = Field(default_factory=list)
    message: str = ""
