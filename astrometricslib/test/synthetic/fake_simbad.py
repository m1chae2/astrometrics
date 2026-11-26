"""Purpose: A stand-in SIMBAD client for tests.

Description: SIMBAD is the online database of named sky objects. The library
reaches it through a `SimbadDriver`, which a test can replace so that it runs
offline. `FakeSimbad` is the one shared replacement: each question gets a
fixed answer or an answer worked out by a function the test gives, every
question is recorded, and a set error makes every question fail, as when the
service cannot be reached. Tests that fake a lower layer (astroquery's own
`Simbad` class) do not use it.
"""

from collections.abc import Callable
from typing import Any

from astropy.table import Table

from astrometricslib._internal.drivers.interfaces.simbad_driver import SimbadDriver

__all__ = ["FakeSimbad"]


def _empty_table(*_arguments: Any) -> Table:
    """Give a new table with no rows, the answer to a question with no match.

    Parameters
    ----------
    *_arguments : `Any`
        The question, ignored.

    Returns
    -------
    table : `astropy.table.Table`
        An empty table.
    """
    return Table()


def _no_answer(*_arguments: Any) -> None:
    """Give no answer, as SIMBAD does for a name it does not know.

    Parameters
    ----------
    *_arguments : `Any`
        The question, ignored.
    """
    return


def _answer_function(answer: Any) -> Callable[..., Any]:
    """Turn a fixed answer or a function into a function.

    Parameters
    ----------
    answer : `Any`
        A function of the question, or a fixed answer.

    Returns
    -------
    function : `Callable`
        A function that gives the answer.
    """
    if callable(answer):
        return answer
    return lambda *_arguments: answer


class FakeSimbad(SimbadDriver):
    """A SIMBAD client with set answers that records every question.

    Attributes
    ----------
    region_calls : `list` [`tuple` [`Any`, `str`]]
        The position and radius of each region question.
    object_calls : `list` [`str`]
        The name of each object question.
    tap_calls : `list` [`str`]
        The text of each table (TAP) question, written in ADQL, the query
        language of astronomy archives.
    failure : `Exception` or `None`
        An error every question raises (after it is recorded), or `None`.
        A test can set it at any time, for example to take the service down
        halfway through.
    """

    def __init__(
        self,
        *,
        region: Any = _empty_table,
        objects: Any = _no_answer,
        tap: Any = _empty_table,
        failure: Exception | None = None,
    ) -> None:
        """Keep the answers to give.

        Parameters
        ----------
        region : `Any`, optional
            The answer to a region question, or a function of
            ``(coordinates, radius)`` that gives it. By default an empty
            table.
        objects : `Any`, optional
            The answer to an object question, or a function of the name that
            gives it. By default `None` (the name is not known).
        tap : `Any`, optional
            The answer to a table question, or a function of the query text
            that gives it. By default an empty table.
        failure : `Exception`, optional
            An error every question raises instead of answering.
        """
        self._region = _answer_function(region)
        self._objects = _answer_function(objects)
        self._tap = _answer_function(tap)
        self.failure = failure
        self.region_calls: list[tuple[Any, str]] = []
        self.object_calls: list[str] = []
        self.tap_calls: list[str] = []

    @property
    def call_count(self) -> int:
        """The number of questions asked, of every kind.

        Returns
        -------
        count : `int`
            The number of questions.
        """
        return len(self.region_calls) + len(self.object_calls) + len(self.tap_calls)

    def _fail_if_set(self) -> None:
        """Raise the error in `failure`, if one is set."""
        if self.failure is not None:
            raise self.failure

    def query_region(
        self,
        coordinates: Any,
        radius: str,
        *,
        votable_fields: tuple[str, ...] = (),
        row_limit: int | None = None,
    ) -> Any:
        """Record a region question and answer it.

        Parameters
        ----------
        coordinates : `Any`
            The center of the region.
        radius : `str`
            The radius of the region.
        votable_fields : `tuple` [`str`], optional
            Ignored.
        row_limit : `int`, optional
            Ignored.

        Returns
        -------
        answer : `Any`
            The set answer.
        """
        self.region_calls.append((coordinates, radius))
        self._fail_if_set()
        return self._region(coordinates, radius)

    def query_object(self, object_name: str, *, votable_fields: tuple[str, ...] = ()) -> Any:
        """Record an object question and answer it.

        Parameters
        ----------
        object_name : `str`
            The name asked for.
        votable_fields : `tuple` [`str`], optional
            Ignored.

        Returns
        -------
        answer : `Any`
            The set answer.
        """
        self.object_calls.append(object_name)
        self._fail_if_set()
        return self._objects(object_name)

    def query_tap(self, adql_query: str) -> Any:
        """Record a table question and answer it.

        Parameters
        ----------
        adql_query : `str`
            The query text.

        Returns
        -------
        answer : `Any`
            The set answer.
        """
        self.tap_calls.append(adql_query)
        self._fail_if_set()
        return self._tap(adql_query)
