"""How the spectral-class gap changes with signal-to-noise.

The table is made by degrading good spectra with added noise.

The classifier (`spectral_classifier.classify_spectral_type`) compares a
spectrum with reference spectra and reports how much better the best one fits
than the runner-up (``rms_gap_to_second_best``) and than the nearest reference
of another class letter (``rms_gap_to_next_class``). A gap under
`stellar_source.AMBIGUOUS_RMS_GAP` is called ambiguous. A reader who wants a
subtype needs to know how much signal-to-noise gives a gap that large.

This module answers by injection. It takes stored spectra that already have
high signal-to-noise and a catalog type, adds white noise until each reaches a
stated signal-to-noise (as the project's own estimator,
`spectrum_signal.estimate_spectrum_signal_to_noise`, reads it), classifies the
degraded copy with the project's own classifier, and records the gaps it
reaches. Nothing is fitted: the classifier and its threshold are the project's.
Two cautions follow from that.

* The result is held out by star field. No spectrum tunes anything, but spectra
  from one field share the night, the instrument state and often the same
  stars' neighbors, so the uncertainty is found by resampling fields, not
  spectra, and the range with each field left out is reported.
* The noise added is white and Gaussian. Real spectra also carry a
  correlated error (the instrument response, sky residue) that does not
  shrink with exposure. The table says what noise alone does; it cannot say
  more.

When even the undegraded spectra do not reach a gap above the threshold in at
least half of the cases, the verdict is ``CONFIDENCE_LIMITED``: the
classifier's own gap, a measure of how sure it is, stays under the threshold
however little noise is added. That says how confident the classifier can be
at this resolution. It does not say the classes cannot be told apart.

Each row also carries the share of copies whose class letter equals the
spectrum's own undegraded class (this isolates what the noise did, apart
from any disagreement with the catalog), and a 16 to 84 percent interval from
resampling fields for each share.

This module calculates. It reads no files except a stored table, one for each
camera, in `data/` (see `class_gap_for_snr`), and it never chooses an
exposure; that belongs to the observing side (``wayfindinglib``). The table
has no caller in the pipeline.
"""

import json
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from astrometricslib.models.stellar_source import AMBIGUOUS_RMS_GAP, ladder_steps_between
from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import classify_spectral_type
from astrometricslib.pipelines.spectroscopy.processing.spectrum_signal import (
    estimate_spectrum_signal_to_noise,
)

__all__ = [
    "CAMERA_NAME",
    "CLASS_GAP_DEFINITION_VERSION",
    "CLASS_GAP_SNR_ESTIMATOR",
    "CONFIDENCE_LIMITED",
    "DEFAULT_SNR_GRID",
    "REACHED_SHARE",
    "ClassGapRow",
    "ClassGapTable",
    "SpectrumSample",
    "class_gap_for_snr",
    "class_gap_table",
    "class_gap_table_path",
    "classify_sample",
    "degrade_to_snr",
    "reachable_snr",
]

# The camera the shipped table was made for. The spectra it degrades were
# taken with this camera and grating, so its resolution element and noise are
# this camera's; a table for another camera is a separate file.
CAMERA_NAME = "ZWO ASI 533MM Pro"

# Changes whenever the way the table is made changes in a way that alters its
# numbers (the estimator, the noise model, the shares). A stored table with
# another number is refused by `class_gap_for_snr`.
CLASS_GAP_DEFINITION_VERSION = 1

# The estimator that reads the signal-to-noise the table is indexed by. A
# stored table made with another estimator is refused.
CLASS_GAP_SNR_ESTIMATOR = "spectrum_signal.estimate_spectrum_signal_to_noise"

_DATA_FOLDER = Path(__file__).resolve().parents[1] / "data"


def class_gap_table_path(camera_name: str = CAMERA_NAME) -> Path:
    """Name the stored table of one camera.

    Parameters
    ----------
    camera_name : `str`, optional
        The camera, for example ``"ZWO ASI 533MM Pro"``.

    Returns
    -------
    path : `pathlib.Path`
        ``data/class_gap_against_snr_<camera>.json`` with the camera name in
        lower case and every run of other characters turned into one
        underscore (``zwo_asi_533mm_pro``).
    """
    suffix = re.sub(r"[^a-z0-9]+", "_", camera_name.lower()).strip("_")
    return _DATA_FOLDER / f"class_gap_against_snr_{suffix}.json"


# The signal-to-noise values the table is made at, per resolution element, by
# the project's estimator. They run from the lowest at which the pipeline still
# classifies (`MINIMUM_SPECTRUM_SIGNAL_TO_NOISE` is 1.5) up to the lowest
# signal-to-noise of the spectra used (30), so every spectrum can be degraded
# to every grid value. A spectrum cannot be improved, so none is used above its
# own value.
DEFAULT_SNR_GRID: tuple[float, ...] = (3.0, 5.0, 8.0, 12.0, 20.0, 30.0)

# A level (class or subtype) counts as reached at a signal-to-noise when at
# least this share of the spectra reach a gap of `AMBIGUOUS_RMS_GAP` or more.
# One half means the typical spectrum separates; chosen by judgment, not tuned
# (SPE-D25 in the validation register).
REACHED_SHARE = 0.5

CONFIDENCE_LIMITED = "confidence-limited at this resolution"

# How close the injected noise must put the estimate to the target, as a
# fraction of the target, before a degraded copy is accepted.
_SNR_TOLERANCE = 0.03
_MAXIMUM_BISECTION_STEPS = 40


@dataclass(frozen=True)
class SpectrumSample:
    """One stored spectrum to degrade.

    Parameters
    ----------
    field_id : `str`
        The star field (target) the spectrum came from. The uncertainty is
        found by resampling these.
    star_id : `str`
        The star's id.
    wavelength_angstrom : `numpy.ndarray`
        The wavelengths, in Angstroms.
    intensity : `numpy.ndarray`
        The response-corrected brightness the classifier compares.
    resolution_element_angstrom : `float`
        The spectrum's own resolution element, in Angstroms.
    catalog_type : `str`
        The catalog spectral type (``"F5Ib"``).
    native_snr : `float`
        The spectrum's own signal-to-noise by the project's estimator.
    """

    field_id: str
    star_id: str
    wavelength_angstrom: np.ndarray
    intensity: np.ndarray
    resolution_element_angstrom: float
    catalog_type: str
    native_snr: float


@dataclass(frozen=True)
class SampleOutcome:
    """What the classifier said about one spectrum at one signal-to-noise.

    Parameters
    ----------
    gap_to_next_class : `float` or `None`
        ``rms_gap_to_next_class`` of the result.
    gap_to_second_best : `float` or `None`
        ``rms_gap_to_second_best`` of the result.
    class_matches_catalog : `bool` or `None`
        Whether the measured class letter equals the catalog's.
    steps_from_catalog : `float` or `None`
        The distance of the measured type from the catalog type, in subtype
        steps (absolute).
    own_class : `str` or `None`
        The measured class letter, or `None` when the classifier gave none.
    class_matches_undegraded : `bool` or `None`
        Whether the measured class letter equals the class letter of the same
        spectrum before any noise was added. `None` for the undegraded
        spectrum itself or when either letter is missing.
    """

    gap_to_next_class: float | None
    gap_to_second_best: float | None
    class_matches_catalog: bool | None
    steps_from_catalog: float | None
    own_class: str | None = None
    class_matches_undegraded: bool | None = None


def classify_sample(
    sample: SpectrumSample,
    intensity: np.ndarray,
    classifier: Callable[..., dict] = classify_spectral_type,
    undegraded_class: str | None = None,
) -> SampleOutcome:
    """Classify a spectrum, or a noisy copy, with the project's classifier.

    Parameters
    ----------
    sample : `SpectrumSample`
        The spectrum, for its wavelengths, resolution element and catalog type.
    intensity : `numpy.ndarray`
        The brightness to classify (the sample's own, or a noisy copy).
    classifier : `Callable`, optional
        `classify_spectral_type`, or a stand-in with its signature, for tests.
    undegraded_class : `str`, optional
        The class letter this spectrum gets before any noise is added. Pass
        it for a noisy copy so the outcome says whether the noise changed the
        class.

    Returns
    -------
    outcome : `SampleOutcome`
        The two gaps and the agreement with the catalog and with the
        undegraded class.
    """
    result = classifier(sample.wavelength_angstrom, intensity, sample.resolution_element_angstrom)
    own_type = str(result.get("spectral_type") or "")
    own_class = own_type[:1].upper()
    catalog_class = sample.catalog_type[:1].upper()
    steps = ladder_steps_between(own_type, sample.catalog_type)
    known_class = own_class if own_class and own_class != "U" else None
    return SampleOutcome(
        gap_to_next_class=_optional_float(result.get("rms_gap_to_next_class")),
        gap_to_second_best=_optional_float(result.get("rms_gap_to_second_best")),
        class_matches_catalog=(own_class == catalog_class) if known_class else None,
        steps_from_catalog=None if steps is None else abs(float(steps)),
        own_class=known_class,
        class_matches_undegraded=(
            (known_class == undegraded_class) if known_class and undegraded_class else None
        ),
    )


def _optional_float(value: object) -> float | None:
    """Read a classifier output as a finite float, or `None`.

    Returns
    -------
    number : `float` or `None`
        The value, or `None` when it is missing or not finite.
    """
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def degrade_to_snr(
    sample: SpectrumSample, target_snr: float, unit_noise: np.ndarray
) -> tuple[np.ndarray, float] | None:
    """Add white noise to a spectrum until it reads a stated signal-to-noise.

    The noise is ``sigma * unit_noise``, with ``sigma`` found by bisection so
    that the project's estimator reads `target_snr` to within 3 percent. The
    estimator reads the spectrum's own scatter, so the added noise joins the
    scatter that was already there.

    Parameters
    ----------
    sample : `SpectrumSample`
        The spectrum to degrade.
    target_snr : `float`
        The signal-to-noise to reach, below the sample's own.
    unit_noise : `numpy.ndarray`
        Standard normal draws, one per sample of the spectrum. The same draws
        are scaled for every step of the bisection, so the reading falls
        smoothly with ``sigma``.

    Returns
    -------
    degraded : `tuple` [`numpy.ndarray`, `float`] or `None`
        The noisy brightness and the signal-to-noise it reads. `None` when
        the target is not below the sample's own, or no noise level reaches it.
    """
    if not target_snr < sample.native_snr:
        return None
    # A stored spectrum can hold NaN where the response correction had no
    # value, so the scale is taken over the finite samples only.
    finite = np.abs(sample.intensity[np.isfinite(sample.intensity)])
    scale = float(np.median(finite)) if finite.size else 0.0
    if not scale > 0:
        return None
    low, high = 0.0, scale
    reading_high = _read(sample, high * unit_noise)
    steps = 0
    while reading_high > target_snr and steps < _MAXIMUM_BISECTION_STEPS:
        high *= 2.0
        reading_high = _read(sample, high * unit_noise)
        steps += 1
    if reading_high > target_snr:
        return None
    for _ in range(_MAXIMUM_BISECTION_STEPS):
        middle = 0.5 * (low + high)
        reading = _read(sample, middle * unit_noise)
        if abs(reading - target_snr) <= _SNR_TOLERANCE * target_snr:
            return sample.intensity + middle * unit_noise, reading
        if reading > target_snr:
            low = middle
        else:
            high = middle
    return None


def _read(sample: SpectrumSample, noise: np.ndarray) -> float:
    """Read the signal-to-noise of a spectrum with noise added.

    Returns
    -------
    snr : `float`
        The project's estimate; 0 when it cannot judge.
    """
    reading = estimate_spectrum_signal_to_noise(
        sample.wavelength_angstrom, sample.intensity + noise, sample.resolution_element_angstrom
    )
    return 0.0 if reading is None or not math.isfinite(reading) else float(reading)


@dataclass(frozen=True)
class ClassGapRow:
    """The class gap reached at one signal-to-noise.

    Parameters
    ----------
    snr : `float`
        The signal-to-noise per resolution element the spectra were degraded
        to. ``None`` stands for the undegraded spectra in
        `ClassGapTable.native`.
    spectrum_count, field_count : `int`
        The spectra and the fields behind the row.
    median_gap_to_next_class, median_gap_to_second_best : `float` or `None`
        The median gaps over all the spectra and noise draws.
    gap_to_next_class_low, gap_to_next_class_high : `float` or `None`
        The 16th and 84th percentiles of the median over field resamples.
    class_separated_share, subtype_separated_share : `float` or `None`
        The share of cases whose gap is at least `AMBIGUOUS_RMS_GAP`.
    class_agreement_share : `float` or `None`
        The share whose class letter equals the catalog's.
    median_steps_from_catalog : `float` or `None`
        The median distance of the measured type from the catalog type, in
        subtype steps.
    leave_one_field_out_range : `tuple` [`float`, `float`] or `None`
        The smallest and largest median ``gap_to_next_class`` with each field
        left out in turn.
    undegraded_class_agreement_share : `float` or `None`
        The share of copies whose class letter equals the class letter of the
        same spectrum before noise. It isolates what the noise did, with no
        part of any disagreement with the catalog. `None` for the undegraded
        row.
    share_intervals : `dict` [`str`, `tuple` [`float`, `float`] or `None`]
        For each share (by field name), the 16th and 84th percentiles of the
        share over resamples of the fields.
    """

    snr: float | None
    spectrum_count: int
    field_count: int
    median_gap_to_next_class: float | None
    median_gap_to_second_best: float | None
    gap_to_next_class_low: float | None
    gap_to_next_class_high: float | None
    class_separated_share: float | None
    subtype_separated_share: float | None
    class_agreement_share: float | None
    median_steps_from_catalog: float | None
    leave_one_field_out_range: tuple[float, float] | None
    undegraded_class_agreement_share: float | None = None
    share_intervals: dict[str, tuple[float, float] | None] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        """Give the row as JSON-ready values.

        Returns
        -------
        record : `dict`
            The fields by name; the range as a two-item list.
        """
        record = dict(self.__dict__)
        record["leave_one_field_out_range"] = (
            None if self.leave_one_field_out_range is None else list(self.leave_one_field_out_range)
        )
        record["share_intervals"] = {
            name: None if interval is None else list(interval)
            for name, interval in self.share_intervals.items()
        }
        return record


@dataclass(frozen=True)
class ClassGapTable:
    """The class gap against signal-to-noise, and what it says.

    Parameters
    ----------
    native : `ClassGapRow`
        The undegraded spectra (``snr`` is `None`).
    rows : `tuple` [`ClassGapRow`, ...]
        One row for each grid signal-to-noise, lowest first.
    class_reachable_snr, subtype_reachable_snr : `float` or `str`
        The lowest grid signal-to-noise from which every higher grid value
        also reaches `REACHED_SHARE`, or `CONFIDENCE_LIMITED` when none does.
    native_agreement_by_field : `dict` [`str`, `dict`]
        For each field, the number of undegraded spectra (``count``) and the
        share whose class letter equals the catalog's
        (``class_agreement_share``). It shows whether the native agreement is
        the same in every field or carried by a few.
    """

    native: ClassGapRow
    rows: tuple[ClassGapRow, ...]
    class_reachable_snr: float | str
    subtype_reachable_snr: float | str
    native_agreement_by_field: dict[str, dict[str, float]] = field(default_factory=dict)


def reachable_snr(
    rows: Sequence[ClassGapRow], share_of: Callable[[ClassGapRow], float | None]
) -> float | str:
    """Find the lowest signal-to-noise from which a level stays separated.

    Parameters
    ----------
    rows : `Sequence` [`ClassGapRow`]
        The grid rows, lowest signal-to-noise first.
    share_of : `Callable`
        Reads the share of separated cases from a row.

    Returns
    -------
    snr : `float` or `str`
        The lowest signal-to-noise at which that row and every higher row have
        a share of at least `REACHED_SHARE`; `CONFIDENCE_LIMITED` when not even
        the highest row does.
    """
    best: float | str = CONFIDENCE_LIMITED
    for row in sorted(rows, key=lambda item: -(item.snr or 0.0)):
        share = share_of(row)
        if share is None or share < REACHED_SHARE:
            break
        best = float(row.snr) if row.snr is not None else best
    return best


# The shares a row reports, by the name of the `ClassGapRow` field. Each reads
# one outcome and gives True, False, or None where the outcome has no answer.
_SHARE_RULES: dict[str, Callable[[SampleOutcome], bool | None]] = {
    "class_separated_share": lambda item: (
        None if item.gap_to_next_class is None else item.gap_to_next_class >= AMBIGUOUS_RMS_GAP
    ),
    "subtype_separated_share": lambda item: (
        None if item.gap_to_second_best is None else item.gap_to_second_best >= AMBIGUOUS_RMS_GAP
    ),
    "class_agreement_share": lambda item: item.class_matches_catalog,
    "undegraded_class_agreement_share": lambda item: item.class_matches_undegraded,
}


def _share(items: list[SampleOutcome], rule: Callable[[SampleOutcome], bool | None]) -> float | None:
    """Find the share of outcomes for which a rule is true.

    Parameters
    ----------
    items : `list` [`SampleOutcome`]
        The outcomes.
    rule : `Callable`
        Gives True, False, or None where the outcome has no answer.

    Returns
    -------
    share : `float` or `None`
        The share among the outcomes that have an answer; `None` if none do.
    """
    found = [value for value in (rule(item) for item in items) if value is not None]
    return float(np.mean(found)) if found else None


def _row(
    snr: float | None,
    outcomes_by_field: dict[str, list[SampleOutcome]],
    bootstrap_count: int,
    rng: np.random.Generator,
) -> ClassGapRow:
    """Summarize the outcomes at one signal-to-noise.

    Parameters
    ----------
    snr : `float` or `None`
        The signal-to-noise, or `None` for the undegraded spectra.
    outcomes_by_field : `dict` [`str`, `list` [`SampleOutcome`]]
        The outcomes of every spectrum and noise draw, by field.
    bootstrap_count : `int`
        How many field resamples give the percentile ranges.
    rng : `numpy.random.Generator`
        The random source for the resamples.

    Returns
    -------
    row : `ClassGapRow`
        The summary.
    """
    fields = sorted(outcomes_by_field)
    everything = [outcome for field_name in fields for outcome in outcomes_by_field[field_name]]

    def median_of(items: list[SampleOutcome], key: Callable[[SampleOutcome], float | None]) -> float | None:
        found = [value for value in (key(item) for item in items) if value is not None]
        return float(np.median(found)) if found else None

    def next_class(item: SampleOutcome) -> float | None:
        return item.gap_to_next_class

    def second_best(item: SampleOutcome) -> float | None:
        return item.gap_to_second_best

    resampled: list[float] = []
    resampled_shares: dict[str, list[float]] = {name: [] for name in _SHARE_RULES}
    for _ in range(bootstrap_count):
        chosen = rng.integers(0, len(fields), len(fields))
        pooled = [outcome for index in chosen for outcome in outcomes_by_field[fields[index]]]
        median = median_of(pooled, next_class)
        if median is not None:
            resampled.append(median)
        for name, rule in _SHARE_RULES.items():
            value = _share(pooled, rule)
            if value is not None:
                resampled_shares[name].append(value)
    leave_out: list[float] = []
    if len(fields) > 1:
        for left_out in fields:
            pooled = [
                outcome
                for field_name in fields
                if field_name != left_out
                for outcome in outcomes_by_field[field_name]
            ]
            median = median_of(pooled, next_class)
            if median is not None:
                leave_out.append(median)
    shares = {name: _share(everything, rule) for name, rule in _SHARE_RULES.items()}
    intervals = {
        name: (float(np.percentile(values, 16)), float(np.percentile(values, 84))) if values else None
        for name, values in resampled_shares.items()
    }
    return ClassGapRow(
        snr=snr,
        spectrum_count=len(everything),
        field_count=len(fields),
        median_gap_to_next_class=median_of(everything, next_class),
        median_gap_to_second_best=median_of(everything, second_best),
        gap_to_next_class_low=float(np.percentile(resampled, 16)) if resampled else None,
        gap_to_next_class_high=float(np.percentile(resampled, 84)) if resampled else None,
        class_separated_share=shares["class_separated_share"],
        subtype_separated_share=shares["subtype_separated_share"],
        class_agreement_share=shares["class_agreement_share"],
        median_steps_from_catalog=median_of(everything, lambda item: item.steps_from_catalog),
        leave_one_field_out_range=(min(leave_out), max(leave_out)) if leave_out else None,
        undegraded_class_agreement_share=shares["undegraded_class_agreement_share"],
        share_intervals=intervals,
    )


def class_gap_table(
    samples: Sequence[SpectrumSample],
    snr_grid: Sequence[float] = DEFAULT_SNR_GRID,
    draws_per_spectrum: int = 5,
    bootstrap_count: int = 400,
    seed: int = 20261024,
    classifier: Callable[..., dict] = classify_spectral_type,
) -> ClassGapTable:
    """Build the class gap against signal-to-noise from stored spectra.

    Parameters
    ----------
    samples : `Sequence` [`SpectrumSample`]
        The high-signal-to-noise spectra with a catalog type.
    snr_grid : `Sequence` [`float`], optional
        The signal-to-noise values to degrade to.
    draws_per_spectrum : `int`, optional
        How many independent noise draws each spectrum gets at each value.
    bootstrap_count : `int`, optional
        How many field resamples give the percentile range.
    seed : `int`, optional
        The random seed, so the table can be rebuilt exactly.
    classifier : `Callable`, optional
        `classify_spectral_type`, or a stand-in for tests.

    Returns
    -------
    table : `ClassGapTable`
        The undegraded row, one row for each grid value, and the lowest
        signal-to-noise at which the class and the subtype stay separated.
    """
    rng = np.random.default_rng(seed)
    native: dict[str, list[SampleOutcome]] = {}
    undegraded_class: dict[int, str | None] = {}
    for index, sample in enumerate(samples):
        outcome = classify_sample(sample, sample.intensity, classifier)
        undegraded_class[index] = outcome.own_class
        native.setdefault(sample.field_id, []).append(outcome)
    native_row = _row(None, native, bootstrap_count, rng)
    native_by_field = {}
    for field_name, outcomes in sorted(native.items()):
        agreement = _share(outcomes, _SHARE_RULES["class_agreement_share"])
        if agreement is not None:
            native_by_field[field_name] = {"count": float(len(outcomes)), "class_agreement_share": agreement}
    rows = []
    for snr in sorted(snr_grid):
        by_field: dict[str, list[SampleOutcome]] = {}
        for index, sample in enumerate(samples):
            for _ in range(draws_per_spectrum):
                unit_noise = rng.standard_normal(sample.intensity.shape)
                degraded = degrade_to_snr(sample, snr, unit_noise)
                if degraded is None:
                    continue
                by_field.setdefault(sample.field_id, []).append(
                    classify_sample(sample, degraded[0], classifier, undegraded_class[index])
                )
        rows.append(_row(snr, by_field, bootstrap_count, rng))
    return ClassGapTable(
        native=native_row,
        rows=tuple(rows),
        class_reachable_snr=reachable_snr(rows, lambda row: row.class_separated_share),
        subtype_reachable_snr=reachable_snr(rows, lambda row: row.subtype_separated_share),
        native_agreement_by_field=native_by_field,
    )


def class_gap_for_snr(
    snr: float, camera_name: str = CAMERA_NAME, table_path: Path | None = None
) -> tuple[dict[str, object] | None, str]:
    """Look up the stored class gap for a spectrum's signal-to-noise.

    The lookup refuses a table it cannot trust: a missing file, a file that
    does not read, a table made for another camera, with another definition
    version, another ambiguity threshold or another signal-to-noise
    estimator than the code uses now. It gives `None` and the reason, never a
    default. It is a lookup only: nothing in the pipeline calls it.

    Parameters
    ----------
    snr : `float`
        The spectrum's signal-to-noise per resolution element, by the
        project's estimator.
    camera_name : `str`, optional
        The camera the spectrum was taken with. It picks the file.
    table_path : `pathlib.Path`, optional
        The stored table, to read a file other than the camera's own.

    Returns
    -------
    entry : `dict` or `None`
        The stored row for the highest grid value at or below `snr` (never a
        better one), with the table's two verdicts under
        ``"class_reachable_snr"`` and ``"subtype_reachable_snr"``. When `snr`
        is below the lowest grid value the lowest row is returned and
        ``"below_grid"`` is true. `None` when the table is refused.
    reason : `str`
        Why the table was refused. Empty when an entry is returned.
    """
    path = table_path if table_path is not None else class_gap_table_path(camera_name)
    if not path.is_file():
        return None, f"no class-gap table is stored for {camera_name} ({path.name})"
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
        rows = sorted(table["rows"], key=lambda row: row["snr"])
        verdicts = (table["class_reachable_snr"], table["subtype_reachable_snr"])
        stored = (
            table["definition_version"],
            table["ambiguous_rms_gap"],
            table["snr_estimator"],
            table["camera"],
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        return None, f"the stored class-gap table could not be read ({type(error).__name__}: {error})"
    if not rows:
        return None, "the stored class-gap table has no rows"
    version, threshold, estimator, camera = stored
    if camera != camera_name:
        return None, f"the stored table was made for {camera}, not {camera_name}"
    if version != CLASS_GAP_DEFINITION_VERSION:
        return (
            None,
            f"the stored table has definition version {version}, "
            f"the code is at {CLASS_GAP_DEFINITION_VERSION}",
        )
    if threshold != AMBIGUOUS_RMS_GAP:
        return (
            None,
            f"the stored table used an ambiguity threshold of {threshold}, "
            f"the classifier uses {AMBIGUOUS_RMS_GAP}",
        )
    if estimator != CLASS_GAP_SNR_ESTIMATOR:
        return (
            None,
            f"the stored table used the estimator {estimator}, the code uses {CLASS_GAP_SNR_ESTIMATOR}",
        )
    at_or_below = [row for row in rows if row["snr"] <= snr]
    chosen = at_or_below[-1] if at_or_below else rows[0]
    return {
        **chosen,
        "below_grid": not at_or_below,
        "class_reachable_snr": verdicts[0],
        "subtype_reachable_snr": verdicts[1],
    }, ""
