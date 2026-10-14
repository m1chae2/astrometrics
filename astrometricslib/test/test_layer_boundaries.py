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

A third ratchet keeps unseeded randomness out of the pipelines (review item
S20). A random generator with no seed gives a different answer on every run,
so a detection threshold or a preview could change between two runs on the
same frame. See `KNOWN_UNSEEDED_RANDOM_SITES`.

A fourth ratchet keeps imports pointing down the layers (issue #78). The
library's "Ratchet Rule" (see
`documentation/library_design/Astrometrics_Library_Implementation.md`) says
no layer imports from a layer above it. The scan reads every import,
including the ones written inside a function body, which hide a dependency
from a quick read of the file's header. See `KNOWN_UPWARD_IMPORTS`.
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


# Files under `pipelines/` that use unseeded randomness on purpose. Each
# entry needs a reason. It is empty: the two sites found by review item S20
# (the source detector's background subsample and the preview scaler's
# percentile sample) now take fixed seeds.
KNOWN_UNSEEDED_RANDOM_SITES: frozenset[str] = frozenset()

# The `numpy.random` module-level functions that draw from numpy's hidden
# global generator. They cannot be given a seed per call.
_LEGACY_NUMPY_RANDOM_FUNCTIONS = frozenset({
    "rand",
    "randn",
    "randint",
    "random",
    "random_sample",
    "ranf",
    "sample",
    "normal",
    "standard_normal",
    "uniform",
    "choice",
    "shuffle",
    "permutation",
    "poisson",
    "seed",
    "bytes",
    "RandomState",
})

# The `random` standard-library functions that draw from its hidden global
# generator.
_LEGACY_STDLIB_RANDOM_FUNCTIONS = frozenset({
    "random",
    "randint",
    "randrange",
    "uniform",
    "choice",
    "choices",
    "shuffle",
    "sample",
    "gauss",
    "normalvariate",
    "seed",
})


def _is_unseeded_random_call(node: ast.AST) -> bool:
    """Decide whether a syntax-tree node draws unseeded randomness.

    Three forms count: ``default_rng()`` (or ``default_rng(None)``) with no
    seed, a ``numpy.random`` legacy function such as ``np.random.randint``,
    and a ``random`` standard-library function such as ``random.random``.

    Parameters
    ----------
    node : `ast.AST`
        Any node from a parsed module.

    Returns
    -------
    is_unseeded : `bool`
        `True` if the node is one of those calls.
    """
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute | ast.Name)):
        return False
    function = node.func
    function_name = function.attr if isinstance(function, ast.Attribute) else function.id

    if function_name == "default_rng":
        seed_is_missing = not node.args and not node.keywords
        seed_is_none = (
            len(node.args) == 1 and isinstance(node.args[0], ast.Constant) and node.args[0].value is None
        )
        return seed_is_missing or seed_is_none

    if not isinstance(function, ast.Attribute):
        return False
    owner = function.value
    # `np.random.<name>` or `numpy.random.<name>`
    if (
        isinstance(owner, ast.Attribute)
        and owner.attr == "random"
        and isinstance(owner.value, ast.Name)
        and owner.value.id in {"np", "numpy"}
    ):
        return function_name in _LEGACY_NUMPY_RANDOM_FUNCTIONS
    # `random.<name>` from the standard library
    if isinstance(owner, ast.Name) and owner.id == "random":
        return function_name in _LEGACY_STDLIB_RANDOM_FUNCTIONS
    return False


def _find_unseeded_random_sites() -> set[str]:
    """List every pipeline file that draws unseeded randomness.

    Test modules are skipped.

    Returns
    -------
    files_with_random : `set` [`str`]
        Paths, relative to `astrometricslib/`, of every non-test file under
        `pipelines/` with at least one unseeded random call.
    """
    files_with_random: set[str] = set()

    for module_path in sorted((ASTROMETRICSLIB_ROOT / "pipelines").rglob("*.py")):
        if _is_test_module(module_path):
            continue

        tree = ast.parse(module_path.read_text(), filename=str(module_path))
        if any(_is_unseeded_random_call(node) for node in ast.walk(tree)):
            files_with_random.add(str(module_path.relative_to(ASTROMETRICSLIB_ROOT)))

    return files_with_random


def test_pipelines_do_not_use_unseeded_randomness() -> None:
    """Verify no pipeline file draws random numbers without a seed.

    A bare ``default_rng()``, a ``numpy.random`` legacy function, or a
    ``random`` standard-library function gives different numbers on every
    run. Build a generator with ``np.random.default_rng(seed)`` and a named
    seed constant instead. A file that truly needs unseeded randomness goes
    on `KNOWN_UNSEEDED_RANDOM_SITES` with a reason.
    """
    new_sites = _find_unseeded_random_sites() - KNOWN_UNSEEDED_RANDOM_SITES

    assert not new_sites, (
        f"Unseeded randomness found in pipeline file(s): {sorted(new_sites)}. "
        f"Pass a fixed seed to np.random.default_rng, or if the file truly "
        f"needs a different answer each run, add it to "
        f"KNOWN_UNSEEDED_RANDOM_SITES with a reason."
    )


def test_the_unseeded_random_list_has_no_stale_entries() -> None:
    """Verify every allowlisted unseeded-random file still uses it.

    A stale entry would leave a file free to add unseeded randomness later
    with nothing to flag it.
    """
    stale_entries = KNOWN_UNSEEDED_RANDOM_SITES - _find_unseeded_random_sites()

    assert not stale_entries, (
        f"KNOWN_UNSEEDED_RANDOM_SITES lists file(s) with no unseeded random "
        f"call left: {sorted(stale_entries)}. Remove them from the list."
    )


def test_the_unseeded_random_scan_recognizes_each_banned_form() -> None:
    """Verify the scan flags the banned calls and accepts seeded ones."""
    banned = [
        "np.random.default_rng()",
        "default_rng(None)",
        "np.random.randint(0, 5)",
        "numpy.random.normal(0, 1)",
    ]
    banned += ["random.random()", "np.random.RandomState(1)"]
    allowed = [
        "np.random.default_rng(0)",
        "np.random.default_rng(seed=3)",
        "rng.integers(0, 5)",
        "np.random.Generator",
    ]

    for source in banned:
        assert _is_unseeded_random_call(ast.parse(source).body[0].value), source
    for source in allowed:
        assert not _is_unseeded_random_call(ast.parse(source).body[0].value), source


# The specific pipeline packages, such as `stacking` and `astrometry`: every
# folder under `pipelines/` that is a Python package, except `shared/` and
# the test folder. They sit above `pipelines/shared/` and below
# `pipelines/tasks.py`.
_PIPELINE_PACKAGES = tuple(
    sorted(
        folder.name
        for folder in (ASTROMETRICSLIB_ROOT / "pipelines").iterdir()
        if (folder / "__init__.py").is_file() and folder.name not in {"shared", "test"}
    )
)

# The pipeline modules that run the specific pipelines: the dispatcher and
# the multi-target batch runner. They sit above every pipeline.
_PIPELINE_RUNNERS = ("astrometricslib.pipelines.tasks", "astrometricslib.pipelines.target_batch")

# The imports a lower layer may not make. Each rule is the folders or files
# the importing file sits in and the module prefixes it may not import. A
# prefix also covers every module below it.
#
# - Drivers are the bottom layer above the shared vocabulary: they serve the
#   pipelines, the public helpers and the public API, and import none of
#   them.
# - The pipelines do not import the public API above them.
# - The shared pipeline code (`pipelines/shared/` and
#   `pipelines/pipeline_base.py`) serves every pipeline, so it imports no
#   specific pipeline and neither pipeline runner.
# - A specific pipeline does not import a pipeline runner (`pipelines.tasks`
#   or `pipelines.target_batch`). Imports between two pipelines are allowed.
# - The shared vocabulary (`models/`, `foundation/`, `utilities/`) is plain
#   data and settings that every layer may import, so it imports no layer.
_UPWARD_IMPORT_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("drivers/",),
        ("astrometricslib.pipelines", "astrometricslib.api", "astrometricslib.visualization"),
    ),
    (("pipelines/",), ("astrometricslib.api",)),
    (
        ("pipelines/shared/", "pipelines/pipeline_base.py"),
        (*(f"astrometricslib.pipelines.{name}" for name in _PIPELINE_PACKAGES), *_PIPELINE_RUNNERS),
    ),
    (tuple(f"pipelines/{name}/" for name in _PIPELINE_PACKAGES), _PIPELINE_RUNNERS),
    (
        ("models/", "foundation/", "utilities/"),
        (
            "astrometricslib.drivers",
            "astrometricslib.pipelines",
            "astrometricslib.api",
            "astrometricslib.visualization",
        ),
    ),
)

# The folders that may not import names from the `astrometricslib` package
# itself (``from astrometricslib import Astrometrics``). The package's
# `__init__.py` is the public API, the top layer.
_NO_PACKAGE_ROOT_IMPORT_FOLDERS = ("drivers/", "pipelines/", "models/", "foundation/", "utilities/")

# Upward imports that were already there when this ratchet was written,
# as "file -> module". This list should only ever shrink. It is empty: the
# last entries were fixed by issue #79.
KNOWN_UPWARD_IMPORTS: frozenset[str] = frozenset()


def _imported_modules(module_path: pathlib.Path) -> set[str]:
    """List every module a file imports, wherever in the file the import is.

    Relative imports are turned into full module names. For
    ``from package import name``, the module is ``package``.

    Parameters
    ----------
    module_path : `pathlib.Path`
        The Python file to read.

    Returns
    -------
    modules : `set` [`str`]
        The full names of the imported modules.
    """
    # The package a file belongs to: its folder, for a module and for an
    # `__init__.py` alike.
    package_parts = module_path.relative_to(ASTROMETRICSLIB_ROOT.parent).parent.parts
    modules: set[str] = set()
    tree = ast.parse(module_path.read_text(), filename=str(module_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                modules.add(node.module or "")
            else:
                base = package_parts[: len(package_parts) - (node.level - 1)]
                modules.add(".".join([*base, *([node.module] if node.module else [])]))
    return modules


def _is_upward_import(relative_path: str, module: str) -> bool:
    """Decide whether one import from one file breaks the layer rule.

    Parameters
    ----------
    relative_path : `str`
        The importing file, relative to `astrometricslib/`, with forward
        slashes.
    module : `str`
        The full name of the imported module.

    Returns
    -------
    is_upward : `bool`
        `True` if a rule in `_UPWARD_IMPORT_RULES` forbids the import, or if
        the file imports from the package root and sits in one of
        `_NO_PACKAGE_ROOT_IMPORT_FOLDERS`.
    """
    if module == "astrometricslib":
        return relative_path.startswith(_NO_PACKAGE_ROOT_IMPORT_FOLDERS)
    for folders, forbidden_prefixes in _UPWARD_IMPORT_RULES:
        if not relative_path.startswith(folders):
            continue
        if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden_prefixes):
            return True
    return False


def _find_upward_imports() -> set[str]:
    """List every upward import in the library's production files.

    Test modules are skipped.

    Returns
    -------
    upward_imports : `set` [`str`]
        One ``"file -> module"`` entry per upward import, with the file
        relative to `astrometricslib/`.
    """
    upward_imports: set[str] = set()
    for module_path in sorted(ASTROMETRICSLIB_ROOT.rglob("*.py")):
        if _is_test_module(module_path):
            continue
        relative_path = module_path.relative_to(ASTROMETRICSLIB_ROOT).as_posix()
        for module in _imported_modules(module_path):
            if _is_upward_import(relative_path, module):
                upward_imports.add(f"{relative_path} -> {module}")
    return upward_imports


def test_no_new_upward_imports() -> None:
    """Verify no file imports from a layer above its own.

    A driver that needs a pipeline's result should be handed it (the
    stacking driver gets the pipeline's steps as a `PipelineSteps`). A
    pipeline should take the catalog connection or the function it needs
    instead of building the public `Astrometrics` object.
    """
    new_imports = _find_upward_imports() - KNOWN_UPWARD_IMPORTS

    assert not new_imports, (
        f"Upward import(s) found: {sorted(new_imports)}. A layer may not import "
        f"from a layer above it; pass the needed value or function in instead."
    )


def test_the_upward_import_list_has_no_stale_entries() -> None:
    """Verify every allowlisted upward import is still there.

    A fixed import should come off the list, so the list only shrinks.
    """
    stale_entries = KNOWN_UPWARD_IMPORTS - _find_upward_imports()

    assert not stale_entries, (
        f"KNOWN_UPWARD_IMPORTS lists import(s) that are gone: {sorted(stale_entries)}. "
        f"Remove them from the list."
    )


def test_the_upward_import_scan_recognizes_each_rule() -> None:
    """Verify the scan flags each upward import and allows downward ones."""
    upward = [
        ("drivers/siril_interface.py", "astrometricslib.pipelines.stacking.stack_runner"),
        ("drivers/siril_interface.py", "astrometricslib.api.processing"),
        ("drivers/siril_interface.py", "astrometricslib"),
        ("pipelines/stacking/stage.py", "astrometricslib.api"),
        ("pipelines/spectroscopy/batch.py", "astrometricslib.pipelines.tasks"),
        ("pipelines/spectroscopy/batch.py", "astrometricslib"),
        ("models/target.py", "astrometricslib.pipelines.shared.frame_grouping"),
        ("pipelines/shared/frame_grouping.py", "astrometricslib.pipelines.stacking.pre_processing"),
        ("pipelines/shared/quality/quality_metrics.py", "astrometricslib.pipelines.astrometry"),
        ("pipelines/shared/target_sessions.py", "astrometricslib.pipelines.tasks"),
        ("pipelines/shared/target_records.py", "astrometricslib.pipelines.target_batch"),
        ("pipelines/pipeline_base.py", "astrometricslib.pipelines.spectroscopy.pipeline"),
        ("pipelines/pipeline_base.py", "astrometricslib.pipelines.tasks"),
        ("pipelines/stacking/stage.py", "astrometricslib.pipelines.target_batch"),
        ("pipelines/photometry/pipeline.py", "astrometricslib.pipelines.tasks"),
        ("pipelines/asteroid_detection/pipeline.py", "astrometricslib.pipelines.target_batch"),
    ]
    downward = [
        ("drivers/siril_interface.py", "astrometricslib.drivers.fits_access"),
        ("drivers/siril_interface.py", "astrometricslib.foundation.errors"),
        ("pipelines/spectroscopy/batch.py", "astrometricslib.pipelines.spectroscopy.runner"),
        ("pipelines/spectroscopy/batch.py", "astrometricslib.pipelines.stacking.stage"),
        ("pipelines/stacking/stage.py", "astrometricslib.pipelines.shared.frame_scanning"),
        ("pipelines/stacking/stage.py", "astrometricslib.pipelines.pipeline_base"),
        ("pipelines/shared/frame_scanning.py", "astrometricslib.pipelines.shared.angles"),
        ("pipelines/shared/frame_scanning.py", "astrometricslib.pipelines.pipeline_base"),
        ("pipelines/pipeline_base.py", "astrometricslib.pipelines.shared.analysis_context"),
        ("pipelines/tasks.py", "astrometricslib.pipelines.stacking.stage"),
        ("pipelines/target_batch.py", "astrometricslib.pipelines.tasks"),
        ("pipelines/spectroscopy/batch.py", "astrometricslib.pipelines.tasks_helpers"),
        ("api/processing.py", "astrometricslib.pipelines.tasks"),
        ("api/processing.py", "astrometricslib"),
    ]

    for relative_path, module in upward:
        assert _is_upward_import(relative_path, module), (relative_path, module)
    for relative_path, module in downward:
        assert not _is_upward_import(relative_path, module), (relative_path, module)
