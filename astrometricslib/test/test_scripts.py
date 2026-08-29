"""Verification suite running demonstration and utility scripts.

Ensures that scripts are continually tested against API changes.
"""

import os
import subprocess
import sys
import textwrap

import pytest


def run_script_as_subprocess(module_name: str, extra_env: dict[str, str] | None = None) -> None:
    """Run a script module as a subprocess.

    Executes the module against the real config and database by
    default. Appends ``--headless`` if the script supports it.

    Parameters
    ----------
    module_name : `str`
        Dotted module path to run via ``python -m``.
    extra_env : `dict` [`str`, `str`], optional
        Extra environment variables to set for the subprocess, merged
        over the inherited environment. Pass
        ``{"ASTROMETRICS_CONFIG_PATH": ...}`` to point the script at a
        throwaway config/database instead of the real one -- a fresh
        ``python -m`` subprocess never imports this package's
        `conftest.py`, so it has none of the in-process test sandbox
        this suite otherwise runs under; without this override, a
        script that mutates the catalog (deletes rows, prunes frame
        records, etc.) does so against the real, on-disk database.
    """
    env = dict(os.environ)
    env["ASTROMETRICS_TESTING"] = "1"
    # Suppresses SirilInterface.launch_siril_gui(), which otherwise
    # pops an interactive GUI window after a successful stack -- see
    # its "HEADLESS" check in astrometricslib/drivers/siril_interface.py.
    env["HEADLESS"] = "1"
    if extra_env:
        env.update(extra_env)

    # 1. Determine if the script accepts --headless
    help_result = subprocess.run(
        [sys.executable, "-m", module_name, "--help"], capture_output=True, text=True, env=env
    )
    args = []
    if "--headless" in help_result.stdout or "--headless" in help_result.stderr:
        args.append("--headless")

    result = subprocess.run(
        [sys.executable, "-m", module_name, *args], capture_output=True, text=True, env=env
    )
    assert result.returncode == 0, (
        f"Script {module_name} failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


# NOTE: astrometricslib/scripts/reset_stellar_object_library.py is
# deliberately NOT exercised here. `run_script_as_subprocess` runs against
# the real config and database by default, and that script deletes every
# row in the stellar catalog -- so covering it meant a plain
# `pytest astrometricslib` silently destroyed the user's real stellar
# catalog. The script itself is fine and still shipped; it just cannot be
# smoke-tested against production data without the same throwaway-catalog
# treatment `test_script_reindex_all_targets` below now gets.


@pytest.mark.slow
def test_script_reindex_all_targets(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Run reindex_all_targets against a throwaway catalog, not the real one.

    reindex_all_targets.py calls reindex_frames(target, prune_missing=True),
    which really does delete a target's frame records once their files are
    judged missing -- so running it via plain `run_script_as_subprocess`
    (real config, real database) would prune real frame rows from the
    user's actual catalog on every `pytest astrometricslib` invocation.
    That is the same class of bug already fixed for
    reset_stellar_object_library.py above, just discovered later.

    Seeds one target with one frame whose file genuinely exists on disk,
    via a throwaway config/database passed through ASTROMETRICS_CONFIG_PATH
    -- both the seed step and the script itself run as fresh subprocesses,
    so neither one ever imports this package's conftest.py sandbox (which
    only isolates in-process AppConfiguration() calls, not subprocesses).
    Asserts the frame survives reindexing, so this exercises real
    prune-vs-keep logic rather than only proving the script boots.
    """
    library_path = tmp_path / "libraryIndex"
    frames_dir = library_path / "frames" / "lights" / "ReindexScriptTestTarget"
    frames_dir.mkdir(parents=True)

    frames_path = library_path / "frames"
    config_path = tmp_path / "astrometrics.config"
    config_path.write_text(f"[Image Library]\npath = {library_path}\nframes_path = {frames_path}\n")

    import numpy as np
    from astropy.io import fits

    frame_path = frames_dir / "frame_001.fits"
    hdu = fits.PrimaryHDU(np.zeros((10, 10), dtype=np.float32))
    hdu.header["OBJECT"] = "ReindexScriptTestTarget"
    hdu.header["EXPOSURE"] = 30.0
    hdu.writeto(frame_path)

    env = {"ASTROMETRICS_CONFIG_PATH": str(config_path)}

    seed_script = textwrap.dedent(f"""
        from astrometricslib import FrameRecord, Target
        from astrometricslib.drivers.local_database import save_target

        frame = FrameRecord(
            path={str(frame_path)!r},
            filter="Luminance",
            role="LIGHT",
            camera="ZWO ASI 533MM Pro",
            date="2026-05-01 00:00:00",
            timestamp=1777507200.0,
            exposure="30.0",
        )
        target = Target(id="ReindexScriptTestTarget", frames=[frame])
        save_target(target=target)
        """)
    seed_result = subprocess.run(
        [sys.executable, "-c", seed_script],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )
    assert seed_result.returncode == 0, (
        f"Seeding the throwaway catalog failed:\nSTDOUT:\n{seed_result.stdout}\nSTDERR:\n{seed_result.stderr}"
    )

    run_script_as_subprocess("astrometricslib.scripts.reindex_all_targets", extra_env=env)

    import sqlite3

    conn = sqlite3.connect(library_path / "astrometrics.db")
    try:
        data_json = conn.execute(
            "SELECT data_json FROM targets WHERE id = ?", ("ReindexScriptTestTarget",)
        ).fetchone()
    finally:
        conn.close()
    assert data_json is not None, "Reindexing should not have removed the target itself."
    assert str(frame_path) in data_json[0], (
        "prune_missing=True should not have removed a frame whose file still exists on disk."
    )


@pytest.mark.slow
def test_script_stellar_catalog_audit():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Run astrometricslib/scripts/stellar_catalog_audit script."""
    run_script_as_subprocess("astrometricslib.scripts.stellar_catalog_audit")
