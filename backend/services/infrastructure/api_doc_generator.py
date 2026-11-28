"""API Documentation Generator for Astrometrics and Wayfinding libraries.

This module provides dynamic generation of structured Markdown documentation
from live Python docstrings, method signatures, and class hierarchies in
astrometricslib and wayfindinglib. It enables in-console reading of the
complete Python API reference without requiring external builds.
"""

from __future__ import annotations

import inspect
import re
import typing
from typing import Any

# In-memory cache for generated markdown content to avoid redundant reflection
_DOCS_CACHE: dict[str, dict[str, str]] = {}


PUBLIC_LIBRARY_MODULES = {
    "astrometricslib": (
        "astrometricslib",
        "astrometricslib.arguments",
        "astrometricslib.exceptions",
        "astrometricslib.reports",
        "astrometricslib.jobs",
        "astrometricslib.drivers",
        "astrometricslib.parameters",
    ),
    "wayfindinglib": (
        "wayfindinglib",
        "wayfindinglib.exceptions",
        "wayfindinglib.reports",
        "wayfindinglib.drivers",
    ),
}
"""For each library, the root and its public modules."""


def _public_attribute(library: str, name: str) -> Any:
    """Find a public name in a library root or in one of its public modules.

    Parameters
    ----------
    library : `str`
        ``astrometricslib`` or ``wayfindinglib``.
    name : `str`
        The name to look up.

    Returns
    -------
    value : `Any`
        The named object, or `None` when no public module has it.
    """
    import importlib

    for module_name in PUBLIC_LIBRARY_MODULES[library]:
        value = getattr(importlib.import_module(module_name), name, None)
        if value is not None:
            return value
    return None


def _class_hints(cls: type) -> dict[str, Any]:
    """Read the annotations of a class, or nothing when one cannot be resolved.

    Parameters
    ----------
    cls : `type`
        The class to read.

    Returns
    -------
    hints : `dict` [`str`, `Any`]
        Attribute name to type. Empty if an annotation names a type that is
        only imported for type checking.
    """
    try:
        return typing.get_type_hints(cls)
    except NameError, TypeError:
        return {}


#: A dotted name, such as ``astrometricslib.models.target.Target``. The last
#: part is kept: ``Target``.
_DOTTED_NAME = re.compile(r"\b(?:[A-Za-z_][A-Za-z0-9_]*\.)+([A-Za-z_][A-Za-z0-9_]*)")

#: The anchor at the end of a reference line, such as
#: ``{#api-TargetFrames-get_rotation}``. The Documentation tab turns it
#: into the ``id`` of the heading or list item and does not show it.
_ENTRY_ANCHOR = re.compile(r"\s\{#(api-[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)?)\}\s*$")

#: The generated topics that hold the classes and methods of the libraries,
#: with the library each one documents.
API_REFERENCE_TOPICS = {"api/astrometricslib": "astrometricslib", "api/wayfindinglib": "wayfindinglib"}

#: The objects the Command Console preloads, with the class each one is.
CONSOLE_OBJECTS = {
    "astrometrics": ("astrometricslib", "Astrometrics"),
    "wayfinder": ("wayfindinglib", "Wayfinder"),
}

#: How many close names `find_doc_entries` offers when nothing matches.
DOC_SUGGESTION_COUNT = 5

#: How close a name must be, from 0 to 1, to be offered as a suggestion.
DOC_SUGGESTION_CUTOFF = 0.6

#: Cached result of `api_doc_entries`.
_ENTRIES_CACHE: list[dict[str, str]] = []


def short_type_names(text: str) -> str:
    """Keep only the last part of each dotted name in a type.

    Parameters
    ----------
    text : `str`
        A type, such as ``astrometricslib.models.target.Target | None``.

    Returns
    -------
    text : `str`
        The same type with short names, such as ``Target | None``.
    """
    return _DOTTED_NAME.sub(r"\1", text)


class _AnnotationText:
    """A type written as text, which a signature prints without quotes.

    Parameters
    ----------
    text : `str`
        The type as it should be printed.
    """

    def __init__(self, text: str) -> None:
        """Keep the text."""
        self.text = text

    def __repr__(self) -> str:
        """Return the text, which is what a signature prints.

        Returns
        -------
        text : `str`
            The type as it should be printed.
        """
        return self.text


def _short_annotation(annotation: Any) -> Any:
    """Write one type with short names.

    Parameters
    ----------
    annotation : `Any`
        A type from a signature, or the empty marker when there is none.

    Returns
    -------
    annotation : `Any`
        The empty marker unchanged, or an `_AnnotationText` with short names.
    """
    if annotation is inspect.Parameter.empty:
        return annotation
    text = annotation if isinstance(annotation, str) else inspect.formatannotation(annotation)
    return _AnnotationText(short_type_names(text))


def format_signature(function: Any, hide_private_return: bool = False) -> str:
    """Write a callable's signature with short type names.

    Only the types are shortened; default values are printed as they are.

    Parameters
    ----------
    function : `Any`
        The function, method or class.
    hide_private_return : `bool`, optional
        When `True`, a return type that names a private (``_internal``)
        class is left out, so no private name is shown.

    Returns
    -------
    signature : `str`
        Such as ``(target_id: str) -> Target | None``, or ``"(...)"`` when
        the signature cannot be read.
    """
    from annotationlib import Format

    try:
        signature = inspect.signature(function, annotation_format=Format.FORWARDREF)
    except ValueError, TypeError, NameError:
        # Some built-in callables have no signature to read.
        return "(...)"
    if hide_private_return and "_internal" in str(signature.return_annotation):
        signature = signature.replace(return_annotation=inspect.Signature.empty)
    parameters = [
        parameter.replace(annotation=_short_annotation(parameter.annotation))
        for parameter in signature.parameters.values()
    ]
    return str(
        signature.replace(
            parameters=parameters, return_annotation=_short_annotation(signature.return_annotation)
        )
    )


def entry_anchor(owner: str, name: str = "") -> str:
    """Make the anchor of one reference entry.

    Parameters
    ----------
    owner : `str`
        The class name, such as ``TargetCatalog``, or for a module-level
        name the last part of its module, such as ``exceptions``.
    name : `str`, optional
        The method or function name. Leave it out for a class heading.

    Returns
    -------
    anchor : `str`
        Such as ``api-TargetFrames-get_rotation``, or ``api-Target``.
    """
    return f"api-{owner}-{name}" if name else f"api-{owner}"


def _anchor_mark(anchor: str) -> str:
    """Write the anchor mark that ends a reference line.

    Parameters
    ----------
    anchor : `str`
        The anchor, from `entry_anchor`.

    Returns
    -------
    mark : `str`
        Such as `` {#api-TargetFrames-get_rotation}``.
    """
    return f" {{#{anchor}}}"


def _qualified_name_of_anchor(anchor: str, library: str) -> str:
    """Turn an anchor back into the name it documents.

    Parameters
    ----------
    anchor : `str`
        Such as ``api-TargetFrames-get_rotation`` or
        ``api-exceptions-NotFoundError``.
    library : `str`
        The library the topic documents.

    Returns
    -------
    name : `str`
        Such as ``TargetFrames.get_rotation`` or
        ``astrometricslib.exceptions.NotFoundError``.
    """
    owner, _, name = anchor.removeprefix("api-").partition("-")
    modules = {module.rsplit(".", 1)[-1]: module for module in PUBLIC_LIBRARY_MODULES[library]}
    owner = modules.get(owner, owner)
    return f"{owner}.{name}" if name else owner


def api_doc_entries() -> list[dict[str, str]]:
    """List every class, method and function the API reference documents.

    Returns
    -------
    entries : `list` of `dict`
        ``topic_id`` (the reference topic), ``anchor_id`` (such as
        ``api-TargetFrames-get_rotation``) and ``qualified_name`` (such as
        ``TargetFrames.get_rotation``), in page order.
    """
    if _ENTRIES_CACHE:
        return list(_ENTRIES_CACHE)
    entries: list[dict[str, str]] = []
    for topic_id, library in API_REFERENCE_TOPICS.items():
        topic = get_api_topic_content(topic_id)
        if topic is None:
            continue
        for line in topic["content"].splitlines():
            match = _ENTRY_ANCHOR.search(line)
            if match:
                anchor = match.group(1)
                entries.append({
                    "topic_id": topic_id,
                    "anchor_id": anchor,
                    "qualified_name": _qualified_name_of_anchor(anchor, library),
                })
    _ENTRIES_CACHE.extend(entries)
    return entries


def _console_path_class_name(path: list[str]) -> str | None:
    """Follow a console path to the class name of the object it reaches.

    Parameters
    ----------
    path : `list` of `str`
        The parts before the last one, such as ``["astrometrics", "targets"]``.

    Returns
    -------
    class_name : `str` or `None`
        Such as ``"TargetCatalog"``, or `None` when the path does not start
        at a preloaded object or a part cannot be followed.
    """
    if not path or path[0] not in CONSOLE_OBJECTS:
        return None
    library, class_name = CONSOLE_OBJECTS[path[0]]
    cls = _public_attribute(library, class_name)
    for part in path[1:]:
        if not isinstance(cls, type):
            return None
        cls = _class_hints(cls).get(part) or _assigned_attribute_class(cls, part)
    return cls.__name__ if isinstance(cls, type) else None


def _assigned_attribute_class(cls: type, attribute: str) -> type | None:
    """Find the class of an attribute a class sets up in its constructor.

    The library facades set their parts in code (``self.targets =
    TargetCatalog(...)``) rather than declaring them, so the constructor's
    source is read.

    Parameters
    ----------
    cls : `type`
        The class, such as ``Astrometrics``.
    attribute : `str`
        The attribute, such as ``targets``.

    Returns
    -------
    attribute_class : `type` or `None`
        The public class the attribute is made from, or `None`.
    """
    pattern = re.compile(rf"self\.{re.escape(attribute)}\s*=\s*(?:[\w.]+\s+or\s+)?([A-Z]\w*)\(")
    for method_name in ("__init__", "_build"):
        method = getattr(cls, method_name, None)
        try:
            source = inspect.getsource(method) if method is not None else ""
        except OSError, TypeError:
            continue
        match = pattern.search(source)
        if match:
            for library in PUBLIC_LIBRARY_MODULES:
                found = _public_attribute(library, match.group(1))
                if isinstance(found, type):
                    return found
    return None


def _as_result(entry: dict[str, str], suggestion: bool) -> dict[str, Any]:
    """Copy an entry for `find_doc_entries`, marked as a match or a suggestion.

    Parameters
    ----------
    entry : `dict`
        One entry from `api_doc_entries`.
    suggestion : `bool`
        `True` when the entry is only a close name.

    Returns
    -------
    result : `dict`
        ``topic_id``, ``anchor_id``, ``qualified_name`` and ``suggestion``.
    """
    return {**entry, "suggestion": suggestion}


def find_doc_entries(name: str) -> list[dict]:
    """Find the API reference entries that document a name.

    The name may be bare (``get_rotation``, which can fit several
    classes), an owner and a name (``TargetFrames.get_rotation``), or a
    path through the console's preloaded objects
    (``astrometrics.targets.frames.delete``). Exact matches come first. When
    nothing matches, close names are returned instead, each marked as a
    suggestion.

    Parameters
    ----------
    name : `str`
        The name to look up.

    Returns
    -------
    entries : `list` of `dict`
        ``topic_id``, ``anchor_id``, ``qualified_name`` and ``suggestion``
        (`False` for a match, `True` for a close name). Empty when nothing
        is close.
    """
    import difflib

    query = name.strip().removesuffix("()")
    if not query:
        return []
    entries = api_doc_entries()
    candidates = [query]
    parts = query.split(".")
    if parts[0] in CONSOLE_OBJECTS:
        # A console path: the method of the object the path reaches
        # (``astrometrics.targets.frames.delete``), or the object itself
        # (``wayfinder.control.mount``).
        candidates = []
        owner = _console_path_class_name(parts[:-1]) if len(parts) > 1 else None
        if owner:
            candidates.append(f"{owner}.{parts[-1]}")
        whole = _console_path_class_name(parts)
        if whole:
            candidates.append(whole)
        candidates.append(query)

    def fits(qualified_name: str, text: str) -> bool:
        """Say whether a qualified name is the text or ends with it.

        Returns
        -------
        fits : `bool`
            `True` for ``TargetCatalog.get`` and the text ``get``.
        """
        return qualified_name == text or qualified_name.endswith(f".{text}")

    for candidate in candidates:
        for compare in (str, str.lower):
            wanted = compare(candidate)
            matches = [entry for entry in entries if fits(compare(entry["qualified_name"]), wanted)]
            if matches:
                return [_as_result(entry, suggestion=False) for entry in matches]
    query = candidates[0]

    by_name = {entry["qualified_name"]: entry for entry in entries}
    close = difflib.get_close_matches(
        query, list(by_name), n=DOC_SUGGESTION_COUNT, cutoff=DOC_SUGGESTION_CUTOFF
    )
    if not close:
        last_parts: dict[str, list[str]] = {}
        for qualified_name in by_name:
            last_parts.setdefault(qualified_name.rsplit(".", 1)[-1], []).append(qualified_name)
        close_last = difflib.get_close_matches(
            query.rsplit(".", 1)[-1], list(last_parts), n=DOC_SUGGESTION_COUNT, cutoff=DOC_SUGGESTION_CUTOFF
        )
        close = [qualified_name for last in close_last for qualified_name in last_parts[last]]
    return [
        _as_result(by_name[qualified_name], suggestion=True)
        for qualified_name in close[:DOC_SUGGESTION_COUNT]
    ]


def format_docstring(doc: str | None) -> str:
    r"""Format a Python docstring into structured Markdown.

    Converts NumPy-style section underlines (e.g. Parameters\n----------)
    into clean Markdown subheadings.

    Parameters
    ----------
    doc : `str` or `None`
        Raw docstring from a function, method, or class.

    Returns
    -------
    formatted : `str`
        Clean Markdown representation.
    """
    if not doc:
        return "No description provided."

    # Convert Numpy-style section headers to Markdown headers
    formatted = re.sub(r"(?m)^([A-Za-z ]+)\n[-=]{3,}\s*$", r"##### \1", doc)
    return formatted.strip()


def format_method_markdown(
    method_name: str,
    method: Any,
    class_name: str = "",
    anchor_owner: str = "",
) -> str:
    """Format a callable method or function into a Markdown block.

    Parameters
    ----------
    method_name : `str`
        The name of the method.
    method : `Any`
        The callable method object to inspect.
    class_name : `str`, optional
        Parent class name for prefixing.
    anchor_owner : `str`, optional
        The owner part of the entry's anchor for a module-level function,
        such as ``astrometricslib``. Leave it out to use ``class_name``.

    Returns
    -------
    markdown : `str`
        Markdown block with signature, docstring, parameters, and return.
        The heading ends with the entry's anchor mark.
    """
    sig = format_signature(method)

    doc = format_docstring(inspect.getdoc(method))
    qual_name = f"{class_name}.{method_name}" if class_name else method_name
    anchor = entry_anchor(anchor_owner or class_name or method_name, method_name)

    return f"#### `{qual_name}{sig}`{_anchor_mark(anchor)}\n\n{doc}\n"


def format_class_markdown(
    cls: type,
    display_title: str | None = None,
    console_alias: str | None = None,
) -> str:
    """Format a Python class and its public callable methods into Markdown.

    Parameters
    ----------
    cls : `type`
        The class to inspect.
    display_title : `str`, optional
        Custom title for the class block.
    console_alias : `str`, optional
        Interactive console alias (e.g. 'targets', 'stars', 'telescope').

    Returns
    -------
    markdown : `str`
        Detailed Markdown document detailing class overview and methods.
    """
    class_name = cls.__name__
    title = display_title or class_name
    doc = format_docstring(inspect.getdoc(cls))

    lines = [f"### `{title}`{_anchor_mark(entry_anchor(class_name))}\n"]
    if console_alias:
        lines.append(f"> **Console Alias:** Available globally as `{console_alias}` in the console.\n")

    lines.append(f"{doc}\n")

    # Inspect public callable methods
    methods: list[tuple[str, Any]] = []
    for m_name, m_func in inspect.getmembers(cls, predicate=callable):
        if m_name.startswith("_") and m_name != "__init__":
            continue
        methods.append((m_name, m_func))

    # Sort __init__ first, then alphabetical
    methods.sort(key=lambda m: (0 if m[0] == "__init__" else 1, m[0]))

    if methods:
        lines.append("#### Method Summary\n")
        for m_name, m_func in methods:
            sig = format_signature(m_func)
            raw_doc = inspect.getdoc(m_func) or ""
            summary = raw_doc.split("\n")[0] if raw_doc else "No description provided."
            lines.append(f"- **`{m_name}{sig}`**: {summary}")
        lines.append("\n---\n")

        lines.append("#### Method Details\n")
        for m_name, m_func in methods:
            lines.append(format_method_markdown(m_name, m_func, class_name=class_name))
            lines.append("\n---\n")

    return "\n".join(lines)


def format_public_module_markdown(module_name: str) -> str:
    """List the public names of one library module, with a summary of each.

    Parameters
    ----------
    module_name : `str`
        A public module, such as ``astrometricslib.exceptions``.

    Returns
    -------
    markdown : `str`
        A heading, the module's purpose, and one line for each name in its
        ``__all__`` (a function shows its signature).
    """
    import importlib

    module = importlib.import_module(module_name)
    first_paragraph = (inspect.getdoc(module) or "").split("\n\n")[0].replace("\n", " ")
    first_paragraph = first_paragraph.removeprefix("Purpose: ")
    lines = [f"### `{module_name}`\n", f"{first_paragraph}\n"]
    owner = module_name.rsplit(".", 1)[-1]
    for name in sorted(getattr(module, "__all__", ())):
        value = getattr(module, name, None)
        if value is None:
            continue
        raw_doc = inspect.getdoc(value) if callable(value) else None
        summary = raw_doc.split("\n")[0] if raw_doc else "No description provided."
        signature = ""
        if callable(value) and not isinstance(value, type):
            # A private class in the return type is not public: show the
            # parameters only, so no private path is printed.
            signature = format_signature(value, hide_private_return=True)
        lines.append(f"- **`{name}{signature}`**: {summary}{_anchor_mark(entry_anchor(owner, name))}")
    lines.append("")
    return "\n".join(lines)


def format_public_modules_markdown(library: str) -> str:
    """List the public modules of a library other than its root.

    Parameters
    ----------
    library : `str`
        ``astrometricslib`` or ``wayfindinglib``.

    Returns
    -------
    markdown : `str`
        One block for each public module after the root.
    """
    return "\n".join(format_public_module_markdown(name) for name in PUBLIC_LIBRARY_MODULES[library][1:])


def generate_api_index() -> str:
    """Generate the root Python API Reference index documentation.

    Returns
    -------
    content : `str`
        Markdown content for the API Reference homepage.
    """
    lines = [
        "# Python API Reference",
        "",
        "Welcome to the **Astrometrics Python API Reference**.",
        "",
        "Astrometrics provides two high-performance Python libraries for scientific image processing, "
        "observatory automation, and astronomical data analysis. Both libraries are accessible directly "
        "within the **Command Console** and can also be imported into custom external Python scripts.",
        "",
        "---",
        "",
        "## Core Libraries",
        "",
        "| Library | Primary Facade | Purpose | Documentation |",
        "| :--- | :--- | :--- | :--- |",
        (
            "| **`astrometricslib`** | `Astrometrics` | FITS processing, calibration, image stacking, "
            "plate-solving, photometry, spectroscopy, and star catalogs. | "
            "[Browse `astrometricslib` API](api/astrometricslib.rst) |"
        ),
        (
            "| **`wayfindinglib`** | `Wayfinder` | Observatory hardware control "
            "(mount, focus, filter, camera), visibility planning, mosaic authoring, and session execution. | "
            "[Browse `wayfindinglib` API](api/wayfindinglib.rst) |"
        ),
        "",
        "---",
        "",
        "## Interactive Console Scope",
        "",
        (
            "When running scripts or commands in the Astrometrics Command Console, the following "
            "instances and utilities are **preloaded and immediately available** in your namespace:"
        ),
        "",
        "| Variable | Type / Module | Description |",
        "| :--- | :--- | :--- |",
        "| `astrometrics` | `Astrometrics` | The image library: targets, stars, processing, quality, "
        "visualization and jobs. |",
        "| `wayfinder` | `Wayfinder` | The observatory library: control, planning, execution and review. |",
        "| `np` | `numpy` | NumPy arrays and math. |",
        "| `plt` | `matplotlib.pyplot` | Matplotlib (figures open in their own windows). |",
        "| `fits` | `astropy.io.fits` | Reading FITS files. |",
        "",
        (
            "These are the objects a script or notebook makes itself (`Astrometrics()` and `Wayfinder()`), "
            "so a script written in the console also runs in a notebook. The app's own services are not "
            "in the console. `help(obj)` prints an object's description and public methods."
        ),
        "",
        "---",
        "",
        "## Quick Example: Look at a Stack",
        "",
        "```python",
        "# Read a target and draw its stack with the detected stars and the named stars",
        'target = astrometrics.targets.get("M 13")',
        "figure = astrometrics.visualization.plot_image(",
        '    target.stacking.stacked_image, overlays=["detections", "stars"]',
        ")",
        "",
        "# Check visibility from your observatory location",
        "report = wayfinder.planning.sky.get_visibility([target])",
        'print(f"Altitude now: {report.objects[0].altitude_deg:.1f} degrees")',
        "```",
        "",
        "---",
        "",
        "## Detailed Library References",
        "",
        (
            "- **[Astrometrics Library (`astrometricslib`)](api/astrometricslib.rst)**: Detailed reference "
            "covering `Astrometrics`, `TargetCatalog`, `StellarCatalog`, `ProcessingPipelines`, "
            "`Visualization`, and utility routines."
        ),
        (
            "- **[Wayfinding Library (`wayfindinglib`)](api/wayfindinglib.rst)**: Detailed reference "
            "covering `Wayfinder`, `ObservatoryControl`, `ObservationPlanning`, "
            "`ObservationExecution`, and drivers."
        ),
        (
            "- **[Interactive Notebook Tutorials](notebooks/index.md)**: Practical end-to-end Jupyter "
            "notebooks demonstrating data reduction, plate solving, and telescope automation."
        ),
    ]
    return "\n".join(lines)


def generate_astrometricslib_docs() -> str:
    """Generate comprehensive API reference Markdown for astrometricslib.

    Returns
    -------
    content : `str`
        Full Markdown reference for astrometricslib.
    """
    import astrometricslib

    sections: list[str] = [
        "# Astrometrics Library (`astrometricslib`)",
        "",
        "The **`astrometricslib`** library is the scientific computation engine of Astrometrics. "
        "It provides modular, high-throughput pipelines for astronomical image calibration, "
        "stacking, plate solving (astrometry), aperture photometry, spectral extraction, "
        "and stellar catalog management.",
        "",
        "---",
        "",
        "## Table of Contents",
        "",
        "- [Primary Facade: `Astrometrics`](#class-astrometrics)",
        "- [Target Catalog: `TargetCatalog`](#class-targetcatalog)",
        "- [Target Frames: `TargetFrames`](#class-targetframes)",
        "- [Stellar Catalog: `StellarCatalog`](#class-stellarcatalog)",
        "- [Processing Pipelines: `ProcessingPipelines`](#class-processingpipelines)",
        "- [Visualization: `Visualization`](#class-visualization)",
        "- [Calibration Catalog: `CalibrationCatalog`](#class-calibrationcatalog)",
        "- [Quality Diagnostics: `QualityRecords`](#class-qualityrecords)",
        "- [Utility Functions](#utility-functions)",
        "- [Core Data Models](#core-data-models)",
        "- [Public Modules](#public-modules)",
        "",
        "---",
        "",
        "## Core Classes & Subsystems",
        "",
    ]

    # 1. Astrometrics Facade
    sections.append('<a id="class-astrometrics"></a>')
    sections.append(
        format_class_markdown(
            astrometricslib.Astrometrics,
            display_title="Astrometrics",
            console_alias="astrometrics",
        )
    )
    sections.append("\n---\n")

    # 2. TargetCatalog
    sections.append('<a id="class-targetcatalog"></a>')
    target_catalog_cls = getattr(astrometricslib, "TargetCatalog", None)
    if target_catalog_cls:
        sections.append(
            format_class_markdown(
                target_catalog_cls,
                display_title="TargetCatalog",
                console_alias="targets",
            )
        )
        sections.append("\n---\n")

    # 2b. TargetFrames, reached as astrometrics.targets.frames
    sections.append('<a id="class-targetframes"></a>')
    target_frames_cls = getattr(astrometricslib, "TargetFrames", None)
    if target_frames_cls:
        sections.append(
            format_class_markdown(
                target_frames_cls,
                display_title="TargetFrames",
                console_alias="targets.frames",
            )
        )
        sections.append("\n---\n")

    # 3. StellarCatalog
    sections.append('<a id="class-stellarcatalog"></a>')
    stellar_catalog_cls = getattr(astrometricslib, "StellarCatalog", None)
    if stellar_catalog_cls:
        sections.append(
            format_class_markdown(
                stellar_catalog_cls,
                display_title="StellarCatalog",
                console_alias="stars",
            )
        )
        sections.append("\n---\n")

    # 4. ProcessingPipelines
    sections.append('<a id="class-processingpipelines"></a>')
    proc_cls = getattr(astrometricslib, "ProcessingPipelines", None)
    if proc_cls:
        sections.append(
            format_class_markdown(
                proc_cls,
                display_title="ProcessingPipelines",
                console_alias="astrometrics.processing",
            )
        )
        sections.append("\n---\n")

    # 5. Visualization
    sections.append('<a id="class-visualization"></a>')
    vis_cls = getattr(astrometricslib, "Visualization", None)
    if vis_cls:
        sections.append(
            format_class_markdown(
                vis_cls,
                display_title="Visualization",
                console_alias="astrometrics.visualization",
            )
        )
        sections.append("\n---\n")

    # 6. CalibrationCatalog & QualityRecords
    sections.append('<a id="class-calibrationcatalog"></a>')
    calib_cls = getattr(astrometricslib, "CalibrationCatalog", None)
    if calib_cls:
        sections.append(
            format_class_markdown(
                calib_cls,
                display_title="CalibrationCatalog",
            )
        )
        sections.append("\n---\n")

    sections.append('<a id="class-qualityrecords"></a>')
    qual_cls = getattr(astrometricslib, "QualityRecords", None)
    if qual_cls:
        sections.append(
            format_class_markdown(
                qual_cls,
                display_title="QualityRecords",
            )
        )
        sections.append("\n---\n")
        # The five objects of `astrometrics.quality`, found through the
        # class's annotations so no private module is imported here.
        import typing

        for child_name, child_cls in typing.get_type_hints(qual_cls).items():
            sections.append(f'<a id="class-{child_cls.__name__.lower()}"></a>')
            sections.append(
                format_class_markdown(
                    child_cls,
                    display_title=child_cls.__name__,
                    console_alias=f"astrometrics.quality.{child_name}",
                )
            )
            sections.append("\n---\n")

    # 7. Utility Functions
    sections.append('<a id="utility-functions"></a>')
    sections.append("## Utility Functions\n")
    utility_names = [
        "capture_job_logs",
        "get_configuration",
    ]
    for u_name in utility_names:
        func = _public_attribute("astrometricslib", u_name)
        if func and callable(func):
            sections.append(format_method_markdown(u_name, func, anchor_owner="astrometricslib"))
            sections.append("\n---\n")

    # 8. Core Data Models
    sections.append('<a id="core-data-models"></a>')
    sections.append("## Core Data Models\n")
    sections.append(
        "The following Pydantic and dataclass models define the structured entities "
        "persisted in the catalog and exchanged across image processing pipelines:\n"
    )
    model_names = [
        "Target",
        "FrameRecord",
        "StellarObject",
        "ProcessingJob",
    ]
    for m_name in model_names:
        m_cls = _public_attribute("astrometricslib", m_name)
        if m_cls and isinstance(m_cls, type):
            doc = format_docstring(inspect.getdoc(m_cls))
            sections.append(f"### `{m_name}`{_anchor_mark(entry_anchor(m_name))}\n\n{doc}\n")

    # 9. Public modules
    sections.append('<a id="public-modules"></a>')
    sections.append("## Public Modules\n")
    sections.append(
        "Besides the package root, a script may import from these modules: "
        "the errors, the report states and helpers, the job tools and the storage "
        "tools. Nothing else is public.\n"
    )
    sections.append(format_public_modules_markdown("astrometricslib"))

    return "\n".join(sections)


def generate_wayfindinglib_docs() -> str:
    """Generate comprehensive API reference Markdown for wayfindinglib.

    Returns
    -------
    content : `str`
        Full Markdown reference for wayfindinglib.
    """
    import wayfindinglib

    sections: list[str] = [
        "# Wayfinding Library (`wayfindinglib`)",
        "",
        "The **`wayfindinglib`** library provides observatory hardware control, "
        "dynamic observation planning, and autonomous execution. "
        "It coordinates INDI-compatible telescope mounts, focusers, filter wheels, "
        "and guiders, calculates celestial target visibility, authorises mosaic grids, "
        "and executes queued imaging runs with automated fault recovery.",
        "",
        "---",
        "",
        "## Table of Contents",
        "",
        "- [Primary Facade: `Wayfinder`](#class-wayfinder)",
        "- [Observatory Control: `ObservatoryControl`](#class-observatorycontrol)",
        "- [Observation Planning: `ObservationPlanning`](#class-observationplanning)",
        "- [Observation Execution: `ObservationExecution`](#class-observationexecution)",
        "- [Observation Review: `ObservationReview`](#class-observationreview)",
        "- [Public Modules](#public-modules)",
        "",
        "---",
        "",
        "## Core Classes & Subsystems",
        "",
    ]

    # 1. Wayfinder Facade
    sections.append('<a id="class-wayfinder"></a>')
    sections.append(
        format_class_markdown(
            wayfindinglib.Wayfinder,
            display_title="Wayfinder",
            console_alias="wayfinder",
        )
    )
    sections.append("\n---\n")

    # 2. ObservatoryControl
    sections.append('<a id="class-observatorycontrol"></a>')
    control_cls = getattr(wayfindinglib, "ObservatoryControl", None)
    if control_cls:
        sections.append(
            format_class_markdown(
                control_cls,
                display_title="ObservatoryControl",
                console_alias="telescope (or wayfinder.control)",
            )
        )
        sections.append("\n---\n")
        # The operations live on the six topic children, such as
        # `control.mount`; the class annotations name each child's class.
        for child_name, child_class in _class_hints(control_cls).items():
            sections.append(
                format_class_markdown(
                    child_class,
                    display_title=f"control.{child_name}",
                    console_alias=f"telescope.{child_name}",
                )
            )
            sections.append("\n---\n")

    # 3. ObservationPlanning
    sections.append('<a id="class-observationplanning"></a>')
    plan_cls = getattr(wayfindinglib, "ObservationPlanning", None)
    if plan_cls:
        sections.append(
            format_class_markdown(
                plan_cls,
                display_title="ObservationPlanning",
                console_alias="planner (or wayfinder.planning)",
            )
        )
        sections.append("\n---\n")
        # The methods live on the four objects, such as `planning.sky`; the
        # class annotations name each object's class.
        for child_name, child_class in _class_hints(plan_cls).items():
            sections.append(
                format_class_markdown(
                    child_class,
                    display_title=f"planning.{child_name}",
                    console_alias=f"planner.{child_name}",
                )
            )
            sections.append("\n---\n")

    # 4. ObservationExecution
    sections.append('<a id="class-observationexecution"></a>')
    exec_cls = getattr(wayfindinglib, "ObservationExecution", None)
    if exec_cls:
        sections.append(
            format_class_markdown(
                exec_cls,
                display_title="ObservationExecution",
                console_alias="executor (or wayfinder.execution)",
            )
        )
        sections.append("\n---\n")

    # 5. ObservationReview
    sections.append('<a id="class-observationreview"></a>')
    review_cls = getattr(wayfindinglib, "ObservationReview", None)
    if review_cls:
        sections.append(
            format_class_markdown(
                review_cls,
                display_title="ObservationReview",
                console_alias="wayfinder.review",
            )
        )
        sections.append("\n---\n")
        # The reads live on the five objects of `review`, such as
        # `review.nights`; the class annotations name each object's class.
        for child_name, child_class in _class_hints(review_cls).items():
            sections.append(
                format_class_markdown(
                    child_class,
                    display_title=f"review.{child_name}",
                    console_alias=f"wayfinder.review.{child_name}",
                )
            )
            sections.append("\n---\n")

    # 6. Public modules
    sections.append('<a id="public-modules"></a>')
    sections.append("## Public Modules\n")
    sections.append(format_public_modules_markdown("wayfindinglib"))

    return "\n".join(sections)


def generate_entity_doc(entity_name: str) -> str:
    """Generate Markdown reference for a single class or function stub.

    Parameters
    ----------
    entity_name : `str`
        Qualified name or class name, e.g. 'TargetCatalog' or
        'astrometricslib.Astrometrics'.

    Returns
    -------
    content : `str`
        Detailed Markdown document for the entity.
    """
    # Strip prefixes like 'api/generated/' or '.rst'
    clean_name = entity_name.split("/")[-1].replace(".rst", "").replace(".md", "")
    short_name = clean_name.split(".")[-1]

    # Search in astrometricslib then wayfindinglib
    obj = (
        _public_attribute("astrometricslib", short_name)
        or _public_attribute("wayfindinglib", short_name)
        or _public_attribute("astrometricslib", clean_name)
        or _public_attribute("wayfindinglib", clean_name)
    )

    if obj is None:
        return f"# {clean_name}\n\nEntity `{clean_name}` was not found in libraries.\n"

    if isinstance(obj, type):
        doc = format_class_markdown(obj, display_title=clean_name)
        return f"# {clean_name}\n\n{doc}"
    elif callable(obj):
        doc = format_method_markdown(
            short_name, obj, anchor_owner=getattr(obj, "__module__", "").rsplit(".", 1)[-1]
        )
        return f"# {clean_name}\n\n{doc}"
    else:
        return f"# {clean_name}\n\n```python\n{obj!r}\n```\n"


def generate_user_interface_index() -> str:
    """Generate overview documentation for the desktop interface.

    Returns
    -------
    content : `str`
        Markdown content for the desktop user interface index.
    """
    lines = [
        "# Desktop app",
        "",
        (
            "The Astrometrics desktop app captures and analyzes images of the night sky. "
            "It gives you a visual way to control observatory equipment and to process "
            "astronomy images."
        ),
        "",
        "## The desktop app's pages",
        "",
        "- **[Getting started](../Getting_Started.md)**: a first session with the app.",
        (
            "- **[Image processing tutorial](user_guides/Image_Processing_and_Analysis_Tutorial.md)**: "
            "one guided path from raw frames to a measured result."
        ),
        (
            "- **[Observatory control guide](user_guides/Observatory_Control_and_Automation_Guide.md)**: "
            "recipes for connecting and running the telescope."
        ),
        "- **[User manual](user_guides/User_Manual.md)**: every view and control of the app.",
        (
            "- **[Desktop app architecture](technical_reference/User_Interface_Architecture.md)**: "
            "how the app is built."
        ),
    ]
    return "\n".join(lines)


def get_api_topics() -> list[dict[str, str]]:
    """Return catalog descriptors for virtual API documentation topics.

    Returns
    -------
    topics : `list` of `dict`
        Topic descriptors compatible with ScriptingService.list_doc_topics().
    """
    return [
        {
            "id": "api/index",
            "title": "Python API Reference",
            "path": "api/index.rst",
            "category": "API Reference",
        },
        {
            "id": "api/astrometricslib",
            "title": "Astrometrics Library (astrometricslib)",
            "path": "api/astrometricslib.rst",
            "category": "API Reference",
        },
        {
            "id": "api/wayfindinglib",
            "title": "Wayfinding Library (wayfindinglib)",
            "path": "api/wayfindinglib.rst",
            "category": "API Reference",
        },
        {
            "id": "user_interface/index",
            "title": "Desktop app",
            "path": "user_interface/index.rst",
            "category": "Desktop Application",
        },
    ]


def get_api_topic_content(topic_id: str) -> dict[str, Any] | None:
    """Resolve and return documentation content for an API or virtual topic.

    Parameters
    ----------
    topic_id : `str`
        Topic identifier or relative path (e.g. 'api/index.rst',
        'api/astrometricslib', 'generated/astrometricslib.Astrometrics.rst').

    Returns
    -------
    topic_data : `dict[str, Any]` or `None`
        Dictionary containing 'id', 'title', and 'content', or None.
    """
    normalized_id = topic_id.strip("/").replace(".rst", "").replace(".md", "").replace(".html", "")

    # Check cache
    if normalized_id in _DOCS_CACHE:
        return _DOCS_CACHE[normalized_id]

    content: str | None = None
    title: str = "Documentation"

    if normalized_id in ("api/index", "api"):
        title = "Python API Reference"
        content = generate_api_index()
    elif normalized_id in ("api/astrometricslib", "astrometricslib"):
        title = "Astrometrics Library (astrometricslib)"
        content = generate_astrometricslib_docs()
    elif normalized_id in ("api/wayfindinglib", "wayfindinglib"):
        title = "Wayfinding Library (wayfindinglib)"
        content = generate_wayfindinglib_docs()
    elif normalized_id in ("user_interface/index", "user_interface"):
        title = "Desktop app"
        content = generate_user_interface_index()
    elif "generated/" in normalized_id or normalized_id.startswith("api/generated/"):
        entity_name = normalized_id.split("generated/")[-1]
        title = entity_name.split(".")[-1]
        content = generate_entity_doc(entity_name)

    if content is not None:
        result = {
            "id": topic_id,
            "title": title,
            "content": content,
        }
        _DOCS_CACHE[normalized_id] = result
        return result

    return None
