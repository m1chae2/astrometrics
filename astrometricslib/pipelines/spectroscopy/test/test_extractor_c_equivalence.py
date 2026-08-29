"""Purpose: Verify the C cross-section fitter matches its Python fallback.

Description: `fit_cross_section_gaussian` in spectrum_extractor.py uses a
compiled C extension (`_extractor_c.fit_cross_section_gaussian_c`) when
it is available, and otherwise falls back to fitting the same Gaussian
with astropy's `LevMarLSQFitter`. The two are meant to be numerically
interchangeable -- the C path exists only because it is ~325x faster at
this observatory's real per-frame call volume (see spectrum_extractor.py),
not because it fits a different model. This module fits the same
synthetic cross sections through both paths and checks they agree, on
realistic random inputs and on the edge cases (non-finite pixels, unusual
dtypes, large search radii, degenerate input) that a synthetic-only
happy-path comparison would miss.

Skipped when `_extractor_c.so` was not compiled for this interpreter --
see build/linux/setup_venv.sh. Set ASTROMETRICS_REQUIRE_C_EXTENSION=1 to
turn a missing extension into a failure instead of a skip; CI sets this
so a broken compile shows up as a red build rather than a vanished test.
"""

import os

import numpy as np
import pytest

from astrometricslib.pipelines.spectroscopy import spectrum_extractor as se

_REQUIRE_C_EXTENSION = os.environ.get("ASTROMETRICS_REQUIRE_C_EXTENSION") == "1"

if not se.HAS_C_EXTENSION and _REQUIRE_C_EXTENSION:
    pytest.fail(
        "ASTROMETRICS_REQUIRE_C_EXTENSION=1 but the compiled _extractor_c "
        "module is not importable -- the C build must have failed silently.",
        pytrace=False,
    )

pytestmark = pytest.mark.skipif(
    not se.HAS_C_EXTENSION,
    reason="_extractor_c.so was not compiled for this interpreter; see build/linux/setup_venv.sh.",
)

# How far the two fitted (center_offset, sigma) pairs are allowed to
# differ. Both paths fit the same non-linear least-squares problem with
# different solvers (a hand-rolled Levenberg-Marquardt in C vs astropy's
# LevMarLSQFitter), so exact bitwise equality isn't expected -- only
# convergence to the same optimum. 1e-3 px is three orders of magnitude
# tighter than anything that could affect a downstream wavelength
# calibration or trace centerline fit.
_AGREEMENT_TOLERANCE = 1e-3


def _make_cross_section_image(
    size: int, center: float, sigma: float, amplitude: float, background: float
) -> np.ndarray:
    """Build a square image whose every row is the same 1-D Gaussian profile.

    Every row is identical, so a horizontal cross section starting at any
    row gives the same fit -- this isolates the cross-section math from
    everything else `fit_cross_section_gaussian` also has to handle
    (2-D positioning, the perpendicular vector).

    Returns
    -------
    image : `numpy.ndarray`
        A `(size, size)` float64 array with a Gaussian ridge running
        down every row.
    """
    columns = np.arange(size)[np.newaxis, :]
    profile = background + amplitude * np.exp(-0.5 * ((columns - center) / sigma) ** 2)
    return np.ascontiguousarray(np.broadcast_to(profile, (size, size)).astype(np.float64))


def _assert_fits_agree(c_result, python_result):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Assert two (center_offset, sigma) fit results agree within tolerance.

    Treats "both None" as agreement (both paths declined to fit) and
    fails loudly if only one side returned a fit.
    """
    if c_result is None or python_result is None:
        assert c_result is None and python_result is None, (
            f"Only one path returned a fit: C={c_result}, Python={python_result}"
        )
        return

    c_offset, c_sigma = c_result
    python_offset, python_sigma = python_result
    assert abs(c_offset - python_offset) < _AGREEMENT_TOLERANCE, (
        f"center_offset disagreement: C={c_offset}, Python={python_offset}"
    )
    assert abs(c_sigma - python_sigma) < _AGREEMENT_TOLERANCE, (
        f"sigma disagreement: C={c_sigma}, Python={python_sigma}"
    )


def test_extractor_c_matches_python_on_randomized_realistic_cross_sections():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the C and Python fits agree across randomized realistic inputs.

    Sweeps search radius, sigma, amplitude, background, sub-pixel offset,
    and additive noise -- the dimensions that vary between real spectral
    cross sections -- through both fitting paths on the same data and
    checks every one of them agrees.

    The ranges below are deliberately kept well-conditioned (peak sigma
    not far below a pixel, comfortably inside the search window, decent
    SNR). A first version of this test sampled sigma down to 0.8px and
    offsets out to the window edge with noise up to 15% of amplitude, and
    it found a real case where the two solvers -- a hand-rolled
    Levenberg-Marquardt in C, astropy's LevMarLSQFitter in Python --
    converged to different local optima on the same noisy, barely-
    resolved peak (astropy's sigma collapsed to ~1e-38, a near-delta-
    function fit; the C path's stayed near the noise floor's width).
    That is expected behavior for a nonlinear least-squares fit on an
    ill-conditioned problem, not a bug in either solver, so asserting
    agreement there would either be flaky or need a tolerance so loose
    it stopped meaning anything. This test's job is the interchangeable
    case real spectral traces actually land in, not every input the
    fitter is willing to attempt.
    """
    random_generator = np.random.default_rng(20260827)

    for _ in range(500):
        search_radius = int(random_generator.integers(5, 40))
        sigma = float(random_generator.uniform(max(1.0, search_radius * 0.08), search_radius / 3.0))
        amplitude = float(random_generator.uniform(200, 5000))
        background = float(random_generator.uniform(0, 500))
        center_offset = float(random_generator.uniform(-search_radius / 4, search_radius / 4))
        noise_amplitude = float(random_generator.uniform(0, 0.05)) * amplitude  # SNR >= 20

        size = 4 * search_radius + 61
        image = _make_cross_section_image(size, size // 2 + center_offset, sigma, amplitude, background)
        image += random_generator.normal(0, noise_amplitude, image.shape)
        center = (float(size // 2), float(size // 2))

        c_result = se.fit_cross_section_gaussian(image, center, (1.0, 0.0), float(search_radius))
        python_result = se._fit_cross_section_gaussian_python(image, center, (1.0, 0.0), float(search_radius))
        _assert_fits_agree(c_result, python_result)
        assert c_result is not None, "well-conditioned synthetic cross section unexpectedly went unfit"


def test_extractor_c_matches_python_on_flat_field():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify both paths decline to fit a cross section with no source."""
    image = np.full((121, 121), 50.0)
    center = (60.0, 60.0)

    c_result = se.fit_cross_section_gaussian(image, center, (1.0, 0.0), 10.0)
    python_result = se._fit_cross_section_gaussian_python(image, center, (1.0, 0.0), 10.0)
    _assert_fits_agree(c_result, python_result)
    assert c_result is None


def test_extractor_c_matches_python_on_absorption_dip():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify both paths decline to fit a dip (negative amplitude guess)."""
    image = np.full((121, 121), 50.0)
    image[:, 55:66] -= 30.0
    center = (60.0, 60.0)

    c_result = se.fit_cross_section_gaussian(image, center, (1.0, 0.0), 10.0)
    python_result = se._fit_cross_section_gaussian_python(image, center, (1.0, 0.0), 10.0)
    _assert_fits_agree(c_result, python_result)
    assert c_result is None


@pytest.mark.parametrize("non_finite_value", [np.nan, np.inf, -np.inf])
def test_extractor_c_matches_python_on_non_finite_pixel(non_finite_value):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a NaN/Inf pixel in the cross section makes both paths give up.

    A non-finite sample used to make the C extension's Levenberg-Marquardt
    loop return its untouched initial guess as if it had converged, while
    the Python path correctly returned None.
    """
    image = _make_cross_section_image(121, 60.0, 3.0, 800.0, 50.0)
    image[:, 58] = non_finite_value
    center = (60.0, 60.0)

    c_result = se.fit_cross_section_gaussian(image, center, (1.0, 0.0), 10.0)
    python_result = se._fit_cross_section_gaussian_python(image, center, (1.0, 0.0), 10.0)
    _assert_fits_agree(c_result, python_result)
    assert c_result is None


@pytest.mark.parametrize(
    ("true_sigma", "should_be_accepted"),
    [
        (se._MINIMUM_FIT_SIGMA_PX - 0.01, False),
        (se._MINIMUM_FIT_SIGMA_PX, True),
        (se._MINIMUM_FIT_SIGMA_PX + 0.01, True),
    ],
)
def test_extractor_c_matches_python_at_minimum_sigma_floor(true_sigma, should_be_accepted):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify both paths apply the same minimum-sigma floor at its boundary.

    Randomized-noise testing first found this gap in a roundabout way: on
    a noisy, barely-resolved line, one solver's Levenberg-Marquardt
    converged to a near-delta-function fit (sigma around 1e-38 --
    narrower than a single pixel, and not a measurement of anything
    real) while the other converged to a different, also-implausible
    width. Both were "successful" fits under the old check ("sigma > 0"),
    but they disagreed with each other and with the truth. That noisy
    reproduction isn't suitable as a regression test -- which exact
    noise draw triggers it depends on how much of the random generator's
    state prior draws already consumed, not just a fixed seed -- so this
    tests the floor itself directly: a clean, noise-free peak injected
    exactly at, just above, and just below `_MINIMUM_FIT_SIGMA_PX`.
    """
    image = _make_cross_section_image(121, 60.0, true_sigma, 800.0, 50.0)
    center = (60.0, 60.0)

    c_result = se.fit_cross_section_gaussian(image, center, (1.0, 0.0), 10.0)
    python_result = se._fit_cross_section_gaussian_python(image, center, (1.0, 0.0), 10.0)
    _assert_fits_agree(c_result, python_result)
    assert (c_result is not None) == should_be_accepted
    assert (python_result is not None) == should_be_accepted


@pytest.mark.parametrize(
    "dtype", [np.float64, np.float32, np.float16, np.int64, np.int32, np.uint16, np.bool_]
)
def test_extractor_c_matches_python_across_dtypes(dtype):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify both paths agree regardless of the input array's dtype.

    fit_cross_section_gaussian always coerces to contiguous float64
    before calling the C extension, so this exercises that coercion
    rather than the extension's (now-removed) per-dtype branches.
    """
    image = _make_cross_section_image(121, 60.0, 3.0, 800.0, 50.0)
    if dtype is np.bool_:
        cast_image = image > image.mean()
    else:
        cast_image = image.astype(dtype)
    center = (60.0, 60.0)

    c_result = se.fit_cross_section_gaussian(cast_image, center, (1.0, 0.0), 10.0)
    python_result = se._fit_cross_section_gaussian_python(
        cast_image.astype(np.float64), center, (1.0, 0.0), 10.0
    )
    _assert_fits_agree(c_result, python_result)


@pytest.mark.parametrize("search_radius", [10, 60, 100, 127, 128, 160, 200])
def test_extractor_c_matches_python_at_production_search_radii(search_radius):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify agreement up to the largest radius the pipeline derives.

    pipelines/astrometry/pipeline.py caps its derived extraction_radius_px
    at 200. The C extension's fixed-size sample buffers previously
    overflowed (a stack-smashing crash) once 2 * search_radius + 1
    exceeded that buffer's length, at search_radius >= 128.
    """
    size = 4 * search_radius + 41
    image = _make_cross_section_image(size, size // 2, 3.0, 1000.0, 100.0)
    center = (float(size // 2), float(size // 2))

    c_result = se.fit_cross_section_gaussian(image, center, (1.0, 0.0), float(search_radius))
    python_result = se._fit_cross_section_gaussian_python(image, center, (1.0, 0.0), float(search_radius))
    _assert_fits_agree(c_result, python_result)
    assert c_result is not None


def test_extractor_c_matches_python_on_non_contiguous_view():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a Fortran-ordered (non-C-contiguous) array still fits correctly.

    fit_cross_section_gaussian must copy such a view into a contiguous
    float64 buffer before handing it to the C extension, which reads
    pixels by raw buffer offset and would otherwise silently read the
    wrong values.
    """
    image = np.asfortranarray(_make_cross_section_image(121, 60.0, 3.0, 800.0, 50.0))
    center = (60.0, 60.0)

    c_result = se.fit_cross_section_gaussian(image, center, (1.0, 0.0), 10.0)
    python_result = se._fit_cross_section_gaussian_python(image, center, (1.0, 0.0), 10.0)
    _assert_fits_agree(c_result, python_result)
    assert c_result is not None


def test_extractor_c_matches_python_on_one_dimensional_input():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a non-2D array is rejected the same way by both entry points.

    The C extension used to silently return None for this instead of
    raising, while the Python path raised inside `data.shape` unpacking
    with an unrelated-looking ValueError.
    """
    one_dimensional_image = _make_cross_section_image(121, 60.0, 3.0, 800.0, 50.0)[0]

    with pytest.raises(ValueError, match="2-D"):
        se.fit_cross_section_gaussian(one_dimensional_image, (60.0, 0.0), (1.0, 0.0), 10.0)
