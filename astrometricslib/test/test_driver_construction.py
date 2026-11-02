"""Purpose: Guard that the pipelines never build a built-in driver themselves.

Description: A test can give the library a plate solver, a stacking program,
a SIMBAD client, a Gaia XP source and a reddening source through
`astrometricslib._internal.testing` (see `drivers/driver_set.py`). That only
works if no pipeline builds `AstrometryNetPlateSolveDriver`,
`SirilStackingDriver`, `AstroquerySimbadDriver`, `AstroqueryGaiaXpDriver` or
`AstroqueryGaiaReddeningDriver` on its own, because such a pipeline would
ignore the driver the caller chose. Every pipeline asks `Drivers` for its
driver instead. This test reads the source of each non-test module under
`pipelines/` and fails when one calls a built-in driver class directly.

A second check covers the indirect case. A class such as `StarIdentifier`
falls back to an empty `Drivers()` when it is given none, and that builds the
built-in drivers. The check fails when a pipeline builds such a class
without a `drivers` argument.

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
    "AstroqueryGaiaReddeningDriver",
    "AstroqueryGaiaXpDriver",
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

# Pipeline modules that still build `SpectroscopyPipeline` or
# `AstrometryPipeline` without `drivers=`, as "path ClassName". Each falls
# back to the built-in drivers, so a caller's replacements do not reach it.
# The list may only shrink.
KNOWN_BARE_CONSTRUCTION_SITES = frozenset({
    # The per-frame worker runs in a spawned worker process
    # (`utilities/parallel_batch.py`). Its arguments are pickled, and a
    # caller's driver (a fake, or a client holding a connection) need not
    # survive that, so the worker cannot be handed `Drivers`. The session's
    # star identification, which runs in the calling process, does get them.
    "spectroscopy/batch.py SpectroscopyPipeline",
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


def _called_name(call: ast.Call) -> str | None:
    """Name the function or class a call node calls.

    Parameters
    ----------
    call : `ast.Call`
        The call.

    Returns
    -------
    name : `str` or `None`
        The plain or attribute name, or `None` for any other callee.
    """
    function = call.func
    return function.id if isinstance(function, ast.Name) else getattr(function, "attr", None)


def _classes_defaulting_to_empty_drivers(trees: dict[pathlib.Path, ast.Module]) -> set[str]:
    """List the pipeline classes whose constructor falls back to `Drivers()`.

    Parameters
    ----------
    trees : `dict` [`pathlib.Path`, `ast.Module`]
        The parsed non-test modules under `pipelines/`.

    Returns
    -------
    class_names : `set` [`str`]
        Each class with a ``Drivers()`` call, with no arguments, in its
        ``__init__``.
    """
    class_names: set[str] = set()
    for tree in trees.values():
        for class_node in ast.walk(tree):
            if not isinstance(class_node, ast.ClassDef):
                continue
            for method in class_node.body:
                if not (isinstance(method, ast.FunctionDef) and method.name == "__init__"):
                    continue
                if any(
                    isinstance(node, ast.Call)
                    and _called_name(node) == "Drivers"
                    and not node.args
                    and not node.keywords
                    for node in ast.walk(method)
                ):
                    class_names.add(class_node.name)
    return class_names


def _find_constructions_without_drivers() -> list[str]:
    """List every pipeline call that builds a driver-defaulting class bare.

    Returns
    -------
    sites : `list` [`str`]
        ``path ClassName`` for each such call. A call passing a
        ``drivers`` keyword, or ``**`` arguments that may carry it, is fine.
    """
    trees = {
        path: ast.parse(path.read_text(), filename=str(path))
        for path in sorted(PIPELINES_ROOT.rglob("*.py"))
        if not _is_test_module(path)
    }
    defaulting_classes = _classes_defaulting_to_empty_drivers(trees)
    sites: list[str] = []
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _called_name(node) not in defaulting_classes:
                continue
            if any(keyword.arg in ("drivers", None) for keyword in node.keywords):
                continue
            sites.append(f"{path.relative_to(PIPELINES_ROOT)} {_called_name(node)}")
    return sorted(set(sites))


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
        f"Take a `Drivers` (astrometricslib._internal.drivers.driver_set) and ask it for the driver, "
        f"so a caller can inject their own through astrometrics_with_drivers(...)."
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


def test_pipelines_pass_drivers_to_classes_that_default_to_built_in_ones() -> None:
    """Verify no pipeline builds a driver-defaulting class without `drivers`.

    `StarIdentifier()` with no drivers builds the built-in plate solver and
    SIMBAD driver, so a caller's replacements would be ignored. Pass
    ``drivers=drivers`` (it may be `None`).
    """
    sites = set(_find_constructions_without_drivers()) - KNOWN_BARE_CONSTRUCTION_SITES

    assert not sites, (
        f"Pipeline call(s) build a class that defaults to the built-in drivers, without `drivers=`: "
        f"{sorted(sites)}. Pass the caller's `Drivers`."
    )


def test_the_bare_construction_list_has_no_stale_entries() -> None:
    """Verify every allowlisted bare construction still exists.

    A stale entry would leave the module free to add a bare construction
    later with nothing to flag it.
    """
    stale_entries = KNOWN_BARE_CONSTRUCTION_SITES - set(_find_constructions_without_drivers())

    assert not stale_entries, (
        f"KNOWN_BARE_CONSTRUCTION_SITES lists call(s) that now pass drivers: {sorted(stale_entries)}. "
        f"Remove them from the list."
    )


def test_the_check_for_missing_drivers_finds_a_bare_star_identifier() -> None:
    """Verify the check finds the classes that default to `Drivers()`."""
    defaulting_classes = _classes_defaulting_to_empty_drivers({
        pathlib.Path("star_identifier.py"): ast.parse(
            "class StarIdentifier:\n"
            "    def __init__(self, drivers=None):\n"
            "        drivers = drivers or Drivers()\n"
        )
    })

    assert defaulting_classes == {"StarIdentifier"}
    real_classes = _classes_defaulting_to_empty_drivers({
        path: ast.parse(path.read_text())
        for path in PIPELINES_ROOT.rglob("*.py")
        if not _is_test_module(path)
    })
    assert "StarIdentifier" in real_classes
