"""Guards the rule that only catalog_services reaches into data_access.

Layer 5 (`data_access/`) is where CatalogAccess -- the repository
object for the target and stellar catalogs -- lives, along with two
modules that read the database or the filesystem for one specific
purpose (`frame_statistics.py`, `background_measurement.py`).
`CatalogAccess` itself is the sanctioned front door: everything from
Layer 1 (`api/`, `mcp/`) down to Layer 3 (the five pipelines) is meant
to reach the catalog only through it, constructing or receiving it as
`catalog_access` rather than importing a data_access submodule and
calling its functions directly.

`catalog_services/` used to violate this: `frame_scanning.py`,
`target_records.py`, and `image_conversions.py` all lived inside
`data_access/` despite doing plain filesystem/CRUD work with no
database concern of their own, and api/, mcp/, and the pipelines
reached straight into them -- skipping Layer 4 entirely. They moved up
to `catalog_services/` (Layer 4) for exactly this reason: it is the
layer the public API is meant to call directly for this kind of work.

This ratchet is deliberately narrower than a full layer-boundary
checker. It only tracks imports of data_access submodules other than
`catalog_access.py` (the sanctioned gateway) and `exceptions.py`
(plain exception classes, not a service) from api/, mcp/, or
pipelines/ code outside pipelines/shared/ (Layer 4, allowed to reach
Layer 5 directly). `frame_statistics.py` and `background_measurement.py`
are still reached this way from outside data_access/ -- tracked below
as known, not-yet-fixed sites, so this ratchet can only shrink from
here, the same way test_layer_boundaries.py's FITS-access list can.
"""

import ast
import pathlib

ASTROMETRICSLIB_ROOT = pathlib.Path(__file__).resolve().parent.parent

# data_access submodules any layer may reach directly: the repository
# object itself, and plain exception classes.
_SANCTIONED_DATA_ACCESS_MODULES = frozenset({"catalog_access", "exceptions"})

# Directories whose modules are allowed to reach straight into
# data_access -- Layer 4 (shared services) and Layer 5 itself.
_ALLOWED_CALLER_PREFIXES = ("catalog_services/", "data_access/", "pipelines/shared/")

# Every file, outside the allowed callers above, that imports a
# non-sanctioned data_access submodule directly when this ratchet was
# written. Only frame_statistics.py and background_measurement.py
# remain -- this list should only ever shrink; see the module
# docstring.
KNOWN_DATA_ACCESS_REACHAROUNDS = frozenset({
    "api/targets.py",
    "pipelines/stacking/stage.py",
})


def _is_test_module(path: pathlib.Path) -> bool:
    """Decide whether a module is a test file rather than production code.

    Returns
    -------
    is_test : `bool`
        `True` if the file is a test module.
    """
    return "test" in path.parts or path.name.startswith("test_") or path.name.endswith("_test.py")


def _imported_data_access_submodule(node: ast.ImportFrom) -> str | None:
    """Return the data_access submodule name a from-import reaches into.

    Handles both ``from astrometricslib.data_access import X`` and
    ``from astrometricslib.data_access.X import Y`` -- the two forms
    used throughout this library.

    Returns
    -------
    submodule_name : `str` or `None`
        The submodule name, or `None` if this import does not reach
        into `astrometricslib.data_access` at all.
    """
    module = node.module or ""
    if module == "astrometricslib.data_access":
        return node.names[0].name if node.names else None
    prefix = "astrometricslib.data_access."
    if module.startswith(prefix):
        return module[len(prefix) :].split(".")[0]
    return None


def _find_data_access_reacharounds() -> set[str]:
    """List every production file that reaches into data_access directly.

    Only counts files outside the allowed callers that import a
    non-sanctioned data_access submodule.

    Returns
    -------
    files_with_reacharounds : `set` [`str`]
        Paths, relative to `astrometricslib/`, of every offending file.
    """
    files_with_reacharounds: set[str] = set()

    for module_path in sorted(ASTROMETRICSLIB_ROOT.rglob("*.py")):
        if _is_test_module(module_path):
            continue
        relative_path = str(module_path.relative_to(ASTROMETRICSLIB_ROOT))
        if relative_path.startswith(_ALLOWED_CALLER_PREFIXES):
            continue

        tree = ast.parse(module_path.read_text(), filename=str(module_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            submodule_name = _imported_data_access_submodule(node)
            if submodule_name and submodule_name not in _SANCTIONED_DATA_ACCESS_MODULES:
                files_with_reacharounds.add(relative_path)
                break

    return files_with_reacharounds


def test_no_new_files_reach_around_catalog_access():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify no new file imports a bare data_access submodule directly.

    New code that needs the target or stellar catalog should receive or
    construct a `CatalogAccess` and call it, the way every other
    consumer does -- not import a data_access submodule and call its
    functions directly.
    """
    found = _find_data_access_reacharounds()
    new_sites = found - KNOWN_DATA_ACCESS_REACHAROUNDS

    assert not new_sites, (
        f"New data_access reach-around(s) found outside "
        f"KNOWN_DATA_ACCESS_REACHAROUNDS: {sorted(new_sites)}. Reach the "
        f"catalog through a CatalogAccess instance instead of importing "
        f"a data_access submodule directly, or if the new code "
        f"genuinely belongs in catalog_services/, move it there."
    )


def test_the_known_list_has_no_stale_entries():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify every allowlisted file still reaches around data_access.

    A stale entry would hide the fact that a call site was fixed,
    letting the allowlist grow unreviewed drift instead of only ever
    shrinking on purpose.
    """
    found = _find_data_access_reacharounds()
    stale_entries = KNOWN_DATA_ACCESS_REACHAROUNDS - found

    assert not stale_entries, (
        f"KNOWN_DATA_ACCESS_REACHAROUNDS lists file(s) with no data_access "
        f"reach-around left: {sorted(stale_entries)}. Remove them from the "
        f"list in this file -- that shrinking is the whole point of the "
        f"ratchet."
    )
