"""Purpose: Name the tools that start work the app must run as its own job.

Description: Some tools start slow work that writes to the library:
stacking a target, analyzing it, cancelling those, naming the stars of a
stack, making a preview again, and copying frames or logs from the
telescope computer.
When the person starts the same work from the app, the backend runs it
as a job: the job shows in the job list with its progress and log, the
app's buttons show it running, its Cancel button reaches it, and the
app hears when it ends.

A library server that ran the work in its own process could record a job
row, but the backend could not stop it, and the app would not hear of it.
So these tools are served by the ``astrometrics-backend`` server instead,
which starts the work through the same backend RPC method the app's button
calls. The library servers leave them out of their reflected tools. Both
sides read the names from here, so a tool cannot be served twice or lost.
This module imports nothing, so every server may use it.
"""

APP_JOB_TOOLS: dict[str, str] = {
    "processing_stack": "processing:stack",
    "processing_identify_stack": "analysis:identify_stack",
    "processing_analyze": "analysis:analyze_image",
    "processing_cancel": "processing:cancel",
    "processing_enhance": "processing:set_bright_object",
    "processing_remake_preview": "processing:remake_preview",
    "observatory_remote_sync_frames": "telescope:sync",
    "observatory_remote_sync_logs": "telescope:sync_logs",
}
"""Tool name -> the backend RPC method that starts its work, the one the
app's own button calls. The ``astrometrics-backend`` server serves these
tools; the library servers do not reflect them."""
