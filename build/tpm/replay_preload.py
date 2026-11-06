"""Purpose: Load the end-of-night state into the throwaway app.

Description: The replay profile starts from a state in which the app has
already downloaded most of the night. ``night_replay.py`` put a spread of the
night's earlier frames into the stand-in telescope computer's picture folder.
This script has two commands that make the app hold them, through the app's
own code:

- ``targets`` adds the night's targets to the throwaway library, with their
  coordinates, as a user would have them before the night (the app finds the
  target the mount points at by matching coordinates against its targets). It
  runs before the backend starts.
- ``ingest`` asks the running throwaway backend to download each target's
  frames with its ``telescope:sync`` method, the same call as the app's
  "Download Frames" button. The backend then copies the frames with its one
  transfer code (rsync through the stand-in ssh), sorts and indexes them and
  measures each new frame (about 11 s per frame). It waits until the syncs end
  and writes how long that took.

The time this takes is setup time. It is reported separately from the run and
does not enter any TPM.

Safety: ``targets`` refuses to run unless the settings file named by
``ASTROMETRICS_CONFIG`` is inside the throwaway folder and the library
paths are too. ``ingest`` talks only to 127.0.0.1 on the backend port it is
given and refuses the real backend's port 5000.

Usage::

    ASTROMETRICS_CONFIG=<sandbox>/astrometrics.config.toml \
    XDG_DATA_HOME=<sandbox>/xdg_data XDG_CACHE_HOME=<sandbox>/xdg_cache \
    XDG_STATE_HOME=<sandbox>/xdg_state \
    .venv/bin/python build/tpm/replay_preload.py targets <sandbox>
    .venv/bin/python build/tpm/replay_preload.py ingest <sandbox> \
        --backend-port N
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

os.environ.setdefault("ASTROMETRICS_TESTING", "1")

#: Seconds the whole ingest may take before the script gives up.
INGEST_TIMEOUT_SECONDS = 1800
#: Seconds between two looks at whether a sync still runs.
POLL_SECONDS = 3.0


def _sexagesimal(value: float, is_right_ascension: bool) -> str:
    """Write a coordinate the way the app's targets hold it.

    Parameters
    ----------
    value : `float`
        Hours for a right ascension, degrees for a declination.
    is_right_ascension : `bool`
        Which of the two it is.

    Returns
    -------
    text : `str`
        ``HH:MM:SS`` or ``+DD:MM:SS``.
    """
    sign = "-" if value < 0 else ("" if is_right_ascension else "+")
    total = round(abs(value) * 3600)
    return f"{sign}{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def night_targets(plan: dict) -> dict[str, tuple[float, float]]:
    """Find the targets of the staged frames and where they are.

    Parameters
    ----------
    plan : `dict`
        The staged ``plan.json``.

    Returns
    -------
    targets : `dict` [`str`, `tuple` [`float`, `float`]]
        Each target's name, with the right ascension (hours) and declination
        (degrees) of its first staged frame.
    """
    found: dict[str, tuple[float, float]] = {}
    for frame in plan["preload_frames"] + plan["window_frames"]:
        found.setdefault(frame["target_id"], (frame["ra_hours"], frame["dec_degrees"]))
    return found


def add_targets(sandbox: Path) -> dict[str, tuple[float, float]]:
    """Add the night's targets to the throwaway library.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder.

    Returns
    -------
    targets : `dict`
        The targets added (see `night_targets`). A library outside the
        throwaway folder stops the run (`seed_library.require_inside`).
    """
    from seed_library import require_inside

    from astrometricslib import Astrometrics, Target, get_configuration

    config = get_configuration()
    require_inside(config.get_library_path(), sandbox, "image library")
    require_inside(config.get_frames_path(), sandbox, "frames folder")
    plan = json.loads((sandbox / "replay" / "plan.json").read_text())
    targets = night_targets(plan)
    astrometrics = Astrometrics()
    for name, (right_ascension, declination) in targets.items():
        astrometrics.targets.add(
            Target(id=name, ra=_sexagesimal(right_ascension, True), dec=_sexagesimal(declination, False))
        )
    astrometrics.targets.save()
    return targets


def _rpc(port: int, method: str, params: dict) -> object:
    """Call one method of the throwaway backend.

    Parameters
    ----------
    port : `int`
        The backend's port (on 127.0.0.1).
    method : `str`
        The RPC method name.
    params : `dict`
        Its keyword arguments.

    Returns
    -------
    result : `object`
        The reply's ``result``.

    Raises
    ------
    RuntimeError
        If the backend answers with an error.
    """
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/rpc", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        reply = json.loads(response.read())
    if "error" in reply:
        raise RuntimeError(f"{method} failed: {reply['error']}")
    result = reply.get("result")
    # The backend wraps every result as {"status": ..., "data": ...}.
    if isinstance(result, dict) and "data" in result and "status" in result:
        return result["data"]
    return result


def ingest(sandbox: Path, backend_port: int) -> dict:
    """Have the running backend download and index the staged earlier frames.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder.
    backend_port : `int`
        The throwaway backend's port.

    Returns
    -------
    result : `dict`
        ``seconds``, the frames each target now has in the library and the
        reply of each sync start.

    Raises
    ------
    RuntimeError
        If a sync cannot start or does not end in time.
    """
    if backend_port == 5000:
        raise RuntimeError("Refusing the real backend's port 5000.")
    plan = json.loads((sandbox / "replay" / "plan.json").read_text())
    started = time.time()
    replies = {}
    for name in night_targets(plan):
        replies[name] = _rpc(backend_port, "telescope:sync", {"object_id": name})
    deadline = started + INGEST_TIMEOUT_SECONDS
    for name in replies:
        while _rpc(backend_port, "telescope:is_syncing", {"object_id": name}):
            if time.time() > deadline:
                raise RuntimeError(f"The sync of {name} did not end in {INGEST_TIMEOUT_SECONDS} s.")
            time.sleep(POLL_SECONDS)
    held = {
        name: len(list((sandbox / "library" / "frames" / "lights" / name).rglob("*.fits")))
        for name in replies
    }
    return {"seconds": round(time.time() - started, 1), "frames_in_library": held, "sync_replies": replies}


def main() -> int:
    """Run one command from the command line.

    Returns
    -------
    status : `int`
        0 on success, 1 on a failure that stops the run.
    """
    parser = argparse.ArgumentParser(description="Load the end-of-night state for the replay run.")
    commands = parser.add_subparsers(dest="command", required=True)
    targets = commands.add_parser("targets")
    targets.add_argument("sandbox", type=Path)
    ingest_command = commands.add_parser("ingest")
    ingest_command.add_argument("sandbox", type=Path)
    ingest_command.add_argument("--backend-port", type=int, required=True)
    ingest_command.add_argument("--out", type=Path, default=None)
    arguments = parser.parse_args()
    sandbox = arguments.sandbox.resolve()
    try:
        if arguments.command == "targets":
            config = os.environ.get("ASTROMETRICS_CONFIG", "")
            if not config or Path(config).resolve().parent != sandbox:
                print(
                    "Refusing: ASTROMETRICS_CONFIG must name the settings file inside the throwaway folder."
                )
                return 1
            print("Targets added:", add_targets(sandbox))
            return 0
        result = ingest(sandbox, arguments.backend_port)
    except (RuntimeError, OSError, urllib.error.URLError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if arguments.out is not None:
        arguments.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
