"""Purpose: Check that the committed TypeScript types match the backend models.

Description: The UI's ``backendTypes.ts`` is generated from the backend's
Pydantic models. A model that changes without a regeneration leaves the UI
type-checking against a stale shape. This test regenerates the text and
compares it with the committed file, so the mismatch fails in CI.
"""

import importlib.util
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPOSITORY_ROOT / "build" / "codegen" / "generate_types.py"
GENERATED = REPOSITORY_ROOT / "ui" / "common" / "types" / "backendTypes.ts"


def test_the_committed_types_match_a_fresh_generation() -> None:
    """Match ``backendTypes.ts`` to a fresh run of the generator."""
    spec = importlib.util.spec_from_file_location("generate_types", GENERATOR)
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)

    fresh = generator.render_types()
    assert GENERATED.read_text() == fresh, (
        "ui/common/types/backendTypes.ts is out of date. "
        "Run build/codegen/generate-types.sh and commit the result."
    )
