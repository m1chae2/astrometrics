"""Purpose: Name the errors the simulator layer raises.

Description: Nothing in the core (the two libraries, the backend and the MCP
servers) raises these, so they live here and not in a library. Both are
`~astrometricslib.exceptions.ConfigurationError`, so code that already handles
a setup problem handles them too.
"""

from astrometricslib.exceptions import ConfigurationError

__all__ = ["GuardError", "SimulationUnavailableError"]


class SimulationUnavailableError(ConfigurationError):
    """The simulated observatory cannot start.

    A needed program is missing, the platform does not allow it (the packaged
    app), or a safety check refused the settings.
    """


class GuardError(ConfigurationError):
    """A simulator safety check failed; the message says which and why."""
