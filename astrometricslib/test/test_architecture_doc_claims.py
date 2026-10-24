"""Purpose: Keep the architecture document's algorithm claims tied to code.

Description: Each algorithm claim in
``documentation/library_design/Astrometrics_Library_Architecture.md`` carries
an HTML comment ``<!-- impl: dotted.path -->`` naming the function, class or
constant that implements it. The quality measurements paper, its companion,
does the same. This test imports every named object. A claim
whose implementation no longer exists fails the test, so a refactor cannot
leave the document describing code that is gone. A claim with no link is a
design goal, and the document says so in its own words.

The tuned values themselves live in
``documentation/library_design/Astrometrics_Library_Implementation.md``,
whose parameter tables carry the same kind of link to the constant that holds
each value. Those links are checked here too.
"""

import importlib
import re
from pathlib import Path

import pytest

ARCHITECTURE_DOCUMENT = (
    Path(__file__).resolve().parents[2]
    / "documentation"
    / "library_design"
    / "Astrometrics_Library_Architecture.md"
)
IMPLEMENTATION_DOCUMENT = ARCHITECTURE_DOCUMENT.with_name("Astrometrics_Library_Implementation.md")
QUALITY_MEASUREMENTS_DOCUMENT = ARCHITECTURE_DOCUMENT.with_name("Astrometrics_Quality_Measurements.md")
IMPL_COMMENT = re.compile(r"<!-- impl: ([^ ]+?) -->")
MINIMUM_LINKED_CLAIMS = 100


def _resolve(dotted: str) -> object | None:
    """Import the longest module prefix of a dotted path, then walk attributes.

    Parameters
    ----------
    dotted : `str`
        A path such as
        ``astrometricslib.pipelines.shared.angles.wrapped_ra_difference_deg``.

    Returns
    -------
    target : `object` or `None`
        The named object, or `None` when no prefix imports or an attribute
        is missing.
    """
    parts = dotted.split(".")
    for split in range(len(parts), 0, -1):
        try:
            target: object = importlib.import_module(".".join(parts[:split]))
        except ImportError:
            continue
        for attribute in parts[split:]:
            target = getattr(target, attribute, None)
            if target is None:
                return None
        return target
    return None


def _linked_claims() -> list[str]:
    """Read every implementation link in the architecture document.

    Returns
    -------
    names : `list` [`str`]
        The dotted paths, in document order, duplicates kept.
    """
    return IMPL_COMMENT.findall(ARCHITECTURE_DOCUMENT.read_text(encoding="utf-8"))


def test_every_architecture_claim_names_code_that_exists() -> None:
    """Each ``impl`` link in the architecture document imports and resolves."""
    names = _linked_claims()
    unresolved = sorted({name for name in names if _resolve(name) is None})
    assert not unresolved, (
        f"{len(unresolved)} impl link(s) in {ARCHITECTURE_DOCUMENT.name} name code that does not "
        f"exist: {unresolved}. Update the document or the link when the code moves."
    )


def test_the_architecture_document_keeps_its_claims_linked() -> None:
    """The document does not silently lose its implementation links."""
    assert len(_linked_claims()) >= MINIMUM_LINKED_CLAIMS


def test_every_quality_measurements_link_names_code_that_exists() -> None:
    """Each ``impl`` link in the quality paper imports and resolves."""
    names = IMPL_COMMENT.findall(QUALITY_MEASUREMENTS_DOCUMENT.read_text(encoding="utf-8"))
    assert names, f"{QUALITY_MEASUREMENTS_DOCUMENT.name} has no impl links"
    unresolved = sorted({name for name in names if _resolve(name) is None})
    assert not unresolved, (
        f"{len(unresolved)} impl link(s) in {QUALITY_MEASUREMENTS_DOCUMENT.name} name code that does "
        f"not exist: {unresolved}. Update the document or the link when the code moves."
    )


def test_every_implementation_document_link_names_code_that_exists() -> None:
    """Each ``impl`` link in the implementation document resolves."""
    names = IMPL_COMMENT.findall(IMPLEMENTATION_DOCUMENT.read_text(encoding="utf-8"))
    assert names, f"{IMPLEMENTATION_DOCUMENT.name} has no impl links"
    unresolved = sorted({name for name in names if _resolve(name) is None})
    assert not unresolved, (
        f"{len(unresolved)} impl link(s) in {IMPLEMENTATION_DOCUMENT.name} name code that does not "
        f"exist: {unresolved}. Update the document or the link when the code moves."
    )


@pytest.mark.parametrize("name", sorted(set(_linked_claims()))[:3])
def test_a_sample_link_is_a_full_astrometricslib_path(name: str) -> None:
    """Links are full dotted paths into the library, not bare names."""
    assert name.startswith("astrometricslib.")
