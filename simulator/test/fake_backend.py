"""Purpose: Stand in for the backend when the launcher is tested.

Description: The launcher tests run this program as the backend child. It
writes what it was started with to the file named by ``FAKE_BACKEND_RECORD``
(the environment, as JSON, plus whether the file ``FAKE_BACKEND_MARKER``
existed), then acts out ``FAKE_BACKEND_BEHAVIOR``. Several behaviors
separated by ``;`` are used one per start, the last one repeating:

* ``exit:N``: end with code N;
* ``switch:SOURCE``: save SOURCE as the observatory source choice, then end
  with the restart code 75;
* ``wait``: run until SIGTERM, then end with code 0;
* ``kill``: kill itself with SIGKILL (a crash).
"""

import json
import os
import signal
import sys
import time
from pathlib import Path

from simulator.source_choice import RESTART_EXIT_CODE, SourceChoice, write_choice


def main() -> int:
    """Record the start and act out the behavior.

    Returns
    -------
    status : `int`
        The exit code.
    """
    record = os.environ.get("FAKE_BACKEND_RECORD")
    marker = os.environ.get("FAKE_BACKEND_MARKER")
    run_number = 0
    if record:
        path = Path(record)
        runs = json.loads(path.read_text()) if path.exists() else []
        run_number = len(runs)
        runs.append({
            "environment": dict(os.environ),
            "marker_present": bool(marker) and Path(marker).exists(),
        })
        path.write_text(json.dumps(runs))
    behaviors = os.environ.get("FAKE_BACKEND_BEHAVIOR", "exit:0").split(";")
    behavior = behaviors[min(run_number, len(behaviors) - 1)]
    if behavior.startswith("exit:"):
        return int(behavior.split(":", 1)[1])
    if behavior.startswith("switch:"):
        write_choice(SourceChoice(behavior.split(":", 1)[1]))
        return RESTART_EXIT_CODE
    if behavior == "kill":
        os.kill(os.getpid(), signal.SIGKILL)
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    while not stopping:
        time.sleep(0.05)
    return 0


if __name__ == "__main__":
    sys.exit(main())
