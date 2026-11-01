"""Purpose: Test the static import check for the tutorial notebooks.

Description: ``build/documentation/check_notebook_imports.py`` must accept
public library names and the notebooks' helpers, and refuse internal modules,
submodules, private names and other third-party packages.
"""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "build" / "documentation" / "check_notebook_imports.py"
_spec = importlib.util.spec_from_file_location("check_notebook_imports", SCRIPT)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


@pytest.mark.parametrize(
    "source",
    [
        "from astrometricslib import Astrometrics, FilterType",
        "import wayfindinglib",
        "from documentation.notebooks.astrometrics.scripts.sample_data_staging import stage_m13_sample_data",
        "import json\nfrom pathlib import Path",
        "%matplotlib inline\nfrom wayfindinglib import Wayfinder",
    ],
)
def test_allowed_imports_pass(source: str) -> None:
    """Public names, helpers and the standard library raise no problem."""
    assert checker.check_source(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "from astrometricslib._internal import thing",
        "import wayfindinglib._internal.x",
        "from astrometricslib.api import Targets",
        "from astrometricslib import _private",
        "import numpy",
        "from . import sibling",
    ],
)
def test_other_imports_fail(source: str) -> None:
    """Internal modules, submodules, private names and others are refused."""
    assert checker.check_source(source)


def test_notebook_code_cells_are_checked(tmp_path: Path) -> None:
    """Only code cells count, and the problem names the cell."""
    notebook = {
        "cells": [
            {"cell_type": "markdown", "source": ["import numpy"]},
            {"cell_type": "code", "source": ["from astrometricslib._internal import x"]},
        ]
    }
    path = tmp_path / "n.ipynb"
    path.write_text(json.dumps(notebook))
    problems = checker.check_notebook(path)
    assert problems and all(p.startswith("cell 1:") for p in problems)


def test_committed_notebooks_pass() -> None:
    """Every committed notebook and helper passes."""
    assert checker.main() == 0
