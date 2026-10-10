"""Purpose: Keep the architecture document's algorithm claims tied to real code.

Description: Each algorithm claim in
``documentation/library_design/Astrometrics_Library_Architecture.md`` carries an
HTML comment ``<!-- impl: dotted.path -->`` naming the function, class or
constant that implements it. This test imports every named object. A claim
whose implementation no longer exists fails the test, so a refactor cannot
leave the document describing code that is gone. A claim with no link is a
design goal, and the document says so in its own words.
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
IMPL_COMMENT = re.compile(r"<!-- impl: ([^ ]+?) -->")
MINIMUM_LINKED_CLAIMS = 100


def _resolve(dotted: str) -> object | None:
    """Import the longest module prefix of a dotted path, then walk attributes.

    Parameters
    ----------
    dotted : `str`
        A path such as ``astrometricslib.pipelines.shared.angles.wrapped_ra_difference_deg``.

    Returns
    -------
    target : `object` or `None`
        The named object, or `None` when no prefix imports or an attribute is missing.
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


@pytest.mark.parametrize("name", sorted(set(_linked_claims()))[:3])
def test_a_sample_link_is_a_full_astrometricslib_path(name: str) -> None:
    """Links are full dotted paths into the library, not bare function names."""
    assert name.startswith("astrometricslib.")
