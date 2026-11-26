"""Tests for the one ranking of star names and the SIMBAD naming step."""

from astropy.table import MaskedColumn, Table

from astrometricslib.pipelines.shared.catalog_star_identity import (
    familiarity_key,
    most_familiar_name,
    name_simbad_star,
    order_names_by_familiarity,
)
from astrometricslib.pipelines.shared.simbad_object_types import (
    normalize_name,
    simbad_row_identifiers,
    without_name_prefix,
)

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
    assert most_familiar_name(VEGA_IDS) == "Vega"


def test_the_order_without_a_proper_name() -> None:
    """Each kind wins over every kind after it; SIMBAD's star mark stays."""
    assert most_familiar_name(VEGA_IDS[:-1]) == "* alf Lyr"
    assert most_familiar_name(VEGA_IDS[:-2]) == "* 3 Lyr"
    assert most_familiar_name(VEGA_IDS[:-3]) == "HD 172167"
    assert most_familiar_name(VEGA_IDS[:-4]) == "HIP 91262"
    assert most_familiar_name(VEGA_IDS[:-5]) == "TYC 3105-2070-1"
    assert most_familiar_name(VEGA_IDS[:1]) == "Gaia DR3 2097900186567046144"


def test_survey_names_rank_before_gaia_and_an_empty_list_has_no_name() -> None:
    """A survey name such as 2MASS ranks before a Gaia number.

    An empty list gives `None`.
    """
    assert familiarity_key("2MASS J18365633+3847012") < familiarity_key("Gaia DR3 1")
    assert most_familiar_name(["Gaia DR3 1", "2MASS J18365633+3847012"]) == "2MASS J18365633+3847012"
    assert most_familiar_name([]) is None
    assert most_familiar_name(["", "  "]) is None


def test_a_numbered_bayer_letter_is_a_bayer_name() -> None:
    """``* alf02 Lib`` is a Bayer name, ranked before Flamsteed."""
    assert most_familiar_name(["* 9 Lib", "* alf02 Lib"]) == "* alf02 Lib"


def test_the_answer_does_not_depend_on_the_order() -> None:
    """Two names of one kind give the first alphabetically, in any order."""
    assert most_familiar_name(["HD 9", "HD 10"]) == most_familiar_name(["HD 10", "HD 9"]) == "HD 10"


def test_order_keeps_the_found_order_for_equal_keys() -> None:
    """Names of equal rank keep their order.

    A variable-star name follows a Flamsteed one.
    """
    assert order_names_by_familiarity(["V* RR Lyr", "HD 2", "HD 1", "* 3 Lyr", "Vega"]) == [
        "Vega",
        "* 3 Lyr",
        "V* RR Lyr",
        "HD 2",
        "HD 1",
    ]


def test_a_simbad_star_listed_by_gaia_is_saved_under_its_gaia_number() -> None:
    """A star Gaia lists is saved under its Gaia number.

    Its name is the proper name and its aliases are familiar names only.
    """
    names = name_simbad_star("* alf Lyr", VEGA_IDS)
    assert names.record_id == "Gaia DR3 2097900186567046144"
    assert names.name == "Vega"
    assert names.gaia_source_id == 2097900186567046144
    assert names.aliases == ["* alf Lyr", "* 3 Lyr", "HD 172167", "HIP 91262"]


def test_a_simbad_star_without_a_gaia_number_keeps_its_main_id() -> None:
    """A star Gaia does not list is saved under SIMBAD's main identifier."""
    names = name_simbad_star("* alf CMa", ["* alf CMa", "NAME Sirius", "HD 48915"])
    assert (names.record_id, names.name, names.gaia_source_id) == ("* alf CMa", "Sirius", None)
    assert names.aliases == ["HD 48915"]


def test_the_name_mark_is_removed_only_at_the_start() -> None:
    """``NAME`` marks a common name; elsewhere in a name it stays."""
    assert without_name_prefix("NAME  Vega") == "Vega"
    assert without_name_prefix("HD  172167") == "HD 172167"
    assert without_name_prefix("Cl NAME 5") == "Cl NAME 5"
    assert normalize_name("NAME  vega") == "VEGA"


def test_a_simbad_row_gives_its_main_id_and_identifiers() -> None:
    """The list is split on ``|``.

    A row without a list (here a masked one) gives its main id.
    """
    table = Table({
        "main_id": ["* alf Lyr", "HD 1"],
        "ids": MaskedColumn(["NAME Vega|HD 172167|", "x"], mask=[False, True]),
    })
    assert simbad_row_identifiers(table[0]) == ("* alf Lyr", ["NAME Vega", "HD 172167"])
    assert simbad_row_identifiers(table[1]) == ("HD 1", ["HD 1"])
