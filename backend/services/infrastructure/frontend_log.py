"""Purpose: Write messages from the browser app into the backend log.

Description: The app's remote logger sends uncaught errors and failed
promises to the ``system:frontend_log`` RPC method. This module writes
them to the ``frontend`` logger, so they appear in the same log as the
backend's own messages.
"""

import logging
from typing import Any

#: The log level for each level name the app sends.
_FRONTEND_LOG_LEVELS = {"info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}


def log_frontend_message(level: str, message: str, stack: str | None = None, **extra: Any) -> None:
    """Write a message from the browser app to the backend log.

    Parameters
    ----------
    level : `str`
        ``"info"``, ``"warn"`` or ``"error"``. Any other value is logged as an
        error.
    message : `str`
        The message text.
    stack : `str`, optional
        The JavaScript stack trace, when there is one.
    **extra
        Other keys the app sends. ``componentStack`` is the React component
        stack, when there is one.
    """
    details = [text for text in (stack, extra.get("componentStack")) if text]
    logging.getLogger("frontend").log(
        _FRONTEND_LOG_LEVELS.get(level, logging.ERROR), "%s", "\n".join([message, *details])
    )
