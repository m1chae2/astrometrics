"""Purpose: Write and read the fit's results file.

Description: The fit writes one JSON file in the world, ``fit_results.json``.
It is the record of what the simulator was set to and why. For each value it
names the public call and parameters, the observing nights used, the source
kind and date, the INDI device, property and element, the value written, the
value before clamping, and any clamping. It also holds a fingerprint of the
world's library copy (the size and modification time of each copied
database), so a new night, which changes the copy, changes the fingerprint
and the values at the next start.

The exposure driver reads the same file to set the properties
(`fitted_device_settings`), so the properties after start equal the values in
the file. The file is written whole and renamed into place, so a reader never
sees half of it.

A value with ``applied`` false is a record only: it says why nothing is
written (no data, or the simulator cannot do it).
"""

import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from simulator.device_settings import DeviceSetting
from simulator.fit.fitting import FitResult

__all__ = [
    "RESULTS_FILE_NAME",
    "fingerprint_library_copy",
    "fitted_controller_settings",
    "fitted_device_settings",
    "fitted_guide_rate_fraction",
    "read_results",
    "write_results",
]

#: The file name, inside the simulator's folder.
RESULTS_FILE_NAME = "fit_results.json"
#: The version of the file layout.
SCHEMA_VERSION = 1
#: The files of the library copy that make up the fingerprint: the copied
#: databases (see `simulator.library_copy`).
FINGERPRINT_PATTERNS = ("*.db", "*.sqlite", "*.sqlite3")


def fingerprint_library_copy(root: Path) -> dict[str, Any]:
    """Describe the world's copied databases by size and modification time.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's folder.

    Returns
    -------
    fingerprint : `dict` [`str`, `object`]
        ``files`` (path relative to the root, size in bytes and modification
        time in seconds, sorted by path) and ``digest`` (a short text that
        changes when any of them does). The frames overlay is not looked at.
    """
    entries: list[dict[str, Any]] = []
    for folder_name in ("library", "wayfinding"):
        folder = root / folder_name
        if not folder.is_dir():
            continue
        for pattern in FINGERPRINT_PATTERNS:
            for path in folder.rglob(pattern):
                if path.is_file():
                    status = path.stat()
                    entries.append({
                        "path": str(path.relative_to(root)),
                        "size_bytes": status.st_size,
                        "modified": round(status.st_mtime, 3),
                    })
    entries.sort(key=lambda entry: entry["path"])
    import hashlib

    text = json.dumps(entries, sort_keys=True)
    return {"files": entries, "digest": hashlib.sha256(text.encode()).hexdigest()[:16]}


def write_results(
    path: Path,
    result: FitResult,
    fingerprint: dict[str, Any],
    inputs: dict[str, Any] | None = None,
) -> None:
    """Write the results file.

    Parameters
    ----------
    path : `pathlib.Path`
        The file to write.
    result : `FitResult`
        The fit.
    fingerprint : `dict`
        From `fingerprint_library_copy`.
    inputs : `dict`, optional
        Facts about the world the values were converted with (frame sizes,
        gains), kept so the conversion can be checked.
    """
    document = {
        "schema_version": SCHEMA_VERSION,
        "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "library_fingerprint": fingerprint,
        "inputs": inputs or {},
        "controller_settings": result.controller_settings,
        "guide_rate_fraction": result.guide_rate_fraction,
        "notes": result.notes,
        "values": [asdict(entry) for entry in result.values],
    }
    temporary = path.with_name(f".{path.name}.writing")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=False), encoding="utf-8")
    os.replace(temporary, path)


def read_results(path: Path) -> dict[str, Any] | None:
    """Read the results file.

    Parameters
    ----------
    path : `pathlib.Path`
        The file.

    Returns
    -------
    document : `dict` or `None`
        The parsed file, or `None` when it is missing, unreadable or of
        another version (a status: the simulators then keep their values).
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    return (
        document if isinstance(document, dict) and document.get("schema_version") == SCHEMA_VERSION else None
    )


def fitted_device_settings(path: Path) -> list[DeviceSetting]:
    """List the INDI properties the results file says to write.

    Parameters
    ----------
    path : `pathlib.Path`
        The results file.

    Returns
    -------
    settings : `list` [`DeviceSetting`]
        One setting for each device and property, with the applied values of
        its elements, in the file's order. Empty when the file is missing.
    """
    document = read_results(path)
    if document is None:
        return []
    grouped: dict[tuple[str, str], list[tuple[str, float]]] = {}
    decimals: dict[tuple[str, str], int | None] = {}
    for entry in document.get("values", []):
        if not entry.get("applied") or entry.get("value") is None or entry["device"] == "PHD2 profile":
            continue
        key = (entry["device"], entry["property_name"])
        grouped.setdefault(key, []).append((entry["element"], float(entry["value"])))
        known = decimals.get(key, entry.get("display_decimals"))
        decimals[key] = (
            None
            if known is None or entry.get("display_decimals") is None
            else max(known, entry["display_decimals"])
        )
    return [
        DeviceSetting(
            device, property_name, tuple(elements), read_back_decimals=decimals.get((device, property_name))
        )
        for (device, property_name), elements in grouped.items()
    ]


def fitted_controller_settings(path: Path) -> dict[str, float] | None:
    """Give the PHD2 guide controller settings the results file holds.

    Parameters
    ----------
    path : `pathlib.Path`
        The results file.

    Returns
    -------
    settings : `dict` [`str`, `float`] or `None`
        The settings, or `None` for PHD2's own.
    """
    document = read_results(path)
    return None if document is None else document.get("controller_settings")


def fitted_guide_rate_fraction(path: Path) -> dict[str, float] | None:
    """Give the guide rates (fractions of 15 arcsec/s) the file wrote.

    Parameters
    ----------
    path : `pathlib.Path`
        The results file.

    Returns
    -------
    fractions : `dict` [`str`, `float`] or `None`
        ``GUIDE_RATE_WE`` and ``GUIDE_RATE_NS``, or `None` when none is
        written.
    """
    document = read_results(path)
    return None if document is None else document.get("guide_rate_fraction")
