"""Pure parse_ig_item + _event_window tests. No browser, fixtures only."""
from genealogy_core.models import Person
from genealogy_scrape.ig import _event_window, parse_ig_item


def _person(county: str = "Dublin") -> Person:
    return Person(surname="Doe", forename="John", county=county)


def test_relative_href_prefixed_with_ig_base():
    rec = parse_ig_item(
        summary="John Doe 1872 Civil Birth",
        detail_bits=["District: North Dublin"],
        href="/civil/1872/foo",
        fallback_url="https://fallback",
        person=_person(),
        forename="John",
        surname="Doe",
        site_label="IrishGenealogy.ie",
        record_type="Civil Birth",
    )
    assert rec.link == "https://www.irishgenealogy.ie/civil/1872/foo"


def test_absolute_href_passes_through():
    rec = parse_ig_item(
        summary="John Doe 1872 Civil Birth",
        detail_bits=[],
        href="https://example.com/rec/1",
        fallback_url="https://fb",
        person=_person(),
        forename="John",
        surname="Doe",
        site_label="IG",
        record_type="Civil Birth",
    )
    assert rec.link == "https://example.com/rec/1"


def test_empty_href_falls_back():
    rec = parse_ig_item(
        summary="Jane Doe 1901 Civil Death",
        detail_bits=[],
        href="",
        fallback_url="https://fallback/results",
        person=_person(),
        forename="Jane",
        surname="Doe",
        site_label="IG",
        record_type="Civil Death",
    )
    assert rec.link == "https://fallback/results"


def test_year_extracted_from_summary():
    rec = parse_ig_item(
        summary="John Doe 1872 Civil Birth",
        detail_bits=[],
        href="/x",
        fallback_url="",
        person=_person(),
        forename="John",
        surname="Doe",
        site_label="IG",
        record_type="Civil Birth",
    )
    assert rec.age_or_year == "1872"


def test_year_missing_leaves_blank():
    rec = parse_ig_item(
        summary="John Doe Civil Birth",
        detail_bits=[],
        href="/x",
        fallback_url="",
        person=_person(),
        forename="John",
        surname="Doe",
        site_label="IG",
        record_type="Civil Birth",
    )
    assert rec.age_or_year == ""


def test_pre_1700_year_rejected_by_regex():
    # Regex only matches 1600-2099; 1599 should be ignored.
    rec = parse_ig_item(
        summary="Nobody 1599 impossibly old",
        detail_bits=[],
        href="/x",
        fallback_url="",
        person=_person(),
        forename="x",
        surname="y",
        site_label="IG",
        record_type="Civil Birth",
    )
    assert rec.age_or_year == ""


def test_place_pulled_from_district_key():
    rec = parse_ig_item(
        summary="John Doe 1872 Civil Birth",
        detail_bits=["District: North Dublin", "Volume: 12"],
        href="/x",
        fallback_url="",
        person=_person(),
        forename="John",
        surname="Doe",
        site_label="IG",
        record_type="Civil Birth",
    )
    assert rec.place == "North Dublin"


def test_place_falls_back_to_person_county_when_no_match():
    rec = parse_ig_item(
        summary="John Doe 1872 Civil Birth",
        detail_bits=["Volume: 12", "Page: 3"],
        href="/x",
        fallback_url="",
        person=_person("Mayo"),
        forename="John",
        surname="Doe",
        site_label="IG",
        record_type="Civil Birth",
    )
    assert rec.place == "Mayo"


def test_place_uses_first_matching_key():
    rec = parse_ig_item(
        summary="x",
        detail_bits=["Area: Westside", "Parish: St Peter's"],
        href="/x",
        fallback_url="",
        person=_person(),
        forename="John",
        surname="Doe",
        site_label="IG",
        record_type="Church Baptism",
    )
    assert rec.place == "Westside"


def test_name_falls_back_when_summary_empty():
    rec = parse_ig_item(
        summary="",
        detail_bits=[],
        href="/x",
        fallback_url="",
        person=_person(),
        forename="Mary",
        surname="Fitzgerald",
        site_label="IG",
        record_type="Civil Birth",
    )
    assert rec.name_found == "Mary Fitzgerald"


def test_name_uses_summary_when_present():
    rec = parse_ig_item(
        summary="John Fitzpatrick 1870 Civil Marriage",
        detail_bits=[],
        href="/x",
        fallback_url="",
        person=_person(),
        forename="John",
        surname="Fitz",
        site_label="IG",
        record_type="Civil Marriage",
    )
    assert rec.name_found == "John Fitzpatrick 1870 Civil Marriage"


def test_other_details_joins_bits_with_pipe():
    rec = parse_ig_item(
        summary="x",
        detail_bits=["District: A", "Volume: 1", "Page: 2"],
        href="/x",
        fallback_url="",
        person=_person(),
        forename="x",
        surname="y",
        site_label="IG",
        record_type="Civil Birth",
    )
    assert rec.other_details == "District: A | Volume: 1 | Page: 2"


def test_record_type_and_site_passed_through():
    rec = parse_ig_item(
        summary="x 1900",
        detail_bits=[],
        href="/x",
        fallback_url="",
        person=_person(),
        forename="x",
        surname="y",
        site_label="IrishGenealogy.ie",
        record_type="Church Burial",
    )
    assert rec.site == "IrishGenealogy.ie"
    assert rec.record_type == "Church Burial"


def test_event_window_birth_passthrough():
    assert _event_window("Civil Birth", (1870, 1880), "civil") == (1870, 1880)
    assert _event_window("Church Baptism", (1870, 1880), "church") == (1870, 1880)


def test_event_window_marriage_shifts_plus_18_to_60():
    assert _event_window("Civil Marriage", (1870, 1880), "civil") == (1888, 1940)
    assert _event_window("Church Marriage", (1870, 1880), "church") == (1888, 1940)


def test_event_window_death_shifts_plus_20_to_100():
    assert _event_window("Civil Death", (1870, 1880), "civil") == (1890, 1980)


def test_event_window_burial_shifts_like_death():
    assert _event_window("Church Burial", (1870, 1880), "church") == (1890, 1980)


def test_event_window_none_window_civil_defaults():
    assert _event_window("Civil Birth", None, "civil") == (1864, 1950)


def test_event_window_none_window_church_defaults_earlier():
    assert _event_window("Church Baptism", None, "church") == (1700, 1950)


def test_person_searched_uses_person_label():
    rec = parse_ig_item(
        summary="x",
        detail_bits=[],
        href="/x",
        fallback_url="",
        person=Person(surname="Smith", forename="Alice"),
        forename="A",
        surname="S",
        site_label="IG",
        record_type="Civil Birth",
    )
    assert rec.person_searched == "Alice Smith"
