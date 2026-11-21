"""Tests for picking the most familiar name of a star from its identifiers."""

from astrometricslib.pipelines.shared.star_designations import designation_rank, most_familiar_designation

VEGA_IDS = [
    "Gaia DR3 2097900186567046144",
    "TYC 3105-2070-1",
    "HIP 91262",
    "HD 172167",
    "* 3 Lyr",
    "* alf Lyr",
    "NAME Vega",
]


def test_a_proper_name_wins() -> None:
    """Vega is known as Vega before alf Lyr, 3 Lyr or any number."""
    assert most_familiar_designation(VEGA_IDS) == "Vega"


def test_the_order_without_a_proper_name() -> None:
    """Each kind wins over every kind after it."""
    assert most_familiar_designation(VEGA_IDS[:-1]) == "alf Lyr"
    assert most_familiar_designation(VEGA_IDS[:-2]) == "3 Lyr"
    assert most_familiar_designation(VEGA_IDS[:-3]) == "HD 172167"
    assert most_familiar_designation(VEGA_IDS[:-4]) == "HIP 91262"
    assert most_familiar_designation(VEGA_IDS[:-5]) == "TYC 3105-2070-1"
    assert most_familiar_designation(VEGA_IDS[:1]) == "Gaia DR3 2097900186567046144"


def test_an_unknown_kind_ranks_last_and_an_empty_list_has_no_name() -> None:
    """2MASS ranks after Gaia; nothing gives `None`."""
    assert designation_rank("2MASS J18365633+3847012") == 7
    assert most_familiar_designation(["2MASS J18365633+3847012", "Gaia DR3 1"]) == "Gaia DR3 1"
    assert most_familiar_designation([]) is None
    assert most_familiar_designation(["", "  "]) is None


def test_a_numbered_bayer_letter_is_a_bayer_name() -> None:
    """``* alf02 Lib`` is a Bayer name, ranked before Flamsteed."""
    assert most_familiar_designation(["* 9 Lib", "* alf02 Lib"]) == "alf02 Lib"
