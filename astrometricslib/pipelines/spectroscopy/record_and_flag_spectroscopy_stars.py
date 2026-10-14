"""Saves a batch of spectroscopy results and flags any shaky ones.

Every place that finishes extracting spectra -- the single-image pipeline
and the per-session batch worker -- needs the same two steps right
before it hands its stars back: reconcile and save them into the shared
catalog, then flag which ones shouldn't be trusted as-is. Keeping both
steps here means neither caller can drift from doing one without the
other, the way the batch worker used to skip reconciliation.
"""

from typing import Any

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.shared.star_recording import (
    StarIdentificationBreakdown,
    merge_spectroscopy_stellar_object,
    record_pipeline_stars,
)
from astrometricslib.pipelines.spectroscopy.post_processing.assess_output_quality import (
    build_spectral_classification_concerns,
)


def record_and_flag_spectroscopy_stars(
    stellar_objects: list[StellarObject],
    *,
    catalog_access: Any,
    target_id: str,
) -> tuple[list[StellarObject], StarIdentificationBreakdown | None, list[dict[str, object]]]:
    """Reconcile, save, and flag one batch of spectroscopy results.

    Runs `record_pipeline_stars` (target-id tagging, unresolved-star
    dropping, position-only/identified-star reconciliation, and the
    catalog merge/save), then builds spectral-classification concerns
    on the saved stars -- so every spectroscopy save path does the
    exact same steps in the exact same order, instead of each one
    hand-rolling its own subset.

    Parameters
    ----------
    stellar_objects : `list` [`StellarObject`]
        The stars this run extracted spectra for.
    catalog_access : `Any`
        Provides catalog reads and the merge/record call.
    target_id : `str`
        The target these stars belong to.

    Returns
    -------
    stellar_objects : `list` [`StellarObject`]
        The saved stars, tagged and reconciled.
    breakdown : `StarIdentificationBreakdown` or `None`
        The drop step's counts (see `record_pipeline_stars`).
    concerns : `list` [`dict`]
        Any stars whose classification shouldn't be trusted as-is (see
        `build_spectral_classification_concerns`).
    """
    stellar_objects, breakdown = record_pipeline_stars(
        stellar_objects,
        catalog_access=catalog_access,
        target_id=target_id,
        merge_function=merge_spectroscopy_stellar_object,
        pipeline_name="spectroscopy",
    )
    concerns = build_spectral_classification_concerns(stellar_objects)
    return stellar_objects, breakdown, concerns
