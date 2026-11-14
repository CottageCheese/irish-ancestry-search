"""Pure parse_nai_row tests. No browser, fixture cell lists only."""
from genealogy_core.models import Person
from genealogy_scrape.nai import parse_nai_household, parse_nai_row


def _person() -> Person:
    return Person(surname="Doe", forename="John", county="Dublin")


def test_full_row_maps_columns_in_order():
    cells = ["Doe", "John", "1901", "34", "Smithfield",
             "Dublin", "North Dock", "Carpenter", "Male", "Dublin"]
    rec = parse_nai_row(cells, "/census/1901/foo", "https://fallback",
                        _person(), "1901", "NAI Census")
    assert rec.name_found == "John Doe"
    assert rec.age_or_year == "34"
    assert rec.place == "Smithfield, Dublin, North Dock"
    assert "Carpenter" in rec.other_details
    assert "Male" in rec.other_details
    assert rec.link == "https://nationalarchives.ie/census/1901/foo"
    assert rec.site == "NAI Census (1901)"
    assert rec.record_type == "Census 1901"


def test_absolute_href_passes_through():
    cells = ["Doe", "John", "1911", "44", "", "", "", "", "", ""]
    rec = parse_nai_row(cells, "https://example.com/x", "https://fb",
                        _person(), "1911", "NAI")
    assert rec.link == "https://example.com/x"


def test_empty_href_falls_back():
    cells = ["Doe", "John", "1911", "44", "Dublin"]
    rec = parse_nai_row(cells, "", "https://fallback/page",
                        _person(), "1911", "NAI")
    assert rec.link == "https://fallback/page"


def test_short_row_tolerates_missing_cells():
    rec = parse_nai_row(["Doe"], "", "https://fb", _person(), "1901", "NAI")
    assert rec.name_found == "Doe"
    assert rec.age_or_year == "1901"  # falls back to year
    assert rec.place == "Dublin"      # falls back to person.county
    assert rec.other_details == ""


def test_empty_cells_fallback():
    rec = parse_nai_row([], "", "https://fb", _person(), "1926", "NAI")
    assert rec.name_found == ""
    assert rec.age_or_year == "1926"
    assert rec.place == "Dublin"


def test_parse_household_excludes_subject():
    rows = [
        ["Furey", "John", "26", "Male", "Head"],
        ["Furey", "Mary", "52", "Female", "Mother"],
        ["Furey", "Thomas", "55", "Male", "Father"],
        ["Kelly", "Bridget", "20", "Female", "Servant"],
    ]
    names = parse_nai_household(rows, "John Furey")
    assert names == ["Mary Furey", "Thomas Furey", "Bridget Kelly"]


def test_parse_household_dedupes_and_skips_empty():
    rows = [
        ["Furey", "Mary", "52"],
        ["Furey", "Mary", "52"],  # duplicate
        ["", "", ""],              # blank row
        ["Kelly", "", ""],         # surname-only
    ]
    names = parse_nai_household(rows, "John Furey")
    assert names == ["Mary Furey", "Kelly"]


def test_parse_household_subject_match_case_insensitive():
    rows = [
        ["furey", "JOHN", "26"],
        ["Furey", "Thomas", "55"],
    ]
    names = parse_nai_household(rows, "John Furey")
    assert names == ["Thomas Furey"]


def test_parse_household_empty_input():
    assert parse_nai_household([], "John Furey") == []
    assert parse_nai_household([[]], "John Furey") == []
