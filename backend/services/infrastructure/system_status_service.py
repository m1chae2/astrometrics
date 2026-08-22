"""Lightweight system status models and aggregation service.

Provides a low-cost "pulse" of telescope and processing state for
frequent UI polling, plus richer health/introspection models used by
the maintenance and scripting endpoints.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from wayfindinglib.models.session.telemetry import IndiStatus


class TelescopePulse(BaseModel):
    """Lightweight snapshot of current telescope state for polling."""

    ra: str = "Unknown"
    dec: str = "Unknown"
    altitude: str = "Unknown"
    azimuth: str = "Unknown"
    tracking_status: str = Field("Unknown", alias="trackingStatus")
    connection_status: str = Field("Disconnected", alias="connectionStatus")
    temperature: str = "Unknown"
    humidity: str = "Unknown"
    filter: str = "Unknown"
    focuser_position: int = Field(0, alias="focuserPosition")
    guiding_history: list[dict[str, Any]] = Field(default_factory=list, alias="guidingHistory")
    alignment_attempts: list[Any] = Field(default_factory=list, alias="alignmentAttempts")
    alignment_active: bool = Field(default=False, alias="alignmentActive")


class ProcessingJobPulse(BaseModel):
    """Lightweight snapshot of a single background processing job."""

    target_id: str
    job_id: str
    status: str


class SystemPulse(BaseModel):
    """Aggregated lightweight system status for frequent polling."""

    telescope: TelescopePulse
    processing: list[ProcessingJobPulse] = []
    # Future: Add camera, safety, storage status here


class SystemStatusService:
    """Service responsible for aggregating system-wide status.

    REQ: BKD-SystemPulse (New)
    """

    def __init__(self, telescope_service=None, image_processing_service=None, astrometrics_service=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self._telescope_service = telescope_service
        self._image_processing_service = image_processing_service
        self._astrometrics_service = astrometrics_service
        self._last_vram_check = 0.0
        self._cached_vram = "Unknown (Non-NVIDIA or driver missing)"
        self._nvidia_available = True

    def get_pulse(self) -> SystemPulse:
        """Collect critical state from other services into one payload.

        Returns
        -------
        result : `SystemPulse`
            Pydantic model of the current telescope and processing
            state.
        """
        # If the consolidated state is available, prioritize reading
        # from it directly (O(1) lookup)
        if self._astrometrics_service:
            state = self._astrometrics_service.get_state()
            tele = state.get("telescope", {})
            proc = state.get("processing", [])

            tele_pulse = TelescopePulse(
                ra=tele.get("ra", "Unknown"),
                dec=tele.get("dec", "Unknown"),
                altitude=tele.get("altitude", "Unknown"),
                azimuth=tele.get("azimuth", "Unknown"),
                trackingStatus=tele.get("trackingStatus", "Unknown"),
                connectionStatus=tele.get("connectionStatus", "Disconnected"),
                temperature=tele.get("temperature", "Unknown"),
                humidity=tele.get("humidity", "Unknown"),
                filter=tele.get("filter", "Unknown"),
                focuserPosition=tele.get("focuserPosition", 0),
                guidingHistory=tele.get("guidingHistory", tele.get("guiding_history", [])),
                alignmentAttempts=tele.get("alignmentAttempts", tele.get("alignment_attempts", [])),
                alignmentActive=tele.get("alignmentActive", tele.get("alignment_active", False)),
            )

            proc_jobs = []
            for job in proc:
                proc_jobs.append(
                    ProcessingJobPulse(
                        target_id=job.get("target_id", "unknown"),
                        job_id=job.get("job_id", "unknown"),
                        status=job.get("status", "unknown"),
                    )
                )

            return SystemPulse(telescope=tele_pulse, processing=proc_jobs)

        # Fallback to legacy on-demand aggregation if
        # astrometrics_service not initialized
        # 1. Telescope Status
        tele_status = TelescopePulse()
        if self._telescope_service:
            try:
                # We use the raw dict from get_status but mapped to
                # our lightweight model
                full_status = self._telescope_service.get_status()
                # Handle Pydantic model or dict return
                if hasattr(full_status, "model_dump"):
                    full_data = full_status.model_dump(by_alias=True)
                elif hasattr(full_status, "dict"):
                    full_data = full_status.dict(by_alias=True)
                else:
                    full_data = full_status

                tele_status = TelescopePulse(
                    ra=full_data.get("ra", "Unknown"),
                    dec=full_data.get("dec", "Unknown"),
                    altitude=full_data.get("altitude", "Unknown"),
                    azimuth=full_data.get("azimuth", "Unknown"),
                    trackingStatus=full_data.get("trackingStatus", "Unknown"),
                    connectionStatus=full_data.get("connectionStatus", "Disconnected"),
                    temperature=full_data.get("temperature", "Unknown"),
                    humidity=full_data.get("humidity", "Unknown"),
                    filter=full_data.get("filter", "Unknown"),
                    focuserPosition=full_data.get("focuserPosition", 0),
                    guidingHistory=full_data.get("guidingHistory", full_data.get("guiding_history", [])),
                    alignmentAttempts=full_data.get(
                        "alignmentAttempts", full_data.get("alignment_attempts", [])
                    ),
                    alignmentActive=full_data.get(
                        "alignmentActive", full_data.get("alignment_active", False)
                    ),
                )
            except Exception as e:
                # Log error but don't fail the pulse
                print(f"Error getting telescope status for pulse: {e}")

        # 2. Processing Status
        proc_jobs = []
        if self._image_processing_service:
            try:
                raw_jobs = self._image_processing_service.get_all_processes()
                for job in raw_jobs:
                    proc_jobs.append(
                        ProcessingJobPulse(
                            target_id=job.get("target_id", "unknown"),
                            job_id=job.get("job_id", "unknown"),
                            status=job.get("status", "unknown"),
                        )
                    )
            except Exception as e:
                print(f"Error getting processing status for pulse: {e}")

        return SystemPulse(telescope=tele_status, processing=proc_jobs)

    def get_resource_usage(self) -> dict[str, Any]:
        """Collect system resource usage (RAM, VRAM).

        Returns
        -------
        resources : `dict`
            Dict with ``"system_ram_usage_percent"`` (`float`) and
            ``"vram_status"``, the cached or freshly polled VRAM
            status string.
        """
        import os
        import time

        import psutil

        resources = {
            "system_ram_usage_percent": psutil.virtual_memory().percent,
            "vram_status": self._cached_vram,
        }

        # Try to get VRAM via nvidia-smi if on Linux, throttling
        # calls to protect laptop battery / dGPU sleep states
        if os.name != "nt" and self._nvidia_available:
            now = time.time()
            if now - self._last_vram_check > 15.0:
                self._last_vram_check = now
                try:
                    import subprocess

                    smi_res = subprocess.run(
                        [
                            "nvidia-smi",
                            "--query-gpu=memory.used,memory.total",
                            "--format=csv,noheader,nounits",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=1.0,
                    )
                    if smi_res.returncode == 0:
                        self._cached_vram = smi_res.stdout.strip() + " MiB"
                        resources["vram_status"] = self._cached_vram
                    else:
                        self._nvidia_available = False
                        self._cached_vram = "Not Available (Non-NVIDIA or driver missing)"
                        resources["vram_status"] = self._cached_vram
                except Exception:
                    self._nvidia_available = False
                    self._cached_vram = "Not Available (Non-NVIDIA or driver missing)"
                    resources["vram_status"] = self._cached_vram

        return resources


class SystemHealth(BaseModel):
    """Aggregates health and resource status of the hardware bus and system."""

    model_config = ConfigDict(populate_by_name=True)

    resources: dict[str, Any] = Field(default_factory=dict, alias="resources")
    indi: IndiStatus = Field(default_factory=IndiStatus, alias="indi")


class IntrospectionMethod(BaseModel):
    """Metadata for a callable method exposed to scripting."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., alias="name")
    doc: str = Field(default="", alias="doc")
    args: list[str] = Field(default_factory=list, alias="args")


class IntrospectionEndpoint(BaseModel):
    """Expose a service class and its methods for RPC discovery."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., alias="name")
    type: str = Field(..., alias="type")
    doc: str = Field(default="", alias="doc")
    methods: list[IntrospectionMethod] = Field(default_factory=list, alias="methods")
