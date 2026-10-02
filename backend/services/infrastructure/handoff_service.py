"""HandoffService: cross-device workspace continuity and synchronization.

Tracks the active workspace state (mode, selected target, coordinates,
mount state) and synchronizes state across desktop and mobile companion
clients via REST endpoints and real-time WebSocket events.
"""

import logging
import threading
import time
from typing import Any

from backend.services.infrastructure.socket_manager import SocketManager

logger = logging.getLogger(__name__)


class HandoffService:
    """Manages active workspace state for cross-device continuity and handoff.

    Thread-safe store for user view state and coordinates, notifying
    subscribers whenever state is updated from any device.
    """

    def __init__(self, socket_manager: SocketManager | None = None) -> None:
        """Initialize HandoffService with a thread lock and default state.

        Parameters
        ----------
        socket_manager : `SocketManager`, optional
            Socket manager used for broadcasting real-time handoff events.
        """
        self._lock = threading.Lock()
        self._socket_manager = socket_manager
        self._state: dict[str, Any] = {
            "active_mode": "Image Viewer",
            "selected_target": None,
            "coordinates": {
                "ra_hours": None,
                "dec_degrees": None,
                "fov_degrees": None,
            },
            "telemetry": {
                "mount_tracking": False,
                "mount_state": "IDLE",
                "active_sequence": None,
            },
            "origin_device": "desktop",
            "updated_at": time.time(),
        }

    def get_state(self) -> dict[str, Any]:
        """Return the current active workspace state.

        Returns
        -------
        state : `dict`
            Current workspace continuity state dictionary.
        """
        with self._lock:
            return dict(self._state)

    def update_state(
        self,
        active_mode: str | None = None,
        selected_target: str | None = None,
        coordinates: dict[str, Any] | None = None,
        telemetry: dict[str, Any] | None = None,
        origin_device: str = "desktop",
    ) -> dict[str, Any]:
        """Update active state fields and broadcast changes to all devices.

        Parameters
        ----------
        active_mode : `str`, optional
            Active UI mode (e.g. 'Planetarium', 'Image Viewer').
        selected_target : `str`, optional
            Selected catalog target designation.
        coordinates : `dict`, optional
            Astronomical coordinates and field-of-view settings.
        telemetry : `dict`, optional
            Live mount and sequence execution telemetry facts.
        origin_device : `str`
            Device identifier updating the state ('desktop' or 'mobile').

        Returns
        -------
        state : `dict`
            Updated workspace continuity state dictionary.
        """
        with self._lock:
            if active_mode is not None:
                self._state["active_mode"] = active_mode
            if selected_target is not None:
                self._state["selected_target"] = selected_target
            if coordinates is not None:
                self._state["coordinates"].update(coordinates)
            if telemetry is not None:
                self._state["telemetry"].update(telemetry)

            self._state["origin_device"] = origin_device
            self._state["updated_at"] = time.time()
            snapshot = dict(self._state)

        logger.info(
            "Handoff state updated by %s: mode=%s, target=%s",
            origin_device,
            snapshot.get("active_mode"),
            snapshot.get("selected_target"),
        )

        if self._socket_manager:
            self._socket_manager.broadcast_ui_event_sync("handoff", snapshot)

        return snapshot

    def beam_to_device(
        self,
        target: str | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        """Beam current target or view to a paired phone via GSConnect.

        Parameters
        ----------
        target : `str`, optional
            Target designation to open on the mobile device.
        mode : `str`, optional
            Workspace view mode to display on the mobile device.

        Returns
        -------
        result : `dict`
            Status and metadata of the beam dispatch attempt.
        """
        import shutil
        import subprocess

        kdeconnect_bin = shutil.which("kdeconnect-cli") or shutil.which("gsconnect-cli")
        deep_link = f"astrometrics://handoff?mode={mode or 'Planetarium'}"
        if target:
            deep_link += f"&target={target}"

        if not kdeconnect_bin:
            return {
                "success": False,
                "message": ("Neither gsconnect-cli nor kdeconnect-cli found in PATH"),
                "deep_link": deep_link,
            }

        try:
            cmd = [kdeconnect_bin, "--open-url", deep_link]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
            return {
                "success": proc.returncode == 0,
                "message": ("Dispatched to GSConnect/KDE Connect" if proc.returncode == 0 else proc.stderr),
                "deep_link": deep_link,
            }
        except Exception as exc:
            logger.warning("Failed to beam to device: %s", exc)
            return {
                "success": False,
                "message": str(exc),
                "deep_link": deep_link,
            }

    def list_paired_devices(self) -> list[dict[str, Any]]:
        """Enumerate paired companion devices via GSConnect or KDE Connect.

        Returns
        -------
        devices : `list` of `dict`
            List of paired devices with id, name, and reachable status.
        """
        import re
        import shutil
        import subprocess

        kdeconnect_bin = shutil.which("kdeconnect-cli") or shutil.which("gsconnect-cli")
        if not kdeconnect_bin:
            return []

        try:
            cmd = [kdeconnect_bin, "-l"]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
            if proc.returncode != 0:
                return []

            devices: list[dict[str, Any]] = []
            # Match lines like: "- Device Name: id12345 (reachable)"
            pattern = re.compile(r"^[-*]\s+(.+?):\s+([a-zA-Z0-9_-]+)\s*(.*)$")
            for line in proc.stdout.splitlines():
                line = line.strip()
                match = pattern.match(line)
                if match:
                    name, dev_id, status = match.groups()
                    devices.append({
                        "id": dev_id,
                        "name": name.strip(),
                        "reachable": "reachable" in status.lower(),
                        "status": status.strip("() "),
                    })
            return devices
        except Exception as exc:
            logger.warning("Failed to list paired devices: %s", exc)
            return []

    def send_device_alert(
        self,
        title: str,
        message: str,
        priority: str = "normal",
        ring_device: bool = False,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch an alert notification to companion devices and WebSocket.

        Broadcasts the alert payload across WebSocket connections, and if
        GSConnect or KDE Connect is available, sends a notification or
        audible ring alarm to paired mobile hardware.

        Parameters
        ----------
        title : `str`
           Title of the alert event (e.g. 'Guide Star Lost').
        message : `str`
           Descriptive text explaining the event or required action.
        priority : `str`, optional
           Alert severity level ('low', 'normal', 'high', 'critical').
        ring_device : `bool`, optional
           Whether to trigger an audible device ring alarm on the phone.
        device_id : `str`, optional
           Specific paired companion device ID to target.

        Returns
        -------
        result : `dict`
           Dispatch status and delivered channel details.
        """
        import shutil
        import subprocess

        alert_payload = {
            "title": title,
            "message": message,
            "priority": priority,
            "ring_device": ring_device,
            "timestamp": time.time(),
            "device_id": device_id,
        }

        # Deliver via real-time WebSocket to all connected companion apps
        if self._socket_manager:
            self._socket_manager.broadcast_ui_event_sync("device_alert", alert_payload)

        kdeconnect_bin = shutil.which("kdeconnect-cli") or shutil.which("gsconnect-cli")
        delivered_channels = ["websocket"]

        if kdeconnect_bin:
            try:
                # Dispatch notification ping
                notify_cmd = [kdeconnect_bin, "--ping-msg", f"{title}: {message}"]
                if device_id:
                    notify_cmd.extend(["--device", device_id])
                subprocess.run(
                    notify_cmd,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                delivered_channels.append("gsconnect_ping")

                # If critical, trigger audible phone ring
                if ring_device:
                    ring_cmd = [kdeconnect_bin, "--ring"]
                    if device_id:
                        ring_cmd.extend(["--device", device_id])
                    subprocess.run(
                        ring_cmd,
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    delivered_channels.append("gsconnect_ring")
            except Exception as exc:
                logger.warning("Failed to dispatch GSConnect alert: %s", exc)

        return {
            "success": True,
            "alert": alert_payload,
            "channels": delivered_channels,
        }

    def share_file_to_device(
        self,
        file_path: str,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        """Share an image or data file to a companion device via GSConnect.

        Parameters
        ----------
        file_path : `str`
            Absolute path of the local file to beam.
        device_id : `str`, optional
            Specific paired device ID to target.

        Returns
        -------
        result : `dict`
            Dispatch status and CLI outcome.
        """
        import os
        import shutil
        import subprocess

        if not os.path.exists(file_path):
            return {
                "success": False,
                "message": f"Target file does not exist: {file_path}",
            }

        kdeconnect_bin = shutil.which("kdeconnect-cli") or shutil.which("gsconnect-cli")
        if not kdeconnect_bin:
            return {
                "success": False,
                "message": ("Neither gsconnect-cli nor kdeconnect-cli found in PATH"),
            }

        try:
            cmd = [kdeconnect_bin, "--share", file_path]
            if device_id:
                cmd.extend(["--device", device_id])
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
            return {
                "success": proc.returncode == 0,
                "message": ("Shared file to device" if proc.returncode == 0 else proc.stderr),
                "file_path": file_path,
            }
        except Exception as exc:
            logger.warning("Failed to share file to device: %s", exc)
            return {
                "success": False,
                "message": str(exc),
                "file_path": file_path,
            }
