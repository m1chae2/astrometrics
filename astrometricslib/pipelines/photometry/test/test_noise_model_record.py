"""Tests for how the photometry runner records the variability indices.

The runner copies each candidate's indices onto its `VariableCandidate`, fits
the run's noise model from the values the sessions stored on the stars, and
records the fitted curve on the quality summary so a plot can show it.
"""

import numpy as np
import pytest

from astrometricslib.models.quality_summary import NoiseModelPoint, PhotometryPipelineQualityMetrics
from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.photometry import runner
from astrometricslib.pipelines.photometry.processing.variability_indices import NoiseModel
from astrometricslib.pipelines.photometry.test.test_variability_injection import run_field


def test_the_noise_curve_is_recorded_as_one_point_per_bin() -> None:
    """Each bin of the model becomes a point with its star count."""
    model = NoiseModel((-9.0, -7.0), (0.01, 0.03), (12, 14))

    points = runner._noise_curve_points(model)

    assert points == [
        NoiseModelPoint(instrumentalMag=-9.0, rmsMag=0.01, starCount=12),
        NoiseModelPoint(instrumentalMag=-7.0, rmsMag=0.03, starCount=14),
    ]
    assert runner._noise_curve_points(None) == []


def test_the_curve_points_are_serialized_under_their_camel_case_names() -> None:
    """The quality summary carries the curve in its payload."""
    metrics = PhotometryPipelineQualityMetrics(
        starsProcessed=1,
        starsFound=1,
        framesProcessed=1,
        variableCandidateCount=0,
        noiseModelCurve=[NoiseModelPoint(instrumentalMag=-8.0, rmsMag=0.02, starCount=20)],
    )

    dumped = metrics.model_dump(by_alias=True)

    assert dumped["noiseModelCurve"] == [{"instrumentalMag": -8.0, "rmsMag": 0.02, "starCount": 20}]


def test_the_runs_curve_is_fitted_from_the_values_the_sessions_stored() -> None:
    """The fit reads each star's stored `instrumental_mag` and `rms_mag`."""
    run = run_field(5)
    stars = [star.photometry for star in run.field.stars]

    magnitudes, scatters = runner._noise_model_inputs(stars)

    assert len(magnitudes) == len(scatters) >= 300
    assert magnitudes == [p.instrumental_mag for p in stars if p.instrumental_mag is not None and p.rms_mag]
    model = runner.fit_noise_model(magnitudes, scatters)
    assert model is not None
    typical = runner._typical_noise_mag(model, stars)
    assert typical == pytest.approx(model.expected_rms_mag(float(np.median(magnitudes))))
    assert 0.0 < typical < 0.1


def test_a_run_without_stored_indices_has_no_curve_and_no_noise_floor() -> None:
    """Light curves with no indices give no inputs, no typical scatter."""
    stars = [PhotometryResult(fluxes_normalized=[1.0, 1.0])]

    assert runner._noise_model_inputs(stars) == ([], [])
    assert runner._typical_noise_mag(None, stars) is None


def test_a_candidate_carries_the_indices_that_flagged_it() -> None:
    """A candidate row shows the indices and the score that flagged it."""
    star = StellarObject(id="A", rightAscension=10.0, declination=20.0)
    star.photometry = PhotometryResult(
        mean_flux=1.0,
        coefficient_of_variation=0.1,
        excess_scatter=3.0,
        reduced_chi_square=9.0,
        stetson_j=1.2,
        variability_score=2.0,
    )

    candidate = runner._format_variable_candidates([star])[0]

    assert (candidate.excess_scatter, candidate.reduced_chi_square) == (3.0, 9.0)
    assert (candidate.stetson_j, candidate.variability_score) == (1.2, 2.0)
