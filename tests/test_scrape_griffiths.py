"""Pure build_griffiths_record tests. No browser."""
from genealogy_core.models import Person
from genealogy_scrape.griffiths import GRIFFITHS_NOTE, build_griffiths_record


def _person() -> Person:
    return Person(surname="Furey", forename="John", county="Galway")


def test_record_fields_set():
    rec = build_griffiths_record(_person(), "Furey",
                                 "https://example/griffith?x=1",
                                 "Griffith's Valuation")
    assert rec.person_searched == "John Furey"
    assert rec.site == "Griffith's Valuation"
    assert rec.record_type == "Griffith's Valuation (manual search)"
    assert rec.name_found == "John Furey"
    assert rec.age_or_year == "~1847-64"
    assert rec.place == "Galway"
    assert rec.other_details == GRIFFITHS_NOTE
    assert rec.link == "https://example/griffith?x=1"


def test_variant_surname_overrides_person_surname():
    rec = build_griffiths_record(_person(), "Furry", "https://x", "G")
    assert rec.name_found == "John Furry"


def test_blank_forename_strips_trailing_space():
    p = Person(surname="Doe", forename="", county="Cork")
    rec = build_griffiths_record(p, "Doe", "https://x", "G")
    assert rec.name_found == "Doe"


def test_place_uses_person_county():
    p = Person(surname="Doe", forename="John", county="Mayo")
    rec = build_griffiths_record(p, "Doe", "https://x", "G")
    assert rec.place == "Mayo"
