"""Rule test: only the Siril driver starts Siril or writes Siril commands.

The Siril driver (``astrometricslib/_internal/drivers/siril/``) is the one
place that starts the Siril program and the one place that writes Siril's
script commands. Everything else (the pipelines, the library scripts, the
other library, the backend, the MCP servers and the simulator) calls the
driver. This test reads the source of every other module and fails when one
of them

* starts a program (``subprocess``, ``run_registered``, ``launch_registered``,
  ``os.system`` or an ``os.exec``/``os.spawn`` call) and also names Siril's
  program (the ``siril_executable`` setting, ``siril-cli`` or the Siril
  Flatpak), or
* holds a string that is a Siril command line (``load "..."``,
  ``platesolve``, ``pcc``, ``autostretch``, ``savejpg``, ``starnet``,
  ``register``, ``stack`` and the others in `SIRIL_COMMAND_PATTERN`).

Tests are not checked: they read the scripts the driver writes, and they
stand in for Siril with other programs. Docstrings and comments are not
checked either, because they describe the commands without running them.
"""

import ast
import functools
import re
from pathlib import Path

import pytest

# The repository root: this file is at
# astrometricslib/_internal/drivers/siril/test/.
REPOSITORY_ROOT = Path(__file__).resolve().parents[5]

# The driver's own folder, the only one allowed to do either.
DRIVER_FOLDER = REPOSITORY_ROOT / "astrometricslib" / "_internal" / "drivers" / "siril"

# The packages whose modules are checked.
CHECKED_PACKAGES = ("astrometricslib", "wayfindinglib", "backend", "mcp_servers", "simulator")

# The start of a Siril script command, as the driver writes them. A string
# (or the fixed text of an f-string) that starts with one of these is a Siril
# command line.
# A command word that is also the name of a setting or a step (``pcc``,
# ``starnet``) counts only with an option after it.
SIRIL_COMMAND_PATTERN = re.compile(
    r"^(?:"
    r"requires \d|load \"|save \"|savejpg \S|platesolve\b|seqplatesolve\b|link \w+$|pcc -|spcc -|"
    r"autostretch\b|mtf \S|rmgreen\b|"
    r"starnet -|setext \S|set32bits\b|set16bits\b|setfindstar\b|setcompress \S|seqapplyreg \S|"
    r"convert \S+ -out|register \S+(?:\s+-|\s*$)|stack \S+ (?:rej|sum|mean|med|min|max)\b|calibrate \S+ -|"
    r"fmul \S|mirrorx\b|cd \.\./process|cd lights|set core\."
    r")"
)

# The names that start a program.
PROCESS_STARTERS = frozenset({"run_registered", "launch_registered", "Popen", "system"})
PROCESS_STARTER_PREFIXES = ("exec", "spawn")
SUBPROCESS_CALLS = frozenset({"run", "Popen", "call", "check_call", "check_output"})

# The words that name the Siril program.
SIRIL_PROGRAM_NAMES = frozenset({"siril_executable", "get_siril_executable"})
SIRIL_PROGRAM_TEXT = ("siril-cli", "org.siril.Siril", "--command=siril")


# Modules that start a program and name Siril without starting Siril, each
# with the reason.
ALLOWED_PROGRAM_STARTS = {
    "astrometricslib/_internal/drivers/program_setup.py": (
        "sets up Siril and GraXpert on Windows; it runs msiexec to unpack the checked GraXpert "
        "installer and never starts Siril"
    ),
}


def checked_modules() -> list[Path]:
    """List the modules the rule applies to.

    Returns
    -------
    paths : `list` [`pathlib.Path`]
        Every ``.py`` file in the checked packages, outside the driver's
        folder, test folders and ``conftest.py`` files.
    """
    paths = []
    for package in CHECKED_PACKAGES:
        for path in sorted((REPOSITORY_ROOT / package).rglob("*.py")):
            relative_parts = path.relative_to(REPOSITORY_ROOT).parts
            if path.is_relative_to(DRIVER_FOLDER) or path.name == "conftest.py":
                continue
            if any(part in {"test", "tests"} for part in relative_parts):
                continue
            paths.append(path)
    return paths


@functools.cache
def parsed_module(path: Path) -> ast.Module:
    """Parse one module once for all the tests here.

    Parameters
    ----------
    path : `pathlib.Path`
        The module.

    Returns
    -------
    tree : `ast.Module`
        Its syntax tree.
    """
    return ast.parse(path.read_text(encoding="utf-8"))


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """Find the string nodes that are docstrings or bare string statements.

    Parameters
    ----------
    tree : `ast.Module`
        The parsed module.

    Returns
    -------
    node_ids : `set` [`int`]
        The ``id`` of each string constant that stands alone as a statement.
    """
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def _fixed_text(node: ast.AST) -> str | None:
    """Give the fixed text at the start of a string or f-string.

    Parameters
    ----------
    node : `ast.AST`
        A syntax tree node.

    Returns
    -------
    text : `str` or `None`
        The string's text, or an f-string's text up to its first value with
        each value read as ``{}``, or `None` for any other node.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "{}" for part in node.values)
    return None


def siril_command_strings(tree: ast.Module) -> list[tuple[int, str]]:
    """Find the strings in a module that are Siril command lines.

    Parameters
    ----------
    tree : `ast.Module`
        The parsed module.

    Returns
    -------
    found : `list` [`tuple` [`int`, `str`]]
        The line number and text of each Siril command string.
    """
    skipped = _docstring_nodes(tree)
    found = []
    inside_fstrings = {
        id(part) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr) for part in node.values
    }
    for node in ast.walk(tree):
        if id(node) in skipped or id(node) in inside_fstrings:
            continue
        text = _fixed_text(node)
        if text is not None and SIRIL_COMMAND_PATTERN.match(text.strip()):
            found.append((getattr(node, "lineno", 0), text))
    return found


def _starts_a_program(tree: ast.Module) -> list[int]:
    """Find the calls in a module that start a program.

    Parameters
    ----------
    tree : `ast.Module`
        The parsed module.

    Returns
    -------
    lines : `list` [`int`]
        The line number of each call to `subprocess.run` and its relatives,
        `run_registered`, `launch_registered`, ``os.system`` or an
        ``os.exec``/``os.spawn`` function.
    """
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name) and function.id in PROCESS_STARTERS:
            lines.append(node.lineno)
        elif isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
            owner, name = function.value.id, function.attr
            if (owner == "subprocess" and name in SUBPROCESS_CALLS) or (
                owner == "os" and (name == "system" or name.startswith(PROCESS_STARTER_PREFIXES))
            ):
                lines.append(node.lineno)
    return lines


def _names_siril(tree: ast.Module) -> bool:
    """Say whether a module's code names the Siril program.

    Parameters
    ----------
    tree : `ast.Module`
        The parsed module.

    Returns
    -------
    names_siril : `bool`
        `True` if the code uses the ``siril_executable`` setting or holds
        the text of Siril's program name, outside docstrings.
    """
    skipped = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in SIRIL_PROGRAM_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in SIRIL_PROGRAM_NAMES:
            return True
        if isinstance(node, ast.arg) and node.arg in SIRIL_PROGRAM_NAMES:
            return True
        if id(node) not in skipped:
            text = _fixed_text(node)
            if text is not None and any(name in text for name in SIRIL_PROGRAM_TEXT):
                return True
    return False


def siril_starts(tree: ast.Module) -> list[int]:
    """Find the program starts in a module that names the Siril program.

    Parameters
    ----------
    tree : `ast.Module`
        The parsed module.

    Returns
    -------
    lines : `list` [`int`]
        The line numbers of the program starts, or an empty list when the
        module starts no program or never names Siril.
    """
    starts = _starts_a_program(tree)
    return starts if starts and _names_siril(tree) else []


def test_no_module_outside_the_driver_starts_siril() -> None:
    """Every Siril start is in the Siril driver."""
    offenders = []
    for path in checked_modules():
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        if relative in ALLOWED_PROGRAM_STARTS:
            continue
        tree = parsed_module(path)
        offenders += [f"{relative}:{line}" for line in siril_starts(tree)]
    assert offenders == [], (
        "These modules start a program and name Siril; start Siril through the Siril driver "
        f"(astrometricslib/_internal/drivers/siril/core) instead: {offenders}"
    )


def test_no_module_outside_the_driver_holds_siril_commands() -> None:
    """Every Siril command line is written by the Siril driver."""
    offenders = []
    for path in checked_modules():
        tree = parsed_module(path)
        offenders += [
            f"{path.relative_to(REPOSITORY_ROOT)}:{line}: {text!r}"
            for line, text in siril_command_strings(tree)
        ]
    assert offenders == [], (
        "These modules hold Siril commands; write them in the Siril driver "
        f"(astrometricslib/_internal/drivers/siril) instead: {offenders}"
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('commands = [f\'load "{name}"\', "starnet -stretch"]', 2),
        ('command = f"platesolve -catalog={catalog}"', 1),
        ('script = ["requires 1.2.0", "stack light_source_ rej n 3 3"]', 2),
        ('"""load "a.fits" is what Siril reads."""', 0),
        ('message = "stack the frames again"', 0),
        ('note = "register the target"', 0),
        ('method = "pcc"', 0),
        ('step = "starnet"', 0),
        ('command = f"pcc -catalog={name}"', 1),
    ],
)
def test_the_command_rule_finds_siril_commands_and_only_them(source: str, expected: int) -> None:
    """The command rule catches script text but not prose or docstrings."""
    assert len(siril_command_strings(ast.parse(source))) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import subprocess\nsubprocess.run([siril_executable, '-s', path])", 1),
        ("run_registered([*parts, '-d', folder], 60)\nname = config.get_siril_executable()", 1),
        ("run_registered(['graxpert', '-cli'], 60)", 0),
        (
            "siril_executable = get_siril_executable()\nrun_siril_script(folder, commands, siril_executable)",
            0,
        ),
    ],
)
def test_the_start_rule_finds_siril_starts_and_only_them(source: str, expected: int) -> None:
    """The start rule catches a Siril start, not a call to the driver."""
    assert len(siril_starts(ast.parse(source))) == expected


def test_the_rule_reads_every_checked_package() -> None:
    """The rule reads modules from the library, the backend and the others."""
    checked = {path.relative_to(REPOSITORY_ROOT).parts[0] for path in checked_modules()}
    assert {"astrometricslib", "wayfindinglib", "backend"} <= checked
    assert not any(path.is_relative_to(DRIVER_FOLDER) for path in checked_modules())


def test_every_allowed_program_start_still_exists() -> None:
    """An allowed module that was moved or removed is taken off the list."""
    for relative in ALLOWED_PROGRAM_STARTS:
        assert (REPOSITORY_ROOT / relative).is_file(), relative
