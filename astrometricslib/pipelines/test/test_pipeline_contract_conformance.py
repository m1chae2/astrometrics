"""Checks that every pipeline adapter actually conforms to its contract.

Adding `AnalysisPipeline` costs nothing if nothing checks that a class
implementing it is honest about which pipeline it is. This file is what
makes the interface load-bearing rather than decorative: each adapter's
`pipeline_name` must match the `pipeline_name` literal on the
`*QualitySummary` class `run_pipeline` assigns its output to. If the two
ever drift apart, `target.<pipeline_name>_quality_summary` would be set
under the wrong pipeline's name, and this is the test that would catch it
before a caller notices a summary is missing where it should be.
"""

import pytest

from astrometricslib.models.quality_summary import (
    AsteroidRecoveryQualitySummary,
    AstrometryQualitySummary,
    PhotometryQualitySummary,
    SpectroscopyQualitySummary,
)
from astrometricslib.pipelines.asteroid_recovery.runner import AsteroidRecoveryPipelineAdapter
from astrometricslib.pipelines.astrometry.runner import AstrometryPipelineAdapter
from astrometricslib.pipelines.contract import AnalysisPipeline
from astrometricslib.pipelines.photometry.runner import PhotometryPipelineAdapter
from astrometricslib.pipelines.spectroscopy.runner import SpectroscopyPipelineAdapter

# Every adapter, paired with the *QualitySummary class run_pipeline sets
# onto the target under `<pipeline_name>_quality_summary`.
_ADAPTER_AND_SUMMARY_PAIRS = [
    (AstrometryPipelineAdapter, AstrometryQualitySummary),
    (SpectroscopyPipelineAdapter, SpectroscopyQualitySummary),
    (PhotometryPipelineAdapter, PhotometryQualitySummary),
    (AsteroidRecoveryPipelineAdapter, AsteroidRecoveryQualitySummary),
]


@pytest.mark.parametrize(("adapter_class", "summary_class"), _ADAPTER_AND_SUMMARY_PAIRS)
def test_adapter_pipeline_name_matches_its_quality_summary(adapter_class, summary_class):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an adapter's `pipeline_name` matches its summary's literal.

    `run_pipeline` builds the target attribute name from
    `adapter.pipeline_name` (`f"{adapter.pipeline_name}_quality_summary"`)
    but the summary object itself carries its own `pipeline_name` field,
    set independently in `models/quality_summary.py`. Nothing forces
    these two to agree except this test.
    """
    adapter = adapter_class()
    summary_pipeline_name = summary_class.model_fields["pipeline_name"].default

    assert adapter.pipeline_name == summary_pipeline_name


@pytest.mark.parametrize("adapter_class", [pair[0] for pair in _ADAPTER_AND_SUMMARY_PAIRS])
def test_every_adapter_is_a_real_analysis_pipeline(adapter_class):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify each adapter actually implements every abstract method.

    `AnalysisPipeline` is an ABC; a subclass missing one of
    `screen_input`/`run`/`validate_output`/`to_result_dict`/`pipeline_name`
    cannot be instantiated at all. Constructing it is the check.
    """
    adapter = adapter_class()
    assert isinstance(adapter, AnalysisPipeline)
