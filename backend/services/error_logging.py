"""Purpose: Log a failure the way the error itself asks.

Description: Every library error says how loud its log line should be
(``log_level``). A telescope or computer that is offline is a status, logged
at ``INFO``; a bad argument is a ``WARNING``; a failed disk or pipeline is an
``ERROR``. A service that catches an error and carries on (the top of a
background thread, a save that is re-raised) logs it with `log_failure`, so it
never logs a status as an ``ERROR``. An exception that is not a library
error is a bug and is logged as an ``ERROR`` with its traceback.
"""

import logging
from typing import Any

from astrometricslib.exceptions import AstrometricsError


def log_failure(logger: logging.Logger, error: BaseException, message: str, *args: Any) -> None:
    """Log one caught failure at the level the error asks for.

    Parameters
    ----------
    logger : `logging.Logger`
        The service's logger.
    error : `BaseException`
        The caught exception.
    message : `str`
        What failed, as a ``%`` format string. The error's code and message
        are added after it.
    *args : `Any`
        The values for ``message``.
    """
    if isinstance(error, AstrometricsError):
        level = error.log_level_number
        detail = f" ({error.code}): {error.message}"
        logger.log(level, "%s", (message % args) + detail, exc_info=error if level >= logging.ERROR else None)
        return
    logger.error(message, *args, exc_info=error)
