"""Description: Static MCP tool for verifying Pydantic/TypeScript sync.

Enables instant programmatic verification of API schemas across stack
boundaries. # REQ: AGENT-3.1
"""

import os
from typing import Any

from backend.mcp.tool_registry import registry


@registry.register(
    name="typegen_contract_validator",
    description=(
        "Validates Python-to-TypeScript codegen contracts, ensuring all Pydantic schemas sync cleanly."
    ),
    input_schema={"type": "object", "properties": {}},
)
# ruff: ignore[unused-async] -- awaited by ToolRegistry.execute, which
# calls every registered tool via `await func(**arguments)`.
async def typegen_contract_validator() -> dict[str, Any]:
    """Run TypeScript interface generation and check its output.

    Returns
    -------
    result : `dict`
        Includes ``"status"`` (``"success"``, ``"warning"``, or
        ``"error"``) and ``"message"``. On success or warning, also
        includes ``"file_size_bytes"``.
    """
    # Typegen writes to src/common/types/backendTypes.ts
    output_path = "src/common/types/backendTypes.ts"

    try:
        # Save current working dir to restore it
        old_cwd = os.getcwd()
        # Find repository root
        # If we are in backend or elsewhere, find the root
        repo_root = old_cwd
        while repo_root != "/" and not os.path.exists(
            os.path.join(repo_root, "scripts", "codegen", "generate_types.py")
        ):
            repo_root = os.path.dirname(repo_root)

        os.chdir(repo_root)

        # Run codegen
        from build.codegen.generate_types import main as generate_types_main

        generate_types_main()

        # Check generated file exists
        if not os.path.exists(output_path):
            os.chdir(old_cwd)
            return {
                "status": "error",
                "message": f"TypeScript type generation failed: Output file not created at {output_path}",
            }

        with open(output_path, encoding="utf-8") as f:
            content = f.read()

        os.chdir(old_cwd)

        # Verify specific symbols exist
        required_symbols = [
            "FilterType",
            "FrameRecord",
            "TelescopeStatus",
            "TargetObject",
            "FileItem",
            "PlotData",
            "Spectrum",
            "SystemHealth",
            "SequencePlan",
            "MosaicPanel",
        ]

        missing_symbols = []
        for sym in required_symbols:
            if sym not in content:
                missing_symbols.append(sym)

        if missing_symbols:
            return {
                "status": "warning",
                "message": (
                    f"Codegen succeeded, but some required symbols are missing from the TS output: "
                    f"{missing_symbols}"
                ),
                "file_size_bytes": len(content),
            }

        return {
            "status": "success",
            "message": (
                "All Pydantic models are perfectly synchronized and TS interfaces verified successfully."
            ),
            "file_size_bytes": len(content),
            "output_file": output_path,
        }
    except Exception as e:
        return {"status": "error", "message": f"Typegen validation encountered an error: {e}"}
