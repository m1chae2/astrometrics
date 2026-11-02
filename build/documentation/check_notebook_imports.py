"""CI check that tutorial notebooks use only the libraries' public names.

The tutorial notebooks teach the two libraries, so a reader who copies a
notebook must get code that works through the public interface. This script
reads the code cells of every notebook under documentation/notebooks/ (and
the helper modules beside them) without running anything, and fails when one
imports:

* anything outside the standard library, other than ``astrometricslib``,
  ``wayfindinglib`` or the notebooks' own helper modules under
  ``documentation.notebooks``;
* a submodule of either library, such as ``astrometricslib.api`` or
  ``astrometricslib._internal`` (only the top-level package is public);
* a name that starts with an underscore.

The check is static because the docs build does not run the notebooks
(``nb_execution_mode = "off"`` in documentation/conf.py).
"""

import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
NOTEBOOKS_DIR = REPO_ROOT / "documentation" / "notebooks"
LIBRARIES = ("astrometricslib", "wayfindinglib")
HELPER_PREFIX = "documentation.notebooks"
THIRD_PARTY_ALLOWED = ("matplotlib",)


def _check_module(module: str, names: list[str]) -> list[str]:
    """Return the problems with one imported module and its imported names.

    Parameters
    ----------
    module : `str`
        Dotted module name, as written after ``import`` or ``from``.
    names : `list` of `str`
        Names taken from the module (empty for a plain ``import``).

    Returns
    -------
    problems : `list` of `str`
        One description per problem, empty if the import is allowed.
    """
    root = module.split(".")[0]
    problems = []
    if module in LIBRARIES:
        pass
    elif root in LIBRARIES:
        problems.append(f"imports the submodule `{module}`; import public names from `{root}` instead")
    elif module == HELPER_PREFIX or module.startswith(HELPER_PREFIX + "."):
        pass
    elif root in sys.stdlib_module_names or root in THIRD_PARTY_ALLOWED:
        pass
    else:
        problems.append(f"imports `{module}`, which is neither a library nor a notebook helper")
    if any(part.startswith("_") for part in module.split(".")):
        problems.append(f"imports the private module `{module}`")
    for name in names:
        if name.startswith("_"):
            problems.append(f"imports the private name `{name}` from `{module}`")
    return problems


def check_source(source: str) -> list[str]:
    """Check the imports in one block of Python source.

    Parameters
    ----------
    source : `str`
        Python source; IPython lines starting with ``%`` or ``!`` are skipped.

    Returns
    -------
    problems : `list` of `str`
        Problems found, empty if every import is allowed.
    """
    lines = [line for line in source.splitlines() if not line.lstrip().startswith(("%", "!"))]
    try:
        tree = ast.parse("\n".join(lines))
    except SyntaxError as error:
        return [f"cannot be parsed: {error}"]
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                problems.extend(_check_module(alias.name, []))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                problems.append("uses a relative import")
            else:
                problems.extend(_check_module(node.module or "", [a.name for a in node.names]))
    return problems


def check_notebook(path: Path) -> list[str]:
    """Check every code cell of one notebook.

    Parameters
    ----------
    path : `Path`
        Path to the ``.ipynb`` file.

    Returns
    -------
    problems : `list` of `str`
        Problems found, each prefixed with the cell number.
    """
    notebook = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    for index, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] == "code":
            problems.extend(f"cell {index}: {p}" for p in check_source("".join(cell["source"])))
    return problems


def main() -> int:
    """Check every notebook and helper module under documentation/notebooks/.

    Returns
    -------
    exit_code : `int`
        0 if every import is allowed, 1 if any problem was found.
    """
    notebooks = [p for p in sorted(NOTEBOOKS_DIR.rglob("*.ipynb")) if ".ipynb_checkpoints" not in p.parts]
    helpers = sorted(NOTEBOOKS_DIR.rglob("*.py"))
    had_problems = False
    for path in notebooks + helpers:
        if path.suffix == ".ipynb":
            problems = check_notebook(path)
        else:
            problems = check_source(path.read_text(encoding="utf-8"))
        if problems:
            had_problems = True
            print(f"\n{path.relative_to(REPO_ROOT)}:")
            for problem in problems:
                print(f"  - {problem}")
    if had_problems:
        print(
            "\nNotebooks may import only astrometricslib or wayfindinglib public names and their own helpers."
        )
        return 1
    print(f"All {len(notebooks)} notebook(s) and {len(helpers)} helper(s) import only public names.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
