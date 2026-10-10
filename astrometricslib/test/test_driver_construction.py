"""Purpose: Guard that the pipelines never build a built-in driver themselves.

Description: `Astrometrics(...)` can be given a plate solver, a stacking
program and a SIMBAD client (see `drivers/driver_set.py`). That only works if
no pipeline builds `AstrometryNetPlateSolveDriver`, `SirilStackingDriver` or
`AstroquerySimbadDriver` on its own, because such a pipeline would ignore the
driver the caller chose. Every pipeline asks `Drivers` for its driver
instead. This test reads the source of each non-test module under
`pipelines/` and fails when one calls a built-in driver class directly.

Ruff's `TID251` (banned-api) cannot enforce this. `pyproject.toml` already
bans every `astrometricslib.drivers` import outside the library, and it
exempts everything inside the library, which is where the pipelines are. So
the check lives here.
"""

import ast
import pathlib

PIPELINES_ROOT = pathlib.Path(__file__).resolve().parent.parent / "pipelines"

# The built-in driver classes that only `drivers/driver_set.py` may build.
BUILT_IN_DRIVER_CLASSES = frozenset({
    "AstrometryNetPlateSolveDriver",
    "AstroquerySimbadDriver",
    "SirilStackingDriver",
})

# Modules under `pipelines/` allowed to build a built-in driver, by path
# relative to `pipelines/`. Each needs a reason.
KNOWN_DRIVER_CONSTRUCTION_SITES = frozenset({
    # `run_siril_stack` wraps a Siril session the caller already holds.
    # It does not choose a default engine, so there is nothing to inject.
    "stacking/stack_runner.py",
})


def _is_test_module(path: pathlib.Path) -> bool:
    """Decide whether a module is a test file rather than production code.

    Parameters
    ----------
    path : `pathlib.Path`
        The module.

    Returns
    -------
    is_test : `bool`
        `True` if the file is a test module.
    """
    return "test" in path.parts or path.name.startswith("test_") or path.name.endswith("_test.py")


def _find_driver_construction_sites() -> set[str]:
    """List every pipeline module that calls a built-in driver class.

    A call counts when it is ``AstroquerySimbadDriver(...)`` or
    ``module.AstroquerySimbadDriver(...)``, or the same for the other two
    classes.

    Returns
    -------
    files_with_construction : `set` [`str`]
        Paths, relative to `pipelines/`, of every non-test module with at
        least one such call.
    """
    files_with_construction: set[str] = set()

    for module_path in sorted(PIPELINES_ROOT.rglob("*.py")):
        if _is_test_module(module_path):
            continue

        tree = ast.parse(module_path.read_text(), filename=str(module_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            called_name = function.id if isinstance(function, ast.Name) else getattr(function, "attr", None)
            if called_name in BUILT_IN_DRIVER_CLASSES:
                files_with_construction.add(str(module_path.relative_to(PIPELINES_ROOT)))
                break

    return files_with_construction


def test_pipelines_do_not_build_built_in_drivers() -> None:
    """Verify no pipeline module builds a built-in driver on its own.

    A pipeline that needs a driver takes a `Drivers` and calls its
    ``plate_solve_or_default``, ``stacking_or_default`` or
    ``simbad_or_default``. A module that must build one anyway goes on
    `KNOWN_DRIVER_CONSTRUCTION_SITES` with a reason.
    """
    new_sites = _find_driver_construction_sites() - KNOWN_DRIVER_CONSTRUCTION_SITES

    assert not new_sites, (
        f"Built-in driver constructed directly in pipeline module(s): {sorted(new_sites)}. "
        f"Take a `Drivers` (astrometricslib.drivers.driver_set) and ask it for the driver, "
        f"so a caller can inject their own through Astrometrics(...)."
    )


def test_the_driver_construction_list_has_no_stale_entries() -> None:
    """Verify every allowlisted module still builds a built-in driver.

    A stale entry would leave the module free to add a direct construction
    later with nothing to flag it.
    """
    stale_entries = KNOWN_DRIVER_CONSTRUCTION_SITES - _find_driver_construction_sites()

    assert not stale_entries, (
        f"KNOWN_DRIVER_CONSTRUCTION_SITES lists module(s) that build no built-in driver any more: "
        f"{sorted(stale_entries)}. Remove them from the list."
    )
