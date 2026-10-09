"""Tests that variable-star candidates are labelled from the catalogs.

The label comes from the saved catalog row. A candidate with no saved row, or
whose catalogs were never asked, must read as unknown and never as "not
listed".
"""

from types import SimpleNamespace

from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.known_variability import KnownVariability
from astrometricslib.models.stellar_source import StellarObject, VariableCandidate
from astrometricslib.pipelines.photometry.post_processing.known_variability_labels import (
    label_known_variability,
)


def make_candidate(star_id: str) -> VariableCandidate:
    """Build a candidate with only its id mattering.

    Returns
    -------
    candidate : `VariableCandidate`
        A candidate with plausible numbers.
    """
    return VariableCandidate(id=star_id, meanFlux=100.0, coefficientOfVariation=0.1, ra=10.0, dec=20.0)


class FakeCatalog:
    """A catalog stand-in that loads stars by id."""

    def __init__(self, stars: list[StellarObject], fail: bool = False) -> None:
        self.stars = {star.id: star for star in stars}
        self.fail = fail

    def get_by_ids(self, table: str, ids: list[str]) -> list[StellarObject]:
        """Load the stars that exist.

        Returns
        -------
        stars : `list` [`StellarObject`]
            The saved stars among `ids`.

        Raises
        ------
        ExternalServiceError
            When this fake was built to fail.
        """
        if self.fail:
            raise ExternalServiceError("The catalog could not be read.")
        return [self.stars[star_id] for star_id in ids if star_id in self.stars]


def test_a_listed_variable_is_labelled_with_the_catalogs_that_said_so() -> None:
    """A saved row that SIMBAD and VSX list gives a known variable."""
    catalog = FakeCatalog([StellarObject(id="A", simbadObjectTypes="*|EB*|V*", vsxVariabilityType="EA/SD")])
    candidate = make_candidate("A")

    label_known_variability([candidate], catalog)

    assert candidate.known_variability == KnownVariability.KNOWN_VARIABLE.value
    assert "SIMBAD or VSX" in candidate.known_variability_note


def test_a_star_not_listed_anywhere_names_the_catalogs_asked_and_not_asked() -> None:
    """The note says which catalogs were consulted, and which were not."""
    catalog = FakeCatalog([StellarObject(id="B", simbadObjectTypes="*|IR")])
    candidate = make_candidate("B")

    label_known_variability([candidate], catalog)

    assert candidate.known_variability == KnownVariability.NOT_LISTED.value
    assert (
        candidate.known_variability_note == "Not listed as variable in SIMBAD. Gaia DR3 or VSX not checked."
    )


def test_a_candidate_with_no_saved_row_is_unknown_not_unlisted() -> None:
    """No row means no catalog was asked."""
    candidate = make_candidate("C")

    label_known_variability([candidate], FakeCatalog([]))

    assert candidate.known_variability == KnownVariability.UNKNOWN.value
    assert "nothing is known" in candidate.known_variability_note


def test_a_saved_row_with_no_catalog_answers_is_unknown() -> None:
    """A star saved before any lookup reads as unknown."""
    candidate = make_candidate("D")

    label_known_variability([candidate], FakeCatalog([StellarObject(id="D")]))

    assert candidate.known_variability == KnownVariability.UNKNOWN.value


def test_no_catalog_access_or_a_failed_lookup_leaves_every_candidate_unknown() -> None:
    """The label never breaks the run, and never claims more than it knows."""
    without = make_candidate("E")
    label_known_variability([without], None)
    assert without.known_variability == KnownVariability.UNKNOWN.value

    failing = make_candidate("F")
    label_known_variability(
        [failing], FakeCatalog([StellarObject(id="F", simbadObjectTypes="*|V*")], fail=True)
    )
    assert failing.known_variability == KnownVariability.UNKNOWN.value


def test_no_candidates_makes_no_request() -> None:
    """An empty list does not touch the catalog."""
    catalog = SimpleNamespace(get_by_ids=lambda *args: (_ for _ in ()).throw(AssertionError("asked")))

    label_known_variability([], catalog)


def test_the_scatter_of_a_run_is_split_by_what_the_catalogs_say() -> None:
    """Listed and unlisted stars are split; the unasked are dropped."""
    from astrometricslib.pipelines.photometry.post_processing.known_variability_labels import (
        split_scatter_by_catalog_status,
    )

    def with_scatter(star_id: str, scatter: float, **row: str) -> StellarObject:
        star = StellarObject(id=star_id, **row)
        star.photometry.coefficient_of_variation = scatter
        return star

    run = [
        with_scatter("V", 0.4, simbadObjectTypes="*|V*"),
        with_scatter("U", 0.1, simbadObjectTypes="*|IR"),
        with_scatter("Asked-nothing", 0.2),
        with_scatter("Missing", 0.3),
    ]
    catalog = FakeCatalog([
        StellarObject(id="V", simbadObjectTypes="*|V*"),
        StellarObject(id="U", simbadObjectTypes="*|IR"),
        StellarObject(id="Asked-nothing"),
    ])

    known, unlisted = split_scatter_by_catalog_status(run, catalog)

    assert known == [0.4]
    assert unlisted == [0.1]


def test_no_catalog_or_a_failed_lookup_gives_empty_groups() -> None:
    """The check is simply not made; it never breaks the run."""
    from astrometricslib.pipelines.photometry.post_processing.known_variability_labels import (
        split_scatter_by_catalog_status,
    )

    star = StellarObject(id="V")
    star.photometry.coefficient_of_variation = 0.4

    assert split_scatter_by_catalog_status([star], None) == ([], [])
    assert split_scatter_by_catalog_status([star], FakeCatalog([], fail=True)) == ([], [])
