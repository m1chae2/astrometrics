"""Purpose: Fixtures, skip rule and update option of the golden suite.

Description: Finds the M 13 sample frames and skips the whole suite when
they are Git LFS pointer files (a checkout without ``git lfs pull``). It
also holds `GoldenStore`, which compares measured numbers with
``golden_values.json``. With ``--update-golden --golden-why "<reason>"`` the
store instead rewrites every pin that moved, so a reviewer sees in the diff
exactly which numbers changed and why.
"""

import datetime
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.test.golden import measurements

GOLDEN_JSON = Path(__file__).with_name("golden_values.json")
"""The file that holds every pinned number."""

_STORE_KEY = pytest.StashKey["GoldenStore"]()
"""Where the store is kept on the pytest config, for the end-of-run summary."""

_JSON_COMMENT = (
    "Golden values measured on the real M 13 sample frames (Git LFS). Each pin has a value, a "
    "tolerance, a tolerance_type ('absolute' or 'relative') and a 'why' naming the change that last "
    "set it. Do not edit by hand: run 'pytest astrometricslib/test/golden --update-golden "
    '--golden-why "<the change>"\' and review the diff. See README.md.'
)
"""Text written at the top of the JSON file (JSON has no comments)."""

_SECTION_COMMENTS = {
    "photometry_pre_S2": (
        "Aperture photometry with every aperture centred on a whole pixel, which is what the pipeline "
        "did before item S2 (sub-pixel apertures) in REVIEW_ACTION_PLAN.md. S2 now centres apertures on "
        "the exact position, so this block is reproduced by rounding the position before the call. The "
        "fluxes are EXPECTED TO MOVE if the aperture sum or the background ring changes, and the block "
        "can be dropped once nobody needs the old numbers."
    ),
    "photometry_sub_pixel": (
        "Aperture photometry with every aperture centred on the exact detected position (item S2). The "
        "difference from photometry_pre_S2 is the effect of S2 on these ten stars."
    ),
    "photometry_sequence": (
        "The committed photometry worker run over the five luminance lights. About 60 stars (detections "
        "ranked 10 to 70 of the first light) are measured. Detections ranked 50 to 100 line the lights up. "
        "Shifts are for lights 020 to 023 against 019. The scatter is the median coefficient of variation "
        "of the unsaturated stars, in percent."
    ),
    "source_detection": (
        "Source detection on one frame. The detector draws a random subsample for its background "
        "only when the 2-D background map fails; on these 3008 x 3008 frames the map works, so the "
        "numbers are repeatable (checked twice on 2026-10-10)."
    ),
}
"""Notes stored with a whole section of the JSON file."""


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the options that regenerate the pinned values.

    Parameters
    ----------
    parser : `pytest.Parser`
        The pytest option parser.
    """
    group = parser.getgroup("golden", "golden-data regression")
    group.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Rewrite golden_values.json from the current code instead of comparing with it.",
    )
    group.addoption(
        "--golden-why",
        action="store",
        default=None,
        help="Reason stored with every pin that --update-golden changes (required with it).",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Create the store and refuse ``--update-golden`` without a reason.

    Parameters
    ----------
    config : `pytest.Config`
        The pytest configuration.

    Raises
    ------
    pytest.UsageError
        If ``--update-golden`` is given without ``--golden-why``.
    """
    updating = bool(config.getoption("--update-golden", default=False))
    why = config.getoption("--golden-why", default=None)
    if updating and not why:
        raise pytest.UsageError('--update-golden needs --golden-why "<the change that moves the numbers>".')
    config.stash[_STORE_KEY] = GoldenStore(updating=updating, why=why)


class GoldenStore:
    """Compare measured numbers with the pinned ones, or rewrite the pins.

    Parameters
    ----------
    updating : `bool`
        If `True`, record new pins instead of comparing.
    why : `str` or `None`
        The reason stored with each pin that changes while updating.
    """

    def __init__(self, updating: bool, why: str | None) -> None:
        self.updating = updating
        self.why = why
        self.document: dict[str, Any] = {}
        if GOLDEN_JSON.exists():
            self.document = json.loads(GOLDEN_JSON.read_text(encoding="utf-8"))
        self.moved: list[str] = []
        self._touched = False

    def check(self, section: str, group: str, measured: dict[str, float]) -> None:
        """Compare one group of measured numbers with its pins, or update them.

        Parameters
        ----------
        section : `str`
            The block of the JSON file, such as ``"raw_frame_quality"``.
        group : `str`
            The frame (or batch) name inside the section.
        measured : `dict` [`str`, `float`]
            The numbers measured now, by name.

        Raises
        ------
        AssertionError
            If not updating and a number is missing, extra, or outside its
            tolerance. The message lists every difference with its ``why``.
        """
        if self.updating:
            self._update(section, group, measured)
            return
        pins = self.document.get(section, {}).get(group)
        if pins is None:
            raise AssertionError(f"No pins for {section}/{group}; run with --update-golden.")
        problems = []
        for name, value in measured.items():
            pin = pins.get(name)
            if pin is None:
                problems.append(f"{name}: measured {value!r} but nothing is pinned")
                continue
            if not measurements.within_tolerance(
                value, pin["value"], pin["tolerance_type"], pin["tolerance"]
            ):
                problems.append(
                    f"{name}: measured {value!r}, pinned {pin['value']!r} "
                    f"(+/- {pin['tolerance']} {pin['tolerance_type']}); last set by: {pin['why']}"
                )
        problems.extend(f"{name}: pinned but not measured" for name in pins if name not in measured)
        if problems:
            raise AssertionError(f"{section}/{group} moved:\n  " + "\n  ".join(problems))

    def _update(self, section: str, group: str, measured: dict[str, float]) -> None:
        """Record new pins for one group, touching only numbers that moved.

        A pin that still matches within its tolerance keeps its value and its
        ``why``, so the diff shows only real changes.

        Parameters
        ----------
        section : `str`
            The block of the JSON file.
        group : `str`
            The frame (or batch) name inside the section.
        measured : `dict` [`str`, `float`]
            The numbers measured now, by name.
        """
        self._touched = True
        pins = self.document.setdefault(section, {}).setdefault(group, {})
        for name in [name for name in pins if name not in measured]:
            del pins[name]
            self.moved.append(f"{section}/{group}/{name}: removed")
        for name, value in measured.items():
            tolerance_type, tolerance = measurements.tolerance_for(name)
            stored = float(f"{value:.9g}") if isinstance(value, float) else int(value)
            old = pins.get(name)
            if old is not None and measurements.within_tolerance(
                value, old["value"], old["tolerance_type"], old["tolerance"]
            ):
                continue
            pins[name] = {
                "value": stored,
                "tolerance": tolerance,
                "tolerance_type": tolerance_type,
                "why": self.why,
            }
            before = "new" if old is None else f"{old['value']!r} -> {stored!r}"
            self.moved.append(f"{section}/{group}/{name}: {before}")

    def save(self) -> None:
        """Write the JSON file, with the notes first, in a stable order."""
        document = {"_comment": _JSON_COMMENT}
        for section, groups in self.document.items():
            if section.startswith("_"):
                continue
            body = {"_comment": _SECTION_COMMENTS[section]} if section in _SECTION_COMMENTS else {}
            document[section] = {
                **body,
                **{key: value for key, value in groups.items() if not key.startswith("_")},
            }
        GOLDEN_JSON.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Write the JSON file at the end of an ``--update-golden`` run.

    Parameters
    ----------
    session : `pytest.Session`
        The finished pytest session.
    """
    store = session.config.stash.get(_STORE_KEY, None)
    if store is not None and store.updating and store._touched:
        store.save()


def pytest_terminal_summary(terminalreporter: Any, config: pytest.Config) -> None:
    """Print which pins an ``--update-golden`` run changed.

    Parameters
    ----------
    terminalreporter : `Any`
        The pytest terminal reporter.
    config : `pytest.Config`
        The pytest configuration.
    """
    store = config.stash.get(_STORE_KEY, None)
    if store is None or not store.updating:
        return
    terminalreporter.section("golden values rewritten")
    for line in store.moved or ["no pin moved"]:
        terminalreporter.line(line)
    terminalreporter.line(f"written {datetime.date.today().isoformat()} to {GOLDEN_JSON}")


@pytest.fixture(scope="session")
def golden(request: pytest.FixtureRequest) -> GoldenStore:
    """Give the tests the store of pinned numbers.

    Parameters
    ----------
    request : `pytest.FixtureRequest`
        The pytest request, used to reach the config.

    Returns
    -------
    store : `GoldenStore`
        The comparer, or the updater under ``--update-golden``.
    """
    return request.config.stash[_STORE_KEY]


@pytest.fixture(scope="session", autouse=True)
def sample_frames_present() -> None:
    """Skip the whole suite unless every sample frame is a real FITS file.

    A checkout without Git LFS holds small text pointer files in place of
    the frames, so the folder is checked for them first.
    """
    if not measurements.SAMPLE_FOLDER.is_dir():
        pytest.skip(f"Sample folder not found: {measurements.SAMPLE_FOLDER}")
    for name in measurements.REQUIRED_FRAMES:
        path = measurements.frame_path(name)
        if not path.exists():
            pytest.skip(f"Sample frame missing: {path}")
        if measurements.is_lfs_pointer(path):
            pytest.skip(f"{path.name} is a Git LFS pointer, not the frame. Run 'git lfs pull'.")


@pytest.fixture(scope="session")
def detection_frame() -> tuple[np.ndarray, fits.Header]:
    """Load the luminance light used for detection, FWHM and photometry.

    Returns
    -------
    data : `numpy.ndarray`
        The 2-D frame.
    header : `astropy.io.fits.Header`
        Its header.
    """
    return measurements.load_frame(measurements.DETECTION_FRAME)


@pytest.fixture(scope="session")
def detected_sources(detection_frame: tuple[np.ndarray, fits.Header]) -> list[dict[str, Any]]:
    """Detect the sources of the detection frame once for the whole session.

    Parameters
    ----------
    detection_frame : `tuple`
        The frame and header from the `detection_frame` fixture.

    Returns
    -------
    sources : `list` [`dict`]
        The sources, brightest first.
    """
    return measurements.detect_sources(detection_frame[0])
