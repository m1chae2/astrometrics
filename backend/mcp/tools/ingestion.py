"""Purpose: MCP tools for data ingestion.

Handles remote telescope folder scanning and ingestion. Delegates
core orchestration to backend services via unified execute_rpc.
REQ: AGENT-1.1
"""

from backend.mcp.tool_registry import execute_rpc, registry


@registry.register(
    name="ingest_all_remote",
    description=(
        "Discovers all target folders on the remote telescope and initiates "
        "parallel ingestion for all of them."
    ),
)
async def ingest_all_remote():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Scan the remote telescope and ingest every folder found.

    Triggers a background ingestion job for each available target
    folder, leveraging the backend's parallel processing
    capabilities.

    Returns
    -------
    result : `dict`
        Includes ``"status"`` and ``"message"``, plus ``"jobs"``
        (per-folder job IDs or errors) when folders were found.
    """
    # 1. Scan for remote folders
    scan_result = await execute_rpc("ingestion:scan")
    if scan_result.get("status") != "success":
        return {"status": "error", "message": scan_result.get("message", "Failed to scan remote targets.")}

    folders = scan_result.get("data", {}).get("folders", [])
    if not folders:
        return {"status": "ok", "message": "No folders found on remote telescope."}

    # 2. Trigger ingestion for each folder
    jobs = []
    for folder in folders:
        # Special case for 'Calibration' if it exists as a folder
        # name vs the meta-target
        # The ingestion service handles mapping 'Calibration' to Dark/Bias/Flat
        ingestion_result = await execute_rpc(
            "ingestion:start", {"type": "remote", "targetName": folder, "sourcePath": folder}
        )
        if ingestion_result.get("status") == "success":
            job_id = ingestion_result.get("data", {}).get("jobId")
            jobs.append({"target": folder, "jobId": job_id})
        else:
            jobs.append({"target": folder, "error": ingestion_result.get("message", "Failed to start")})

    return {
        "status": "ok",
        "message": f"Initiated parallel ingestion for {len(folders)} folders.",
        "jobs": jobs,
    }
