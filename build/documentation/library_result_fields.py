"""Purpose: Sphinx extension that lists the fields of a library's results.

Description: A call on a public sub-API returns a model (a Python class with
typed fields) that the library keeps private. A script reads the fields by
name and never imports the class, so the API reference page lists the fields
and shows no import path. The ``library-result-fields`` directive finds every
private model that the calls of one library return, including the models
nested inside them, and writes one table of fields for each.

Usage in a page::

    .. library-result-fields:: astrometricslib
"""

import ast
import importlib
import inspect
import logging
import types
import typing
from typing import Any

from docutils import nodes
from docutils.statemachine import StringList
from pydantic import BaseModel
from sphinx.application import Sphinx
from sphinx.util.docutils import SphinxDirective

logger = logging.getLogger(__name__)

_PUBLIC_MODULES = {
    "astrometricslib": (
        "astrometricslib",
        "astrometricslib.exceptions",
        "astrometricslib.reports",
        "astrometricslib.jobs",
        "astrometricslib.drivers",
    ),
    "wayfindinglib": ("wayfindinglib", "wayfindinglib.exceptions", "wayfindinglib.reports"),
}
"""For each library, the root and the public modules."""

_SUB_API_CLASSES = {
    "astrometricslib": (
        ("astrometricslib", "TargetCatalog"),
        ("astrometricslib", "StellarCatalog"),
        ("astrometricslib", "ProcessingPipelines"),
        ("astrometricslib", "CalibrationCatalog"),
        ("astrometricslib", "QualityRecords"),
        ("astrometricslib", "Visualization"),
        ("astrometricslib.jobs", "Jobs"),
    ),
    "wayfindinglib": (
        ("wayfindinglib", "ObservatoryControl"),
        ("wayfindinglib", "ObservationPlanning"),
        ("wayfindinglib", "ObservationExecution"),
        ("wayfindinglib", "ObservationReview"),
        ("wayfindinglib.api.control.mount", "MountControl"),
        ("wayfindinglib.api.control.imaging", "ImagingControl"),
        ("wayfindinglib.api.control.guiding", "GuidingControl"),
        ("wayfindinglib.api.control.safety", "SafetyControl"),
        ("wayfindinglib.api.control.equipment", "EquipmentControl"),
        ("wayfindinglib.api.control.remote", "RemoteControl"),
    ),
}
"""For each library, the sub-API classes whose calls return the results."""

_MODEL_MODULES = {
    "astrometricslib": ("astrometricslib.models", "astrometricslib.pipelines", "astrometricslib.api"),
    "wayfindinglib": ("wayfindinglib.models", "wayfindinglib.api"),
}
"""For each library, the packages imported so every model class is known."""


def _public_names(library: str) -> set[str]:
    """Collect the names a library offers publicly.

    Parameters
    ----------
    library : `str`
        ``astrometricslib`` or ``wayfindinglib``.

    Returns
    -------
    names : `set` [`str`]
        The names in ``__all__`` of the root and of every public module.
    """
    names: set[str] = set()
    for module_name in _PUBLIC_MODULES[library]:
        module = importlib.import_module(module_name)
        names.update(getattr(module, "__all__", ()))
    return names


def _known_models(library: str) -> dict[str, type[BaseModel]]:
    """Find every model class of a library by name.

    A call's return annotation can name a class that is only imported for type
    checkers, so the annotation is resolved against this table.

    Parameters
    ----------
    library : `str`
        ``astrometricslib`` or ``wayfindinglib``.

    Returns
    -------
    models : `dict` [`str`, `type` [`pydantic.BaseModel`]]
        The model classes, keyed by class name.
    """
    import pkgutil

    for package_name in _MODEL_MODULES[library]:
        package = importlib.import_module(package_name)
        for info in pkgutil.walk_packages(getattr(package, "__path__", []), package_name + "."):
            try:
                importlib.import_module(info.name)
            except ImportError, OSError, RuntimeError:  # a module that needs absent hardware code
                logger.debug("Skipped %s while listing result models", info.name)
    found: dict[str, type[BaseModel]] = {}
    pending = list(BaseModel.__subclasses__())
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        if cls.__module__.startswith(library):
            found.setdefault(cls.__name__, cls)
    return found


def _models_in(annotation: Any) -> list[type[BaseModel]]:
    """List the model classes an annotation names.

    Parameters
    ----------
    annotation : `Any`
        A resolved type annotation, such as ``list[Foo] | None``.

    Returns
    -------
    models : `list` [`type` [`pydantic.BaseModel`]]
        The model classes inside the annotation.
    """
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        return [annotation]
    found: list[type[BaseModel]] = []
    for argument in typing.get_args(annotation):
        found.extend(_models_in(argument))
    return found


def _type_text(annotation: Any) -> str:
    """Write an annotation as plain text with no module path.

    Parameters
    ----------
    annotation : `Any`
        A resolved type annotation.

    Returns
    -------
    text : `str`
        For example ``list[Foo] | None``.
    """
    if annotation is type(None):
        return "None"
    if annotation is Ellipsis:
        return "..."
    arguments = typing.get_args(annotation)
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        return " | ".join(_type_text(argument) for argument in arguments)
    if origin is typing.Literal:
        return "one of " + ", ".join(repr(argument) for argument in arguments)
    if inspect.isclass(annotation):
        return annotation.__name__
    if origin is not None and arguments:
        name = getattr(origin, "__name__", str(origin))
        return f"{name}[{', '.join(_type_text(argument) for argument in arguments)}]"
    return str(annotation).replace("typing.", "")


def _return_models(method: Any, namespace: dict[str, Any]) -> list[type[BaseModel]]:
    """List the models a method returns.

    Parameters
    ----------
    method : `Callable`
        A public method of a sub-API.
    namespace : `dict` [`str`, `Any`]
        Names used to resolve annotations that only type checkers import.

    Returns
    -------
    models : `list` [`type` [`pydantic.BaseModel`]]
        The model classes in the return annotation.
    """
    try:
        annotation = typing.get_type_hints(method, localns=namespace).get("return")
    except NameError, TypeError, AttributeError:  # an annotation naming something absent
        return []
    return _models_in(annotation)


def _field_comments(model: type[BaseModel]) -> dict[str, str]:
    """Read the comment above each field in a model's source.

    Parameters
    ----------
    model : `type` [`pydantic.BaseModel`]
        The model class.

    Returns
    -------
    comments : `dict` [`str`, `str`]
        The comment text of each field that has one, keyed by field name. A
        string right after the field counts as well.
    """
    try:
        source_lines, _ = inspect.getsourcelines(model)
        tree = ast.parse(_dedent(source_lines))
    except OSError, TypeError, SyntaxError:
        return {}
    class_node = tree.body[0]
    if not isinstance(class_node, ast.ClassDef):
        return {}
    comments: dict[str, str] = {}
    body = class_node.body
    for index, node in enumerate(body):
        if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)):
            continue
        parts: list[str] = []
        line_index = node.lineno - 2
        while line_index >= 0 and source_lines[line_index].strip().startswith("#"):
            parts.insert(0, source_lines[line_index].strip().lstrip("#").strip())
            line_index -= 1
        following = body[index + 1] if index + 1 < len(body) else None
        if (
            isinstance(following, ast.Expr)
            and isinstance(following.value, ast.Constant)
            and isinstance(following.value.value, str)
        ):
            parts.append(" ".join(following.value.value.split()))
        if parts:
            comments[node.target.id] = " ".join(parts)
    return comments


def _dedent(source_lines: list[str]) -> str:
    """Join source lines and remove the common indentation.

    Parameters
    ----------
    source_lines : `list` [`str`]
        The lines of a class.

    Returns
    -------
    source : `str`
        The class source, ready for ``ast.parse``.
    """
    import textwrap

    return textwrap.dedent("".join(source_lines))


def _summary(model: type[BaseModel]) -> str:
    """Take the first paragraph of a model's docstring.

    Parameters
    ----------
    model : `type` [`pydantic.BaseModel`]
        The model class.

    Returns
    -------
    summary : `str`
        The paragraph on one line, or an empty string.
    """
    doc = inspect.getdoc(model) or ""
    if doc.startswith("Usage docs:") or doc.startswith("!!! abstract"):
        return ""
    return " ".join(doc.split("\n\n")[0].split())


def _cell(text: str) -> str:
    """Make text safe for one table cell.

    Parameters
    ----------
    text : `str`
        The cell text.

    Returns
    -------
    cell : `str`
        The text on one line, with backticks closed off from the markup.
    """
    return " ".join(text.split())


def collect_result_models(library: str) -> list[type[BaseModel]]:
    """Find the private models that the calls of a library return.

    Parameters
    ----------
    library : `str`
        ``astrometricslib`` or ``wayfindinglib``.

    Returns
    -------
    models : `list` [`type` [`pydantic.BaseModel`]]
        Each private model once, sorted by name. A model the library offers
        publicly is left out, because the module sections document it. The
        models nested inside the fields of a returned model are included.
    """
    public = _public_names(library)
    namespace: dict[str, Any] = dict(_known_models(library))
    queue: list[type[BaseModel]] = []
    for module_name, class_name in _SUB_API_CLASSES[library]:
        sub_api = getattr(importlib.import_module(module_name), class_name)
        module_namespace = {**vars(importlib.import_module(sub_api.__module__)), **namespace}
        for name, member in inspect.getmembers(sub_api, callable):
            if name.startswith("_"):
                continue
            queue.extend(_return_models(member, module_namespace))
    seen: dict[str, type[BaseModel]] = {}
    while queue:
        model = queue.pop()
        if model.__name__ in seen:
            continue
        seen[model.__name__] = model
        for field in model.model_fields.values():
            queue.extend(_models_in(field.annotation))
    return sorted(
        (model for name, model in seen.items() if name not in public),
        key=lambda model: model.__name__.lower(),
    )


def render_model(model: type[BaseModel]) -> list[str]:
    """Write the reStructuredText for one model's fields.

    Parameters
    ----------
    model : `type` [`pydantic.BaseModel`]
        The model class.

    Returns
    -------
    lines : `list` [`str`]
        A rubric with the model's summary, then a table with one row per field.
        The class's module is not shown.
    """
    lines = [f".. rubric:: {model.__name__}", "", _summary(model) or "", ""]
    comments = _field_comments(model)
    if not model.model_fields:
        return [*lines, "This result has no fields of its own.", ""]
    lines += [
        ".. list-table::",
        "   :header-rows: 1",
        "   :widths: 22 28 50",
        "",
        "   * - Field",
        "     - Type",
        "     - Meaning",
    ]
    for name, field in model.model_fields.items():
        meaning = field.description or comments.get(name, "")
        lines += [
            f"   * - ``{name}``",
            f"     - ``{_cell(_type_text(field.annotation))}``",
            f"     - {_cell(meaning)}",
        ]
    lines.append("")
    return lines


_INTRODUCTION = (
    "A call returns a result model. The model classes are private, so this section",
    "shows no import path: read the fields by name from what the call returns. A",
    "result that the package root or one of the public modules above documents is",
    "not repeated here.",
    "",
)


class LibraryResultFields(SphinxDirective):
    """List the fields of every private result model of one library."""

    required_arguments = 1
    has_content = False

    def run(self) -> list[nodes.Node]:
        """Write one field table for each result model.

        Returns
        -------
        result : `list` [`docutils.nodes.Node`]
            The parsed tables, or an error node when the library is unknown.
        """
        library = self.arguments[0]
        if library not in _PUBLIC_MODULES:
            return [self.state_machine.reporter.error(f"Unknown library {library!r}", line=self.lineno)]
        lines: list[str] = list(_INTRODUCTION)
        for model in collect_result_models(library):
            lines += render_model(model)
        container = nodes.container()
        self.state.nested_parse(StringList(lines, source=self.get_source_info()[0]), 0, container)
        return list(container.children)


def setup(app: Sphinx) -> dict[str, Any]:
    """Register the ``library-result-fields`` directive.

    Parameters
    ----------
    app : `sphinx.application.Sphinx`
        The running Sphinx application.

    Returns
    -------
    metadata : `dict`
        Sphinx extension metadata (version and parallel-safety flags).
    """
    app.add_directive("library-result-fields", LibraryResultFields)
    return {"version": "0.1", "parallel_read_safe": True, "parallel_write_safe": True}
