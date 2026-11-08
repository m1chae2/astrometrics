"""Purpose: Tests for the flow target of the TPM harness.

Description: The flow runs in Electron by default, or in a named browser.
Covers the flow target in the setup key: an Electron baseline never mixes
with a browser one, and the processing scenario and older runs keep their
keys.

Run with ``.venv/bin/pytest build/tpm/test -q``.
"""

import sys
from pathlib import Path

TPM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TPM_DIR))

from setup_fingerprint import make_setup_key  # ruff: ignore[module-import-not-at-top-of-file]

FINGERPRINT = {
    "cpu_model": "cpu",
    "displays": ["eDP-1 1920x1080 @60Hz"],
    "ac_online": True,
    "power_profile": "balanced",
}


def test_flow_target_separates_electron_from_browsers() -> None:
    """Electron, Brave and Chrome runs each get their own key."""
    keys = {
        target: make_setup_key(FINGERPRINT, "intel", "synthetic", "offline", flow_target=target)
        for target in ("electron", "brave", "chrome")
    }
    assert len(set(keys.values())) == 3


def test_older_runs_keep_their_key() -> None:
    """A run with no target (an older one) has the key it always had."""
    without = make_setup_key(FINGERPRINT, "intel", "synthetic", "offline")
    assert make_setup_key(FINGERPRINT, "intel", "synthetic", "offline", flow_target=None) == without
    assert make_setup_key(FINGERPRINT, "intel", "synthetic", "offline", flow_target="electron") != without


def test_processing_key_ignores_the_flow_target() -> None:
    """The processing scenario has no page, so its key does not change."""
    tools = {"siril": "1.4.4"}
    plain = make_setup_key(FINGERPRINT, "none", "synthetic", "offline", False, "processing", tools)
    with_target = make_setup_key(
        FINGERPRINT, "none", "synthetic", "offline", False, "processing", tools, "electron"
    )
    assert plain == with_target
