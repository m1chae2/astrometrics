"""Regression tests for pipeline architecture documentation parity.

This module verifies that implementation references embedded in the
architecture design documents resolve to live, active code objects, and
that the generated pipeline review guides name every quality gate the code
defines and are up to date with their generator.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest


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


def _load_review_doc_generator() -> ModuleType:
    """Load the review-guide generator script as a module.

    The generator is a script, not part of a package, so it is loaded from
    its path (as `backend/tests/test_client_configs.py` loads its script).

    Returns
    -------
    generator : `types.ModuleType`
        The loaded `generate_pipeline_review_docs` module.
    """
    import importlib.util

    script_path = REPO_ROOT / "build" / "documentation" / "generate_pipeline_review_docs.py"
    spec = importlib.util.spec_from_file_location("generate_pipeline_review_docs", script_path)
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    # Registered first: the generator's dataclasses look their module up here.
    sys.modules[spec.name] = generator
    spec.loader.exec_module(generator)
    return generator


def _gate_names_in_source(package_dir: Path) -> set[str]:
    """Collect every gate name a pipeline package defines.

    This reads the source independently of the generator: a module-level
    string constant named ``GATE_NAME`` or ``*_GATE_NAME`` is a gate name.
    Test folders are skipped.

    Parameters
    ----------
    package_dir : `Path`
        The pipeline package folder.

    Returns
    -------
    names : `set` [`str`]
        The gate names.
    """
    pattern = re.compile(r'^(?:[A-Z0-9_]+_)?GATE_NAME\s*(?::\s*str\s*)?=\s*"([^"]+)"', re.MULTILINE)
    names: set[str] = set()
    for path in package_dir.rglob("*.py"):
        if any(part in ("test", "tests") for part in path.relative_to(package_dir).parts):
            continue
        names.update(pattern.findall(path.read_text(encoding="utf-8")))
    return names


PIPELINE_GUIDES = {
    "astrometry": "astrometry.md",
    "photometry": "photometry.md",
    "spectroscopy": "spectroscopy.md",
    "asteroid_detection": "asteroid_detection.md",
    "stacking": "stacking.md",
}
REPO_ROOT = Path(__file__).resolve().parents[3]
GUIDES_DIR = REPO_ROOT / "documentation" / "library_design" / "pipelines"


@pytest.mark.parametrize("pipeline", sorted(PIPELINE_GUIDES))
def test_every_gate_in_code_is_in_its_committed_guide(pipeline: str) -> None:
    """Fail when a gate defined in the code is missing from its review guide.

    Reads the committed guide (before any regeneration), so adding a gate
    without regenerating the guides fails here.

    Parameters
    ----------
    pipeline : `str`
        The pipeline package name.
    """
    gate_names = _gate_names_in_source(REPO_ROOT / "astrometricslib" / "pipelines" / pipeline)
    assert gate_names, f"no gate names found for {pipeline}"
    guide = (GUIDES_DIR / PIPELINE_GUIDES[pipeline]).read_text(encoding="utf-8")
    missing = sorted(name for name in gate_names if f"`{name}`" not in guide)
    assert not missing, (
        f"{PIPELINE_GUIDES[pipeline]} does not mention gate(s) {missing}; describe them in "
        "GATE_DESCRIPTIONS in build/documentation/generate_pipeline_review_docs.py and rerun it"
    )


def test_generator_discovers_and_describes_every_gate() -> None:
    """Check that the generator finds every gate and has a sentence for each.

    The generator's own discovery must agree with an independent read of the
    source, every gate must have an entry in `GATE_DESCRIPTIONS`, and no
    entry may describe a gate the code no longer has.
    """
    generator = _load_review_doc_generator()
    all_names: set[str] = set()
    for pipeline in PIPELINE_GUIDES:
        discovered = {gate.name for gate in generator.discover_gate_names(pipeline)}
        expected = _gate_names_in_source(REPO_ROOT / "astrometricslib" / "pipelines" / pipeline)
        assert discovered == expected, f"{pipeline}: generator found {discovered}, source has {expected}"
        all_names |= discovered
    descriptions = set(generator.GATE_DESCRIPTIONS)
    assert not all_names - descriptions, f"gates with no description: {sorted(all_names - descriptions)}"
    assert not descriptions - all_names, (
        f"descriptions of gates the code lacks: {sorted(descriptions - all_names)}"
    )


def test_index_gate_counts_match_code() -> None:
    """Check that the index's gate counts equal the gates the code defines."""
    index = (GUIDES_DIR / "index.md").read_text(encoding="utf-8")
    for pipeline, guide in PIPELINE_GUIDES.items():
        count = len(_gate_names_in_source(REPO_ROOT / "astrometricslib" / "pipelines" / pipeline))
        row = next(line for line in index.splitlines() if f"({guide})" in line)
        numbers = [int(n) for n in re.findall(r"(\d+) (?:run gates?|period checks?)", row)]
        assert sum(numbers) == count, f"index row for {pipeline} says {numbers}, code has {count}"


def test_pipeline_review_docs_generation(tmp_path: Path) -> None:
    """Verify that all modular pipeline review guides are generated and valid.

    Generates the guides into a temporary folder, checks that every guide and
    the index are produced with the required sections, and that the
    committed guides match a fresh generation.

    Parameters
    ----------
    tmp_path : `Path`
        A temporary folder from pytest.
    """
    generator = _load_review_doc_generator()
    generated_files = generator.generate_pipeline_review_docs(tmp_path)

    expected_files = {"index.md", *PIPELINE_GUIDES.values()}
    actual_files = {f.name for f in generated_files}
    assert expected_files == actual_files, f"Expected {expected_files}, got {actual_files}"

    for f in generated_files:
        content = f.read_text(encoding="utf-8")
        assert len(content) > 500, f"Document {f.name} is unexpectedly short"
        assert "<<" not in content, f"Document {f.name} has an unfilled placeholder"
        assert "Not yet described" not in content, f"Document {f.name} has an undescribed gate"
        if f.name != "index.md":
            assert "## 1. Overview & Scientific Objective" in content
            assert "## 2. Input Requirements & FITS Header Specification" in content
            assert "## 3. Mathematical & Algorithmic Formulation" in content
            assert "## 4. Processing Flow" in content
            assert "## 5. Tuned Calibration Parameters" in content
            assert "## 6. Degraded States & Fallback Pathways" in content
            assert "## 7. Quality Summary Data Models" in content
        committed = (GUIDES_DIR / f.name).read_text(encoding="utf-8")
        assert committed == content, (
            f"{f.name} is out of date; run build/documentation/generate_pipeline_review_docs.py"
        )
