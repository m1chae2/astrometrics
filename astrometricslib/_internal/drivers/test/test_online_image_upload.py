"""Tests for the online solve of a whole image.

The online service accepts only a flat (two-dimensional) image, so a color
stack is averaged to one plane before it is uploaded, and a flat image is
uploaded as it is.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib._internal.drivers.astrometry_net_client import AstrometryNetOnlineClient


@pytest.fixture
def solver() -> AstrometryNetOnlineClient:
    """Return an online client with a made-up key (nothing is sent).

    Returns
    -------
    solver : `AstrometryNetOnlineClient`
        A client whose service object each test replaces.
    """
    return AstrometryNetOnlineClient(api_key="test-key")


def test_a_colour_stack_is_flattened_before_upload(
    solver: AstrometryNetOnlineClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """astrometry.net's uploader crashes on a 3D array.

    It raised "weights.ndim (2) must match len(axes) (3)" for every DSLR
    stack, so the online fallback was unusable for exactly the targets
    most likely to need it.
    """
    colour_path = tmp_path / "colour.fits"
    fits.PrimaryHDU(np.ones((3, 8, 6), dtype=np.float32)).writeto(colour_path)
    uploaded = {}

    def _fake_solve_from_image(path, **kwargs: object):  # ruff: ignore[missing-return-type-private-function, missing-type-function-argument]
        with fits.open(path) as hdul:
            uploaded["ndim"] = hdul[0].data.ndim
        return fits.Header({"CRVAL1": 1.0})

    monkeypatch.setattr(solver, "astrometry_net", SimpleNamespace(solve_from_image=_fake_solve_from_image))

    assert solver.solve_image(str(colour_path)) is not None
    assert uploaded["ndim"] == 2


def test_a_mono_stack_is_uploaded_unchanged(
    solver: AstrometryNetOnlineClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Monochrome frames must not pay for a rewrite they do not need."""
    mono_path = tmp_path / "mono.fits"
    fits.PrimaryHDU(np.ones((8, 6), dtype=np.float32)).writeto(mono_path)
    uploaded = {}

    def _fake_solve_from_image(path, **kwargs: object):  # ruff: ignore[missing-return-type-private-function, missing-type-function-argument]
        uploaded["path"] = path
        return fits.Header({"CRVAL1": 1.0})

    monkeypatch.setattr(solver, "astrometry_net", SimpleNamespace(solve_from_image=_fake_solve_from_image))
    solver.solve_image(str(mono_path))

    assert uploaded["path"] == str(mono_path)
