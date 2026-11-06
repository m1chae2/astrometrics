"""Purpose: Tests that the offline sandbox settings avoid real services.

Description: The offline observatory profile must not reach a real INDI server
or a real PHD2 on this computer. Both are pointed at port 1, where nothing
listens, instead of their usual ports (7624 and 4400).

Run with ``.venv/bin/pytest build/tpm/test -q``.
"""

import sys
import tomllib
from pathlib import Path

TPM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TPM_DIR))

import write_sandbox_config  # ruff: ignore[module-import-not-at-top-of-file]


def test_offline_profile_points_indi_and_phd2_at_a_port_nothing_listens_on(tmp_path: Path) -> None:
    """INDI and PHD2 both use port 1, so no real local service is touched."""
    config_path = write_sandbox_config.write_sandbox_config(tmp_path, "offline")
    settings = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert settings["Observatory.Telescope"]["indi_port"] == "1"
    assert settings["Observatory.Guiding"]["phd2_port"] == "1"
    assert settings["Observatory.Guiding"]["phd2_location"] == "local"
