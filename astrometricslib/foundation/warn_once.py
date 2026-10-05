"""A helper that logs a given warning only once per run.

A scan of thousands of frames can hit the same config mistake on every frame.
Logging it each time would bury everything else, so it is logged once.
"""

import functools
import logging


@functools.cache
def warn_once(logger: logging.Logger, message: str) -> None:
    """Log a warning the first time this logger and message are seen.

    Parameters
    ----------
    logger : `logging.Logger`
        The logger to write to.
    message : `str`
        The text to log. Because the result is cached, a second call with the
        same logger and text does nothing.
    """
    logger.warning(message)
