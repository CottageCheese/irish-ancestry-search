from genealogy_core.models import Person, Record
from genealogy_core.story import build_story, _extract_year


def _rec(**kw):
    base = dict(
        person_searched="William Furey", site="IG",
        record_type="Civil Birth", name_found="William Furey",
        age_or_year="1870", place="Galway",
        other_details="", link="http://x/1",
    )
    base.update(kw)
    return Record(**base)


def _person(forename="William", birth=1870, **kw):
    return Person(surname="Furey", forename=forename, birth_year_approx=birth, **kw)


def test_empty_when_no_records():
    assert build_story(_person(), []) == []


def test_empty_when_no_confirmed_and_no_likely():
    # score<10 and not confirmed → not enough signal
    r = _rec(score=4)
    assert build_story(_person(), [r]) == []


def test_birth_sentence_from_confirmed():
    r = _rec(status="confirmed", record_type="Civil Birth", age_or_year="1870", place="Galway")
    story = build_story(_person(), [r])
    assert any("William was born" in s and "Galway" in s and "1870" in s for s in story)


def test_falls_back_to_likely_if_no_confirmed():
    r = _rec(score=12, record_type="Civil Birth", age_or_year="1870", place="Cork")
    story = build_story(_person(), [r])
    assert any("William was born" in s for s in story)


def test_census_sentence_pulls_year_from_record_type():
    r = _rec(status="confirmed", site="NAI (1901)", record_type="Census 1901",
             age_or_year="31", place="Galway")
    story = build_story(_person(), [r])
    assert any("1901" in s and "census" in s and "Galway" in s for s in story)


def test_chronological_order():
    recs = [
        _rec(status="confirmed", record_type="Census 1911", age_or_year="41", place="Galway"),
        _rec(status="confirmed", record_type="Civil Birth", age_or_year="1870", place="Galway"),
        _rec(status="confirmed", record_type="Census 1901", age_or_year="31", place="Galway"),
    ]
    story = build_story(_person(), recs)
    non_fact = [s for s in story if not s.startswith("\u2728")]
    years = [int(y) for s in non_fact for y in __import__("re").findall(r"\b(18\d\d|19\d\d)\b", s)]
    assert years == sorted(years)


def test_dedupes_same_kind_same_year():
    r1 = _rec(status="confirmed", record_type="Civil Birth", age_or_year="1870", place="Galway")
    r2 = _rec(status="confirmed", record_type="Church Baptism", age_or_year="1870", place="Galway")
    story = build_story(_person(), [r1, r2])
    born = [s for s in story if "was born" in s]
    assert len(born) == 1


def test_rejected_records_excluded_from_likely_fallback():
    r = _rec(score=12, status="rejected", record_type="Civil Birth", age_or_year="1870")
    assert build_story(_person(), [r]) == []


def test_fun_facts_capped_at_two():
    r = _rec(status="confirmed", record_type="Civil Birth", age_or_year="1870", place="Galway")
    story = build_story(_person(birth=1870), [r])
    facts = [s for s in story if s.startswith("\u2728")]
    assert len(facts) <= 2


def test_fun_facts_skip_years_already_in_story():
    """If the 1901 census is already a story event, don't also say
    'age 31 when the 1901 census was taken' — that's redundant."""
    r = _rec(status="confirmed", site="NAI (1901)", record_type="Census 1901",
             age_or_year="31", place="Galway")
    story = build_story(_person(birth=1870), [r])
    facts = [s for s in story if s.startswith("\u2728")]
    assert not any("1901 census" in f for f in facts)


def test_fun_facts_use_age_at_event():
    r = _rec(status="confirmed", record_type="Civil Birth", age_or_year="1870", place="Galway")
    story = build_story(_person(birth=1870), [r])
    facts = [s for s in story if s.startswith("\u2728")]
    # Great Famine 1845 is before birth → excluded; Titanic 1912 → age 42; 1901 census → age 31
    assert all("was " in f for f in facts)
    assert not any("Famine" in f for f in facts)


def test_no_fun_facts_when_birth_year_missing():
    r = _rec(status="confirmed", record_type="Civil Birth", age_or_year="1870", place="Galway")
    story = build_story(_person(birth=None), [r])
    assert not any(s.startswith("\u2728") for s in story)


def test_griffith_sentence():
    r = _rec(status="confirmed", site="Griffith", record_type="Griffith",
             age_or_year="~1847-64", place="Galway")
    story = build_story(_person(), [r])
    assert any("Griffith" in s and "Galway" in s for s in story)


def test_extract_year_picks_first_valid():
    assert _extract_year("1870") == 1870
    assert _extract_year("", "Census 1901") == 1901
    assert _extract_year("age 42", "born 1870") == 1870
    assert _extract_year("no year") is None
    assert _extract_year("") is None


def test_forename_fallback_when_blank():
    p = Person(surname="Furey", forename="", birth_year_approx=1870)
    r = _rec(status="confirmed", record_type="Civil Birth", age_or_year="1870", place="Galway")
    story = build_story(p, [r])
    assert any("They was born" in s for s in story)
