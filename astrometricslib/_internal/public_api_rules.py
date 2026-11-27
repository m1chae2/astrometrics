"""Purpose: Check public methods against the API design rules, for tests.

Description: `documentation/library_design/Public_API_Design_Guide.md` sets
seven rules for the public methods of `astrometricslib` and `wayfindinglib`.
This module walks the public objects from a library's root object, checks
each method against the rules a program can test (its verb, its argument
names and defaults, its return type, its unit suffixes, its depth and the
`Owner:` line of its docstring) and compares the result with a baseline file
of known departures. Both libraries' `test_public_surface.py` use it, so the
rules are written once. Only tests may import this module.
"""

from __future__ import annotations

import importlib
import inspect
import re
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "PublicMethod",
    "PublicObject",
    "baseline_problems",
    "find_violations",
    "read_baseline",
    "walk_public_api",
]

VERBS = frozenset({
    "get",
    "query",
    "resolve",
    "create",
    "update",
    "delete",
    "add",
    "remove",
    "restore",
    "scan",
    "match",
    "assess",
    "measure",
    "predict",
    "record",
    "run",
    "render",
})
"""The verbs a method name may be, on their own (guide, Table 1)."""

VERB_FORMS = re.compile(r"(get|count_by|plot|record|measure|predict|map|set)_[a-z0-9_]+")
"""The verb forms that take a noun, a field or a setting after the verb."""

DEVICE_ACTIONS = frozenset({
    "connect",
    "slew",
    "park",
    "unpark",
    "capture",
    "expose",
    "stop",
    "move",
    "solve",
    "identify",
    "abort",
    "reconcile",
    "close",
})
"""The named list of device actions (guide, Section 3)."""

BANNED_ARGUMENTS = frozenset({
    "register_job",
    "expected_revision",
    "log_file",
    "output_file",
    "detail",
    "as_cards",
    "figsize",
    "max_line_chars",
})
"""Argument names that carry a client need into a signature (guide, Rule 1)."""

UNIT_WORDS = frozenset({"seconds", "minutes", "degrees", "percent"})
"""Unit words a numeric argument name writes as a short suffix instead."""

SHAPE = ("library", "area", "object", "method")
"""The deepest a public name may be (guide, Rule 6)."""

FOLD_BELOW = ("first", "second", "third")
"""An object with fewer methods than this folds into its parent (Rule 6)."""

OPERAND_SLOTS = ("subject", "operand")
"""The leading positional arguments that may go without a default (Rule 1)."""

OWNER_LINE = re.compile(r"^\s*Owner:\s*(\S+)\s*$", re.MULTILINE)
"""The docstring line that names a method's owner function (Rule 7)."""

NUMERIC = re.compile(r"\b(int|float)\b")
"""Finds a number type in the text of an annotation."""


@dataclass(frozen=True)
class PublicMethod:
    """One public method, found by walking from the root object.

    Attributes
    ----------
    qualified_name : `str`
        The dotted path from the root, such as ``astrometrics.stars.get``.
    function : `collections.abc.Callable`
        The bound method.
    """

    qualified_name: str
    function: Callable[..., object]


@dataclass(frozen=True)
class PublicObject:
    """One public object below the root (an area or an object of an area).

    Attributes
    ----------
    qualified_name : `str`
        The dotted path from the root, such as ``astrometrics.quality``.
    method_count : `int`
        How many public methods the object has of its own.
    """

    qualified_name: str
    method_count: int


def walk_public_api(
    root: object, root_name: str, is_sub_api: Callable[[object], bool]
) -> tuple[list[PublicMethod], list[PublicObject]]:
    """List every public method and object reachable from a root object.

    Properties are skipped without being read. Attributes that are neither a
    method nor a sub-API (settings, constants, a shared handle) are skipped.

    Parameters
    ----------
    root : `object`
        The library's root object, such as an `Astrometrics`.
    root_name : `str`
        The name the guide uses for the root, such as ``"astrometrics"``.
    is_sub_api : `collections.abc.Callable`
        Says whether an attribute value is a sub-API object to walk into.

    Returns
    -------
    methods : `list` [`PublicMethod`]
        Every public method, sorted by qualified name.
    objects : `list` [`PublicObject`]
        Every sub-API object below the root, sorted by qualified name.
    """
    methods: list[PublicMethod] = []
    objects: list[PublicObject] = []
    seen: set[int] = set()

    def visit(instance: object, path: str) -> int:
        """Walk one object and return how many methods it has of its own.

        Parameters
        ----------
        instance : `object`
            The object to walk.
        path : `str`
            Its dotted path from the root.

        Returns
        -------
        count : `int`
            The number of public methods found directly on it.
        """
        seen.add(id(instance))
        count = 0
        for name in sorted(name for name in dir(instance) if not name.startswith("_")):
            if isinstance(inspect.getattr_static(type(instance), name, None), property):
                continue
            value = getattr(instance, name)
            qualified = f"{path}.{name}"
            if inspect.ismethod(value) or inspect.isfunction(value):
                methods.append(PublicMethod(qualified, value))
                count += 1
            elif is_sub_api(value) and id(value) not in seen:
                objects.append(PublicObject(qualified, visit(value, qualified)))
        return count

    visit(root, root_name)
    return sorted(methods, key=lambda m: m.qualified_name), sorted(objects, key=lambda o: o.qualified_name)


def _annotation_parts(annotation: object) -> list[str]:
    """Split a return annotation into the texts of its union members.

    Parameters
    ----------
    annotation : `object`
        A type, a typing construct or a string annotation.

    Returns
    -------
    parts : `list` [`str`]
        One text per member; a single type gives one.
    """
    if isinstance(annotation, str):
        parts, depth, start = [], 0, 0
        for index, character in enumerate(annotation):
            if character in "[(":
                depth += 1
            elif character in "])":
                depth -= 1
            elif character == "|" and depth == 0:
                parts.append(annotation[start:index].strip())
                start = index + 1
        parts.append(annotation[start:].strip())
        if len(parts) == 1 and parts[0].startswith(("Optional[", "typing.Optional[")):
            return [parts[0][parts[0].index("[") + 1 : -1], "None"]
        return parts
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        members = typing.get_args(annotation)
        return ["None" if member is type(None) else inspect.formatannotation(member) for member in members]
    return [inspect.formatannotation(annotation)]


def _owner_exists(dotted_path: str) -> bool:
    """Say whether a dotted path names a function that can be imported.

    Parameters
    ----------
    dotted_path : `str`
        A module path followed by a function name, possibly through a class.

    Returns
    -------
    exists : `bool`
        True when the longest importable module prefix holds a callable at
        the rest of the path.
    """
    parts = dotted_path.split(".")
    for split in range(len(parts) - 1, 0, -1):
        try:
            value: object = importlib.import_module(".".join(parts[:split]))
        except ImportError:
            continue
        for name in parts[split:]:
            value = getattr(value, name, None)
        return callable(value) and not inspect.isclass(value)
    return False


def _method_violations(method: PublicMethod) -> list[str]:
    """Check one method against the rules a program can test.

    Parameters
    ----------
    method : `PublicMethod`
        The method to check.

    Returns
    -------
    rules : `list` [`str`]
        One rule name per departure, such as ``"verb"`` or
        ``"no-default(telescope)"``.
    """
    rules = []
    name = method.qualified_name.rsplit(".", 1)[-1]
    if name not in VERBS and name not in DEVICE_ACTIONS and not VERB_FORMS.fullmatch(name):
        rules.append("verb")
    signature = inspect.signature(method.function)
    positional = 0
    for parameter in signature.parameters.values():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue
        argument = parameter.name
        if argument in BANNED_ARGUMENTS or argument.startswith("on_") or argument.endswith("_callback"):
            rules.append(f"banned-argument({argument})")
        is_option = parameter.kind == parameter.KEYWORD_ONLY or positional >= len(OPERAND_SLOTS)
        if parameter.kind != parameter.KEYWORD_ONLY:
            positional += 1
        if is_option and parameter.default is parameter.empty:
            rules.append(f"no-default({argument})")
        annotation = parameter.annotation
        text = annotation if isinstance(annotation, str) else inspect.formatannotation(annotation)
        if UNIT_WORDS & set(argument.split("_")) and NUMERIC.search(text):
            rules.append(f"unit-suffix({argument})")
    if signature.return_annotation is signature.empty:
        rules.append("no-return-annotation")
    else:
        parts = _annotation_parts(signature.return_annotation)
        others = [part for part in parts if part != "None"]
        if len(others) > 1:
            rules.append("return-union")
        if others == ["bool"]:
            rules.append("bool-return")
    if len(method.qualified_name.split(".")) > len(SHAPE):
        rules.append("depth")
    owner = OWNER_LINE.search(inspect.getdoc(method.function) or "")
    if owner is None or not _owner_exists(owner.group(1)):
        rules.append("owner")
    return rules


def find_violations(
    methods: list[PublicMethod], objects: list[PublicObject], fold_exempt_parents: tuple[str, ...] = ()
) -> set[str]:
    """Check every method and object, and list each departure.

    Parameters
    ----------
    methods : `list` [`PublicMethod`]
        The methods to check.
    objects : `list` [`PublicObject`]
        The objects to check against the fold rule.
    fold_exempt_parents : `tuple` [`str`], optional
        Qualified names whose child objects keep fewer than three methods
        (the pipeline children of ``processing``).

    Returns
    -------
    violations : `set` [`str`]
        Lines of the form ``<qualified name>: <rule>``.
    """
    found = {f"{method.qualified_name}: {rule}" for method in methods for rule in _method_violations(method)}
    for public_object in objects:
        parent = public_object.qualified_name.rsplit(".", 1)[0]
        is_object = len(public_object.qualified_name.split(".")) >= len(SHAPE) - 1
        if is_object and parent not in fold_exempt_parents and public_object.method_count < len(FOLD_BELOW):
            found.add(f"{public_object.qualified_name}: fold")
    return found


def read_baseline(path: Path) -> set[str]:
    """Read the known departures from a baseline file.

    Parameters
    ----------
    path : `pathlib.Path`
        The baseline file. Lines that start with ``#`` and blank lines are
        skipped.

    Returns
    -------
    entries : `set` [`str`]
        One entry per remaining line.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")}


def baseline_problems(found: set[str], path: Path) -> list[str]:
    """Compare the departures found with a baseline file.

    Parameters
    ----------
    found : `set` [`str`]
        The departures `find_violations` found.
    path : `pathlib.Path`
        The baseline file.

    Returns
    -------
    problems : `list` [`str`]
        One message for new departures and one for stale baseline lines;
        empty when the two match.
    """
    baseline = read_baseline(path)
    problems = []
    if new := sorted(found - baseline):
        problems.append(
            "New departures from the public API design rules "
            "(documentation/library_design/Public_API_Design_Guide.md); fix them, do not add them to "
            f"{path.name}:\n" + "\n".join(new)
        )
    if stale := sorted(baseline - found):
        problems.append(
            f"These departures are fixed; delete their lines from {path.name}:\n" + "\n".join(stale)
        )
    return problems
