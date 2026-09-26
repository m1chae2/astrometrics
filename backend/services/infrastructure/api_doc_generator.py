"""API Documentation Generator for Astrometrics and Wayfinding libraries.

This module provides dynamic generation of structured Markdown documentation
from live Python docstrings, method signatures, and class hierarchies in
astrometricslib and wayfindinglib. It enables in-console reading of the
complete Python API reference without requiring external builds.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

# In-memory cache for generated markdown content to avoid redundant reflection
_DOCS_CACHE: dict[str, dict[str, str]] = {}


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

    Returns
    -------
    markdown : `str`
        Markdown block with signature, docstring, parameters, and return.
    """
    try:
        sig = str(inspect.signature(method))
    except Exception:
        sig = "(...)"

    doc = format_docstring(inspect.getdoc(method))
    qual_name = f"{class_name}.{method_name}" if class_name else method_name

    return f"#### `{qual_name}{sig}`\n\n{doc}\n"


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

    lines = [f"### `{title}`\n"]
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
            try:
                sig = str(inspect.signature(m_func))
            except Exception:
                sig = "(...)"
            raw_doc = inspect.getdoc(m_func) or ""
            summary = raw_doc.split("\n")[0] if raw_doc else "No description provided."
            lines.append(f"- **`{m_name}{sig}`**: {summary}")
        lines.append("\n---\n")

        lines.append("#### Method Details\n")
        for m_name, m_func in methods:
            lines.append(format_method_markdown(m_name, m_func, class_name=class_name))
            lines.append("\n---\n")

    return "\n".join(lines)


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
        "| `astrometrics` | `Astrometrics` | Master facade for imaging, pipelines, and catalogs. |",
        "| `wayfinder` | `Wayfinder` | Master facade for hardware, planning, and execution. |",
        "| `targets` | `TargetCatalog` | Direct shortcut to `astrometrics.targets`. |",
        "| `stars` | `StellarCatalog` | Direct shortcut to `astrometrics.stars`. |",
        "| `telescope` | `ObservatoryControl` | Direct shortcut to `wayfinder.control`. |",
        "| `planner` | `ObservationPlanning` | Direct shortcut to `wayfinder.planning`. |",
        "| `executor` | `ObservationExecution` | Direct shortcut to `wayfinder.execution`. |",
        "| `plt` | `matplotlib.pyplot` | Matplotlib interface (plots open in figure windows). |",
        "| `np` / `pd` / `astropy` | Scientific Stack | Standard data manipulation libraries. |",
        "",
        "---",
        "",
        "## Quick Example: Target Inspection & Analysis",
        "",
        "```python",
        "# Inspect targets in your observatory database",
        "all_targets = targets.list()",
        'print(f"Total targets: {len(all_targets)}")',
        "",
        "# Retrieve a specific target",
        'target = targets.get("M 42")',
        'print(f"Target {target.id}: RA {target.ra}, DEC {target.dec}")',
        "",
        "# Check visibility from your observatory location",
        'vis = planner.get_visibility(target_name="M 42")',
        "print(f\"Current Altitude: {vis.get('altitude', 0):.1f}°\")",
        "",
        "# Plot stacked light frame or spectroscopy if available",
        "if target.stacked_image:",
        "    astrometrics.visualization.plot_astrometry(target.stacked_image)",
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
        "- [Stellar Catalog: `StellarCatalog`](#class-stellarcatalog)",
        "- [Processing Pipelines: `ProcessingPipelines`](#class-processingpipelines)",
        "- [Visualization: `Visualization`](#class-visualization)",
        "- [Moving Object Recovery: `MovingObjectRecovery`](#class-movingobjectrecovery)",
        "- [Calibration Catalog: `CalibrationCatalog`](#class-calibrationcatalog)",
        "- [Quality Diagnostics: `QualityDiagnostics`](#class-qualitydiagnostics)",
        "- [Utility Functions](#utility-functions)",
        "- [Core Data Models](#core-data-models)",
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

    # 6. MovingObjectRecovery
    sections.append('<a id="class-movingobjectrecovery"></a>')
    moving_cls = getattr(astrometricslib, "MovingObjectRecovery", None)
    if moving_cls:
        sections.append(
            format_class_markdown(
                moving_cls,
                display_title="MovingObjectRecovery",
                console_alias="astrometrics.moving_objects",
            )
        )
        sections.append("\n---\n")

    # 7. CalibrationCatalog & QualityDiagnostics
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

    sections.append('<a id="class-qualitydiagnostics"></a>')
    qual_cls = getattr(astrometricslib, "QualityDiagnostics", None)
    if qual_cls:
        sections.append(
            format_class_markdown(
                qual_cls,
                display_title="QualityDiagnostics",
            )
        )
        sections.append("\n---\n")

    # 8. Utility Functions
    sections.append('<a id="utility-functions"></a>')
    sections.append("## Utility Functions\n")
    utility_names = [
        "capture_job_logs",
        "classify_and_sort_fits_files",
        "derive_target_sessions",
        "get_configuration",
        "parse_coordinate_string",
        "resolve_worker_counts",
        "run_parallel_batch",
        "run_siril_stack",
    ]
    for u_name in utility_names:
        func = getattr(astrometricslib, u_name, None)
        if func and callable(func):
            sections.append(format_method_markdown(u_name, func))
            sections.append("\n---\n")

    # 9. Core Data Models
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
        "FitsHeaderEntry",
        "PhotometryResult",
        "SpectroscopyResult",
        "AsteroidDetectionCandidate",
        "AnalysisResult",
        "BatchRunSummary",
        "ProcessingJob",
    ]
    for m_name in model_names:
        m_cls = getattr(astrometricslib, m_name, None)
        if m_cls and isinstance(m_cls, type):
            doc = format_docstring(inspect.getdoc(m_cls))
            sections.append(f"### `{m_name}`\n\n{doc}\n")

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
        "- [Hardware Drivers & Exceptions](#hardware-drivers)",
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

    # 5. Hardware Drivers & Exceptions
    sections.append('<a id="hardware-drivers"></a>')
    sections.append("## Hardware Drivers & Exceptions\n")
    for name in ["IndiInterface", "SimulatorIndiInterface", "AstrometryHardwareError"]:
        cls_obj = getattr(wayfindinglib, name, None)
        if cls_obj and isinstance(cls_obj, type):
            doc = format_docstring(inspect.getdoc(cls_obj))
            sections.append(f"### `{name}`\n\n{doc}\n")

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
    import astrometricslib
    import wayfindinglib

    # Strip prefixes like 'api/generated/' or '.rst'
    clean_name = entity_name.split("/")[-1].replace(".rst", "").replace(".md", "")
    short_name = clean_name.split(".")[-1]

    # Search in astrometricslib then wayfindinglib
    obj = (
        getattr(astrometricslib, short_name, None)
        or getattr(wayfindinglib, short_name, None)
        or getattr(astrometricslib, clean_name, None)
        or getattr(wayfindinglib, clean_name, None)
    )

    if obj is None:
        return f"# {clean_name}\n\nEntity `{clean_name}` was not found in libraries.\n"

    if isinstance(obj, type):
        doc = format_class_markdown(obj, display_title=clean_name)
        return f"# {clean_name}\n\n{doc}"
    elif callable(obj):
        doc = format_method_markdown(short_name, obj)
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
        "# Astrometrics Desktop Application",
        "",
        (
            "The **Astrometrics Desktop Application** is a specialized workstation for "
            "astrophotography, photometry, spectroscopy, and automated observatory control. "
            "It provides a visual interface to manage targets, capture and process celestial images, "
            "and inspect astronomical data."
        ),
        "",
        "---",
        "",
        "## User Guides",
        "",
        (
            "- **[Desktop User Manual](user_interface/user_guides/User_Manual.md)**: "
            "Comprehensive guide covering the navigation modes, target database, and workstation layout."
        ),
        (
            "- **[Hardware Control & Automation Guide]"
            "(user_interface/user_guides/Observatory_Control_and_Automation_Guide.md)**: "
            "Instructions for connecting to INDI devices, configuring mounts, and managing equipment."
        ),
        (
            "- **[Image Processing & Science Analysis Tutorial]"
            "(user_guides/Image_Processing_and_Analysis_Tutorial.md)**: "
            "Walkthrough for image calibration, FITS frame stacking, photometry, and spectroscopy."
        ),
        "",
        "---",
        "",
        "## Technical Architecture",
        "",
        (
            "- **[User Interface Architecture](technical_reference/User_Interface_Architecture.md)**: "
            "Deep dive into the Electron multi-window architecture, React state management, and WebSockets."
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
            "title": "Astrometrics Desktop Application",
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
        title = "Astrometrics Desktop Application"
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
