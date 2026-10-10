"""Purpose: Guard that each spectral-match limit is defined in one place.

Description: The limits for "poor match", "no match at all", "ambiguous" and
"differs from catalog" live in `astrometricslib.models.stellar_source`, each
with a docstring that states its unit. This test imports every limit from
there. It then reads the source of the modules that once held copies
(`spectral_classifier.py`, `compare_to_catalog.py`, `assess_output_quality.py`
and `run_gates.py`) with ``ast``. It fails if any of them assigns a module
constant whose name looks like a limit: a name containing ``THRESHOLD`` or
ending in ``_RMS``, ``_GAP``, ``_MARGIN``, ``_STEPS`` or ``_SUBTYPES``. A
module may import a limit but may not define one.
"""

import ast
import inspect
from pathlib import Path
from types import ModuleType

import pytest

from astrometricslib.models import stellar_source
from astrometricslib.models.stellar_source import (
    AMBIGUOUS_RMS_GAP,
    DIFFERS_FROM_CATALOG_SUBTYPES,
    NO_GOOD_MATCH_RMS,
    UNRELIABLE_MATCH_RMS,
)
from astrometricslib.pipelines.spectroscopy.post_processing import (
    assess_output_quality,
    compare_to_catalog,
    run_gates,
)
from astrometricslib.pipelines.spectroscopy.processing import spectral_classifier

# Each shared limit, its value, and the unit its docstring must state.
SHARED_LIMITS: dict[str, tuple[float, str]] = {
    "NO_GOOD_MATCH_RMS": (NO_GOOD_MATCH_RMS, "relative RMS"),
    "UNRELIABLE_MATCH_RMS": (UNRELIABLE_MATCH_RMS, "relative RMS"),
    "AMBIGUOUS_RMS_GAP": (AMBIGUOUS_RMS_GAP, "relative RMS"),
    "DIFFERS_FROM_CATALOG_SUBTYPES": (DIFFERS_FROM_CATALOG_SUBTYPES, "subtype steps"),
}

# Endings of module constant names that look like a match limit.
LIMIT_NAME_SUFFIXES = ("_RMS", "_GAP", "_MARGIN", "_STEPS", "_SUBTYPES")

# The modules that may import a limit but must not define one.
MODULES_THAT_MUST_NOT_DEFINE_LIMITS = (
    spectral_classifier,
    compare_to_catalog,
    assess_output_quality,
    run_gates,
)

# Names these modules used for limits that now live only in `stellar_source`.
RETIRED_NAMES = (
    "POOR_MATCH_RMS_THRESHOLD",
    "UNRELIABLE_MATCH_RMS_THRESHOLD",
    "CATALOG_DISAGREEMENT_SUBCLASS_STEPS",
    "AMBIGUOUS_PROBABILITY_MARGIN",
    "LOW_CONFIDENCE_THRESHOLD",
    "_RANKING_SOFTMAX_TEMPERATURE",
)


def module_body(module: ModuleType) -> list[ast.stmt]:
    """Parse a module's source file without running it.

    Parameters
    ----------
    module : `types.ModuleType`
        An imported module.

    Returns
    -------
    body : `list` [`ast.stmt`]
        The statements directly in the module body.
    """
    source_file = inspect.getsourcefile(module)
    assert source_file is not None
    return ast.parse(Path(source_file).read_text()).body


def assigned_names(node: ast.stmt) -> list[str]:
    """Name what a statement assigns, if it is a plain or annotated assignment.

    Parameters
    ----------
    node : `ast.stmt`
        A statement from a module body.

    Returns
    -------
    names : `list` [`str`]
        The assigned names, or an empty list for any other statement.
    """
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    else:
        return []
    return [target.id for target in targets if isinstance(target, ast.Name)]


def limit_like(name: str) -> bool:
    """Say whether a constant's name looks like a match limit.

    Parameters
    ----------
    name : `str`
        A module constant name.

    Returns
    -------
    looks_like_a_limit : `bool`
        `True` if the name contains ``THRESHOLD`` or ends in one of
        `LIMIT_NAME_SUFFIXES`.
    """
    return "THRESHOLD" in name.upper() or name.upper().endswith(LIMIT_NAME_SUFFIXES)


@pytest.mark.parametrize("name", sorted(SHARED_LIMITS))
def test_each_limit_is_defined_in_stellar_source_with_its_unit_documented(name: str) -> None:
    """Verify the limit is defined once, with its unit in the docstring."""
    body = module_body(stellar_source)
    index = next(i for i, node in enumerate(body) if assigned_names(node) == [name])
    docstring_node = body[index + 1]
    assert isinstance(docstring_node, ast.Expr), f"{name} has no docstring after it"
    docstring = " ".join(str(ast.literal_eval(docstring_node.value)).split())
    assert SHARED_LIMITS[name][1] in docstring, f"{name}'s docstring does not state its unit"


@pytest.mark.parametrize("module", MODULES_THAT_MUST_NOT_DEFINE_LIMITS, ids=lambda m: m.__name__)
def test_no_other_module_defines_a_limit_of_its_own(module: ModuleType) -> None:
    """Verify the module assigns no constant that looks like a match limit."""
    own = [name for node in module_body(module) for name in assigned_names(node) if limit_like(name)]
    assert own == [], f"{module.__name__} defines {own}; import it from stellar_source"


@pytest.mark.parametrize("module", MODULES_THAT_MUST_NOT_DEFINE_LIMITS, ids=lambda m: m.__name__)
def test_a_limit_a_module_holds_is_the_one_from_stellar_source(module: ModuleType) -> None:
    """Verify a shared limit a module exposes keeps the defined value."""
    for name, (value, _unit) in SHARED_LIMITS.items():
        if hasattr(module, name):
            assert getattr(module, name) == value


@pytest.mark.parametrize("module", MODULES_THAT_MUST_NOT_DEFINE_LIMITS, ids=lambda m: m.__name__)
def test_the_retired_limit_names_no_longer_exist(module: ModuleType) -> None:
    """Verify the module no longer has any of the retired duplicate names."""
    assert [name for name in RETIRED_NAMES if hasattr(module, name)] == []


def test_the_separate_well_separated_limit_is_gone_from_stellar_source() -> None:
    """Verify only one ambiguity limit remains, `AMBIGUOUS_RMS_GAP`."""
    assert not hasattr(stellar_source, "WELL_SEPARATED_POINTS")
    ambiguity_names = [
        name for node in module_body(stellar_source) for name in assigned_names(node) if "GAP" in name
    ]
    assert ambiguity_names == ["AMBIGUOUS_RMS_GAP"]
