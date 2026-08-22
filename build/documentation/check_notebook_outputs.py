"""CI check that committed notebook outputs look like a real, fresh run.

The FITS sample library the tutorial notebooks run against is too large
to ship to a hosted CI runner, so CI can't actually re-execute them (see
build/linux/execute_notebooks.sh, which does that locally). This script
is the next best thing: it inspects each notebook's saved outputs and
fails if they don't look like the product of one clean, top-to-bottom
`jupyter nbconvert --execute` run --

* any cell whose output includes an error, or
* any non-empty code cell with no execution_count (never actually run
  before saving), or
* execution_count values that aren't exactly 1, 2, 3, ... in cell
  order (a sign cells were run out of order, or only some cells were
  re-run after an edit, rather than a single fresh pass).

It does not, and can't, catch a cell whose *output happens to still be
correct* despite the code having silently drifted -- only re-execution
against real data does that.
"""

import sys
from pathlib import Path

import nbformat

NOTEBOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "documentation" / "notebooks"


def check_notebook(path: Path) -> list[str]:
    """Check one notebook's saved outputs for signs of a stale/dirty run.

    Parameters
    ----------
    path : `Path`
        Path to the ``.ipynb`` file to check.

    Returns
    -------
    problems : `list` of `str`
        Human-readable descriptions of every problem found, empty if
        the notebook looks like a clean, fresh, top-to-bottom run.
    """
    problems = []
    nb = nbformat.read(path, as_version=4)

    expected_execution_count = 1
    for cell_index, cell in enumerate(nb.cells):
        if cell.cell_type != "code":
            continue
        if not cell.source.strip():
            continue

        if cell.execution_count is None:
            problems.append(f"cell {cell_index}: never executed (no execution_count)")
            continue

        if cell.execution_count != expected_execution_count:
            problems.append(
                f"cell {cell_index}: execution_count is {cell.execution_count}, "
                f"expected {expected_execution_count} -- looks like cells were run "
                "out of order or only partially re-run"
            )
        expected_execution_count = cell.execution_count + 1

        for output in cell.get("outputs", []):
            if output.get("output_type") == "error":
                ename = output.get("ename", "Error")
                evalue = output.get("evalue", "")
                problems.append(f"cell {cell_index}: saved output is an error -- {ename}: {evalue}")

    return problems


def main() -> int:
    """Check every notebook under documentation/notebooks/.

    Returns
    -------
    exit_code : `int`
        0 if every notebook is clean, 1 if any problems were found.
    """
    notebook_paths = sorted(NOTEBOOKS_DIR.rglob("*.ipynb"))
    notebook_paths = [p for p in notebook_paths if ".ipynb_checkpoints" not in p.parts]

    if not notebook_paths:
        print(f"No notebooks found under {NOTEBOOKS_DIR}.")
        return 0

    had_problems = False
    for path in notebook_paths:
        problems = check_notebook(path)
        if problems:
            had_problems = True
            print(f"\n{path.relative_to(NOTEBOOKS_DIR.parent.parent)}:")
            for problem in problems:
                print(f"  - {problem}")

    if had_problems:
        print(
            "\nOne or more notebooks have stale, missing, or errored "
            "output. Run ./build/linux/execute_notebooks.sh locally "
            "(against the real sample data), review the diff, and "
            "commit the refreshed notebook(s)."
        )
        return 1

    print(f"All {len(notebook_paths)} notebook(s) have clean, fresh, top-to-bottom output.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
