"""Unified service-level resilience and circuit-breaker decorator.

Protects service endpoints and background routines from unexpected
hardware or subsystem failures.

REQ: SYS-1.0, BKD-1.5
"""

import functools
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def resilient_service(
    fallback_value: Any = None,
    fallback_factory: Callable[[], Any] | None = None,
    exceptions: type[Exception] | tuple = Exception,
    max_failures: int = 5,
    cooldown_seconds: float = 30.0,
) -> Callable:
    """Wrap a service method to prevent cascading failures.

    Wraps execution in a try-except block, tracks consecutive
    failures, and integrates a lightweight circuit breaker that
    redirects calls to a fallback value when the threshold is
    exceeded.

    Parameters
    ----------
    fallback_value : `Any`, optional
        Default return value on failure if no factory is specified.
    fallback_factory : `Callable`, optional
        Callable factory producing the default return value
        dynamically. Called with the wrapped method's first
        positional argument (``self``) if the factory accepts one.
    exceptions : `type` or `tuple`, optional
        Exception class or tuple of classes to catch (default is
        `Exception`).
    max_failures : `int`, optional
        Consecutive failure threshold before opening the circuit.
    cooldown_seconds : `float`, optional
        Seconds the circuit remains open before entering the
        half-open state.

    Returns
    -------
    decorator : `~collections.abc.Callable`
        A decorator that wraps a sync or async service method with
        the resilience/circuit-breaker behavior described above.
    """

    def decorator(func: Callable) -> Callable:
        import inspect

        failures = 0
        last_failure_time = 0.0
        circuit_open = False

        def get_fallback(*args):  # ruff: ignore[missing-type-args, missing-return-type-private-function]
            if fallback_factory:
                # If the factory accepts the service instance (self)
                sig = inspect.signature(fallback_factory)
                if len(sig.parameters) > 0 and args:
                    return fallback_factory(args[0])
                return fallback_factory()
            return fallback_value

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:  # ruff: ignore[missing-type-args, missing-type-kwargs]
            nonlocal failures, last_failure_time, circuit_open

            if circuit_open:
                if time.time() - last_failure_time > cooldown_seconds:
                    logger.info(f"Circuit for '{func.__name__}' is half-open. Attempting execution.")
                    circuit_open = False
                else:
                    logger.warning(
                        f"Circuit for '{func.__name__}' is OPEN. Returning fallback. "
                        f"Cooldown remaining: {int(cooldown_seconds - (time.time() - last_failure_time))}s"
                    )
                    return get_fallback(*args)

            try:
                result = await func(*args, **kwargs)
                failures = 0
                return result
            except exceptions as e:
                failures += 1
                last_failure_time = time.time()
                logger.error(
                    f"Resilience hit in async service method '{func.__name__}' "
                    f"(Failures: {failures}/{max_failures}): {e}",
                    exc_info=True,
                )
                if failures >= max_failures:
                    circuit_open = True
                    logger.critical(
                        f"Circuit for async service method '{func.__name__}' is OPEN. "
                        f"Tripping breaker for {cooldown_seconds}s."
                    )
                return get_fallback(*args)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:  # ruff: ignore[missing-type-args, missing-type-kwargs]
            nonlocal failures, last_failure_time, circuit_open

            if circuit_open:
                if time.time() - last_failure_time > cooldown_seconds:
                    logger.info(f"Circuit for '{func.__name__}' is half-open. Attempting execution.")
                    circuit_open = False
                else:
                    logger.warning(
                        f"Circuit for '{func.__name__}' is OPEN. Returning fallback. "
                        f"Cooldown remaining: {int(cooldown_seconds - (time.time() - last_failure_time))}s"
                    )
                    return get_fallback(*args)

            try:
                result = func(*args, **kwargs)
                failures = 0
                return result
            except exceptions as e:
                failures += 1
                last_failure_time = time.time()
                logger.error(
                    f"Resilience hit in service method '{func.__name__}' "
                    f"(Failures: {failures}/{max_failures}): {e}",
                    exc_info=True,
                )
                if failures >= max_failures:
                    circuit_open = True
                    logger.critical(
                        f"Circuit for service method '{func.__name__}' is OPEN. "
                        f"Tripping breaker for {cooldown_seconds}s."
                    )
                return get_fallback(*args)

        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper

    return decorator
