"""Purpose: Ingest and parse native PHD2 GuideLog text files.

Description: Parses calibration metadata and frame-by-frame GuideStep rows
(Time, dx, dy, dRA, dDec, PulseRA, PulseDec, StarMass, SNR) from native PHD2
guide log files (PHD2_GuideLog_*.txt) for SQLite storage and tracking analysis.
"""

import os
import re
from datetime import datetime
from typing import Any


def parse_phd2_guide_log(file_path: str, target_name: str | None = None) -> list[dict[str, Any]]:
    """Parse a PHD2 guide log text file into normalized sample dictionaries.

    Parameters
    ----------
    file_path : `str`
        Path to the PHD2_GuideLog_*.txt file.
    target_name : `str` | `None`, optional
        Celestial target name to associate with the parsed samples.

    Returns
    -------
    samples : `list` [`dict` [`str`, `Any`]]
        Parsed frame-by-frame guiding samples ready for SQLite persistence.
    """
    if not os.path.isfile(file_path):
        return []

    samples: list[dict[str, Any]] = []
    in_guiding_section = False
    headers: list[str] = []
    section_start_epoch: float | None = None

    with open(file_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue

            # Detect section start (e.g. "Guiding Begins at 2026-09-24...")
            if stripped.startswith("Guiding Begins at"):
                match = re.search(r"Guiding Begins at\s+([\d\-]+ [\d:]+)", stripped)
                if match:
                    try:
                        dt = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
                        section_start_epoch = dt.timestamp()
                    except ValueError:
                        section_start_epoch = None
                in_guiding_section = True
                headers = []
                continue

            # Detect section end
            if stripped.startswith("Guiding Ends at"):
                in_guiding_section = False
                headers = []
                continue

            if in_guiding_section:
                # Column header line
                if stripped.startswith("Frame,") or "RADistanceGuide" in stripped:
                    headers = [h.strip() for h in stripped.split(",")]
                    continue

                if headers and not stripped.startswith("#"):
                    parts = [p.strip() for p in stripped.split(",")]
                    if len(parts) >= len(headers):
                        row = dict(zip(headers, parts, strict=False))
                        try:
                            # Relative time in seconds from section start
                            rel_time = float(row.get("Time", 0.0))
                            epoch_time = (
                                section_start_epoch + rel_time
                                if section_start_epoch is not None
                                else rel_time
                            )

                            ra_dur = float(row.get("RADuration", 0.0))
                            if row.get("RADirection") == "West":
                                ra_dur = -ra_dur

                            dec_dur = float(row.get("DECDuration", 0.0))
                            if row.get("DECDirection") == "South":
                                dec_dur = -dec_dur

                            dra = float(row.get("RADistanceGuide", row.get("dx", 0.0)))
                            ddec = float(row.get("DECDistanceGuide", row.get("dy", 0.0)))

                            snr_str = row.get("SNR")
                            snr_val = float(snr_str) if snr_str else None

                            mass_str = row.get("StarMass")
                            mass_val = float(mass_str) if mass_str else None

                            samples.append({
                                "timestamp": epoch_time,
                                "time": epoch_time,
                                "target_name": target_name,
                                "dra": dra,
                                "ddec": ddec,
                                "pulse_ra": ra_dur,
                                "pulse_dec": dec_dur,
                                "snr": snr_val,
                                "star_mass": mass_val,
                            })
                        except ValueError, KeyError:
                            continue

    return samples
