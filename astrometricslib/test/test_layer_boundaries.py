"""Guards the one rule about reading and writing FITS files.

A FITS file's pixel data does not always live where expected.
It can sit in the primary header (HDU 0), or the primary header can be
empty and the real data sits one HDU later (HDU 1). Get this wrong and a
whole camera's worth of images silently reads as blank. This exact bug
has already been fixed once, in `frame_scanning.py`, and it is one of the
easiest bugs to reintroduce: any new call to `astropy.io.fits.open` (or
`getheader`, `getdata`, `writeto`, `getval`, `setval`) written outside the
handful of places that already account for the HDU0/HDU1 rule brings the
bug straight back.

So this file is a ratchet, not a one-time check. It walks every module in
`astrometricslib` (skipping the tests) looking for those six calls, and
compares what it finds against `KNOWN_FITS_ACCESS_SITES` below. A new
call site that is not on the list fails the build -- either the new code
belongs in one of the modules that already own this rule
(`drivers/fits_access.py` is the current canonical home;
`drivers/image.py` and `pipelines/shared/frame_scanning.py` also
handle it, each for their own reasons -- see `fits_access.py`'s docstring),
or, if it genuinely needs to be its own site, it needs to be added to the
list *and* reviewed for the HDU0/HDU1 rule at the same time. A second check
makes sure the list can only shrink, never grow stale: every entry on it
must still contain a real call, so deleting the code without deleting the
matching list entry also fails the build.

A second ratchet in this file keeps the wall clock out of the pipelines
(`datetime.now()` and `datetime.utcnow()` under `pipelines/`). A pipeline
that reads the clock instead of a frame's own capture time fabricates
timestamps. See `KNOWN_WALL_CLOCK_SITES`.
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
    "drivers/calibration_library.py",
    "drivers/fits_access.py",
    "drivers/image.py",
    "drivers/astrometry_net_driver.py",
    "drivers/siril_interface.py",
    "pipelines/astrometry/utilities/catalog_seeding.py",
    "pipelines/astrometry/pre_processing/fwhm.py",
    "pipelines/shared/session_identification.py",
    "pipelines/asteroid_detection/pipeline.py",
    "pipelines/photometry/pre_processing/frame_photometry.py",
    "pipelines/photometry/processing/variability_analyzer.py",
    "pipelines/shared/image_conversions.py",
    "pipelines/shared/quality/background_measurement.py",
    "pipelines/shared/quality/quality_metrics.py",
    "scripts/backfill_focal_length.py",
    # Reads pixels on its own on purpose: it re-measures saved numbers by
    # a path independent of the pipeline's FITS access.
    "scripts/recompute_headline_numbers.py",
    "scripts/spectral_registration_quality_analysis.py",
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


def test_no_new_files_call_fits_directly() -> None:
    """Verify no file outside the known list reads or writes FITS data raw.

    New code should call through `pipelines/shared/frame_scanning.py` or
    `drivers/image.py`, which already apply the HDU0/HDU1 rule, rather
    than opening a FITS file directly.
    """
    found = _find_raw_fits_access_sites()
    new_sites = found - KNOWN_FITS_ACCESS_SITES

    assert not new_sites, (
        f"New raw fits.* call site(s) found outside KNOWN_FITS_ACCESS_SITES: "
        f"{sorted(new_sites)}. Route the new code through "
        f"drivers/fits_access.py instead, or if it genuinely needs its own "
        f"call site, review it for the HDU0/HDU1 rule and add it to "
        f"KNOWN_FITS_ACCESS_SITES in this file."
    )


def test_the_known_list_has_no_stale_entries() -> None:
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


# Files under `pipelines/` that call `datetime.now()` or `datetime.utcnow()`
# on purpose. Each entry needs a reason. A pipeline must never use the clock
# to stamp a frame or a measurement (review item S5); it may use it to date
# its own bookkeeping.
KNOWN_WALL_CLOCK_SITES = frozenset({
    # Writes the time a rejected frame was moved into quarantine, in the
    # move log. It dates the move itself, not any frame's observation.
    "pipelines/stacking/pre_processing/frame_quarantine.py",
})


def _find_wall_clock_sites() -> set[str]:
    """List every pipeline file that reads the wall clock.

    A call counts when it has the form ``datetime.now(...)`` or
    ``datetime.utcnow(...)``, including ``datetime.datetime.now(...)``.
    Test modules are skipped.

    Returns
    -------
    files_with_clock : `set` [`str`]
        Paths, relative to `astrometricslib/`, of every non-test file under
        `pipelines/` with at least one such call.
    """
    files_with_clock: set[str] = set()

    for module_path in sorted((ASTROMETRICSLIB_ROOT / "pipelines").rglob("*.py")):
        if _is_test_module(module_path):
            continue

        tree = ast.parse(module_path.read_text(), filename=str(module_path))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"now", "utcnow"}
            ):
                continue
            owner = node.func.value
            owner_name = owner.id if isinstance(owner, ast.Name) else getattr(owner, "attr", None)
            if owner_name == "datetime":
                files_with_clock.add(str(module_path.relative_to(ASTROMETRICSLIB_ROOT)))
                break

    return files_with_clock


def test_pipelines_do_not_read_the_wall_clock() -> None:
    """Verify no pipeline file reads the wall clock.

    The banned calls are ``datetime.now()`` and ``datetime.utcnow()``. A
    frame's time comes from its ``DATE-OBS`` header (see
    `parse_observation_time`). Falling back to the current time gave a
    frame with no capture time a made-up one. A file that needs the clock
    for its own bookkeeping goes on `KNOWN_WALL_CLOCK_SITES` with a reason.
    """
    new_sites = _find_wall_clock_sites() - KNOWN_WALL_CLOCK_SITES

    assert not new_sites, (
        f"datetime.now()/utcnow() found in pipeline file(s): {sorted(new_sites)}. "
        f"Read the frame's capture time from its header instead, or if the "
        f"file dates its own bookkeeping, add it to KNOWN_WALL_CLOCK_SITES "
        f"with a reason."
    )


def test_the_wall_clock_list_has_no_stale_entries() -> None:
    """Verify every allowlisted wall-clock file still reads the clock.

    A stale entry would leave a file free to add a clock call later with
    nothing to flag it.
    """
    stale_entries = KNOWN_WALL_CLOCK_SITES - _find_wall_clock_sites()

    assert not stale_entries, (
        f"KNOWN_WALL_CLOCK_SITES lists file(s) with no datetime.now()/utcnow() "
        f"call left: {sorted(stale_entries)}. Remove them from the list."
    )
