"""Conformance test: every quality gate has a test that makes it fail.

A gate that has never failed could be hard-wired to pass, and a gate that
never says "not checked" could be hiding a check that did not run. This test
keeps new gates from arriving untested. It reads the source with ``ast``,
without running anything, and checks three things:

1. Every gate name defined in the pipelines is in the registry below, and
   every registered gate is defined. A new gate cannot be forgotten.
2. Every gate is built from a named ``*GATE_NAME`` constant, so it can be
   found. A gate built from a bare string is invisible to check 1.
3. For every gate, each status it can reach as ``failed`` or ``not_checked``
   appears in a test function that also names the gate.

The third check is a safety net, not a proof. A test function that names two
gates and one status counts for both. It catches the forgotten case, which is
the case that happens.

The checkers are also run on deliberately broken input (the last tests), so
the checker itself is shown to go red.
"""

import ast
from collections.abc import Iterable
from pathlib import Path

import pytest

PIPELINES_ROOT = Path(__file__).resolve().parents[1]

FAILED = "failed"
PASSED = "passed"
NOT_CHECKED = "not_checked"
ALL_THREE = frozenset({PASSED, FAILED, NOT_CHECKED})

# Every gate, with the statuses it can reach. A gate that cannot fail (or
# cannot be "not checked") by design says so here, so the omission is a
# decision on record. Add a new gate here when you add it to the code.
GATE_REGISTRY: dict[str, dict[str, frozenset[str]]] = {
    "stacking": {
        "frame_quarantine": ALL_THREE,
        # Setting aside a minority gain corrects the stack, so it never fails.
        "gain_homogeneity": frozenset({PASSED, NOT_CHECKED}),
        "background_homogeneity": ALL_THREE,
        "flat_level": ALL_THREE,
        "flat_noise": ALL_THREE,
        "calibration_metadata": ALL_THREE,
        # Not checked when no calibration master was applied.
        "calibration_frame_count": ALL_THREE,
        # Not checked for a single-exposure stack.
        "exposure_group_linearity": ALL_THREE,
        "rejected_pixel_fraction": ALL_THREE,
        "stack_sharpness": ALL_THREE,
        "spectral_registration": ALL_THREE,
        "saturated_pixel_fraction": ALL_THREE,
        "zero_pixel_fraction": ALL_THREE,
        # No Siril warning is the measurement, so it is never "not checked".
        "negative_pixels": frozenset({PASSED, FAILED}),
    },
    "astrometry": {
        "plate_solve": frozenset({PASSED, FAILED}),
        "source_detection": frozenset({PASSED, FAILED}),
        "astrometric_residual": ALL_THREE,
        "catalog_matches": ALL_THREE,
        "catalog_lookup": ALL_THREE,
    },
    "photometry": {
        "ensemble_frame_rejection": ALL_THREE,
        "capture_timestamps": frozenset({PASSED, FAILED}),
        "session_content": ALL_THREE,
        "session_plate_solve": ALL_THREE,
        "photometry_work": frozenset({PASSED, FAILED}),
        "comparison_ensemble": ALL_THREE,
        "registration_drift": ALL_THREE,
        # Too few stars only makes the cutoff unreliable; it never fails.
        "scatter_population": frozenset({PASSED, NOT_CHECKED}),
        "variability_discrimination": ALL_THREE,
        "detectable_amplitude": ALL_THREE,
        # Checks made on a period result (not the run's summary): the hold-out
        # and the alias test.
        "holdout_nights": ALL_THREE,
        "alias_ambiguity": ALL_THREE,
    },
    "spectroscopy": {
        "spectra_extracted": frozenset({PASSED, FAILED}),
        "zero_order_saturation": ALL_THREE,
        "spectral_classification": ALL_THREE,
        "catalog_agreement": ALL_THREE,
        "feature_significance": ALL_THREE,
        "resolution_measured": frozenset({PASSED, NOT_CHECKED}),
    },
    "asteroid_detection": {
        # Too few frames means "could not look", never a failure.
        "search_frames": frozenset({PASSED, NOT_CHECKED}),
        "pointing_metadata": ALL_THREE,
        "ephemeris_cross_match": ALL_THREE,
        "unmatched_movers": ALL_THREE,
    },
}

# The statuses that need a test of their own. A gate that passes is not the
# worry; one that cannot fail, or silently never says "not checked", is.
STATUSES_NEEDING_A_TEST = (FAILED, NOT_CHECKED)

GATE_BUILDERS = frozenset({"failed_gate", "passed_gate", "unchecked_gate"})


def python_files(root: Path) -> list[Path]:
    """List the Python files under a folder, leaving out caches.

    Returns
    -------
    files : `list` [`pathlib.Path`]
        The files, sorted.
    """
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def is_test_file(path: Path) -> bool:
    """Say whether a file is a test.

    Returns
    -------
    is_test : `bool`
        True for a ``test_*.py`` file or any file in a ``test`` folder.
    """
    return path.name.startswith("test_") or "test" in path.parts


def parse(path: Path) -> ast.Module:
    """Parse one Python file.

    Returns
    -------
    tree : `ast.Module`
        The parsed file.
    """
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def gate_constants(files: Iterable[Path]) -> dict[str, str]:
    """Find every ``*GATE_NAME = "..."`` constant, and imports that rename one.

    Parameters
    ----------
    files : `Iterable` [`pathlib.Path`]
        Files to read.

    Returns
    -------
    constants : `dict` [`str`, `str`]
        Each constant's name mapped to the gate name it holds.
    """
    trees = [parse(path) for path in files]
    constants: dict[str, str] = {}
    for tree in trees:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.endswith("GATE_NAME")
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                constants[node.targets[0].id] = node.value.value
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.asname and alias.name in constants:
                        constants[alias.asname] = constants[alias.name]
    return constants


def unnamed_gate_calls(files: Iterable[Path]) -> list[str]:
    """Find gate-building calls whose first argument is not a ``*GATE_NAME``.

    Parameters
    ----------
    files : `Iterable` [`pathlib.Path`]
        Source files to read.

    Returns
    -------
    problems : `list` [`str`]
        One ``file:line`` for each call that builds a gate from anything
        other than a ``*GATE_NAME`` constant.
    """
    problems: list[str] = []
    for path in files:
        for node in ast.walk(parse(path)):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            builder = function.id if isinstance(function, ast.Name) else getattr(function, "attr", None)
            if builder not in GATE_BUILDERS or not node.args:
                continue
            first = node.args[0]
            name = first.id if isinstance(first, ast.Name) else getattr(first, "attr", "")
            if not name.endswith("GATE_NAME"):
                problems.append(f"{path.name}:{node.lineno}")
    return problems


def covered_pairs(test_files: Iterable[Path], constants: dict[str, str]) -> set[tuple[str, str]]:
    """Find the (gate, status) pairs that some test function names together.

    A test function covers a pair when it refers to the gate (by its constant
    or by its name as a string) and to the status (as ``GateStatus.X`` or as
    a string).

    Parameters
    ----------
    test_files : `Iterable` [`pathlib.Path`]
        Test files to read.
    constants : `dict` [`str`, `str`]
        Gate name constants, from `gate_constants`.

    Returns
    -------
    pairs : `set` [`tuple` [`str`, `str`]]
        Every (gate name, status) pair covered.
    """
    known_gates = set(constants.values()) | {gate for gates in GATE_REGISTRY.values() for gate in gates}
    pairs: set[tuple[str, str]] = set()
    for path in test_files:
        for function in ast.walk(parse(path)):
            if not isinstance(function, ast.FunctionDef) or not function.name.startswith("test_"):
                continue
            gates: set[str] = set()
            statuses: set[str] = set()
            for node in ast.walk(function):
                if isinstance(node, ast.Name) and node.id in constants:
                    gates.add(constants[node.id])
                elif isinstance(node, ast.Attribute):
                    if node.attr in constants:
                        gates.add(constants[node.attr])
                    if isinstance(node.value, ast.Name) and node.value.id == "GateStatus":
                        statuses.add(node.attr.lower())
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value in known_gates:
                        gates.add(node.value)
                    if node.value in ALL_THREE:
                        statuses.add(node.value)
            pairs.update((gate, status) for gate in gates for status in statuses)
    return pairs


def missing_red_team_tests(
    registry: dict[str, dict[str, frozenset[str]]], pairs: set[tuple[str, str]]
) -> list[str]:
    """List the registered (gate, status) pairs that no test covers.

    Returns
    -------
    missing : `list` [`str`]
        One ``pipeline.gate -> status`` line for each reachable ``failed`` or
        ``not_checked`` status with no test.
    """
    return [
        f"{pipeline}.{gate} -> {status}"
        for pipeline, gates in registry.items()
        for gate, reachable in gates.items()
        for status in STATUSES_NEEDING_A_TEST
        if status in reachable and (gate, status) not in pairs
    ]


# ------------------------------------------------------------- the real code


def source_files() -> list[Path]:
    """List the pipeline source files that define or build gates.

    Returns
    -------
    files : `list` [`pathlib.Path`]
        Non-test files under the pipelines folder.
    """
    return [path for path in python_files(PIPELINES_ROOT) if not is_test_file(path)]


def test_every_gate_in_the_code_is_in_the_registry_and_the_reverse() -> None:
    """A gate cannot be added unregistered, nor stay registered once gone."""
    defined = set(gate_constants(source_files()).values())
    registered = {gate for gates in GATE_REGISTRY.values() for gate in gates}

    assert defined - registered == set(), "gates defined in code but missing from GATE_REGISTRY"
    assert registered - defined == set(), "gates in GATE_REGISTRY that no code defines"


def test_gate_names_are_unique_across_pipelines() -> None:
    """Two pipelines must not share a gate name; tests match on the name."""
    names = [gate for gates in GATE_REGISTRY.values() for gate in gates]

    assert len(names) == len(set(names))


def test_every_gate_is_built_from_a_named_constant() -> None:
    """A gate built from a bare string would escape the registry check."""
    assert unnamed_gate_calls(source_files()) == []


def test_every_reachable_failed_and_not_checked_status_has_a_test() -> None:
    """Each gate has a test that fails it and one that leaves it unchecked."""
    constants = gate_constants(python_files(PIPELINES_ROOT))
    test_files = [path for path in python_files(PIPELINES_ROOT) if is_test_file(path)]

    missing = missing_red_team_tests(GATE_REGISTRY, covered_pairs(test_files, constants))

    assert missing == [], "gates with a reachable status that no test exercises"


# ------------------------------------------------------ the checker goes red


def write(path: Path, text: str) -> Path:
    """Write a small Python file for a checker test.

    Returns
    -------
    path : `pathlib.Path`
        The file written.
    """
    path.write_text(text, encoding="utf-8")
    return path


def test_the_checker_finds_a_gate_that_is_in_no_registry(tmp_path: Path) -> None:
    """A gate constant that is not registered is reported."""
    source = write(tmp_path / "gates.py", 'NEW_GATE_NAME = "brand_new_check"\n')

    defined = set(gate_constants([source]).values())
    registered = {gate for gates in GATE_REGISTRY.values() for gate in gates}

    assert defined - registered == {"brand_new_check"}


def test_the_checker_finds_a_gate_built_from_a_bare_string(tmp_path: Path) -> None:
    """A gate built from a string literal is reported with its location."""
    source = write(
        tmp_path / "gates.py",
        "from astrometricslib.models.gate_result import failed_gate\n\n"
        'gate = failed_gate("loose_check", "bad")\n',
    )

    assert unnamed_gate_calls([source]) == ["gates.py:3"]


def test_the_checker_accepts_a_gate_built_from_a_named_constant(tmp_path: Path) -> None:
    """A constant whose name ends in GATE_NAME is accepted."""
    source = write(
        tmp_path / "gates.py",
        'X_GATE_NAME = "x"\n'
        'gate = failed_gate(X_GATE_NAME, "bad")\n'
        "other = passed_gate(module.X_GATE_NAME)\n",
    )

    assert unnamed_gate_calls([source]) == []


def test_the_checker_reports_a_gate_that_no_test_makes_fail(tmp_path: Path) -> None:
    """A gate tested only for passing is reported as missing its red test."""
    source = write(tmp_path / "gates.py", 'LOOSE_GATE_NAME = "loose_check"\n')
    test = write(
        tmp_path / "test_gates.py",
        "from gates import LOOSE_GATE_NAME\n\n"
        "def test_it_passes():\n"
        "    assert gate(LOOSE_GATE_NAME).status is GateStatus.PASSED\n",
    )
    constants = gate_constants([source, test])
    registry = {"demo": {"loose_check": ALL_THREE}}

    missing = missing_red_team_tests(registry, covered_pairs([test], constants))

    assert missing == ["demo.loose_check -> failed", "demo.loose_check -> not_checked"]


def test_the_checker_is_satisfied_by_tests_for_both_statuses(tmp_path: Path) -> None:
    """Tests that name the gate with FAILED and with NOT_CHECKED satisfy it."""
    source = write(tmp_path / "gates.py", 'LOOSE_GATE_NAME = "loose_check"\n')
    test = write(
        tmp_path / "test_gates.py",
        "def test_red():\n"
        "    assert gate(LOOSE_GATE_NAME).status is GateStatus.FAILED\n\n"
        "def test_unchecked():\n"
        '    assert gates["loose_check"].status.value == "not_checked"\n',
    )
    constants = gate_constants([source, test])
    registry = {"demo": {"loose_check": ALL_THREE}}

    assert missing_red_team_tests(registry, covered_pairs([test], constants)) == []


def test_the_checker_does_not_count_a_status_named_in_another_test(tmp_path: Path) -> None:
    """The gate and the status must appear in the same test function."""
    source = write(tmp_path / "gates.py", 'LOOSE_GATE_NAME = "loose_check"\n')
    test = write(
        tmp_path / "test_gates.py",
        "def test_names_the_gate():\n"
        "    gate(LOOSE_GATE_NAME)\n\n"
        "def test_names_a_status():\n"
        "    assert x is GateStatus.FAILED\n",
    )
    constants = gate_constants([source, test])

    pairs = covered_pairs([test], constants)

    assert ("loose_check", "failed") not in pairs


def test_a_renamed_import_still_resolves_to_its_gate(tmp_path: Path) -> None:
    """``GATE_NAME as ALIAS`` resolves, as the stacking stage imports it."""
    source = write(tmp_path / "gates.py", 'GATE_NAME = "loose_check"\n')
    user = write(tmp_path / "user.py", "from gates import GATE_NAME as ALIAS_GATE_NAME\n")

    assert gate_constants([source, user])["ALIAS_GATE_NAME"] == "loose_check"


@pytest.mark.parametrize("pipeline", sorted(GATE_REGISTRY))
def test_every_pipeline_in_the_registry_has_gates(pipeline: str) -> None:
    """An empty pipeline entry would hide a pipeline that lost its gates."""
    assert GATE_REGISTRY[pipeline]
