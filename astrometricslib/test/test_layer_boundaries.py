"""Guards the one rule about reading and writing FITS files.

A FITS file's pixel data does not always live where you would expect.
It can sit in the primary header (HDU 0), or the primary header can be
empty and the real data sits one HDU later (HDU 1). Get this wrong and a
whole camera's worth of images silently reads as blank. We have already
fixed this exact bug once, in `frame_scanning.py`, and it is one of the
easiest bugs to reintroduce: any new call to `astropy.io.fits.open` (or
`getheader`, `getdata`, `writeto`, `getval`, `setval`) written outside the
handful of places that already account for the HDU0/HDU1 rule brings the
bug straight back.

So this file is a ratchet, not a one-time check. It walks every module in
`astrometricslib` (skipping the tests) looking for those six calls, and
compares what it finds against `KNOWN_FITS_ACCESS_SITES` below. A new
call site that is not on the list fails the build -- either the new code
belongs in one of the modules that already own this rule
(`image_processing/fits_access.py` is the current canonical home;
`image_processing/image.py` and `data_access/frame_scanning.py` also handle it,
each for their own reasons -- see `fits_access.py`'s docstring), or, if
it genuinely needs to be its own site, it needs to be added to the list *and*
reviewed for the HDU0/HDU1 rule at the same time. A second check
makes sure the list can only shrink, never grow stale: every entry on it
must still contain a real call, so deleting the code without deleting the
matching list entry also fails the build.
"""

import ast
import pathlib

ASTROMETRICSLIB_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The astropy.io.fits functions that read or write pixel data straight off
# disk, bypassing whatever HDU-selection rule the caller was supposed to
# apply.
_RAW_FITS_ACCESS_METHODS = frozenset({"open", "getheader", "getdata", "writeto", "getval", "setval"})

# Every file that called a raw fits.* method when this ratchet was written.
# This list should only ever shrink -- see the module docstring. Adding to
# it is only correct alongside a review of the new call site for the
# HDU0/HDU1 rule.
KNOWN_FITS_ACCESS_SITES = frozenset({
    "data_access/background_measurement.py",
    "data_access/frame_scanning.py",
    "data_access/image_conversions.py",
    "drivers/siril_interface.py",
    "image_processing/fits_access.py",
    "image_processing/image.py",
    "image_processing/quality_metrics.py",
    "scripts/backfill_focal_length.py",
    "scripts/spectral_registration_quality_analysis.py",
    "tasks/moving_object_tasks/moving_object_pipeline_tasks.py",
    "tasks/stellar_tasks/astrometry_tasks/catalog_seeding.py",
    "tasks/stellar_tasks/astrometry_tasks/plate_solver.py",
    "tasks/stellar_tasks/astrometry_tasks/session_identification.py",
    "tasks/stellar_tasks/photometry_tasks/variability_analyzer.py",
    "tasks/target_tasks/pipelines/astrometry.py",
    "utilities/calibration_library.py",
    "visualization/helpers.py",
})


def _is_test_module(path: pathlib.Path) -> bool:
    """Decide whether a module is a test file rather than production code.

    Returns
    -------
    is_test : `bool`
        `True` if the file is a test module.
    """
    return "test" in path.parts or path.name.startswith("test_") or path.name.endswith("_test.py")


def _find_raw_fits_access_sites() -> set[str]:
    """List every production file that calls a raw fits.* access method.

    Only calls made through the name `fits` are counted -- every call
    site in the library imports it the same way, ``from astropy.io import
    fits``, so this does not need to trace other import aliases.

    Returns
    -------
    files_with_access : `set` [`str`]
        Paths, relative to `astrometricslib/`, of every file with at
        least one raw fits.* call.
    """
    files_with_access: set[str] = set()

    for module_path in sorted(ASTROMETRICSLIB_ROOT.rglob("*.py")):
        if _is_test_module(module_path):
            continue

        tree = ast.parse(module_path.read_text(), filename=str(module_path))
        for node in ast.walk(tree):
            calls_raw_fits_method = (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _RAW_FITS_ACCESS_METHODS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "fits"
            )
            if calls_raw_fits_method:
                files_with_access.add(str(module_path.relative_to(ASTROMETRICSLIB_ROOT)))
                break

    return files_with_access


def test_no_new_files_call_fits_directly():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify no file outside the known list reads or writes FITS data raw.

    New code should call through `data_access/frame_scanning.py` or
    `image_processing/image.py`, which already apply the HDU0/HDU1 rule, rather
    than opening a FITS file directly.
    """
    found = _find_raw_fits_access_sites()
    new_sites = found - KNOWN_FITS_ACCESS_SITES

    assert not new_sites, (
        f"New raw fits.* call site(s) found outside KNOWN_FITS_ACCESS_SITES: "
        f"{sorted(new_sites)}. Route the new code through "
        f"image_processing/fits_access.py instead, or if it genuinely needs its own "
        f"call site, review it for the HDU0/HDU1 rule and add it to "
        f"KNOWN_FITS_ACCESS_SITES in this file."
    )


def test_the_known_list_has_no_stale_entries():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify every allowlisted file still has a raw fits.* call in it.

    A stale entry would hide the fact that a call site was fixed or
    deleted, letting the allowlist grow unreviewed drift instead of only
    ever shrinking on purpose.
    """
    found = _find_raw_fits_access_sites()
    stale_entries = KNOWN_FITS_ACCESS_SITES - found

    assert not stale_entries, (
        f"KNOWN_FITS_ACCESS_SITES lists file(s) with no raw fits.* call "
        f"left: {sorted(stale_entries)}. Remove them from the list in this "
        f"file -- that shrinking is the whole point of the ratchet."
    )
