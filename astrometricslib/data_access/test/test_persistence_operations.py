"""Purpose: Regression tests for target_catalog persistence semantics.

Description: Covers the data-integrity bug where get_targets(target_id)
silently reloaded and replaced the whole in-memory target cache on every
call -- orphaning mutations made to previously fetched targets -- and
where save_targets() blindly overwrote the entire target_catalog with
whatever a single process's stale in-memory snapshot held, clobbering
concurrent edits made by other processes to targets it never touched.
"""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from astrometricslib import Astrometrics, Target
from astrometricslib.drivers import disk_interface
from astrometricslib.utilities.config_loader import AppConfiguration


def _make_isolated_config(tmp_path) -> AppConfiguration:  # ruff: ignore[missing-type-function-argument]
    """Build an AppConfiguration pointed at a fresh, empty tmp_path library.

    Returns
    -------
    AppConfiguration
        A configuration pointed at a fresh, empty library under tmp_path.
    """
    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    frames_path = library_path / "frames"
    frames_path.mkdir(parents=True)

    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path), "frames_path": str(frames_path)}})
    return config


def test_sequential_fetch_mutate_save_persists_both_targets(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Fetching target A then target B no longer orphans A's in-memory edit.

    Reproduces the reported mechanism: within a single Astrometrics
    instance, get_targets('A') followed by get_targets('B') used to
    silently reload and replace api._targets on the second call, dropping
    any mutation already made to A. Both edits must now survive a save.
    """
    config = _make_isolated_config(tmp_path)

    disk_interface.save_target(app_config=config, target=Target(id="Target Alpha"))
    disk_interface.save_target(app_config=config, target=Target(id="Target Beta"))

    astrometrics = Astrometrics(app_config=config)

    target_a = astrometrics.targets.get("Target Alpha")
    target_a.common_name = "Alpha Mutated"

    target_b = astrometrics.targets.get("Target Beta")
    target_b.common_name = "Beta Mutated"

    astrometrics.targets.save()

    reloaded = Astrometrics(app_config=config)
    reloaded_a = reloaded.targets.get("Target Alpha")
    reloaded_b = reloaded.targets.get("Target Beta")

    assert reloaded_a.common_name == "Alpha Mutated"
    assert reloaded_b.common_name == "Beta Mutated"


def test_get_target_by_id_does_not_reload_catalog_on_cache_hit(tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Repeated by-id lookups of already-cached targets skip the disk read."""
    from astrometricslib.data_access.butler import DiskButler

    config = _make_isolated_config(tmp_path)
    disk_interface.save_target(app_config=config, target=Target(id="Target Alpha"))
    disk_interface.save_target(app_config=config, target=Target(id="Target Beta"))

    spy = mocker.spy(DiskButler, "get")
    astrometrics = Astrometrics(app_config=config)
    calls_after_construction = spy.call_count

    astrometrics.targets.get("Target Alpha")
    astrometrics.targets.get("Target Beta")
    astrometrics.targets.get("Target Alpha")

    assert spy.call_count == calls_after_construction


def test_get_target_by_id_discovers_new_disk_record_without_dropping_cache(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A cache-miss lookup pulls in new records without dropping cache.

    If target_id isn't resident yet, get_target() must still find it by
    reading from disk -- but additively, not by replacing api._targets,
    so an unsaved edit to an already-cached target survives the lookup.
    """
    config = _make_isolated_config(tmp_path)
    disk_interface.save_target(app_config=config, target=Target(id="Target Alpha"))

    astrometrics = Astrometrics(app_config=config)
    target_a = astrometrics.targets.get("Target Alpha")
    target_a.common_name = "Alpha Mutated"

    # Created by a hypothetical concurrent process/write, not yet known to
    # this high-level interface.
    disk_interface.save_target(app_config=config, target=Target(id="Target Gamma"))

    target_gamma = astrometrics.targets.get("Target Gamma")
    assert target_gamma is not None

    # The earlier, unsaved mutation to Target Alpha must still be intact
    # in-memory.
    still_cached_a = astrometrics.targets.get("Target Alpha")
    assert still_cached_a is target_a
    assert still_cached_a.common_name == "Alpha Mutated"


def test_save_targets_does_not_clobber_untouched_targets_concurrent_edit(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """save_targets() only merges in targets this process actually touched.

    Simulates a concurrent process editing "Target Charlie" on disk after
    this process's astrometrics already holds a stale in-memory copy of
    it. This process never fetches Charlie, so its save must leave the
    concurrent edit intact rather than overwriting it with the stale
    snapshot.
    """
    config = _make_isolated_config(tmp_path)
    disk_interface.save_target(app_config=config, target=Target(id="Target Alpha"))
    disk_interface.save_target(app_config=config, target=Target(id="Target Charlie", common_name="original"))

    astrometrics = Astrometrics(app_config=config)

    target_a = astrometrics.targets.get("Target Alpha")
    target_a.common_name = "Alpha Mutated"

    # A concurrent process updates Charlie directly on disk; this
    # high-level interface's in-memory copy of Charlie (loaded at
    # construction) is now stale.
    disk_interface.save_target(
        app_config=config, target=Target(id="Target Charlie", common_name="changed by other process")
    )

    astrometrics.targets.save()

    reloaded = Astrometrics(app_config=config)
    assert reloaded.targets.get("Target Alpha").common_name == "Alpha Mutated"
    assert reloaded.targets.get("Target Charlie").common_name == "changed by other process"


_SUBPROCESS_WORKER = textwrap.dedent(
    """
    import sys
    import time

    from astrometricslib import Astrometrics

    role, target_id, new_value, sentinel_dir = sys.argv[1:5]

    astrometrics = Astrometrics()
    target = astrometrics.targets.get(target_id)
    target.common_name = new_value

    if role == "slow":
        open(sentinel_dir + "/slow_loaded", "w").close()
        deadline = time.time() + 10
        while not __import__("os").path.exists(sentinel_dir + "/fast_done"):
            if time.time() > deadline:
                raise TimeoutError("timed out waiting for fast worker")
            time.sleep(0.05)
        astrometrics.targets.save()
        open(sentinel_dir + "/slow_done", "w").close()
    else:
        deadline = time.time() + 10
        while not __import__("os").path.exists(sentinel_dir + "/slow_loaded"):
            if time.time() > deadline:
                raise TimeoutError("timed out waiting for slow worker to load")
            time.sleep(0.05)
        astrometrics.targets.save()
        open(sentinel_dir + "/fast_done", "w").close()
    """
)


@pytest.mark.slow
def test_concurrent_processes_do_not_clobber_each_others_target_edits(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Two overlapping processes editing different targets don't race.

    "slow" constructs its astrometrics (snapshotting the whole catalog) and
    fetches/mutates Target Alpha, then waits for "fast" to fetch, mutate,
    and save Target Beta before saving its own edit. Under the old
    replace-on-save behavior, slow's save would push its stale
    pre-fast-edit snapshot of Target Beta and silently wipe out fast's
    change. Under merge-on-save, slow only pushes what it touched
    (Target Alpha), so fast's edit to Target Beta survives.

    Raises
    ------
    RuntimeError
        If the slow worker subprocess exits before reaching its
        rendezvous point.
    TimeoutError
        If the slow worker subprocess never reaches its rendezvous
        point within the deadline.
    """
    config = _make_isolated_config(tmp_path)
    disk_interface.save_target(app_config=config, target=Target(id="Target Alpha"))
    disk_interface.save_target(app_config=config, target=Target(id="Target Beta"))

    config_path = tmp_path / "astrometrics.config"
    library_path = tmp_path / "library"
    frames_path = library_path / "frames"
    config_path.write_text(f"[Image Library]\npath = {library_path}\nframes_path = {frames_path}\n")

    worker_script = tmp_path / "_concurrent_save_worker.py"
    worker_script.write_text(_SUBPROCESS_WORKER)

    sentinel_dir = tmp_path / "sentinels"
    sentinel_dir.mkdir()

    repo_root = Path(__file__).resolve().parents[3]

    env = dict(os.environ)
    env["ASTROMETRICS_CONFIG_PATH"] = str(config_path)
    env["ASTROMETRICS_TESTING"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(repo_root), env.get("PYTHONPATH")]))

    slow_proc = subprocess.Popen(
        [sys.executable, str(worker_script), "slow", "Target Alpha", "Alpha Mutated", str(sentinel_dir)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Give the slow worker a head start so it snapshots the catalog before
    # the fast worker's edit lands, deterministically reproducing the race
    # window rather than relying on OS scheduling luck.
    deadline = time.time() + 10
    while not (sentinel_dir / "slow_loaded").exists():
        if slow_proc.poll() is not None:
            out, err = slow_proc.communicate()
            raise RuntimeError(f"slow worker exited early:\nSTDOUT:\n{out}\nSTDERR:\n{err}")
        if time.time() > deadline:
            slow_proc.kill()
            out, err = slow_proc.communicate()
            raise TimeoutError(
                f"slow worker never reached its rendezvous point:\nSTDOUT:\n{out}\nSTDERR:\n{err}"
            )
        time.sleep(0.05)

    fast_proc = subprocess.Popen(
        [sys.executable, str(worker_script), "fast", "Target Beta", "Beta Mutated", str(sentinel_dir)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    fast_out, fast_err = fast_proc.communicate(timeout=15)
    slow_out, slow_err = slow_proc.communicate(timeout=15)

    assert fast_proc.returncode == 0, f"fast worker failed:\nSTDOUT:\n{fast_out}\nSTDERR:\n{fast_err}"
    assert slow_proc.returncode == 0, f"slow worker failed:\nSTDOUT:\n{slow_out}\nSTDERR:\n{slow_err}"

    reloaded = Astrometrics(app_config=config)
    assert reloaded.targets.get("Target Alpha").common_name == "Alpha Mutated"
    assert reloaded.targets.get("Target Beta").common_name == "Beta Mutated"
