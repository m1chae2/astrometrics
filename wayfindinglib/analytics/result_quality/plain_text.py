"""Purpose: Small helpers that put quality findings into everyday words.

Description: Every quality rule has a technical message (with the numbers,
for scripts and the AI companion) and a plain message (for the observer).
The plain message is one or two short sentences in everyday words. This
module holds the few helpers the rules share when they write it:

* `spectral_type_phrase` names a star's spectral type as a hedged phrase,
  such as "an orange K-type star", because a type from a spectrum is a best
  match and not a certainty.
* `exposure_phrases` writes the exposure of a capture setting as a phrase
  that reads well mid-sentence ("the 90 s frames", or "these frames" when the
  exposure is not known).
* `plain_missing_data` writes the plain message of a rule that could not be
  checked because data is missing.
* `format_plain` fills a rule's plain template, and stops with a clear error
  when a field is missing, so a wrong template is found by a test and not
  by a user.

The module imports only the rule model.
"""

from collections.abc import Mapping
from typing import Any

from wayfindinglib.models.quality.rule import QualityRule

__all__ = [
    "SPECTRAL_CLASS_COLORS",
    "exposure_phrases",
    "format_plain",
    "plain_missing_data",
    "spectral_type_phrase",
]

SPECTRAL_CLASS_COLORS: dict[str, str] = {
    "O": "blue",
    "B": "blue-white",
    "A": "white",
    "F": "yellow-white",
    "G": "yellow",
    "K": "orange",
    "M": "red",
}
"""The color a star of each spectral class shows to the eye.

These are the usual textbook colors of the Harvard classes. They only help
the reader picture the star; the class letter is always given too.
"""


def spectral_type_phrase(spectral_type: str) -> str:
    """Name a spectral type in words, as a hedged description of a star.

    Parameters
    ----------
    spectral_type : `str`
        The type the classifier gave, such as ``"K3V"`` or ``"A0"``.

    Returns
    -------
    phrase : `str`
        For example ``"an orange K-type star"``. A type whose first letter is
        not a known class gives ``"a star of type X"``.
    """
    letter = spectral_type.strip()[:1].upper()
    color = SPECTRAL_CLASS_COLORS.get(letter)
    if color is None:
        return f"a star of type {spectral_type.strip() or 'unknown'}"
    first_word = color.split("-")[0]
    article = "an" if first_word[0] in "aeiou" else "a"
    return f"{article} {color} {letter}-type star"


def exposure_phrases(exposure_seconds: float | None) -> dict[str, str]:
    """Write a capture setting's exposure as phrases for plain templates.

    Parameters
    ----------
    exposure_seconds : `float` or `None`
        The exposure of one frame, in seconds, or ``None`` when the setting
        does not say.

    Returns
    -------
    phrases : `dict` [`str`, `str`]
        ``exposure_phrase`` (``"the 90 s frames"`` or ``"these frames"``) and
        ``exposure_clause`` (``"90 s "`` or an empty string, to put before the
        word "frames").
    """
    if exposure_seconds is None:
        return {"exposure_phrase": "these frames", "exposure_clause": ""}
    return {
        "exposure_phrase": f"the {exposure_seconds:g} s frames",
        "exposure_clause": f"{exposure_seconds:g} s ",
    }


def format_plain(rule: QualityRule, values: Mapping[str, Any]) -> str:
    """Fill a rule's plain template.

    Parameters
    ----------
    rule : `QualityRule`
        The rule.
    values : `Mapping` [`str`, `Any`]
        The fields of the template. Extra fields are ignored.

    Returns
    -------
    plain_message : `str`
        The plain message.

    Notes
    -----
    A template that needs a field ``values`` does not hold raises a
    ``KeyError``. That is a bug in the code that builds the statement, and a
    test finds it.
    """
    return rule.plain_wording.format_map(dict(values))


def plain_missing_data(rule_name: str) -> str:
    """Write the plain message of a rule that could not be checked.

    Parameters
    ----------
    rule_name : `str`
        The rule's name, such as ``"calibration_gap"``.

    Returns
    -------
    plain_message : `str`
        A sentence that says the check could not be made and where to read
        what is missing.
    """
    words = rule_name.replace("_", " ")
    return (
        f"We could not check '{words}' yet because some measurements are missing. "
        "The reason says what to measure."
    )
