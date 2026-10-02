"""Tests for the report of stored telescope and ISO values a re-scan changes.

Uses stand-in frames and small FITS files, so it never reads the real library.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
from astropy.io import fits

from astrometricslib.scripts.report_equipment_disagreements import (
    find_iso_disagreements,
    find_telescope_disagreements,
)
from astrometricslib.utilities.observatory_setups import ObservatorySetups, OpticConfig, SetupConfig

SETUPS = ObservatorySetups(
    optics=(
        OpticConfig(name="Apertura 75Q", focal_length_mm=405.0),
        OpticConfig(name="Nikkor 300mm", focal_length_mm=300.0),
    ),
    setups=(
        SetupConfig(name="D5300 on Apertura", camera_name="Nikon D5300", optic_name="Apertura 75Q"),
        SetupConfig(name="D5300 on Nikkor", camera_name="Nikon D5300", optic_name="Nikkor 300mm"),
    ),
)


def make_frame(**fields: object) -> SimpleNamespace:
    """Build a stand-in frame record.

    Returns
    -------
    frame : `types.SimpleNamespace`
        A frame with the attributes the report reads.
    """
    defaults = {
        "role": "LIGHT",
        "camera": "Nikon DSLR DSC D5300",
        "focal_length_mm": 300.0,
        "telescope": "Nikkor 300mm",
        "path": "/x.fits",
        "iso": "800",
    }
    defaults.update(fields)
    return SimpleNamespace(**defaults)


def test_only_the_frames_whose_telescope_would_change_are_counted() -> None:
    """Check the real-library case: 300 mm frames labelled Apertura."""
    target = SimpleNamespace(
        frames=[
            make_frame(),
            make_frame(telescope="Apertura 75Q"),
            make_frame(telescope="Apertura 75Q"),
            make_frame(role="DARK", telescope="Apertura 75Q"),
        ]
    )
    result = find_telescope_disagreements([target], SETUPS)
    assert dict(result) == {("Nikon DSLR DSC D5300", "Apertura 75Q", "Nikkor 300mm"): 2}


def test_a_frame_with_no_frames_attribute_is_skipped() -> None:
    """Check that a target with no frames does not raise."""
    assert dict(find_telescope_disagreements([SimpleNamespace(frames=None)], SETUPS)) == {}


def test_iso_disagreements_compare_the_header_with_the_stored_value(tmp_path: Path) -> None:
    """Check the forced-800 case: the header says 100 but 800 is stored."""

    def write(name: str, **cards: object) -> str:
        """Write a small FITS file and return its path.

        Returns
        -------
        path : `str`
            The file just written.
        """
        hdu = fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32))
        for key, value in cards.items():
            hdu.header[key] = value
        path = tmp_path / name
        hdu.writeto(path)
        return str(path)

    target = SimpleNamespace(
        frames=[
            make_frame(path=write("a.fits", ISOSPEED=100), iso="800"),
            make_frame(path=write("b.fits", ISOSPEED=800), iso="800"),
            make_frame(path=write("c.fits"), iso="800"),
            make_frame(path=str(tmp_path / "missing.fits"), iso="800"),
        ]
    )
    result = find_iso_disagreements([target])
    assert dict(result) == {("Nikon DSLR DSC D5300", "800", "100"): 1}
