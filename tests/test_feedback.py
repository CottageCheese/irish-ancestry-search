from genealogy_core.feedback import extract_hints, score_hint_bonus
from genealogy_core.kin import annotate_records
from genealogy_core.models import Person, Record


def _rec(**kw) -> Record:
    base = dict(
        person_searched="John Furey", site="NAI", record_type="Census 1901",
        name_found="John Furey", age_or_year="1901", place="Ballybeg Galway",
        other_details="Mary Furey wife", link="http://x/?id=1",
    )
    base.update(kw)
    return Record(**base)


def test_extract_hints_groups_by_person():
    confirmed = [
        _rec(person_searched="John Furey", place="Ballybeg Galway",
             age_or_year="1901", other_details="Mary wife"),
        _rec(person_searched="Patrick Murphy", place="Cork City",
             age_or_year="1911"),
    ]
    hints = extract_hints(confirmed)
    assert "ballybeg" in hints["John Furey"]["places"]
    assert "galway" in hints["John Furey"]["places"]
    assert 1901 in hints["John Furey"]["years"]
    assert "mary" in hints["John Furey"]["kin"]
    assert "cork" in hints["Patrick Murphy"]["places"]


def test_score_hint_bonus_place_match():
    hints = {"places": {"ballybeg"}, "kin": set(), "years": set()}
    r = _rec(place="Ballybeg Mayo")
    pts, reason = score_hint_bonus(r, hints)
    assert pts == 2
    assert "ballybeg" in reason


def test_score_hint_bonus_year_within_two():
    hints = {"places": set(), "kin": set(), "years": {1901}}
    assert score_hint_bonus(_rec(age_or_year="1903"), hints)[0] == 2
    assert score_hint_bonus(_rec(age_or_year="1910"), hints)[0] == 0


def test_score_hint_bonus_empty():
    assert score_hint_bonus(_rec(), None) == (0, "")
    assert score_hint_bonus(_rec(), {}) == (0, "")


def test_annotate_records_applies_hints():
    person = Person(surname="Furey", forename="John", county="Galway",
                    birth_year_approx=1880, year_range=20)
    r_hinted = _rec(place="Ballybeg Galway")
    r_plain = _rec(link="http://x/?id=2", place="Dublin", other_details="")
    hints = {"places": {"ballybeg"}, "kin": set(), "years": set()}
    annotate_records(person, [r_hinted, r_plain], hints=hints)
    assert r_hinted.score > r_plain.score
    assert "feedback:place" in r_hinted.kin_hits


def test_annotate_records_no_hints_backward_compat():
    person = Person(surname="Furey", forename="John")
    r = _rec()
    annotate_records(person, [r])
    assert r.score > 0


def test_extract_hints_rejected_builds_anti_sets():
    confirmed = [_rec(place="Ballybeg Galway", age_or_year="1901",
                      other_details="Mary wife")]
    rejected = [_rec(place="Dublin City", age_or_year="1870",
                     other_details="Patrick son")]
    hints = extract_hints(confirmed, rejected)["John Furey"]
    assert "dublin" in hints["anti_places"]
    assert 1870 in hints["anti_years"]
    assert "patrick" in hints["anti_kin"]


def test_extract_hints_confirmed_wins_over_rejected():
    confirmed = [_rec(place="Ballybeg Galway")]
    rejected = [_rec(place="Ballybeg Mayo")]
    hints = extract_hints(confirmed, rejected)["John Furey"]
    assert "ballybeg" in hints["places"]
    assert "ballybeg" not in hints["anti_places"]
    assert "mayo" in hints["anti_places"]


def test_score_hint_bonus_anti_place_penalises():
    hints = {"places": set(), "kin": set(), "years": set(),
             "anti_places": {"dublin"}, "anti_kin": set(), "anti_years": set()}
    pts, reason = score_hint_bonus(_rec(place="Dublin City"), hints)
    assert pts == -2
    assert "anti_place:dublin" in reason


def test_score_hint_bonus_anti_year_within_two():
    hints = {"places": set(), "kin": set(), "years": set(),
             "anti_places": set(), "anti_kin": set(), "anti_years": {1870}}
    assert score_hint_bonus(_rec(age_or_year="1871"), hints)[0] == -2
    assert score_hint_bonus(_rec(age_or_year="1880"), hints)[0] == 0


def test_score_hint_bonus_mixed_positive_and_negative():
    hints = {"places": {"ballybeg"}, "kin": set(), "years": set(),
             "anti_places": {"dublin"}, "anti_kin": set(), "anti_years": set()}
    pts, reason = score_hint_bonus(_rec(place="Ballybeg Dublin"), hints)
    assert pts == 0
    assert "place:ballybeg" in reason
    assert "anti_place:dublin" in reason
