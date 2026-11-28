#!/usr/bin/env python3
"""Purpose: Copy the study frames out of the library and strip their solutions.

Description: Each frame of the sample is read from the library (never
written) and copied to ``<study>/frames/<id>.fits`` with its plate-solution
cards (the sky map and SIP distortion terms) removed. The pointing-hint cards
stay: ``RA``, ``DEC``, ``OBJCTRA``, ``OBJCTDEC``, ``FOCALLEN`` and ``XPIXSZ``.
The old plate solution of the library copy is saved to
``<study>/out/library_wcs/<id>.hdr`` as a third reference. The manifest,
``<study>/out/manifest.json``, records each frame's hint cards and the
library it came from, so the later scripts can refuse to write there.

Usage::

    prep_frames.py --library <library folder> --study <study folder>
"""

import argparse
import json
from pathlib import Path

from astropy.io import fits
from study_common import WCS_CARD_PATTERN, add_study_argument, open_study

ZWO_M13 = "lights/M 13/Apertura 75Q/ZWO ASI 533MM Pro"
ZWO_M31 = "lights/M 31/Apertura 75Q/ZWO ASI 533MM Pro"
NIKON_M13 = "lights/M 13/Apertura 75Q/Nikon DSLR DSC D5300"

# The study sample: (study id, path inside the library, note). The poor
# frames (trailed, grating, off field, short exposures) test the solvers on
# frames a night can produce; the stacks test the finished products.
SAMPLE = [
    ("m13_z_L003", ZWO_M13 + "/M_13_Light_Luminance_003.fits", "frame"),
    ("m13_z_L012", ZWO_M13 + "/M_13_Light_Luminance_012.fits", "frame"),
    ("m13_z_L021", ZWO_M13 + "/M_13_Light_Luminance_021.fits", "frame"),
    ("m13_z_L030", ZWO_M13 + "/M_13_Light_Luminance_030.fits", "frame"),
    ("m13_z_L042", ZWO_M13 + "/M_13_Light_Luminance_042.fits", "frame"),
    ("m13_z_L036_trailed", ZWO_M13 + "/_excluded/M_13_Light_Luminance_036.fits", "poor: trailed (set aside)"),
    ("m13_z_S010_grating", ZWO_M13 + "/M_13_Light_Spectroscopy_010.fits", "poor: grating frame"),
    ("m13_z_S030_grating", ZWO_M13 + "/M_13_Light_Spectroscopy_030.fits", "poor: grating frame"),
    ("m13_z_J004", ZWO_M13 + "/M_13_Light_Luminance_Spectroscopy_004.fits", "frame (2026-01-11)"),
    ("m13_z_J007", ZWO_M13 + "/M_13_Light_Luminance_Spectroscopy_007.fits", "frame (2026-01-11)"),
    ("m31_z_001_20s", ZWO_M31 + "/M_31_Light_Luminance_001.fits", "short 20 s"),
    ("m31_z_002_30s", ZWO_M31 + "/M_31_Light_Luminance_002.fits", "short 30 s"),
    ("m31_z_004_120s", ZWO_M31 + "/M_31_Light_Luminance_004.fits", "120 s"),
    ("m31_z_020", ZWO_M31 + "/M_31_Light_Luminance_020.fits", "frame"),
    ("m31_z_040", ZWO_M31 + "/M_31_Light_Luminance_040.fits", "frame"),
    ("m31_z_060", ZWO_M31 + "/M_31_Light_Luminance_060.fits", "frame"),
    ("m31_z_080", ZWO_M31 + "/M_31_Light_Luminance_080.fits", "frame"),
    ("m31_z_100", ZWO_M31 + "/M_31_Light_Luminance_100.fits", "frame"),
    ("m31_z_119", ZWO_M31 + "/M_31_Light_Luminance_119.fits", "frame (end of night)"),
    (
        "m31_z_121_offfield",
        ZWO_M31 + "/_excluded/M_31_Light_Luminance_121.fits",
        "poor: off field (set aside)",
    ),
    ("m13_n_A001", NIKON_M13 + "/M13_Light_001.fits", "Nikon CFA"),
    ("m13_n_A015", NIKON_M13 + "/M13_Light_015.fits", "Nikon CFA"),
    ("m13_n_B001", NIKON_M13 + "/M_13_Light_001.fits", "Nikon CFA"),
    ("m13_n_B015", NIKON_M13 + "/M_13_Light_015.fits", "Nikon CFA"),
    ("m13_n_B030", NIKON_M13 + "/M_13_Light_030.fits", "Nikon CFA"),
    ("m13_stack_zwo", "stacks/lights/M 13/M_13_L_Stacked_ZWO_ASI_533MM_Pro_405mm.fits", "stack"),
    ("m31_stack_zwo", "stacks/lights/M 31/M_31_L_Stacked_ZWO_ASI_533MM_Pro_405mm.fits", "stack"),
    (
        "m13_stack_nikon",
        "stacks/lights/M 13/M_13_NONE_Stacked_Nikon_DSLR_DSC_D5300_300mm.fits",
        "Nikon stack (RGB)",
    ),
]

# Header cards kept in the manifest: the pointing hints and the facts the
# judge needs (date, exposure, size, color filter pattern).
HINT_KEYS = [
    "DATE-OBS",
    "EXPTIME",
    "RA",
    "DEC",
    "OBJCTRA",
    "OBJCTDEC",
    "FOCALLEN",
    "XPIXSZ",
    "YPIXSZ",
    "XBINNING",
    "BAYERPAT",
    "NAXIS",
    "NAXIS1",
    "NAXIS2",
    "NAXIS3",
    "BITPIX",
    "INSTRUME",
    "TELESCOP",
]


def copy_frame(source: Path, destination: Path, old_wcs_path: Path) -> fits.Header:
    """Copy one frame without its plate solution and save the old solution.

    Parameters
    ----------
    source : `pathlib.Path`
        The library frame, opened read-only.
    destination : `pathlib.Path`
        The stripped copy inside the study folder.
    old_wcs_path : `pathlib.Path`
        Where to save the library copy's plate-solution cards.

    Returns
    -------
    header : `astropy.io.fits.Header`
        The library frame's full header.
    """
    with fits.open(source, memmap=False) as hdul:
        header = hdul[0].header.copy()
        data = hdul[0].data
        old_wcs_path.write_text(
            "\n".join(str(card) for card in header.cards if WCS_CARD_PATTERN.match(card.keyword))
        )
        stripped = header.copy()
        for key in list(stripped.keys()):
            if key and WCS_CARD_PATTERN.match(key):
                del stripped[key]
        # HISTORY and COMMENT lines stay; they give no position.
        fits.PrimaryHDU(data=data, header=stripped).writeto(destination, overwrite=True)
    return header


def main() -> None:
    """Copy every sample frame without its solution and write the manifest."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--library", required=True, help="The library folder the frames are read from.")
    add_study_argument(parser)
    arguments = parser.parse_args()
    library = Path(arguments.library).expanduser().resolve()
    study = open_study(arguments.study, library=str(library))
    (study.out / "library_wcs").mkdir(exist_ok=True)
    manifest = []
    for ident, relative_path, note in SAMPLE:
        source = library / relative_path
        header = copy_frame(
            source, study.frames / f"{ident}.fits", study.out / "library_wcs" / f"{ident}.hdr"
        )
        row = {
            "id": ident,
            "library": str(library),
            "source": str(source),
            "note": note,
            "had_wcs": any(key.startswith("CTYPE") for key in header),
            "had_sip": "A_ORDER" in header,
        }
        for key in HINT_KEYS:
            value = header.get(key)
            row[key] = bool(value) if isinstance(value, bool) else value
        manifest.append(row)
        print(ident, row["NAXIS1"], row["NAXIS2"], row["RA"], row["DEC"], row["FOCALLEN"], row["XPIXSZ"])
    study.manifest_path.write_text(json.dumps(manifest, indent=1, default=str))


if __name__ == "__main__":
    main()
