"""Purpose: Shared checks for the plain wording of quality statements.

Description: Every quality rule writes a technical message (with the numbers)
and a plain message (everyday words for the observer). Three test files check
the plain side: the template conformance test,
the tests of the rule modules (which check each statement they build) and the
live-status tests. They share this module, so all of them hold the plain text
to the same rules:

* it is not empty;
* it uses no statistics term from `BANNED_TERMS`;
* it has at most `MAXIMUM_SENTENCES` sentences;
* every number in it also appears in the technical message, so the plain text
  never states a value the technical text does not.

This module imports only the models.
"""

import re

from wayfindinglib.models.quality.statements import TargetStatement

__all__ = [
    "BANNED_TERMS",
    "FORECAST_WORDS",
    "MAXIMUM_SENTENCES",
    "assert_plain_message_is_sound",
    "banned_terms_in",
    "sentences_of",
]

MAXIMUM_SENTENCES = 2
"""The most sentences a plain message may have."""

BANNED_TERMS: tuple[str, ...] = (
    "rms",
    "sigma",
    "regime",
    "bootstrap",
    "chi-square",
    "chi square",
    "variance",
    "standard deviation",
    "snr",
    "signal-to-noise",
    "p-value",
    "percentile",
    "median",
    "moffat",
    "fwhm",
    "poisson",
    "gaussian",
    "covariance",
    "confidence interval",
    "statistically",
    "z-score",
    "periodogram",
    "quarantine",
    "apertures",
)
"""Terms a plain message must not use (matched as whole words, any case)."""

FORECAST_WORDS: tuple[str, ...] = (
    "we expect",
    "roughly",
    "about",
    "likely",
    "may ",
    "probably",
    "looks like",
    "estimated",
    "forecast",
)
"""Words that show a statement is a forecast or a best guess."""


def banned_terms_in(text: str) -> list[str]:
    """List the banned statistics terms a text uses.

    Parameters
    ----------
    text : `str`
        A plain message or template. The names of placeholders in braces are
        not part of the text.

    Returns
    -------
    terms : `list` [`str`]
        The banned terms found, in the order of `BANNED_TERMS`.
    """
    lowered = re.sub(r"\{[^{}]*\}", " ", text).lower()
    return [term for term in BANNED_TERMS if re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", lowered)]


def sentences_of(text: str) -> list[str]:
    """Split a text into sentences.

    A full stop followed by a space ends a sentence, so a number such as
    ``0.05`` does not.

    Parameters
    ----------
    text : `str`
        A plain message or template.

    Returns
    -------
    sentences : `list` [`str`]
        The sentences, without empty ones.
    """
    return [part for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part]


def _numbers_in(text: str) -> list[str]:
    """List the numbers written in a text, as written.

    Returns
    -------
    numbers : `list` [`str`]
        Each run of digits with an optional decimal part, such as ``"0.05"``.
    """
    return re.findall(r"\d+(?:[.,]\d+)?", text)


def assert_plain_message_is_sound(statement: TargetStatement) -> None:
    """Check the plain message of a built statement.

    Parameters
    ----------
    statement : `TargetStatement`
        A statement a rule built.

    Raises
    ------
    AssertionError
        If the plain message is empty, uses a banned term, has too many
        sentences, or holds a number the technical message does not.
    """
    plain = statement.plain_message
    rule = statement.rule
    if not plain.strip():
        raise AssertionError(f"{rule}: the plain message is empty")
    found = banned_terms_in(plain)
    if found:
        raise AssertionError(f"{rule}: banned terms {found} in {plain!r}")
    if len(sentences_of(plain)) > MAXIMUM_SENTENCES:
        raise AssertionError(f"{rule}: too many sentences in {plain!r}")
    technical = statement.message.replace(",", "")
    for number in _numbers_in(plain):
        if number.replace(",", "") not in technical:
            raise AssertionError(f"{rule}: the number {number} of {plain!r} is not in {statement.message!r}")
