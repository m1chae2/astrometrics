"""Purpose: Tests that the run's measured bases exist before they are used.

Description: The spectroscopy limits that are multiples of the line spread
(the zero-point offset, the refraction limit, the center search and the shift
smoothing) are resolved when a star is applied, classified and checked. The
run's trail width, dispersion and line spread are measured from the spectra
just read, so they must be recorded between reading every star and applying
the first one. Worker processes must read the run's equipment and keep one
frame's measurements out of the next frame's.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.foundation import parameter_context as parameter_context_module
from astrometricslib.foundation.parameter_context import (
    ParameterContext,
    collect_default_notices,
    note_default_notices,
    parameter_context,
    record_basis,
    run_basis_value,
    worker_item_parameter_context,
)
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import REFERENCE_SETUP_BASES
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.tuned_parameter import RunBasis
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    record_measured_spectral_bases,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.wavelength_zero_point import (
    ZERO_POINT_OFFSET_LIMIT_ANGSTROM,
)
from astrometricslib.pipelines.spectroscopy.test.test_extended_target_batch_processing import (
    _build_pipeline,
    _ordinary_star,
)
from astrometricslib.utilities import parallel_batch

WAVELENGTHS = [4000.0 + 10.0 * step for step in range(200)]


def _result(sigma: float) -> dict:
    """Build the part of a star's extraction result the bases are read from.

    Returns
    -------
    result : `dict`
        Wavelengths 1 nanometer (10 Angstroms) apart, in nanometers as an
        extraction result keeps them, and a constant trail width.
    """
    wavelengths_nm = [400.0 + step for step in range(200)]
    return {"wavelengths": wavelengths_nm, "trail_width_px": [sigma] * 200}


def test_the_bases_are_measured_from_the_spectra_and_returned() -> None:
    """The median trail FWHM, pixel step and line spread are recorded."""
    spectra = [(WAVELENGTHS, [2.0] * 200), (WAVELENGTHS, [2.0] * 200), ([], None)]
    with parameter_context() as context:
        measured = record_measured_spectral_bases(spectra)
    assert context.bases[RunBasis.TRAIL_WIDTH_PX] == pytest.approx(2.355 * 2.0)
    assert context.bases[RunBasis.DISPERSION_ANGSTROM_PER_PX] == pytest.approx(10.0)
    assert context.bases[RunBasis.SPECTRAL_RESOLUTION_ANGSTROM] == pytest.approx(2.355 * 2.0 * 10.0)
    assert measured == context.bases


def test_nothing_is_measured_without_spectra() -> None:
    """A run with no spectra measures no basis."""
    with parameter_context() as context:
        assert record_measured_spectral_bases([([], None)]) == {}
    assert context.bases == {}


def test_the_bases_are_known_before_the_first_star_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    """The line spread is recorded after every read, before any apply."""
    pipeline = _build_pipeline()
    stars = [_ordinary_star(100.0 + 50.0 * index, 100.0) for index in range(3)]
    results = iter(_result(2.0) for _ in stars)
    seen: list[tuple[float | None, float]] = []

    def read_star(self: SpectroscopyPipeline, image: object, pos: tuple, **_: object) -> dict:
        """Give a star's extraction result, as if the star was read.

        Returns
        -------
        result : `dict`
            The next fake result.
        """
        return next(results)

    def apply_result(self: SpectroscopyPipeline, star: StellarObject, result: dict, image: object) -> None:
        """Record the zero-point limit and the line spread at this moment."""
        seen.append((
            run_basis_value(RunBasis.SPECTRAL_RESOLUTION_ANGSTROM),
            resolve(ZERO_POINT_OFFSET_LIMIT_ANGSTROM).value,
        ))

    monkeypatch.setattr(SpectroscopyPipeline, "_process_single_star", read_star)
    monkeypatch.setattr(SpectroscopyPipeline, "_apply_result_to_stellar_object", apply_result)
    monkeypatch.setattr(SpectroscopyPipeline, "_subtract_neighbor_wings", lambda *_: None)
    with parameter_context() as context:
        pipeline._process_target_stars(SimpleNamespace(), stars, None, False)
    assert len(seen) == 3
    line_spread = 2.355 * 2.0 * 10.0
    expected_limit = ZERO_POINT_OFFSET_LIMIT_ANGSTROM.reference_value * line_spread
    expected_limit /= REFERENCE_SETUP_BASES[RunBasis.SPECTRAL_RESOLUTION_ANGSTROM]
    for resolution, limit in seen:
        assert resolution == pytest.approx(line_spread)
        assert limit == pytest.approx(expected_limit)
    assert not any("spectral resolution" in notice for notice in context.notices)


def test_a_notice_says_why_the_basis_is_unknown() -> None:
    """The reason tells a missing setup from a basis nobody has measured."""
    with parameter_context() as context:
        resolve(ZERO_POINT_OFFSET_LIMIT_ANGSTROM)
    assert "no setup in the config matches this camera and optic" in context.notices[0]
    with parameter_context() as context:
        context.setup_bases = SimpleNamespace(value=lambda basis: None)
        resolve(ZERO_POINT_OFFSET_LIMIT_ANGSTROM)
    assert "the run has not measured it yet and the setup cannot give it" in context.notices[0]


def test_each_item_of_a_worker_starts_with_no_measured_bases(monkeypatch: pytest.MonkeyPatch) -> None:
    """A basis one frame measured does not carry into the next frame."""
    key = ("Camera", "Optic", 405.0, "SPEC")
    keys_used: list[tuple] = []
    monkeypatch.setattr(parameter_context_module, "current_equipment_key", lambda: key)

    def stand_in_context(*arguments: object) -> object:
        """Open a fresh context, as the real builder does, and note the key.

        Returns
        -------
        manager : `object`
            A context manager that yields a new `ParameterContext`.
        """
        keys_used.append(arguments)
        return parameter_context()

    monkeypatch.setattr(parameter_context_module, "_equipment_parameter_context", stand_in_context)
    with worker_item_parameter_context() as first:
        record_basis(RunBasis.TRAIL_WIDTH_PX, 9.0)
        assert first is not None and first.bases[RunBasis.TRAIL_WIDTH_PX] == pytest.approx(9.0)
    with worker_item_parameter_context() as second:
        assert second is not None and second is not first and second.bases == {}
    assert keys_used == [key, key]


def test_a_worker_with_no_equipment_gets_no_item_context() -> None:
    """With nothing bound, the item reads the active setup as before."""
    with worker_item_parameter_context() as context:
        assert context is None


def test_the_worker_pool_hands_the_equipment_to_each_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker initializer binds the key the parent passed."""
    bound: list[object] = []
    monkeypatch.setattr(parallel_batch, "bind_equipment_key", bound.append)
    key = ("Camera", None, 405.0, "SPEC")
    parallel_batch._initialize_worker_process(niceness=0, max_memory_mb=0, equipment_key=key)
    assert bound == [key]


def test_notices_from_a_worker_reach_the_job() -> None:
    """The defaults a worker used are added once to the job that started it."""
    context = ParameterContext()
    context.note("one")
    with collect_default_notices() as job_notices:
        note_default_notices(["a", "b", "a"])
        note_default_notices(context.notices)
    assert job_notices == ["a", "b", "one"]
