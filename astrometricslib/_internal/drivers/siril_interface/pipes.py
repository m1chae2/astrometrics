"""Purpose: Siril's command and output pipes, in the way the system needs.

Description: Siril takes commands from one named pipe and writes its output
to another. Linux and Windows make and open those pipes differently, so each
has its own module, and this one imports the right one once, when it is first
loaded, the way `astrometricslib.foundation.system` does. `session` calls
these names and never checks the system.

* `create_pipes` gives the paths of the command and output pipes. On Linux it
  makes them as FIFO files in the work folder; on Windows it only chooses
  names, because Siril creates the pipes itself.
* `open_pipe` opens one end. On Linux it waits for Siril to open the other
  end; on Windows it tries again until Siril has made the pipe.
* `stop_pipe_holders` kills the helper processes still tied to the work
  folder after Siril was stopped (Linux); on Windows it does nothing.
"""

import sys

if sys.platform == "win32":
    from astrometricslib._internal.drivers.siril_interface._pipes_windows import (
        create_pipes,
        open_pipe,
        stop_pipe_holders,
    )
else:
    from astrometricslib._internal.drivers.siril_interface._pipes_posix import (
        create_pipes,
        open_pipe,
        stop_pipe_holders,
    )

__all__ = ["create_pipes", "open_pipe", "stop_pipe_holders"]
