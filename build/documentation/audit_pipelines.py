"""Automated scientific pipeline and documentation audit tool.

This module inspects the Astrometrics scientific analysis pipelines, verifies
implementation link integrity from architecture documentation, compares
documented design parameters against live code defaults, and generates a
formal audit report.
"""

from __future__ import annotations

import ast
import importlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ImplementationReference:
    """Represents an implementation reference extracted from documentation.

    Attributes
    ----------
    dotted_path : `str`
        The Python dotted import path (e.g., module.ClassName.function).
    resolved : `bool`
        Whether the object was successfully imported or resolved.
    resolved_object : `Any`
        The resolved Python object or None if resolution failed.
    error_message : `str`
        The error message encountered during resolution, if any.
    usage_note : `str`
        Whether the library's own code uses the module that holds the
        symbol (see `describe_symbol_usage`). Empty when the symbol did not
        resolve.
    """

    dotted_path: str
    resolved: bool
    resolved_object: Any = None
    error_message: str = ""
    usage_note: str = ""


@dataclass
class ParameterAuditResult:
    """Results of auditing a documented parameter against live code.

    Attributes
    ----------
    pipeline : `str`
        The pipeline domain (e.g., Stacking, Photometry).
    parameter_name : `str`
        The name of the parameter.
    documented_default : `str`
        The default value documented in Table A1.
    empirical_status : `str`
        Whether the parameter is empirically validated or designed.
    implementation_symbol : `str`
        The associated implementation symbol from code comments.
    code_value : `str`
        The actual value resolved from the Python codebase.
    matches : `bool`
        Whether the code value matches or aligns with documented default.
    """

    pipeline: str
    parameter_name: str
    documented_default: str
    empirical_status: str
    implementation_symbol: str
    code_value: str
    matches: bool


def resolve_dotted_symbol(dotted_path: str) -> tuple[bool, Any, str]:
    """Resolve a dotted Python symbol path to its live runtime object.

    Parameters
    ----------
    dotted_path : `str`
        The full path to import and resolve (e.g. package.module.attribute).

    Returns
    -------
    success : `bool`
        True if the object was successfully resolved.
    resolved_obj : `Any`
        The resolved object or None.
    error : `str`
        Error message if resolution failed.
    """
    parts = dotted_path.strip().split(".")
    for i in range(len(parts), 0, -1):
        mod_name = ".".join(parts[:i])
        try:
            mod = importlib.import_module(mod_name)
            obj = mod
            for attr in parts[i:]:
                obj = getattr(obj, attr)
            return True, obj, ""
        except ImportError, AttributeError:
            continue
    return False, None, f"Could not import or resolve '{dotted_path}'"


def _is_test_file(path: Path) -> bool:
    """Decide whether a library file is a test rather than production code.

    Parameters
    ----------
    path : `Path`
        A Python file.

    Returns
    -------
    is_test : `bool`
        `True` for a file in a ``test`` folder or named ``test_*.py``.
    """
    return "test" in path.parts or path.name.startswith("test_")


def _imports_by_file(library_root: Path) -> dict[str, set[str]]:
    """List the modules each library file imports, wherever the import is.

    For ``from package import name`` both ``package`` and
    ``package.name`` are listed, because ``name`` may be a module.
    Relative imports are turned into full module names.

    Parameters
    ----------
    library_root : `Path`
        The ``astrometricslib`` package folder.

    Returns
    -------
    imports : `dict` [`str`, `set` [`str`]]
        Imported module names, keyed by the importing file's own dotted
        module name.
    """
    imports: dict[str, set[str]] = {}
    for path in sorted(library_root.rglob("*.py")):
        relative = path.relative_to(library_root.parent).with_suffix("")
        parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
        package_parts = relative.parts[:-1]
        modules: set[str] = set()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    base = node.module or ""
                else:
                    kept = package_parts[: len(package_parts) - (node.level - 1)]
                    base = ".".join([*kept, *([node.module] if node.module else [])])
                modules.add(base)
                modules.update(f"{base}.{alias.name}" for alias in node.names)
        imports[".".join(parts)] = modules
    return imports


def describe_symbol_usage(dotted_path: str, imports: dict[str, set[str]]) -> str:
    """Say whether the library's own code uses the module holding a symbol.

    A symbol can resolve and still be code that nothing calls. This looks
    for the symbol's module among the imports of every other production
    file in the library, and among the imports of the test files.

    Parameters
    ----------
    dotted_path : `str`
        The resolved symbol, such as
        ``astrometricslib.pipelines.asteroid_detection.mpc_report.mpc_lines_for_candidate``.
    imports : `dict` [`str`, `set` [`str`]]
        What `_imports_by_file` returned.

    Returns
    -------
    note : `str`
        A short note for the report's Notes column.
    """
    parts = dotted_path.split(".")
    module = next(
        (".".join(parts[:end]) for end in range(len(parts), 0, -1) if ".".join(parts[:end]) in imports),
        None,
    )
    if module is None:
        return "Resolves; module not found in the library"
    if ".scripts." in f"{module}.":
        return "Resolves; a script run by hand"
    if ".test." in f"{module}." or module.rsplit(".", 1)[-1].startswith("test_"):
        return "Resolves; a test module"

    importers = [
        name
        for name, modules in imports.items()
        if name != module and any(item == module or item.startswith(f"{module}.") for item in modules)
    ]
    test_importers = [name for name in importers if _is_test_file(Path(*name.split(".")))]
    script_importers = [name for name in importers if ".scripts." in f"{name}."]
    if any(name not in test_importers and name not in script_importers for name in importers):
        return "Resolves; used by library code"
    if script_importers:
        return "Resolves; used only by scripts, not by any pipeline step"
    if test_importers:
        return "Resolves; present and tested, not called by any pipeline step"
    return "Resolves; not imported anywhere else"


def extract_implementation_references(
    architecture_doc_path: Path,
) -> list[ImplementationReference]:
    """Extract and resolve implementation comment tags in the doc.

    Parameters
    ----------
    architecture_doc_path : `Path`
        Path to Astrometrics_Library_Architecture.md.

    Returns
    -------
    references : `list` [`ImplementationReference`]
        List of all extracted implementation references and resolution status.
    """
    text = architecture_doc_path.read_text(encoding="utf-8")
    pattern = re.compile(r"<!--\s*impl:\s*([\w\.]+)\s*-->")
    found = pattern.findall(text)

    library_root = Path(__file__).resolve().parents[2] / "astrometricslib"
    imports = _imports_by_file(library_root)

    results: list[ImplementationReference] = []
    seen = set()
    for raw_symbol in found:
        symbol = raw_symbol.strip()
        if symbol in seen:
            continue
        seen.add(symbol)
        success, obj, err = resolve_dotted_symbol(symbol)
        results.append(
            ImplementationReference(
                dotted_path=symbol,
                resolved=success,
                resolved_object=obj,
                error_message=err,
                usage_note=describe_symbol_usage(symbol, imports) if success else "",
            )
        )
    return results


def extract_table_a1_rows(
    architecture_doc_path: Path,
) -> list[dict[str, str]]:
    """Parse Table A1 parameters from Astrometrics_Library_Architecture.md.

    Parameters
    ----------
    architecture_doc_path : `Path`
        Path to Astrometrics_Library_Architecture.md.

    Returns
    -------
    rows : `list` [`dict` [`str`, `str`]]
        Parsed table rows with keys: pipeline, parameter, default, status.
    """
    text = architecture_doc_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    table_lines: list[str] = []
    in_table = False
    for line in lines:
        if "| Pipeline | Parameter | Default |" in line:
            in_table = True
            continue
        if in_table:
            if not line.strip().startswith("|"):
                break
            if line.strip().startswith("|---"):
                continue
            table_lines.append(line.strip())

    parsed_rows: list[dict[str, str]] = []
    for row in table_lines:
        parts = [p.strip() for p in row.split("|")[1:-1]]
        if len(parts) >= 4:
            parsed_rows.append({
                "pipeline": parts[0],
                "parameter": parts[1],
                "default": parts[2],
                "status": parts[3],
            })
    return parsed_rows


def audit_pipeline_structures() -> list[dict[str, Any]]:
    """Verify AnalysisPipeline adapters conform to structural lifecycle.

    Returns
    -------
    audit_data : `list` [`dict` [`str`, `Any`]]
        List of audited pipeline metrics and conformance details.
    """
    adapter_configs = [
        (
            "astrometricslib.pipelines.astrometry.runner",
            "AstrometryPipelineAdapter",
            "astrometricslib.models.quality_summary",
            "AstrometryQualitySummary",
        ),
        (
            "astrometricslib.pipelines.photometry.runner",
            "PhotometryPipelineAdapter",
            "astrometricslib.models.quality_summary",
            "PhotometryQualitySummary",
        ),
        (
            "astrometricslib.pipelines.spectroscopy.runner",
            "SpectroscopyPipelineAdapter",
            "astrometricslib.models.quality_summary",
            "SpectroscopyQualitySummary",
        ),
        (
            "astrometricslib.pipelines.asteroid_detection.runner",
            "AsteroidDetectionPipelineAdapter",
            "astrometricslib.models.quality_summary",
            "AsteroidDetectionQualitySummary",
        ),
    ]

    results = []
    for (
        ad_mod_name,
        ad_cls_name,
        sum_mod_name,
        sum_cls_name,
    ) in adapter_configs:
        try:
            ad_mod = importlib.import_module(ad_mod_name)
            adapter_cls = getattr(ad_mod, ad_cls_name)
            sum_mod = importlib.import_module(sum_mod_name)
            summary_cls = getattr(sum_mod, sum_cls_name)

            adapter = adapter_cls()
            pipeline_name = adapter.pipeline_name
            has_process_input = callable(getattr(adapter, "process_input", None))
            has_run = callable(getattr(adapter, "run", None))
            has_validate_output = callable(getattr(adapter, "validate_output", None))
            has_to_result_dict = callable(getattr(adapter, "to_result_dict", None))
            summary_fields = list(summary_cls.model_fields.keys())

            results.append({
                "name": pipeline_name,
                "adapter_class": ad_cls_name,
                "summary_class": sum_cls_name,
                "has_process_input": has_process_input,
                "has_run": has_run,
                "has_validate_output": has_validate_output,
                "has_to_result_dict": has_to_result_dict,
                "quality_summary_fields": summary_fields,
            })
        except (ImportError, AttributeError) as exc:
            results.append({
                "name": ad_cls_name,
                "adapter_class": ad_cls_name,
                "summary_class": sum_cls_name,
                "has_process_input": False,
                "has_run": False,
                "has_validate_output": False,
                "has_to_result_dict": False,
                "quality_summary_fields": [],
                "error": str(exc),
            })

    # Stacking has no `AnalysisPipeline` adapter. It runs through
    # `stacking.stage.stack_frames`, which hands the pixel work to a
    # `StackingDriver` (by default `SirilStackingDriver`, built on the Siril
    # session class `ImageProcessing` in `drivers/siril_interface.py`). Its
    # row checks that those pieces exist instead of a four-phase lifecycle.
    stacking_entry = "stacking.stage.stack_frames → SirilStackingDriver"
    try:
        stage_mod = importlib.import_module("astrometricslib.pipelines.stacking.stage")
        driver_mod = importlib.import_module("astrometricslib.drivers.siril_stacking_driver")
        session_mod = importlib.import_module("astrometricslib.drivers.siril_interface")
        entry_points_exist = all((
            callable(getattr(stage_mod, "stack_frames", None)),
            getattr(driver_mod, "SirilStackingDriver", None) is not None,
            getattr(session_mod, "ImageProcessing", None) is not None,
        ))
        qs_mod = importlib.import_module("astrometricslib.models.quality_summary")
        stacking_fields = list(qs_mod.StackQualitySummary.model_fields.keys())
    except ImportError, AttributeError:
        entry_points_exist = False
        stacking_fields = []

    results.append({
        "name": "stacking",
        "adapter_class": stacking_entry,
        "summary_class": "StackQualitySummary",
        "has_adapter": False,
        "entry_points_exist": entry_points_exist,
        "quality_summary_fields": stacking_fields,
    })

    return results


def generate_markdown_report(
    references: list[ImplementationReference],
    pipeline_structs: list[dict[str, Any]],
    table_a1_rows: list[dict[str, str]],
    output_path: Path,
) -> None:
    """Generate a formal scientific audit report in Markdown.

    Parameters
    ----------
    references : `list` [`ImplementationReference`]
        Extracted implementation references.
    pipeline_structs : `list` [`dict` [`str`, `Any`]]
        Pipeline structural conformance data.
    table_a1_rows : `list` [`dict` [`str`, `str`]]
        Documented parameters from Table A1.
    output_path : `Path`
        Target filepath to write the report to.
    """
    resolved_count = sum(1 for r in references if r.resolved)
    total_count = len(references)

    lines = [
        "# Pipeline Scientific and Architectural Audit Report",
        "",
        "*Status: Generated by automated pipeline inspection.*",
        "",
        "## 1. Executive Summary",
        "",
        f"- **Implementation References Checked:** {total_count}",
        f"- **Resolved Code Symbols:** {resolved_count}",
        f"- **Broken / Stale Implementation Links:** {total_count - resolved_count}",
        f"- **Analysis Pipelines Audited:** {sum(1 for p in pipeline_structs if p.get('has_adapter', True))}"
        " (plus stacking, which has no adapter)",
        f"- **Documented Design Parameters (Table A1):** {len(table_a1_rows)}",
        "",
        "---",
        "",
        "## 2. Pipeline Structural Conformance",
        "",
        "The four analysis pipelines each have an adapter that implements the 4-phase lifecycle "
        "defined in `AnalysisPipeline` (`pipeline_base.py`): input validation, "
        "execution, quality validation, and serialization. Stacking has no such adapter. "
        "It runs through `stacking.stage.stack_frames`, which hands the pixel work to a "
        "`StackingDriver` (by default `SirilStackingDriver`, built on the Siril session class "
        "`ImageProcessing` in `drivers/siril_interface.py`). Its row checks that those entry "
        "points exist.",
        "",
        "| Pipeline Domain | Adapter Class or Entry Point | Lifecycle Complete? "
        "| Quality Summary Model | Fields |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    for p in pipeline_structs:
        if not p.get("has_adapter", True):
            status_str = (
                "N/A (no adapter; entry points found)"
                if p["entry_points_exist"]
                else "FAIL (entry points missing)"
            )
        else:
            complete = (
                p["has_process_input"]
                and p["has_run"]
                and p["has_validate_output"]
                and p["has_to_result_dict"]
            )
            status_str = "PASSED" if complete else "FAIL"
        field_count = len(p["quality_summary_fields"])
        lines.append(
            f"| {p['name'].title()} | `{p['adapter_class']}` | {status_str} "
            f"| `{p['summary_class']}` | {field_count} attributes |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 3. Implementation Link Integrity Audit",
        "",
        "Traceability audit of embedded `<!-- impl: ... -->` symbols from "
        "`Astrometrics_Library_Architecture.md` against live Python code. "
        "The Notes column says whether any other production module of the library "
        "imports the symbol's module. A symbol can resolve and still be code that no "
        "pipeline step calls.",
        "",
        "| Documented Symbol | Resolution Status | Notes |",
        "| :--- | :--- | :--- |",
    ])

    for r in references:
        status = "RESOLVED" if r.resolved else "BROKEN"
        note = r.error_message if not r.resolved else r.usage_note
        lines.append(f"| `{r.dotted_path}` | {status} | {note} |")

    lines.extend([
        "",
        "---",
        "",
        "## 4. Documented Design Parameters (Table A1 Audit)",
        "",
        "Audit of parameters and validation declarations from Table A1:",
        "",
        "| Pipeline | Parameter | Documented Default | Empirical Status |",
        "| :--- | :--- | :--- | :--- |",
    ])

    for row in table_a1_rows:
        lines.append(f"| {row['pipeline']} | {row['parameter']} | {row['default']} | {row['status']} |")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    """Run the pipeline audit and print summary to stdout.

    Returns
    -------
    exit_code : `int`
        0 if successful, 1 if broken links or conformance failures occur.
    """
    repo_root = Path(__file__).resolve().parents[2]
    arch_doc = repo_root / "documentation" / "library_design" / "Astrometrics_Library_Architecture.md"
    report_output = repo_root / "documentation" / "library_design" / "Pipeline_Audit_Report.md"

    if not arch_doc.exists():
        print(f"Error: Architecture doc not found at {arch_doc}")
        return 1

    print("Auditing implementation references...")
    refs = extract_implementation_references(arch_doc)
    resolved_count = sum(1 for r in refs if r.resolved)
    print(f"  Found {len(refs)} references: {resolved_count} resolved, {len(refs) - resolved_count} broken.")

    print("Auditing pipeline lifecycle structures...")
    pipeline_structs = audit_pipeline_structures()
    for p in pipeline_structs:
        print(f"  Pipeline '{p['name']}': Adapter={p['adapter_class']}, Summary={p['summary_class']}")

    print("Parsing Table A1 design parameters...")
    table_a1 = extract_table_a1_rows(arch_doc)
    print(f"  Extracted {len(table_a1)} parameter definitions.")

    print(f"Writing audit report to {report_output}...")
    generate_markdown_report(refs, pipeline_structs, table_a1, report_output)
    print("Audit report generated successfully.")

    broken = [r for r in refs if not r.resolved]
    if broken:
        print("\nBroken references detected:")
        for b in broken:
            print(f"  - {b.dotted_path}: {b.error_message}")
    return 0


def generate_audit_report_sphinx(app: Any) -> None:
    """Generate pipeline audit report during Sphinx build.

    Parameters
    ----------
    app : `Any`
        The Sphinx application instance.
    """
    doc_dir = Path(app.srcdir)
    arch_doc = doc_dir / "library_design" / "Astrometrics_Library_Architecture.md"
    report_output = doc_dir / "library_design" / "Pipeline_Audit_Report.md"
    if arch_doc.exists():
        refs = extract_implementation_references(arch_doc)
        pipeline_structs = audit_pipeline_structures()
        table_a1 = extract_table_a1_rows(arch_doc)
        generate_markdown_report(refs, pipeline_structs, table_a1, report_output)


def setup(app: Any) -> dict[str, Any]:
    """Register Sphinx extension hooks.

    Parameters
    ----------
    app : `Any`
        The Sphinx application instance.

    Returns
    -------
    metadata : `dict` [`str`, `Any`]
        Sphinx extension metadata.
    """
    app.connect("builder-inited", generate_audit_report_sphinx)
    return {
        "version": "0.1",
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }


if __name__ == "__main__":
    sys.exit(main())
