"""Regression tests for pipeline architecture documentation parity.

This module verifies that implementation references embedded in the
architecture design documents resolve to live, active code objects,
preventing documentation drift across pipeline releases.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path


def _resolve_symbol(dotted_path: str) -> bool:
    """Attempt to import and resolve a dotted Python symbol.

    Parameters
    ----------
    dotted_path : `str`
        The dotted import path to test.

    Returns
    -------
    success : `bool`
        True if the object was successfully resolved, False otherwise.
    """
    parts = dotted_path.strip().split(".")
    for i in range(len(parts), 0, -1):
        mod_name = ".".join(parts[:i])
        try:
            mod = importlib.import_module(mod_name)
            obj = mod
            for attr in parts[i:]:
                obj = getattr(obj, attr)
            return True
        except ImportError, AttributeError:
            continue
    return False


def test_architecture_document_implementation_links_resolve() -> None:
    """Verify all embedded implementation tags resolve to active symbols.

    Ensures that every `<!-- impl: ... -->` link in
    `Astrometrics_Library_Architecture.md` maps directly to an active
    module, class, method, or constant in the codebase.
    """
    # Locate repo root from current test file path
    repo_root = Path(__file__).resolve().parents[3]
    arch_doc = repo_root / "documentation" / "library_design" / "Astrometrics_Library_Architecture.md"

    assert arch_doc.exists(), f"Architecture document not found at {arch_doc}"

    content = arch_doc.read_text(encoding="utf-8")
    pattern = re.compile(r"<!--\s*impl:\s*([\w\.]+)\s*-->")
    symbols = pattern.findall(content)

    assert len(symbols) > 50, "Expected at least 50 implementation references"

    broken = [s for s in set(symbols) if not _resolve_symbol(s)]
    assert not broken, (
        f"Found {len(broken)} stale or broken implementation links in architecture doc: {broken}"
    )


def test_pipeline_review_docs_generation() -> None:
    """Verify that all modular pipeline review guides are generated and valid.

    Checks that `generate_pipeline_review_docs` creates review guides for all
    five scientific pipelines plus the parent index, and that each document
    contains the required scientific sections and quality models.
    """
    import importlib.util

    repo_root = Path(__file__).resolve().parents[3]
    # The generator is a script, not part of a package, so it is loaded from
    # its path (as `backend/tests/test_client_configs.py` loads its script).
    script_path = repo_root / "build" / "documentation" / "generate_pipeline_review_docs.py"
    spec = importlib.util.spec_from_file_location("generate_pipeline_review_docs", script_path)
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    generate_pipeline_review_docs = generator.generate_pipeline_review_docs

    output_dir = repo_root / "documentation" / "library_design" / "pipelines"
    generated_files = generate_pipeline_review_docs(output_dir)

    expected_files = {
        "index.md",
        "astrometry.md",
        "photometry.md",
        "spectroscopy.md",
        "asteroid_detection.md",
        "stacking.md",
    }
    actual_files = {f.name for f in generated_files}
    assert expected_files == actual_files, f"Expected {expected_files}, got {actual_files}"

    for f in generated_files:
        content = f.read_text(encoding="utf-8")
        assert len(content) > 500, f"Document {f.name} is unexpectedly short"
        if f.name != "index.md":
            assert "## 1. Overview & Scientific Objective" in content
            assert "## 2. Input Requirements & FITS Header Specification" in content
            assert "## 3. Mathematical & Algorithmic Formulation" in content
            assert "## 4. Four-Stage Lifecycle Architecture" in content
            assert "## 5. Tuned Calibration Parameters" in content
            assert "## 6. Degraded States & Fallback Pathways" in content
            assert "## 7. Quality Summary Data Models" in content
