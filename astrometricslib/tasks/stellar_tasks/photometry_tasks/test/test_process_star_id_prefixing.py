"""Unit tests for VariabilityAnalyzer.process()'s id_prefix parameter.

Session-scoped photometry runs one independent process() call per
observing session (pixel-position re-centroiding against a single
reference frame only holds within one session's consistent framing).
Since Star_N ids are otherwise only unique within a single process()
call, each session's call must be able to give its stars a distinct,
deterministic id prefix so they don't collide when persisted together.
"""

import numpy as np
import pytest
from astropy.io import fits
from astropy.modeling.models import Gaussian2D

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.tasks.stellar_tasks.photometry_tasks.variability_analyzer import VariabilityAnalyzer


def _write_single_star_fits(path):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Write a synthetic light frame FITS file with one Gaussian source."""
    rng = np.random.default_rng(0)
    data = rng.normal(100.0, 5.0, (64, 64)).astype(np.float32)
    yy, xx = np.mgrid[0:64, 0:64]
    data += Gaussian2D(5000.0, 32.0, 32.0, 2.0, 2.0)(xx, yy)
    header = fits.Header()
    header["DATE-OBS"] = "2026-01-01T00:00:00"
    fits.PrimaryHDU(data.astype(np.float32), header=header).writeto(path, overwrite=True)


def test_process_uses_plain_star_ids_by_default(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify process() ids default to plain Star_N without a prefix."""
    path = tmp_path / "frame.fits"
    _write_single_star_fits(path)

    analyzer = VariabilityAnalyzer()
    analyzer.process([str(path)])

    assert analyzer.stellar_objects
    assert analyzer.stellar_objects[0].id == "Star_1"


def test_process_prefixes_star_ids_when_given(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify process() prefixes ids with id_prefix when given."""
    path = tmp_path / "frame.fits"
    _write_single_star_fits(path)

    analyzer = VariabilityAnalyzer()
    analyzer.process([str(path)], id_prefix="sess:")

    assert analyzer.stellar_objects
    assert analyzer.stellar_objects[0].id == "sess:Star_1"


def test_process_seeded_stars_keep_their_real_identity(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify seed_stars are tracked with their own id/name.

    Mirrors the pixel position identify_session_stars() would produce:
    since photometry's shared identify step detects on this exact same
    reference frame file, a seed star's star_data centroid is already
    valid in this frame's own pixel space -- no reprojection needed.
    """
    path = tmp_path / "frame.fits"
    _write_single_star_fits(path)

    seed_star = StellarObject(id="* alf Lyr", name="Vega")
    seed_star.star_data = {"xcentroid": 32.0, "ycentroid": 32.0}
    seed_star.right_ascension = 279.23473479
    seed_star.declination = 38.78368896
    seed_star.spectral_type = "A0Va"

    analyzer = VariabilityAnalyzer()
    analyzer.process([str(path)], id_prefix="sess:", seed_stars=[seed_star])

    assert len(analyzer.stellar_objects) == 1
    tracked = analyzer.stellar_objects[0]
    # id_prefix is ignored for seeded stars -- they already have a real id
    assert tracked.id == "* alf Lyr"
    assert tracked.name == "Vega"
    assert tracked.right_ascension == pytest.approx(279.23473479)
    assert tracked.spectral_type == "A0Va"
    # Flux was actually measured at the seeded position, not left unset
    assert tracked.flux > 0
    assert tracked.light_curve is not None
    assert tracked.light_curve.fluxes == [tracked.flux]


def test_process_seeded_stars_skip_entries_missing_a_centroid(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A seed star with no usable pixel position is silently skipped."""
    path = tmp_path / "frame.fits"
    _write_single_star_fits(path)

    good_star = StellarObject(id="* alf Lyr", name="Vega")
    good_star.star_data = {"xcentroid": 32.0, "ycentroid": 32.0}

    positionless_star = StellarObject(id="Unmatched", name="Unmatched")
    positionless_star.star_data = {}

    analyzer = VariabilityAnalyzer()
    analyzer.process([str(path)], seed_stars=[good_star, positionless_star])

    assert len(analyzer.stellar_objects) == 1
    assert analyzer.stellar_objects[0].id == "* alf Lyr"
